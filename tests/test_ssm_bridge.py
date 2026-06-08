from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ssm_bridge import SsmBridgeBackend, SsmBridgeError


def write_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "default": "dev-box",
                "targets": {
                    "dev-box": {
                        "aws_profile": "example-profile",
                        "auto_sso_login": True,
                        "sso_profile": "example-profile",
                        "instance_id": "i-0123456789abcdef0",
                        "host_hint": "dev-box.example.internal",
                        "private_ip": "10.0.0.1",
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def completed(stdout: str = "{}", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["aws"], returncode=returncode, stdout=stdout, stderr=stderr)


def ec2_response(*instances: dict[str, str]) -> str:
    return json.dumps(
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": instance["instance_id"],
                            "State": {"Name": instance.get("state", "running")},
                            "PrivateIpAddress": instance.get("private_ip", ""),
                            "PublicDnsName": instance.get("public_dns", ""),
                            "Placement": {"AvailabilityZone": instance.get("availability_zone", "us-east-1a")},
                            "LaunchTime": instance.get("launch_time", "2026-06-08T00:00:00+00:00"),
                            "Tags": [{"Key": "Name", "Value": instance.get("name", "")}],
                        }
                        for instance in instances
                    ]
                }
            ]
        }
    )


def ssm_info_response(*instances: dict[str, str]) -> str:
    return json.dumps(
        {
            "InstanceInformationList": [
                {
                    "InstanceId": instance["instance_id"],
                    "PingStatus": instance.get("ping_status", "Online"),
                    "IPAddress": instance.get("private_ip", ""),
                    "ComputerName": instance.get("computer_name", ""),
                }
                for instance in instances
            ]
        }
    )


def test_resolve_default_target(tmp_path: Path) -> None:
    config = tmp_path / "targets.json"
    write_config(config)
    backend = SsmBridgeBackend(config_path=config)

    target = backend.resolve_target()

    assert target.name == "dev-box"
    assert target.aws_profile == "example-profile"
    assert target.instance_id == "i-0123456789abcdef0"
    assert target.auto_sso_login is True
    assert target.sso_profile == "example-profile"


def test_resolve_target_overrides(tmp_path: Path) -> None:
    config = tmp_path / "targets.json"
    write_config(config)
    backend = SsmBridgeBackend(config_path=config)

    target = backend.resolve_target(aws_profile="other", instance_id="i-override")

    assert target.aws_profile == "other"
    assert target.instance_id == "i-override"


def test_unknown_target_requires_overrides(tmp_path: Path) -> None:
    config = tmp_path / "targets.json"
    write_config(config)
    backend = SsmBridgeBackend(config_path=config)

    with pytest.raises(ValueError, match="unknown target"):
        backend.resolve_target(target="missing")


def test_find_instances_by_name_includes_ssm_status(tmp_path: Path) -> None:
    config = tmp_path / "targets.json"
    write_config(config)
    backend = SsmBridgeBackend(config_path=config)
    responses = [
        completed(
            ec2_response(
                {"instance_id": "i-1", "name": "alpha-dev-box", "private_ip": "10.0.0.1"},
                {"instance_id": "i-2", "name": "beta-dev-box", "private_ip": "10.0.0.2"},
            )
        ),
        completed(
            ssm_info_response(
                {
                    "instance_id": "i-2",
                    "ping_status": "Online",
                    "private_ip": "10.0.0.2",
                    "computer_name": "beta-dev-box.example.internal",
                }
            )
        ),
    ]

    with patch("ssm_bridge.subprocess.run", side_effect=responses) as run:
        result = backend.find_instances("dev-box")

    assert result["count"] == 2
    assert result["instances"][0]["instance_id"] == "i-1"
    assert result["instances"][0]["ssm_online"] is False
    assert result["instances"][1]["instance_id"] == "i-2"
    assert result["instances"][1]["ssm_online"] is True
    assert run.call_args_list[0].args[0][:4] == ["aws", "ec2", "describe-instances", "--profile"]
    assert run.call_args_list[1].args[0][:4] == ["aws", "ssm", "describe-instance-information", "--profile"]


def test_resolve_env_only_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing_config = tmp_path / "missing.json"
    monkeypatch.setenv("SSM_BRIDGE_TARGET", "env-box")
    monkeypatch.setenv("SSM_BRIDGE_AWS_PROFILE", "env-profile")
    monkeypatch.setenv("SSM_BRIDGE_INSTANCE_ID", "i-0env")
    monkeypatch.setenv("SSM_BRIDGE_AUTO_SSO_LOGIN", "false")
    backend = SsmBridgeBackend(config_path=missing_config)

    target = backend.resolve_target()

    assert target.name == "env-box"
    assert target.aws_profile == "env-profile"
    assert target.instance_id == "i-0env"
    assert target.auto_sso_login is False


def test_config_path_can_come_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "targets.json"
    write_config(config)
    monkeypatch.setenv("SSM_BRIDGE_CONFIG", str(config))

    backend = SsmBridgeBackend()

    assert backend.resolve_target().name == "dev-box"


def test_explicit_args_override_config_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "targets.json"
    write_config(config)
    monkeypatch.setenv("SSM_BRIDGE_AWS_PROFILE", "env-profile")
    monkeypatch.setenv("SSM_BRIDGE_INSTANCE_ID", "i-env")
    backend = SsmBridgeBackend(config_path=config)

    target = backend.resolve_target(aws_profile="arg-profile", instance_id="i-arg")

    assert target.aws_profile == "arg-profile"
    assert target.instance_id == "i-arg"


