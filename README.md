# SSM Bridge

SSM Bridge is a local MCP server and terminal CLI for running commands and moving files on AWS SSM-managed Linux instances.

It is useful when you want an MCP client or local terminal workflow to inspect a host, fetch logs, upload a script, read a config file, or run diagnostics without opening SSH.

## Requirements

- Python 3.11+
- AWS CLI
- AWS Session Manager plugin
- AWS credentials with the SSM permissions needed for your target instances

Install Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

## Quickstart with Environment Variables

For a single target, you can configure SSM Bridge entirely through environment variables:

```bash
export SSM_BRIDGE_TARGET=dev-box
export SSM_BRIDGE_AWS_PROFILE=example-profile
export SSM_BRIDGE_INSTANCE_ID=i-0123456789abcdef0
```

Optional variables:

```bash
export SSM_BRIDGE_HOST_HINT=dev-box.example.internal
export SSM_BRIDGE_PRIVATE_IP=10.0.0.10
export SSM_BRIDGE_AUTO_SSO_LOGIN=true
export SSM_BRIDGE_SSO_PROFILE=example-profile
```

If AWS reports an expired SSO token and `SSM_BRIDGE_AUTO_SSO_LOGIN` is not false, SSM Bridge runs:

```bash
aws sso login --profile "$SSM_BRIDGE_SSO_PROFILE"
```

## Quickstart with a Config File

Copy the example config and edit it for your own AWS account and instance:

```bash
cp targets.example.json targets.json
```

`targets.json` is ignored by git so real instance IDs, hostnames, and private IPs stay local.

Use a custom config location with:

```bash
export SSM_BRIDGE_CONFIG=/path/to/targets.json
```

## MCP Tools

```text
ssm_list_targets
ssm_status
ssm_run
ssm_upload
ssm_download
ssm_get_file
```

`ssm_run` executes arbitrary shell commands through AWS Systems Manager Run Command using `AWS-RunShellScript`. The effective user and privileges are controlled by AWS SSM and the target host configuration.

## Codex Registration

Add this to `~/.codex/config.toml`:

```toml
[mcp_servers.ssmBridge]
command = "python3"
args = ["/absolute/path/to/ssm-bridge-mcp/mcp_server.py"]
```

## MCP Examples

```text
ssm_status(target="dev-box")
ssm_run(command="whoami && hostname", target="dev-box")
ssm_get_file(remote_path="/etc/hostname", target="dev-box")
ssm_upload(local_path="/tmp/script.sh", remote_path="/tmp/script.sh", target="dev-box")
ssm_download(remote_path="/var/log/messages", local_path="/tmp/messages.log", target="dev-box")
```

You can also pass `aws_profile` and `instance_id` directly instead of using a named target:

```text
ssm_run(
  command="uname -a",
  aws_profile="example-profile",
  instance_id="i-0123456789abcdef0"
)
```

## Terminal CLI

```bash
python3 cli.py targets
python3 cli.py status --target dev-box
python3 cli.py run 'whoami && hostname' --target dev-box
python3 cli.py get-file /etc/hostname --target dev-box
python3 cli.py upload /tmp/script.sh /tmp/script.sh --target dev-box
python3 cli.py download /tmp/remote.log /tmp/remote.log --target dev-box
```

Direct target overrides are also supported:

```bash
python3 cli.py run 'uptime' \
  --aws-profile example-profile \
  --instance-id i-0123456789abcdef0
```

## Good Use Cases

- Inspecting one SSM-managed instance from an MCP client
- Fetching logs or small config files
- Uploading helper scripts
- Running diagnostics or one-off maintenance commands

## Not For

- Replacing AWS IAM or host-level access controls
- Multi-host orchestration
- Audited privileged access workflows
- Long-running interactive shell sessions

## Security Notes

SSM Bridge intentionally exposes arbitrary command execution. Treat access to the MCP server and CLI like access to the target instance. Use least-privilege IAM, review which hosts your AWS profile can reach, and avoid exposing this MCP server over a network transport.

## Tests

```bash
python3 -m pytest -q
```
