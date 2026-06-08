from __future__ import annotations

from mcp_server import create_mcp_server


class FakeBackend:
    def list_targets(self):
        return {"default": "dev-box", "targets": []}

    def status(self, **kwargs):
        return {"online": True, "kwargs": kwargs}

    def find_instances(self, **kwargs):
        return {"count": 0, "instances": [], "kwargs": kwargs}

    def run_command(self, command, **kwargs):
        return {"success": True, "command": command, "kwargs": kwargs}

    def upload_file(self, local_path, remote_path, **kwargs):
        return {"local_path": local_path, "remote_path": remote_path, "kwargs": kwargs}

    def download_file(self, remote_path, local_path, **kwargs):
        return {"remote_path": remote_path, "local_path": local_path, "kwargs": kwargs}

    def get_file(self, remote_path, **kwargs):
        return {"remote_path": remote_path, "content": "", "kwargs": kwargs}


def test_create_mcp_server() -> None:
    app = create_mcp_server(FakeBackend())
    assert app is not None
