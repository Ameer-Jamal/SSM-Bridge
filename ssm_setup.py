"""Local, agent-friendly configuration and browser-mediated AWS SSO login."""
from __future__ import annotations

import atexit
import configparser
import json
import os
import re
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ssm_bridge import SsmBridgeBackend


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SsmSetup:
    def __init__(self, backend: SsmBridgeBackend, aws_config: Path | None = None) -> None:
        self.backend = backend
        self.aws_config = aws_config or Path(
            os.environ.get("AWS_CONFIG_FILE", str(Path.home() / ".aws/config"))
        ).expanduser()
        self._lock = threading.RLock()
        self._logins: dict[str, dict[str, Any]] = {}
        atexit.register(self.close)

    def close(self) -> None:
        """Do not leave pending AWS CLI children behind when the server exits."""
        with self._lock:
            for flow in self._logins.values():
                if flow["status"] == "pending":
                    flow["status"] = "cancelled"
                    flow["process"].kill()

    def configure_sso(
        self, *, aws_profile: str, start_url: str, sso_region: str,
        account_id: str, role_name: str, region: str,
    ) -> dict[str, Any]:
        """Add a profile without replacing existing settings or storing credentials."""
        for value in (aws_profile, sso_region, role_name, region):
            if not value or any(char in value for char in "\r\n[]"):
                raise ValueError("Profile, role and regions must be nonempty single-line values")
        if not re.fullmatch(r"\d{12}", account_id):
            raise ValueError("account_id must contain 12 digits")
        parsed = urlparse(start_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or any(char.isspace() for char in start_url)):
            raise ValueError("start_url must be an HTTPS portal URL without credentials or query parameters")
        session = f"ssm-bridge-{aws_profile}"
        sections = {
            f"sso-session {session}": {
                "sso_start_url": start_url, "sso_region": sso_region,
                "sso_registration_scopes": "sso:account:access",
            },
            "default" if aws_profile == "default" else f"profile {aws_profile}": {
                "sso_session": session, "sso_account_id": account_id,
                "sso_role_name": role_name, "region": region, "output": "json",
            },
        }
        with self._lock:
            original = self.aws_config.read_text() if self.aws_config.exists() else ""
            config = configparser.RawConfigParser()
            config.read_string(original)
            additions = []
            for section, fields in sections.items():
                if config.has_section(section):
                    if dict(config.items(section)) != fields:
                        raise ValueError(f"Existing [{section}] differs; choose a new profile name")
                else:
                    additions.append(f"\n[{section}]\n" + "".join(
                        f"{key} = {value}\n" for key, value in fields.items()
                    ))
            if additions:
                atomic_write(self.aws_config, original + "".join(additions))
        return {"aws_profile": aws_profile, "config_path": str(self.aws_config),
                "configured": True, "next_step": "ssm_login_start"}

    def login_start(self, *, aws_profile: str) -> dict[str, Any]:
        if not aws_profile or aws_profile.startswith("-"):
            raise ValueError("A named AWS profile is required")
        with self._lock:
            for login_id, flow in self._logins.items():
                if flow["aws_profile"] == aws_profile and flow["status"] == "pending":
                    return self.login_status(login_id=login_id)
            # Keep recent completed IDs readable while bounding memory usage.
            if len(self._logins) >= 32:
                for key, old in list(self._logins.items()):
                    if old["status"] != "pending":
                        del self._logins[key]
                        break
            if sum(flow["status"] == "pending" for flow in self._logins.values()) >= 4:
                raise ValueError("Four login flows are already pending; finish them first")
            process = subprocess.Popen(
                [self.backend.aws_binary, "sso", "login", "--profile", aws_profile,
                 "--no-browser", "--use-device-code"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                env={**os.environ, "AWS_CONFIG_FILE": str(self.aws_config), "AWS_PAGER": ""},
            )
            login_id = uuid.uuid4().hex
            flow = {"aws_profile": aws_profile, "status": "pending", "output": "",
                    "ready": threading.Event(), "process": process}
            self._logins[login_id] = flow
            worker = threading.Thread(target=self._read_login, args=(flow,), daemon=True)
            flow["worker"] = worker
            worker.start()
        flow["ready"].wait(10)
        return self.login_status(login_id=login_id)

    def _read_login(self, flow: dict[str, Any]) -> None:
        process = flow["process"]

        def expire() -> None:
            with self._lock:
                if flow["status"] == "pending":
                    flow["status"] = "expired"
                    process.kill()
                    flow["ready"].set()

        timer = threading.Timer(900, expire)
        timer.daemon = True
        timer.start()
        try:
            assert process.stdout is not None
            for line in process.stdout:
                with self._lock:
                    flow["output"] = (flow["output"] + line)[-16000:]
                    if "user_code=" in line:
                        flow["ready"].set()
            returncode = process.wait()
            with self._lock:
                if flow["status"] == "pending":
                    flow["status"] = "success" if returncode == 0 else "failed"
        except OSError:
            with self._lock:
                flow["status"] = "failed"
        finally:
            timer.cancel()
            if process.stdout is not None:
                process.stdout.close()
            flow["ready"].set()

    def login_status(self, *, login_id: str, wait_seconds: int = 0) -> dict[str, Any]:
        with self._lock:
            flow = self._logins.get(login_id)
            if flow is None:
                raise ValueError("Unknown login_id; start a new login after a server restart")
        # Bounded waiting keeps the agent responsive while the human completes MFA.
        flow["worker"].join(max(0, min(wait_seconds, 20)))
        with self._lock:
            urls = re.findall(r"https://[^\s\x1b]+", flow["output"])
            url = next((url for url in urls if "user_code=" in url), "")
            result = {"login_id": login_id, "aws_profile": flow["aws_profile"],
                      "status": flow["status"], "verification_url": url}
            if flow["status"] == "pending":
                result["next_step"] = (
                    "Open verification_url, complete browser login and human MFA approval, "
                    "then call ssm_login_status with this login_id. Do not start another login."
                    if url else "Call ssm_login_status again; AWS CLI is starting."
                )
            elif flow["status"] != "success":
                result["next_step"] = "Login did not complete. Start a new login or inspect AWS CLI locally."
            return result

    def save_target(
        self, *, target: str, aws_profile: str, instance_id: str,
        make_default: bool = False,
    ) -> dict[str, Any]:
        if not target or not aws_profile or not re.fullmatch(r"i-[0-9a-f]{8,17}", instance_id):
            raise ValueError("A target name, AWS profile and valid EC2 instance ID are required")
        # Verify identity and SSM before writing a target; do not run a remote command.
        verified = self.backend.status(aws_profile=aws_profile, instance_id=instance_id)
        if not verified["online"]:
            raise ValueError("Instance is not online in SSM; target was not saved")
        node = verified["ssm"]
        entry = {"aws_profile": aws_profile, "instance_id": instance_id,
                 "host_hint": node.get("ComputerName", ""),
                 "private_ip": node.get("IPAddress", ""),
                 "auto_sso_login": True, "sso_profile": aws_profile}
        with self._lock:
            data = self.backend._load_config()
            targets = data.setdefault("targets", {})
            if target in targets and (
                targets[target].get("aws_profile") != aws_profile
                or targets[target].get("instance_id") != instance_id
            ):
                raise ValueError("Target already points to another host; choose a new target name")
            targets[target] = entry
            if make_default:
                data["default"] = target
            atomic_write(self.backend.config_path, json.dumps(data, indent=2) + "\n")
        return {"saved": True, "target": target, "config_path": str(self.backend.config_path),
                "identity": verified["identity"], "online": True}
