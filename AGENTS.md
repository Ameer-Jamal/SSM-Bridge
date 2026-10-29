# Working on SSM Bridge

- Keep this public repository generic. Never commit local AWS config, credentials,
  target inventories, account IDs, private hostnames, or login approval codes.
- Preserve existing profiles, targets and defaults during setup. Use named profiles
  and explicit targets; verify account identity and SSM status before saving a host.
- For IAM Identity Center setup, use `ssm_configure_sso`, `ssm_login_start`, and
  `ssm_login_status`. Open the returned URL and let the human complete Okta/MFA.
  Reuse the login ID while approval is pending; do not send repeated push requests.
  Never claim login succeeded until status is `success`, then verify identity.
- Direct Okta SAML federation requires the organization's approved credential
  provider; do not treat an Okta dashboard URL as an IAM Identity Center start URL.
- Discover the actual environment's instance before saving a target. Do not guess
  from similar hostnames or reuse another target's account/host metadata.
- Setup writes local configuration only. Host commands and uploads require the
  user's task authorization; do not run remote fixes just to verify setup.
- Run `python -m pytest -q`, `git diff --check`, and a package installation/help
  smoke check before pushing shared code. Add regression coverage for auth and
  target-selection behavior. Keep tests independent of live AWS credentials.
