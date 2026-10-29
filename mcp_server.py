from __future__ import annotations

from typing import Any

from ssm_bridge import SsmBridgeBackend
from ssm_setup import SsmSetup

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    FastMCP = None


def create_mcp_server(backend: SsmBridgeBackend | None = None):
    if FastMCP is None:
        raise ImportError("The 'mcp' package is required to run the SSM Bridge MCP server.")

    backend = backend or SsmBridgeBackend()
    app = FastMCP("SSM Bridge", json_response=True)
    setup = SsmSetup(backend)

    @app.tool()
    def ssm_configure_sso(
        aws_profile: str, start_url: str, sso_region: str,
        account_id: str, role_name: str, region: str,
    ) -> dict[str, Any]:
        """Create local IAM Identity Center profile from known portal/account details; preserve existing profiles."""
        return setup.configure_sso(
            aws_profile=aws_profile, start_url=start_url, sso_region=sso_region,
            account_id=account_id, role_name=role_name, region=region,
        )

    @app.tool()
    def ssm_login_start(aws_profile: str) -> dict[str, Any]:
        """Start browser SSO login; return approval URL. Human completes Okta/MFA; poll ssm_login_status."""
        return setup.login_start(aws_profile=aws_profile)

    @app.tool()
    def ssm_login_status(login_id: str, wait_seconds: int = 0) -> dict[str, Any]:
        """Check a pending login; wait up to 20 seconds. Never approve MFA on the user's behalf."""
        return setup.login_status(login_id=login_id, wait_seconds=wait_seconds)

    @app.tool()
    def ssm_save_target(
        target: str, aws_profile: str, instance_id: str, make_default: bool = False,
    ) -> dict[str, Any]:
        """Verify identity/SSM and save a local target; refuse to replace another host. No remote commands."""
        return setup.save_target(
            target=target, aws_profile=aws_profile, instance_id=instance_id, make_default=make_default,
        )

    @app.tool()
    def ssm_list_targets() -> dict[str, Any]:
        """List configured AWS SSM targets and non-secret metadata."""
        return backend.list_targets()

    @app.tool()
    def ssm_status(target: str = "", aws_profile: str = "", instance_id: str = "") -> dict[str, Any]:
        """Check AWS identity and SSM online status for a configured target."""
        return backend.status(target=target, aws_profile=aws_profile, instance_id=instance_id)

    @app.tool()
    def ssm_find_instances(query: str, aws_profile: str = "") -> dict[str, Any]:
        """Find EC2 instances by instance id or Name tag substring and include SSM status."""
        return backend.find_instances(query=query, aws_profile=aws_profile)

    @app.tool()
    def ssm_run(
        command: str,
        target: str = "",
        aws_profile: str = "",
        instance_id: str = "",
        timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        """Run an arbitrary shell command through AWS SSM."""
        return backend.run_command(
            command,
            target=target,
            aws_profile=aws_profile,
            instance_id=instance_id,
            timeout_seconds=timeout_seconds,
        )

    @app.tool()
    def ssm_upload(
        local_path: str,
        remote_path: str,
        target: str = "",
        aws_profile: str = "",
        instance_id: str = "",
    ) -> dict[str, Any]:
        """Upload a local file through AWS SSM using base64 chunks."""
        return backend.upload_file(
            local_path,
            remote_path,
            target=target,
            aws_profile=aws_profile,
            instance_id=instance_id,
        )

    @app.tool()
    def ssm_download(
        remote_path: str,
        local_path: str,
        target: str = "",
        aws_profile: str = "",
        instance_id: str = "",
    ) -> dict[str, Any]:
        """Download a remote file to a local path through AWS SSM."""
        return backend.download_file(
            remote_path,
            local_path,
            target=target,
            aws_profile=aws_profile,
            instance_id=instance_id,
        )

    @app.tool()
    def ssm_get_file(
        remote_path: str,
        target: str = "",
        aws_profile: str = "",
        instance_id: str = "",
        max_bytes: int = 200000,
    ) -> dict[str, Any]:
        """Read a small remote text file into the MCP response."""
        return backend.get_file(
            remote_path,
            target=target,
            aws_profile=aws_profile,
            instance_id=instance_id,
            max_bytes=max_bytes,
        )

    return app


def main() -> None:
    app = create_mcp_server()
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
