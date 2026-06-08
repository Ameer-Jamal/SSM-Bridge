from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).with_name("targets.json")
DEFAULT_UPLOAD_CHUNK_SIZE = 7000
DEFAULT_TEXT_READ_LIMIT = 200_000

ENV_CONFIG_PATH = "SSM_BRIDGE_CONFIG"
ENV_TARGET = "SSM_BRIDGE_TARGET"
ENV_AWS_PROFILE = "SSM_BRIDGE_AWS_PROFILE"
ENV_INSTANCE_ID = "SSM_BRIDGE_INSTANCE_ID"
ENV_HOST_HINT = "SSM_BRIDGE_HOST_HINT"
ENV_PRIVATE_IP = "SSM_BRIDGE_PRIVATE_IP"
ENV_AUTO_SSO_LOGIN = "SSM_BRIDGE_AUTO_SSO_LOGIN"
ENV_SSO_PROFILE = "SSM_BRIDGE_SSO_PROFILE"


class SsmBridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class SsmTarget:
    name: str
    aws_profile: str
    instance_id: str
    host_hint: str = ""
    private_ip: str = ""
    auto_sso_login: bool = True
    sso_profile: str = ""

    def public_dict(self) -> dict[str, str | bool]:
        return {
            "name": self.name,
            "aws_profile": self.aws_profile,
            "instance_id": self.instance_id,
            "host_hint": self.host_hint,
            "private_ip": self.private_ip,
            "auto_sso_login": self.auto_sso_login,
            "sso_profile": self.sso_profile or self.aws_profile,
        }


