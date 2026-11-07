# FIxbot

> **Disclaimer:** This repository is published only as a public code and
> solution reference. It is not a supported, turnkey deployment package. If you
> intend to deploy FIxbot, please contact us first at
> [fixbot@fi.muni.cz](mailto:fixbot@fi.muni.cz) and we will help you.

FIxbot is an infrastructure platform for running per-Course AI study
assistants. Each Course gets its own isolated Open WebUI instance, its own
databases, and a set of teacher-curated **Pipes** — specialised assistants that
help students study, practise, or take an examination.

This repository contains the Helm chart that provisions the platform: the
per-Course Open WebUI stacks, PostgreSQL clusters, backups, content delivery,
ingress, and optional observability.

> **Status: source-available, not turnkey.**
> The chart is published so the design and implementation can be read, learned
> from, and discussed. It still contains assumptions about the target platform
> and expects several external services (identity provider, LLM backend,
> object storage, ingress, CloudNativePG, and optional GitLab-based content
> delivery). See [Roadmap](#roadmap) for the planned work to make external
> deployments first-class.

## Architecture

The detailed architecture description is in
[`docs/architecture/SYSTEM-DESCRIPTION.md`](docs/architecture/SYSTEM-DESCRIPTION.md),
with diagrams in [`docs/architecture/`](docs/architecture/).

In short:

- **One Course = one subdomain + one Open WebUI instance + two databases.**
  The Open WebUI DB holds application state; the Pipe State DB holds Pipe
  Session state.
- **Pipes are built from content, not baked into images.** A Pipe Base, a
  System Prompt, and a Question Knowledge Base are merged by Pipe Control and
  propagated into the Course instance.
- **Content is versioned in Git.** Teacher pushes are validated by CI and
  delivered as immutable, per-repository epochs.
- **Backups are per database.** CNPG streams WAL and takes weekly full backups
  to S3-compatible object storage.
- **Observability is optional and namespace-scoped.** Prometheus, Grafana, and
  Loki can be enabled independently.

Domain vocabulary is defined in [`CONTEXT.md`](CONTEXT.md).

## Repository layout

| Path | Purpose |
|---|---|
| `Chart.yaml`, `values.yaml` | Helm chart metadata and generic default values |
| `values.example.yaml` | Example overrides for a fuller deployment |
| `templates/` | Kubernetes resources rendered per Course and for shared services |
| `scripts/` | In-cluster helper scripts embedded into ConfigMaps |
| `docs/adr/` | Architecture decision records |
| `docs/architecture/` | Architecture description, diagrams, and regeneration tooling |

## Quick start (render only)

```sh
helm lint .
helm template fixbot . -f values.example.yaml > rendered.yaml
```

The default `values.yaml` is deliberately minimal. `values.example.yaml` shows
a fuller configuration with backups, SSO, monitoring, and one example Course.

## Prerequisites

A real deployment expects:

- Kubernetes with an ingress controller and cert-manager
- CloudNativePG (and the Barman Cloud Plugin if backups are enabled)
- An S3-compatible object store for database backups and optional Loki storage
- An OIDC identity provider (optional; password auth can be used for testing)
- An OpenAI-compatible LLM endpoint for the AI backend
- A storage class for the databases and optional PVCs
- Optional: GitLab (or compatible) repositories and CI for the content pipeline
- Optional: a logging operator compatible with the SyslogNG CRDs

Secrets are **never created by the chart**. They are referenced by name and
must exist before the dependent pods start. See `templates/NOTES.txt` for the
list and `values.yaml` for the expected Secret names.

## Configuration

The main knobs are documented inline in [`values.yaml`](values.yaml). The most
important groups are:

- `domain`, `namespace`, ingress/TLS settings
- `openWebUI` — image, replicas, resources, provisioning, feature switches
- `postgres`, `stateDb` — CloudNativePG cluster sizing and storage
- `backup`, `interimBackupDump` — database backup options
- `aiass`, `oidc` — LLM backend and SSO
- `pipeControl`, `gitlabProvisioning` — content delivery and repository setup
- `monitoring`, `logging`, `dashboards` — optional observability
- `courses` — one entry per Course slug

## Security

- Never commit Secret values, kubeconfigs, or credentials.
- Report vulnerabilities according to [`SECURITY.md`](SECURITY.md).
- The chart uses PodSecurity `restricted` settings and namespace-scoped
  NetworkPolicies where possible.
- Review the notes in [`SECURITY.md`](SECURITY.md) before exposing an instance
  to the public internet.

## License

No license has been added yet. Until one is provided, the source is available
for viewing but all rights are reserved. See the repository owner for licensing
questions.

## Roadmap

The next step is to make the chart externally deployable without the original
platform assumptions. Planned work includes:

- moving all provider-specific settings into values
- making the GitLab-based content pipeline, public site, monitoring, and
  backups fully optional
- publishing or replacing the private Pipe Control and site images
- providing generic identity, LLM, storage, and ingress examples
- adding a render-and-deploy test on a disposable local cluster
- splitting production deployment configuration into a separate private repo

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for more detail.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).
