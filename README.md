# SSM Bridge

<img width="1536" height="1024" alt="ChatGPT Image Jun 3, 2026, 11_00_25 AM" src="https://github.com/user-attachments/assets/1dbe64fa-9762-492b-8075-537718c7cec6" />

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

To install the CLI entry points from `pyproject.toml`, install the project locally:

```bash
python3 -m pip install -e .
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

If AWS reports an expired SSO token and `SSM_BRIDGE_AUTO_SSO_LOGIN` is not false, SSM Bridge runs `aws sso login` using `SSM_BRIDGE_SSO_PROFILE` when set, or `SSM_BRIDGE_AWS_PROFILE` otherwise:

```bash
aws sso login --profile example-profile
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

## Multiple accounts and AWS GovCloud

Add entries under `targets` in your local `targets.json` to use multiple environments.
Each entry selects its own AWS profile and instance; `default` is only the fallback
when a call does not select a target. For example:

```json
{
  "default": "dev-box",
  "targets": {
    "dev-box": {
      "aws_profile": "development",
      "instance_id": "i-0123456789abcdef0"
    },
    "govcloud-dev": {
      "aws_profile": "govcloud",
      "instance_id": "i-0fedcba9876543210"
    }
  }
}
```

Configure the `govcloud` AWS CLI profile with the target's GovCloud region
(`us-gov-east-1` or `us-gov-west-1`). The bridge uses the AWS CLI's region resolution,
so check for inherited `AWS_REGION` or `AWS_DEFAULT_REGION` overrides as well.
For an IAM Identity Center portal backed by Okta:

```bash
aws configure sso --profile govcloud
aws sso login --profile govcloud
aws sts get-caller-identity --profile govcloud
```

Use your organization's SSO start URL and SSO region, which can differ from the
target instance's region. Direct Okta SAML console federation instead requires
your organization's approved CLI credential provider. Set `auto_sso_login` to
`false` for profiles that do not use `aws sso login`.

Select the target explicitly with `ssm_status(target="govcloud-dev")` or
`ssm_run(command="hostname", target="govcloud-dev")`. Target configuration is
reloaded for each operation; new targets do not require a server restart. Alternatively,
pass `aws_profile` and `instance_id` directly to supported tools. An explicit
different AWS profile also becomes the profile used for automatic SSO refresh.
Overriding the account or instance clears the saved target's hostname and IP
metadata so the response does not describe a different host.

Console access alone does not guarantee SSM access. The instance must be managed
by SSM, and the caller needs the relevant EC2 discovery and SSM Run Command
permissions. Keep real account and instance details in local configuration.

## MCP Tools

```text
ssm_list_targets
ssm_status
ssm_find_instances
ssm_run
ssm_upload
ssm_download
ssm_get_file
ssm_configure_sso
ssm_login_start
ssm_login_status
ssm_save_target
```

## Agent-driven setup and human approval

An agent can configure access using the tools below. Obtain the real portal,
account, role, and regions from the user's AWS access portal or administrator.
No permanent access keys or Okta passwords are required by the bridge.

1. Call `ssm_configure_sso(aws_profile="govcloud", start_url="https://example.awsapps.com/start",
   sso_region="us-gov-east-1", account_id="123456789012", role_name="ReadOnly",
   region="us-gov-west-1")`. This adds local configuration, preserves other profiles,
   and refuses to overwrite a conflicting profile. Skip this step for an existing profile.
2. Call `ssm_login_start(aws_profile="govcloud")`. It returns a `login_id`,
   `status`, and `verification_url` once AWS CLI has produced its device URL.
3. Open that URL in the user's browser. Let the user complete Okta login and MFA.
   Call `ssm_login_status(login_id="...", wait_seconds=20)` while approval is pending.
   Keep the user informed and reuse the same login; do not repeatedly start new
   flows or send new push notifications. If Okta returns to the account list,
   reopen the same verification URL to resume device approval.
4. After status is `success`, call `ssm_find_instances` with the explicit profile.
   Match the intended environment using authoritative instance or routing data.
5. Call `ssm_save_target(target="govcloud-dev", aws_profile="govcloud",
   instance_id="i-0123456789abcdef0")`. It checks AWS identity and SSM online status
   before saving the local target. Review the returned account identity against
   the intended account. Defaults are preserved unless `make_default=true`.
6. Use the explicit target for subsequent authorized diagnostics or fixes.

Login is handled by AWS CLI and its normal local SSO cache. Tools never return
access keys, refresh tokens, or raw login output. A login flow expires after
15 minutes; AWS may expire its device code earlier. Pending flows belong to the
running MCP server and must be restarted if that server exits. Status calls wait
at most 20 seconds so the agent can remain responsive during human approval.
Direct Okta SAML federation without IAM Identity Center still requires the
organization's approved credential tool; these setup tools do not bypass it.

Terminal agents can use the same setup path:

```bash
ssm-bridge configure-sso --aws-profile govcloud \
  --start-url https://example.awsapps.com/start \
  --sso-region us-gov-east-1 --account-id 123456789012 \
  --role-name ReadOnly --region us-gov-west-1
ssm-bridge login --aws-profile govcloud
# Leave login running while the human approves the browser/Okta request.
ssm-bridge find dev-box --aws-profile govcloud
ssm-bridge save-target --target govcloud-dev --aws-profile govcloud \
  --instance-id i-0123456789abcdef0
```

Only user-specific local configuration is written. Setup does not create IAM
permissions, install an SSM Agent, or execute commands on the remote host.

`ssm_run` executes arbitrary shell commands through AWS Systems Manager Run Command using `AWS-RunShellScript`. The effective user and privileges are controlled by AWS SSM and the target host configuration.

`ssm_find_instances` searches EC2 by instance id or by a Name tag substring and includes SSM online status when available. `ssm_run` refuses an explicit fuzzy target when multiple EC2 instances match the target string. Pass `aws_profile` and `instance_id` after choosing an instance.

## Codex Registration

Register the MCP server with Codex:

```bash
codex mcp add ssmBridge -- python3 /absolute/path/to/ssm-bridge/mcp_server.py
```

Or add this manually to `~/.codex/config.toml`:

```toml
[mcp_servers.ssmBridge]
command = "python3"
args = ["/absolute/path/to/ssm-bridge/mcp_server.py"]
```

## MCP Examples

```text
ssm_status(target="dev-box")
ssm_find_instances(query="dev-box")
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

Run through Python:

```bash
python3 cli.py targets
python3 cli.py status --target dev-box
python3 cli.py run 'whoami && hostname' --target dev-box
python3 cli.py get-file /etc/hostname --target dev-box
python3 cli.py upload /tmp/script.sh /tmp/script.sh --target dev-box
python3 cli.py download /tmp/remote.log /tmp/remote.log --target dev-box
```

Or use the installed `ssm-bridge` command after `python3 -m pip install -e .`:

```bash
ssm-bridge targets
ssm-bridge status --target dev-box
ssm-bridge run 'whoami && hostname' --target dev-box
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