class SsmBridgeBackend:
    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        aws_binary: str = "aws",
        upload_chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
    ) -> None:
        self.config_path = Path(config_path or os.environ.get(ENV_CONFIG_PATH) or DEFAULT_CONFIG_PATH)
        self.aws_binary = aws_binary
        self.upload_chunk_size = upload_chunk_size
        self._config = self._load_config()

    def list_targets(self) -> dict[str, Any]:
        default_name = self._config.get("default", "")
        targets = [
            self._target_from_config(name, data).public_dict()
            for name, data in sorted((self._config.get("targets") or {}).items())
        ]
        env_target = self._target_from_env()
        if env_target:
            targets.append(env_target.public_dict())
        return {
            "default": default_name or (env_target.name if env_target else ""),
            "targets": targets,
            "config_path": str(self.config_path),
            "config_exists": self.config_path.exists(),
        }

    def status(
        self,
        *,
        target: str = "",
        aws_profile: str = "",
        instance_id: str = "",
    ) -> dict[str, Any]:
        resolved = self.resolve_target(target=target, aws_profile=aws_profile, instance_id=instance_id)
        identity = self._aws_json(
            ["sts", "get-caller-identity", "--profile", resolved.aws_profile],
            description="get AWS caller identity",
            target=resolved,
        )
        info = self._aws_json(
            [
                "ssm",
                "describe-instance-information",
                "--profile",
                resolved.aws_profile,
                "--filters",
                f"Key=InstanceIds,Values={resolved.instance_id}",
            ],
            description="describe SSM instance information",
            target=resolved,
        )
        records = info.get("InstanceInformationList") or []
        return {
            "target": resolved.public_dict(),
            "identity": identity,
            "ssm": records[0] if records else {},
            "online": bool(records and records[0].get("PingStatus") == "Online"),
        }

    def find_instances(self, query: str, *, aws_profile: str = "") -> dict[str, Any]:
        term = query.strip()
        if not term:
            raise ValueError("query is required")
        profile = self._profile_for_lookup(aws_profile=aws_profile)
        instances = self._find_ec2_instances(term, aws_profile=profile)
        ssm_by_id = self._ssm_info_by_instance_id(profile, [item["instance_id"] for item in instances])
        for item in instances:
            ssm = ssm_by_id.get(item["instance_id"], {})
            item["ssm"] = ssm
            item["ssm_online"] = ssm.get("PingStatus") == "Online"
        return {
            "query": term,
            "aws_profile": profile,
            "count": len(instances),
            "instances": instances,
        }

    def run_command(
        self,
        command: str,
        *,
        target: str = "",
        aws_profile: str = "",
        instance_id: str = "",
        timeout_seconds: int = 600,
        comment: str = "ssm-bridge-mcp",
    ) -> dict[str, Any]:
        if not command.strip():
            raise ValueError("command is required")
        self._raise_if_ambiguous_target(target=target, aws_profile=aws_profile, instance_id=instance_id)
        resolved = self.resolve_target(target=target, aws_profile=aws_profile, instance_id=instance_id)
        params = json.dumps({"commands": [command]})
        send = self._aws_json(
            [
                "ssm",
                "send-command",
                "--profile",
                resolved.aws_profile,
                "--instance-ids",
                resolved.instance_id,
                "--document-name",
                "AWS-RunShellScript",
                "--comment",
                comment,
                "--parameters",
                params,
            ],
            description="send SSM shell command",
            target=resolved,
        )
        command_id = ((send.get("Command") or {}).get("CommandId") or "").strip()
        if not command_id:
            raise SsmBridgeError(f"AWS did not return a command id: {send}")

        wait = subprocess.run(
            [
                self.aws_binary,
                "ssm",
                "wait",
                "command-executed",
                "--profile",
                resolved.aws_profile,
                "--command-id",
                command_id,
                "--instance-id",
                resolved.instance_id,
            ],
            text=True,
            capture_output=True,
            timeout=max(1, timeout_seconds),
            check=False,
        )

        invocation = self._aws_json(
            [
                "ssm",
                "get-command-invocation",
                "--profile",
                resolved.aws_profile,
                "--command-id",
                command_id,
                "--instance-id",
                resolved.instance_id,
            ],
            description="get SSM command invocation",
            target=resolved,
        )
        status = invocation.get("Status") or ""
        response_code = invocation.get("ResponseCode")
        return {
            "target": resolved.public_dict(),
            "command": command,
            "command_id": command_id,
            "status": status,
            "response_code": response_code,
            "stdout": invocation.get("StandardOutputContent") or "",
            "stderr": invocation.get("StandardErrorContent") or "",
            "wait_return_code": wait.returncode,
            "wait_stderr": wait.stderr or "",
            "success": status == "Success" and response_code == 0,
        }

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        *,
        target: str = "",
        aws_profile: str = "",
        instance_id: str = "",
    ) -> dict[str, Any]:
        source = Path(local_path).expanduser()
        if not source.is_file():
            raise FileNotFoundError(f"local file not found: {source}")
        if not remote_path.strip():
            raise ValueError("remote_path is required")

        remote_q = shlex.quote(remote_path)
        remote_dir_q = shlex.quote(os.path.dirname(remote_path) or ".")
        tmp_remote = f"/tmp/ssm-bridge-mcp-{os.getpid()}-{source.name}.b64"
        tmp_q = shlex.quote(tmp_remote)

        self._ensure_success(
            self.run_command(
                f"mkdir -p {remote_dir_q} && : > {tmp_q}",
                target=target,
                aws_profile=aws_profile,
                instance_id=instance_id,
            )
        )

        encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        for chunk in self._chunks(encoded, self.upload_chunk_size):
            self._ensure_success(
                self.run_command(
                    f"printf %s {shlex.quote(chunk)} >> {tmp_q}",
                    target=target,
                    aws_profile=aws_profile,
                    instance_id=instance_id,
                )
            )

        result = self.run_command(
            f"base64 -d {tmp_q} > {remote_q} && rm -f {tmp_q} && wc -c {remote_q} && ls -l {remote_q}",
            target=target,
            aws_profile=aws_profile,
            instance_id=instance_id,
        )
        self._ensure_success(result)
        return {
            "local_path": str(source),
            "remote_path": remote_path,
            "local_bytes": source.stat().st_size,
            "verification": result,
        }

    def download_file(
        self,
        remote_path: str,
        local_path: str,
        *,
        target: str = "",
        aws_profile: str = "",
        instance_id: str = "",
    ) -> dict[str, Any]:
        if not remote_path.strip():
            raise ValueError("remote_path is required")
        destination = Path(local_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        remote_q = shlex.quote(remote_path)
        result = self.run_command(
            f"base64 {remote_q}",
            target=target,
            aws_profile=aws_profile,
            instance_id=instance_id,
        )
        self._ensure_success(result)
        destination.write_bytes(base64.b64decode(result["stdout"]))
        return {
            "remote_path": remote_path,
            "local_path": str(destination),
            "bytes": destination.stat().st_size,
            "command_id": result["command_id"],
        }

    def get_file(
        self,
        remote_path: str,
        *,
        target: str = "",
        aws_profile: str = "",
        instance_id: str = "",
        max_bytes: int = DEFAULT_TEXT_READ_LIMIT,
    ) -> dict[str, Any]:
        if not remote_path.strip():
            raise ValueError("remote_path is required")
        limit = max(1, min(max_bytes, DEFAULT_TEXT_READ_LIMIT))
        remote_q = shlex.quote(remote_path)
        result = self.run_command(
            f"wc -c {remote_q} && head -c {limit} {remote_q}",
            target=target,
            aws_profile=aws_profile,
            instance_id=instance_id,
        )
        self._ensure_success(result)
        stdout = result["stdout"]
        first_line, _, content = stdout.partition("\n")
        size_text = first_line.strip().split(" ")[0] if first_line.strip() else "0"
        size = int(size_text) if size_text.isdigit() else 0
        return {
            "remote_path": remote_path,
            "bytes": size,
            "max_bytes": limit,
            "truncated": size > limit,
            "content": content,
            "command_id": result["command_id"],
        }

    def resolve_target(
        self,
        *,
        target: str = "",
        aws_profile: str = "",
        instance_id: str = "",
    ) -> SsmTarget:
        target_name = target or os.environ.get(ENV_TARGET, "") or self._config.get("default") or ""
        target_data = (self._config.get("targets") or {}).get(target_name, {})
        env_target = self._target_from_env()

        if not target_data and env_target and (not target or target == env_target.name):
            target_data = env_target.public_dict()
            target_name = env_target.name

        if not target_data and not (aws_profile and instance_id):
            raise ValueError(f"unknown target: {target_name}")

        resolved = self._target_from_config(target_name or "override", target_data)
        return SsmTarget(
            name=resolved.name,
            aws_profile=aws_profile or resolved.aws_profile,
            instance_id=instance_id or resolved.instance_id,
            host_hint=resolved.host_hint,
            private_ip=resolved.private_ip,
            auto_sso_login=resolved.auto_sso_login,
            sso_profile=resolved.sso_profile or resolved.aws_profile,
        )

    def _raise_if_ambiguous_target(self, *, target: str, aws_profile: str, instance_id: str) -> None:
        term = target.strip()
        if instance_id or not term:
            return
        resolved = self.resolve_target(target=target, aws_profile=aws_profile, instance_id=instance_id)
        matches = self._find_ec2_instances(term, aws_profile=aws_profile or resolved.aws_profile)
        if len(matches) <= 1:
            return
        candidates = [
            {
                "instance_id": item["instance_id"],
                "name": item["name"],
                "state": item["state"],
                "private_ip": item["private_ip"],
            }
            for item in matches
        ]
        raise SsmBridgeError(
            "ambiguous target "
            f"{term!r}: found {len(matches)} EC2 instances. "
            "Pass aws_profile and instance_id, or use ssm_find_instances to choose one. "
            f"Candidates: {json.dumps(candidates, sort_keys=True)}"
        )

    def _find_ec2_instances(self, query: str, *, aws_profile: str) -> list[dict[str, Any]]:
        if not aws_profile:
            raise ValueError("aws_profile is required for EC2 instance search")
        filters = ["Name=instance-state-name,Values=pending,running,stopping,stopped"]
        if query.startswith("i-"):
            args = [
                "ec2",
                "describe-instances",
                "--profile",
                aws_profile,
                "--instance-ids",
                query,
                "--filters",
                *filters,
            ]
        else:
            args = [
                "ec2",
                "describe-instances",
                "--profile",
                aws_profile,
                "--filters",
                f"Name=tag:Name,Values=*{query}*",
                *filters,
            ]
        data = self._aws_json(args, description="describe EC2 instances")
        return self._ec2_instances_from_response(data)

    def _ssm_info_by_instance_id(self, aws_profile: str, instance_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not instance_ids:
            return {}
        info = self._aws_json(
            [
                "ssm",
                "describe-instance-information",
                "--profile",
                aws_profile,
                "--filters",
                f"Key=InstanceIds,Values={','.join(instance_ids)}",
            ],
            description="describe SSM instance information",
        )
        records = info.get("InstanceInformationList") or []
        return {
            str(record.get("InstanceId")): record
            for record in records
            if record.get("InstanceId")
        }

    def _profile_for_lookup(self, *, aws_profile: str = "") -> str:
        if aws_profile:
            return aws_profile
        target_name = os.environ.get(ENV_TARGET, "") or self._config.get("default") or ""
        target_data = (self._config.get("targets") or {}).get(target_name, {})
        env_target = self._target_from_env()
        if target_data:
            return self._target_from_config(target_name, target_data).aws_profile
        if env_target:
            return env_target.aws_profile
        raise ValueError("aws_profile is required for EC2 instance search")

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {"default": "", "targets": {}}
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    @staticmethod
    def _ec2_instances_from_response(data: dict[str, Any]) -> list[dict[str, Any]]:
        instances = []
        for reservation in data.get("Reservations") or []:
            for item in reservation.get("Instances") or []:
                tags = {
                    str(tag.get("Key")): str(tag.get("Value"))
                    for tag in item.get("Tags") or []
                    if tag.get("Key") is not None
                }
                placement = item.get("Placement") or {}
                state = item.get("State") or {}
                instances.append(
                    {
                        "instance_id": str(item.get("InstanceId") or ""),
                        "name": tags.get("Name", ""),
                        "state": str(state.get("Name") or ""),
                        "private_ip": str(item.get("PrivateIpAddress") or ""),
                        "public_dns": str(item.get("PublicDnsName") or ""),
                        "availability_zone": str(placement.get("AvailabilityZone") or ""),
                        "launch_time": str(item.get("LaunchTime") or ""),
                    }
                )
        return sorted(instances, key=lambda item: (item["name"], item["instance_id"]))

    @staticmethod
    def _target_from_config(name: str, data: dict[str, Any]) -> SsmTarget:
        return SsmTarget(
            name=name,
            aws_profile=str(data.get("aws_profile") or ""),
            instance_id=str(data.get("instance_id") or ""),
            host_hint=str(data.get("host_hint") or ""),
            private_ip=str(data.get("private_ip") or ""),
            auto_sso_login=_bool_from_value(data.get("auto_sso_login", True)),
            sso_profile=str(data.get("sso_profile") or data.get("aws_profile") or ""),
        )

    @staticmethod
    def _target_from_env() -> SsmTarget | None:
        aws_profile = os.environ.get(ENV_AWS_PROFILE, "")
        instance_id = os.environ.get(ENV_INSTANCE_ID, "")
        if not (aws_profile and instance_id):
            return None
        return SsmTarget(
            name=os.environ.get(ENV_TARGET, "") or "env",
            aws_profile=aws_profile,
            instance_id=instance_id,
            host_hint=os.environ.get(ENV_HOST_HINT, ""),
            private_ip=os.environ.get(ENV_PRIVATE_IP, ""),
            auto_sso_login=_bool_from_value(os.environ.get(ENV_AUTO_SSO_LOGIN, "true")),
            sso_profile=os.environ.get(ENV_SSO_PROFILE, "") or aws_profile,
        )

    def _aws_json(self, args: list[str], *, description: str, target: SsmTarget | None = None) -> dict[str, Any]:
        result = self._run_aws(args)
        if result.returncode != 0 and target and self._is_expired_sso_error(result.stderr):
            if self._try_sso_login(target):
                result = self._run_aws(args)

        if result.returncode != 0:
            raise SsmBridgeError(
                f"failed to {description}: exit={result.returncode}; stderr={result.stderr.strip()}"
            )
        try:
            return json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise SsmBridgeError(f"failed to parse AWS JSON for {description}: {exc}") from exc

    def _run_aws(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [self.aws_binary, *args, "--output", "json"],
            text=True,
            capture_output=True,
            check=False,
        )
        return result

    def _try_sso_login(self, target: SsmTarget) -> bool:
        if not target.auto_sso_login:
            return False
        profile = target.sso_profile or target.aws_profile
        if not profile:
            return False
        result = subprocess.run(
            [self.aws_binary, "sso", "login", "--profile", profile],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise SsmBridgeError(
                f"failed to refresh AWS SSO for profile {profile}: "
                f"exit={result.returncode}; stderr={result.stderr.strip()}"
            )
        return True

    @staticmethod
    def _is_expired_sso_error(stderr: str) -> bool:
        lowered = (stderr or "").lower()
        return "sso" in lowered and (
            "token has expired" in lowered
            or "error when retrieving token" in lowered
            or "the sso session associated with this profile has expired"
        )

    @staticmethod
    def _ensure_success(result: dict[str, Any]) -> None:
        if not result.get("success"):
            raise SsmBridgeError(
                "remote command failed: "
                f"status={result.get('status')}; "
                f"code={result.get('response_code')}; "
                f"command_id={result.get('command_id')}; "
                f"stderr={result.get('stderr')}"
            )

    @staticmethod
    def _chunks(value: str, size: int) -> list[str]:
        if not value:
            return [""]
        return [value[index : index + size] for index in range(0, len(value), size)]


def _bool_from_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() not in {"0", "false", "no", "off"}
