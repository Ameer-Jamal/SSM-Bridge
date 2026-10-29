from __future__ import annotations

import io
import json
import threading
from unittest.mock import Mock, patch

import pytest

from ssm_bridge import SsmBridgeBackend
from ssm_setup import SsmSetup


@pytest.fixture
def setup(tmp_path):
    return SsmSetup(SsmBridgeBackend(config_path=tmp_path / "targets.json"), tmp_path / "aws-config")


def configure(setup, **overrides):
    return setup.configure_sso(**{
        "aws_profile": "govcloud", "start_url": "https://example.awsapps.com/start",
        "sso_region": "us-gov-east-1", "account_id": "123456789012",
        "role_name": "ReadOnly", "region": "us-gov-west-1", **overrides,
    })


def test_profile_creation_preserves_existing_bytes_and_is_idempotent(setup):
    original = "# existing profile\n[profile dev]\nregion = us-east-1\n"
    setup.aws_config.write_text(original)
    configure(setup)
    first = setup.aws_config.read_text()
    configure(setup)
    assert first.startswith(original)
    assert setup.aws_config.read_text() == first
    assert "region = us-gov-west-1" in first
    assert setup.aws_config.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="differs"):
        configure(setup, role_name="Admin")
    assert setup.aws_config.read_text() == first


@pytest.mark.parametrize("values", [
    {"aws_profile": "bad\nprofile"}, {"account_id": "123"},
    {"start_url": "http://example.com"},
    {"start_url": "https://example.com/?token=secret"},
    {"start_url": "https://user:secret@example.com"},
])
def test_invalid_profile_input_never_writes(setup, values):
    with pytest.raises(ValueError):
        configure(setup, **values)
    assert not setup.aws_config.exists()


def test_target_save_verifies_before_persisting_and_live_backend_reloads(setup):
    setup.backend.config_path.write_text(json.dumps({"default": "dev", "targets": {"dev": {
        "aws_profile": "dev", "instance_id": "i-0123456789abcdef0",
    }}}))
    verified = {"online": True, "identity": {"Account": "123456789012"},
                "ssm": {"ComputerName": "gov-host", "IPAddress": "10.1.1.1"}}
    with patch.object(setup.backend, "status", return_value=verified):
        setup.save_target(target="gov", aws_profile="govcloud", instance_id="i-0fedcba9876543210")
        with pytest.raises(ValueError, match="another host"):
            setup.save_target(target="dev", aws_profile="govcloud", instance_id="i-0fedcba9876543210")
    assert setup.backend.list_targets()["default"] == "dev"
    target = setup.backend.resolve_target(target="gov")
    assert target.aws_profile == "govcloud"
    assert target.host_hint == "gov-host"
    assert setup.backend.config_path.stat().st_mode & 0o777 == 0o600


def test_offline_instance_not_saved(setup):
    with patch.object(setup.backend, "status", return_value={"online": False}):
        with pytest.raises(ValueError, match="not online"):
            setup.save_target(target="gov", aws_profile="govcloud", instance_id="i-0123456789abcdef0")
    assert not setup.backend.config_path.exists()


class PendingOutput:
    def __init__(self):
        self.finish = threading.Event()

    def __iter__(self):
        yield "https://example.awsapps.com/start/#/device?user_code=ABCD-EFGH\n"
        self.finish.wait(5)

    def close(self):
        pass


def test_login_returns_url_while_pending_reuses_flow_and_waits_for_completion(setup):
    output = PendingOutput()
    process = Mock(stdout=output)
    process.wait.return_value = 0
    with patch("ssm_setup.subprocess.Popen", return_value=process) as popen:
        first = setup.login_start(aws_profile="govcloud")
        try:
            assert first["status"] == "pending"
            assert "user_code=ABCD-EFGH" in first["verification_url"]
            assert setup.login_start(aws_profile="govcloud")["login_id"] == first["login_id"]
            assert popen.call_count == 1
            assert "--no-browser" in popen.call_args.args[0]
            assert "--use-device-code" in popen.call_args.args[0]
            assert popen.call_args.kwargs["env"]["AWS_CONFIG_FILE"] == str(setup.aws_config)
        finally:
            output.finish.set()
        done = setup.login_status(login_id=first["login_id"], wait_seconds=2)
    assert done["status"] == "success"
    assert "output" not in done


def test_login_failure_does_not_return_raw_output(setup):
    process = Mock(stdout=io.StringIO("private diagnostic\n"))
    process.wait.return_value = 1
    with patch("ssm_setup.subprocess.Popen", return_value=process):
        result = setup.login_start(aws_profile="govcloud")
        result = setup.login_status(login_id=result["login_id"], wait_seconds=2)
    assert result["status"] == "failed"
    assert "private diagnostic" not in json.dumps(result)


def test_unknown_login_reports_restart_recovery(setup):
    with pytest.raises(ValueError, match="start a new login"):
        setup.login_status(login_id="missing")


def test_real_subprocess_login_is_nonblocking_until_approval_file(setup, tmp_path):
    gate = tmp_path / "approved"
    executable = tmp_path / "fake-aws"
    executable.write_text(
        "#!/usr/bin/env python3\nimport pathlib, time\n"
        "print('https://example.awsapps.com/start/#/device?user_code=ABCD-EFGH', flush=True)\n"
        f"gate = pathlib.Path({str(gate)!r})\n"
        "while not gate.exists():\n    time.sleep(0.01)\n"
    )
    executable.chmod(0o700)
    setup.backend.aws_binary = str(executable)
    result = setup.login_start(aws_profile="govcloud")
    try:
        assert result["status"] == "pending"
        gate.touch()
        assert setup.login_status(login_id=result["login_id"], wait_seconds=2)["status"] == "success"
    finally:
        setup.close()


def test_login_expiry_kills_child_and_reports_expired(setup):
    output = PendingOutput()
    process = Mock(stdout=output)
    process.wait.return_value = -9
    with patch("ssm_setup.subprocess.Popen", return_value=process), patch("ssm_setup.threading.Timer") as timer:
        result = setup.login_start(aws_profile="govcloud")
        timer.call_args.args[1]()
        output.finish.set()
        final = setup.login_status(login_id=result["login_id"], wait_seconds=2)
    process.kill.assert_called_once()
    assert final["status"] == "expired"