def test_run_command_returns_structured_invocation(tmp_path: Path) -> None:
    config = tmp_path / "targets.json"
    write_config(config)
    backend = SsmBridgeBackend(config_path=config)

    responses = [
        completed(json.dumps({"Command": {"CommandId": "cmd-1"}})),
        completed(""),
        completed(json.dumps({"Status": "Success", "ResponseCode": 0, "StandardOutputContent": "ok\n"})),
    ]

    with patch("ssm_bridge.subprocess.run", side_effect=responses) as run:
        result = backend.run_command("whoami")

    assert result["success"] is True
    assert result["command_id"] == "cmd-1"
    assert result["stdout"] == "ok\n"
    assert run.call_args_list[0].args[0][:4] == ["aws", "ssm", "send-command", "--profile"]


def test_run_command_refuses_ambiguous_target(tmp_path: Path) -> None:
    config = tmp_path / "targets.json"
    write_config(config)
    data = json.loads(config.read_text(encoding="utf-8"))
    data["targets"]["nxg26"] = {
        "aws_profile": "example-profile",
        "instance_id": "i-old",
        "host_hint": "dev-nxg26-pod.example.internal",
        "private_ip": "10.0.0.1",
    }
    config.write_text(json.dumps(data), encoding="utf-8")
    backend = SsmBridgeBackend(config_path=config)

    with patch(
        "ssm_bridge.subprocess.run",
        return_value=completed(
            ec2_response(
                {"instance_id": "i-a", "name": "alpha-nxg26", "private_ip": "10.0.0.1"},
                {"instance_id": "i-b", "name": "beta-nxg26", "private_ip": "10.0.0.2"},
            )
        ),
    ) as run:
        with pytest.raises(SsmBridgeError, match="ambiguous target 'nxg26'"):
            backend.run_command("whoami", target="nxg26")

    assert run.call_count == 1


def test_run_command_instance_id_bypasses_ambiguity_check(tmp_path: Path) -> None:
    config = tmp_path / "targets.json"
    write_config(config)
    backend = SsmBridgeBackend(config_path=config)
    responses = [
        completed(json.dumps({"Command": {"CommandId": "cmd-1"}})),
        completed(""),
        completed(json.dumps({"Status": "Success", "ResponseCode": 0, "StandardOutputContent": "ok\n"})),
    ]

    with patch("ssm_bridge.subprocess.run", side_effect=responses) as run:
        result = backend.run_command(
            "whoami",
            aws_profile="example-profile",
            instance_id="i-explicit",
        )

    assert result["success"] is True
    assert "i-explicit" in run.call_args_list[0].args[0]


def test_upload_uses_base64_chunks(tmp_path: Path) -> None:
    config = tmp_path / "targets.json"
    write_config(config)
    local_file = tmp_path / "hello.txt"
    local_file.write_text("hello", encoding="utf-8")
    backend = SsmBridgeBackend(config_path=config, upload_chunk_size=4)

    calls: list[str] = []

    def fake_run(command: str, **kwargs):
        calls.append(command)
        return {"success": True, "command_id": "cmd", "status": "Success", "response_code": 0, "stdout": ""}

    with patch.object(backend, "run_command", side_effect=fake_run):
        result = backend.upload_file(str(local_file), "/tmp/hello.txt")

    encoded = base64.b64encode(b"hello").decode("ascii")
    assert result["local_bytes"] == 5
    assert any(encoded[:4] in call for call in calls)
    assert any("base64 -d" in call for call in calls)


def test_failed_aws_json_raises(tmp_path: Path) -> None:
    config = tmp_path / "targets.json"
    write_config(config)
    backend = SsmBridgeBackend(config_path=config)

    with patch("ssm_bridge.subprocess.run", return_value=completed("", "expired", 255)):
        with pytest.raises(SsmBridgeError, match="expired"):
            backend.status()


def test_expired_sso_runs_login_and_retries(tmp_path: Path) -> None:
    config = tmp_path / "targets.json"
    write_config(config)
    backend = SsmBridgeBackend(config_path=config)
    responses = [
        completed("", "aws: [ERROR]: Error when retrieving token from sso: Token has expired", 255),
        completed("", "", 0),
        completed(json.dumps({"UserId": "u"}), "", 0),
        completed(json.dumps({"InstanceInformationList": []}), "", 0),
    ]

    with patch("ssm_bridge.subprocess.run", side_effect=responses) as run:
        result = backend.status()

    assert result["identity"] == {"UserId": "u"}
    assert run.call_args_list[1].args[0] == ["aws", "sso", "login", "--profile", "example-profile"]


def test_auto_sso_login_can_be_disabled(tmp_path: Path) -> None:
    config = tmp_path / "targets.json"
    write_config(config)
    data = json.loads(config.read_text(encoding="utf-8"))
    data["targets"]["dev-box"]["auto_sso_login"] = False
    config.write_text(json.dumps(data), encoding="utf-8")
    backend = SsmBridgeBackend(config_path=config)

    with patch(
        "ssm_bridge.subprocess.run",
        return_value=completed("", "aws: [ERROR]: Error when retrieving token from sso: Token has expired", 255),
    ) as run:
        with pytest.raises(SsmBridgeError, match="Token has expired"):
            backend.status()

    assert run.call_count == 1
