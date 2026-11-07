# Self-hosted observability (Prometheus + Grafana + Loki) with CNPG-based S3 backups

## Status

Proposed. This ADR is the written requirement for monitoring the platform. It records
the **decisions** (metrics/log scope, storage, retention, access) so that the monitoring
stack can be built later against a concrete spec. The chart currently ships only the
building blocks (dashboard ConfigMap + `prometheus.io` scrape annotations) with no live
backend.

## Context

- **Personal Monitoring (the previous monitoring app) is deprecated** ("will be removed soon") and is
  **not installed** in the cluster: there are no Prometheus/Grafana/ServiceMonitor/
  PodMonitor CRDs anywhere visible. The webui pods' `/metrics` endpoint is live
  (verified returning 200) but is scraped by nothing — the dashboard ConfigMap is orphaned.
- **No CNPG Postgres exporter.** None of the `Cluster` resources set `spec.metrics`, so
  the dashboard's `pg_stat_*` panels cannot populate even where a Prometheus exists.
- **The shipped dashboard is stale since scaling landed.** `example` is now a **StatefulSet**
  (4 webui replicas) plus Dragonfly/Redis + Chroma; `example`/`test` are single-replica
  Deployments. The dashboard's Course variable and replicas panels query
  `kube_deployment_status_replicas_available`, which does not match StatefulSets, and it
  has **no panels** for Chroma, Dragonfly, multi-instance Postgres / primary-replica,
  DB-migration, or connection-pool saturation. Chroma and Dragonfly pods carry no scrape
  annotations.
- **No S3 backups are active.** `backup.enabled` is `false` on every Course (no
  `barmanObjectStore`, no `ScheduledBackup`, no `fixbot-s3-creds` secret). Backup health
  cannot be monitored until backups run.
- **Quota headroom.** `fixbot` ResourceQuota: `limits.memory` 32Gi, currently
  `used 24192Mi` → ~8.4Gi headroom. A minimal Prometheus + Grafana + Loki stack
  (≈1–1.3Gi) fits; larger rollouts will need to watch this.

## Scope

Monitor everything that keeps a Course serving, including the scaled components:

- **Open WebUI**, per Course, as **Deployment** (`example`, `test`) or **StatefulSet**
  (`example`): availability (`/health`), available replicas, CPU/memory/network,
  `/metrics:8080`.
- **CNPG Postgres**, per Course: connections (watch against `maxConnections`), cache-hit
  ratio, DB size, WAL archiving; **primary vs replica + failover** for multi-instance
  Courses (`example` = 2 instances on `zfs-csi`); the **pod-0 DB-migration init** outcome.
- **Chroma** (vector store): availability (heartbeat `/api/v2/heartbeat`), CPU/mem;
  ephemeral-reset tracking (embeddings rebuild on pod churn — an availability gap, not
  data loss).
- **Dragonfly/Redis** (sessions/websockets hub when HA): availability, memory; its outage
  means all webui replicas lose shared state.
- **S3 backups**: last-successful age / RPO / WAL-archiving gap.
- **Provisioning Job** outcome (admin + settings reconciliation on each `helm upgrade`).
- **Public status page** availability (`/api/status`).
- **Logs** for all of the above.

**Out of scope (recorded as follow-on, not in this ADR):** ingress/TLS certificate
expiry, 5xx / request latency, ResourceQuota headroom alarms, `Released`-PV leak
tracking, per-model LLM usage metrics.

## Decisions

### 1. Metric pipeline — self-hosted Prometheus + Grafana

Replace the reliance on the previous monitoring app with a self-hosted **Prometheus + Grafana** in
`fixbot`. Prometheus scrapes:
- webui pods `/metrics` (port 8080) — annotations already present;
- **CNPG Postgres exporter**, enabled by adding `spec.metrics` to each `Cluster`
  (this is the missing piece that finally powers the `pg_stat_*` panels);
- `kube-state-metrics` + cAdvisor for infra/replica/pod metrics;
- Chroma (`:8000/metrics` + heartbeat) and Dragonfly (admin port) — add scrape
  annotations to those pods;
- the status page `/api/status` for availability.

Grafana is the **single web client** for dashboards **and** log exploration (Loki
datasource). Prometheus and Loki are **internal only** (no direct exposure).

### 2. Log pipeline — logging operator → self-hosted Loki

the platform runs a managed Logging operator (`logging.banzaicloud.io`, `SyslogNGFlow`/
`SyslogNGOutput`, with a configurable logging reference) — the chart's `syslog.yaml` already targets
it. It is a **stateless router** (it stores nothing); all log bytes land only at a
destination we specify. Decision: route `part-of=fixbot` pod logs to a **self-hosted
Loki** via a syslog-ng receiver, visualized in the same Grafana.

This avoids deploying promtail and keeps collection free (platform-operated), at the cost
of needing the in-cluster syslog→Loki sink. Enable via the existing `logging.enabled`
values + `logging.syslog.host/port`.

### 3. Log/metrics storage — dedicated S3, 30-day compactor retention

- Loki stores to a **dedicated S3 bucket** (S3-compatible object storage), **separate
  from the CNPG backup bucket**, so compaction/retention can never touch backup objects.
