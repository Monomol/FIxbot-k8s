# Use a Dedicated Single-Instance CNPG Pipe State DB per Course

Every Course runs a SECOND CNPG Cluster (`<release>-<slug>-state-db`,
database `fixbot_state`) for Pipe Session state, kept logically and
physically separate from the Course's Open WebUI database.

## Status

Accepted. `stateDb` in `values.yaml`; backups stay off
(`stateDb.backup.enabled: false`) until the dedicated S3 identity exists
(the backup-credentials task).

## Context

Pipes (the Open WebUI functions students chat with) are stateful across a
conversation: each chat session carries a transcript, a question index and a
submitted flag that must survive between messages. Open WebUI itself is
stateless — a Pipe invocation receives only the current request, and the
Pipe object itself may be updated mid-conversation (a teacher pushes a
content fix, Pipe Control propagates it). Neither event may lose in-flight
session state, so that state must live outside both the Pipe code and the
Open WebUI application database.

## Decision

Give every Course a dedicated Pipe State DB:

- **Second CNPG Cluster.** `<release>-<slug>-state-db` with database
  `fixbot_state` — a separate failure and backup domain from the Open WebUI
  DB, sized independently (`stateDb.storageSize: 2Gi` default).
- **Chart-owned schema.** The `pipe_sessions` table is declared via CNPG
  `postInitApplicationSQL` (runs once at bootstrap, idempotent). Pipes only
  ever do INSERT/UPDATE — the chart is the schema authority; there are no
  Pipe-side migrations.
- **Ingress-only access.** The NetworkPolicy admits connections from that
  Course's webui pods only (same platform-sanctioned pattern as the main
  DB). Pipes reach the database through `FIXBOT_STATE_DB_URL`, injected as
  an env var on the webui pods.
- **Single instance everywhere.** Writes are appends to rows keyed by
  distinct `chat_id`; two students never touch the same row, so there is no
  lock contention for replication to solve. Even the ~700-student Course is
  a light write load (roughly one row update per message). Contrast the
  Open WebUI DB, which needed HA (2 instances on `zfs-csi`) only at high
  webui replica counts. One instance keeps the per-Course quota cost
  minimal (100m CPU / 256Mi memory requests), and `nfs-csi` reattach
  covers node failure (recovery = Pod restart + WAL replay) — the same
  reasoning as `0003-postgres-storage-nfs-vs-zfs.md`.

## Backups and teardown archival

Backups write to their OWN bucket, `s3://example-fixbot-state-backups/<slug>`, under
a dedicated S3 identity `example-fixbot-state-backups` — the ADR 0004 pattern of one
identity per bucket. Retention matches the main DB while the Course is live:
weekly full backup plus continuous WAL, 30 days.

Deleting a Course IS the semester-end archival strategy: Helm removes the
Course's ObjectStore and ScheduledBackup along with its other resources, so
barman pruning stops and the last base backup plus its WALs remain in S3
indefinitely. There is no "retire" workflow — delete the Course.

## Consequences

- Two CNPG Clusters per Course doubles the DB-related ops surface
  (Clusters, Secrets, NetworkPolicies, ObjectStores, ScheduledBackups).
- The Status Page metrics set grows a `fixbot_state_db_*` family alongside
  the existing Open WebUI DB series.
- Until the dedicated S3 identity + bucket exist, the Pipe State DB runs
  unbacked (`stateDb.backup.enabled: false`, the backup-credentials task).
