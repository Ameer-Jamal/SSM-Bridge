from __future__ import annotations

from typing import Any

from ssm_bridge import SsmBridgeBackend

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    FastMCP = None


def create_mcp_server(backend: SsmBridgeBackend | None = None):
    if FastMCP is None:
        raise ImportError("The 'mcp' package is required to run the SSM Bridge MCP server.")

    backend = backend or SsmBridgeBackend()
    app = FastMCP("SSM Bridge", json_response=True)

    @app.tool()
    def ssm_list_targets() -> dict[str, Any]:
        """List configured AWS SSM targets and non-secret metadata."""
        return backend.list_targets()

    @app.tool()
    def ssm_status(target: str = "", aws_profile: str = "", instance_id: str = "") -> dict[str, Any]:
        """Check AWS identity and SSM online status for a configured target."""
        return backend.status(target=target, aws_profile=aws_profile, instance_id=instance_id)

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