- **Retention: 30 days, enforced by the Loki Compactor** (not by a blanket bucket
  lifecycle rule, which would corrupt Loki's index/seed). Required config:
  `compactor.retention_enabled: true`, `limits_config.retention_period: 744h`,
  `max_query_lookback: 744h`, `delete_request_store` set, 24h index period, and the S3
  credentials **must have `DeleteObject`** permission. Retention runs per-day on the
  compactor schedule (+~2h sweeper delay); not retroactive to already-ingested logs.
- Sizing estimate: ~9 app + DB + vector + cache pods → **~100–400 MB/day → 3–12 GB @
  30 days** (a small bucket budget).
- Prometheus TSDB retention: short default (e.g. 15d) unless a longer window is required.

### 4. Access — private Grafana at `monitoring.fixbot.example.com`, admin-only via OIDC SSO

- **Subdomain** `monitoring.fixbot.example.com` — covered by the existing
  `*.fixbot.example.com → ingress.example.com` wildcard, so cert-manager
  (`letsencrypt-prod`) issues TLS with no DNS work. Not advertised on the public site
  (mirrors the `listed:false` pattern).
- **Auth: OIDC SSO, gated to a dedicated admin group.** Grafana
  Generic OAuth with `groups_attribute_path` + `allowed_groups` + `role_attribute_strict`
  admits **only** members of an identity-provider group (e.g. `fixbot-monitoring-admins`) holding the
  3 admin e-mails; every other user is denied. Requirement: the OIDC client
  must return the **`groups`** claim (group memberships) — requested via
  the `groups` scope.
- Verify at build time that Grafana's Generic OAuth options (`use_pkce`, `auth_style`,
  `use_refresh_token`, `signout_redirect_url`) match the configured OIDC client (they
  all are supported).

### 5. Visualization — Course-Overview dashboard, extended

Extend `templates/dashboards.yaml`:
- Fix the per-Course variable + replicas panel to match **both** Deployments and
  StatefulSets (`kube_deployment_* ∪ kube_statefulset_*`), so `example` appears.
- Retain/refresh the existing **Open WebUI** and **PostgreSQL** rows (now powered by
  `spec.metrics`).
- Add rows: **Chroma**, **Dragonfly/Redis**, **Postgres HA / pool saturation**
  (`maxConnections` vs `replicas × (poolSize+overflow)`), **Backups (RPO/age)**,
  **Provisioning**, **Availability** (status page), and a **logs** panel via the Loki
  datasource.

### 6. SLO — one definition of "a Course is OK"

A Course is considered healthy when **all** hold:
- `/health` returns 200 across the status-page probing window, **and**
- available webui replicas ≥ the configured `replicas` (for HA, all 4 for `example`), **and**
- (HA only) DB migrations are current (pod-0 migration init succeeded), **and**
- the last S3 backup is within `backup.retentionPolicy` (30d; ideal: latest WAL recent).

## S3 prerequisites (to be provided by the project admins at implementation time)

Two separate keys/buckets, never shared, so each service's permissions and lifecycle stay
independent:

| Purpose | Secret (K8s, in `fixbot`) | Permissions | Bucket |
|---|---|---|---|
| CNPG DB backup (`backup.*`) | `fixbot-s3-creds` (`ACCESS_KEY_ID`/`ACCESS_SECRET_KEY`) | read/write/**delete** within the backup bucket (barman retention purges old WAL/backups) | CNPG backup bucket (`example-fixbot-pg-backups`), per-Course `<slug>` prefix |
| Loki log store (retention) | `fixbot-loki-s3-creds` | **read/write + `DeleteObject`** (compactor purge) | dedicated `example-fixbot-loki` bucket |

Admins create these Secrets (and the buckets); the values (`destinationPath`,
`endpointURL`) are wired into `values.yaml` under `backup` and a `monitoring.loki` block.

## Consequences

- **Enabling DB S3 backups is a prerequisite** for the Backups/RPO panel and for backup
  health monitoring; it is executed separately but alongside this ADR
  (set `backup.*`, create `fixbot-s3-creds`, `helm upgrade`).
- When the stack is built, chart changes will be: `Cluster.spec.metrics`; scrape
  annotations on Chroma/Dragonfly pods; a `monitoring:` values block (Prometheus,
  Grafana, Loki ingredients, S3 secret refs); Grafana OIDC/group config + Ingress +
  NetworkPolicy for the private `monitoring.fixbot.example.com`; dashboard extensions.
- Monitoring components live **inside** `fixbot` and therefore **consume quota** —
  the ~8.4Gi `limits.memory` headroom must be watched as Courses/replicas grow.
- Prometheus/Grafana/Loki are **ephemeral by nature** (reproducible from the chart;
  only Loki log chunks and Prometheus TSDB are backed by storage). Their storage uses
  `Delete`-reclaim or S3, deliberately **not** the `Retain`-grade DB storage class.

## Considered options not taken

- **the previous monitoring app** — deprecated and uninstalled; rejected as a home.
- **Fully self-hosted promtail→Loki collection** — heavier in-namespace footprint than
  the logging operator route; kept as a fallback if the operator path is unsuitable.
- **Basic-auth / shared password for Grafana** — rejected for per-person SSO + group gating.
- **Table-Manager / bucket-lifecycle retention** — rejected in favour of the Loki
  Compactor (index-aware; safer for object stores).
