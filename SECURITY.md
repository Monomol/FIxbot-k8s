# Security policy

## Reporting a vulnerability

Please report security issues privately to the maintainers rather than opening
a public issue. If the repository has a private security-advisory feature,
use it. Include:

- a description of the issue and its impact
- the affected file, template, or configuration
- steps to reproduce, if applicable
- any suggested remediation

Do not include actual credentials, tokens, private keys, or personal data in
reports, issues, or pull requests.

## Secrets

This chart never stores Secret values. Credentials are referenced by Kubernetes
Secret name and must be created separately. If you discover a committed secret:

1. Treat it as compromised and rotate it immediately.
2. Remove it from the working tree and from git history before publishing.
3. Report the incident to the maintainers.

## Deployment hardening

Before exposing FIxbot to the public internet:

- enable SSO and disable password authentication where possible
- review the default admin list and self-service signup setting
- keep PodSecurity `restricted` and NetworkPolicies enabled
- use TLS for ingress and for all external service calls
- protect the Pipe Control `/sync` endpoint with a strong Bearer token
- restrict image registries and use image pull secrets for private images
- back up both the Open WebUI DB and the Pipe State DB
- review the database backup retention and restore procedure
