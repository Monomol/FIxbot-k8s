# FIxbot — detailed infrastructure description

A prose model of the running system, suitable as input to another LLM (for
diagramming, image generation, or question answering). It describes the
**generic** structure, not live replica counts. Domain terms follow
[`CONTEXT.md`](../../CONTEXT.md).

## 1. What the system is

FIxbot is a study assistant for courses. Each **Course** gets its own
subdomain (`<slug>.fixbot.example.com`) and its own **Open WebUI** instance.
Students chat with **Pipes** — specialised teaching assistants built from a
teacher-curated **Question Knowledge Base (QKB)**, a system prompt, and a Pipe
base implementation. A Pipe can also examine or exercise the student. The whole
platform runs in a single Kubernetes namespace (`fixbot`) on the platform
cluster, managed by one Helm release (`fixbot`).

Two database concepts must not be conflated:

- the **Open WebUI DB** — application state: users, chats, settings;
- the **Pipe State DB** — Pipe-specific state: sessions, valves, step data.

Every Course has **both**, as separate CNPG Postgres clusters.

## 2. Users and the edge

Three human roles appear at the edge: **students**, **teachers**, and
**administrators/operators**. All web traffic uses HTTPS hostnames under
`*.fixbot.example.com`, resolved to the ingress endpoint. TLS certificates
are issued by cert-manager (`letsencrypt-prod`). An nginx ingress routes by
host:

- the root domain serves the **public site**;
- each Course slug serves that Course's **Open WebUI**;
- `monitoring.` serves **Grafana**;
- `pipe-control.` serves **Pipe Control**.

In addition, every Course host routes the path prefix `/qkb-assets/` to Pipe
Control, which serves inline question figures from that Course's QKB repository.
The figure URL contains a non-secret token derived from the file name; the
assets are obfuscated, not access-controlled.

## 3. Shared namespace services

These exist once per cluster, beside the per-Course stacks:

- **Public site** — Home, a "How to start" walkthrough, About, Contacts, Status,
  and Report pages. It also exports `fixbot_*` and `fixbot_state_db_*`
  Prometheus metrics.
- **Pipe Control** — the content-delivery front. It exposes:
  - `POST /sync?wait=1` — the single serialized wait queue for all content
    triggers (CI, the reconcile CronJob, ops);
  - `GET /repos` — the content tree and per-repository epoch HEADs, i.e. the
    "green memory";
  - `GET /targets`, `GET /health`;
  - the public `/qkb-assets/<token>/<file>` route on every Course host.

  On a sha-bearing `/sync` it spawns a **propagate Job**; it does not write
  content itself.
- **Shared content volume** (`fixbot-pipe-content` PVC) — holds
  `sys-prompts/`, `pipe-bases/`, and `qkb/<repo>/`. It is the single published
  tree that Course instances mount read-only.
- **content-reconcile CronJob** — every five minutes, calls `/sync` with
  `scope=full` and no sha. This is **deploy-only**: it converges instances to
  already-published green epochs and never re-fetches unpinned HEADs.
- **Provisioning Job** (per Course, post-install/post-upgrade hook) — reconciles
  Open WebUI admins and pushes model settings (default models, pinned models,
  model order, metadata, params). On a fresh database it bootstraps a service
  account and mints its Open WebUI API key.
- **Monitoring stack** — Prometheus, Grafana, Loki, promtail/logsink. Grafana is
  exposed at `monitoring.<domain>` and logs in through OIDC. Prometheus scrapes
  the namespace and the Open WebUI DBs' metrics port; cluster/node metrics come
  from a read-only the platform `cluster-prometheus` datasource. Loki stores logs.
- **Interim backup CronJob** — daily `pg_dump` of every Course DB to an
  `nfs-csi-backup` PVC with 14-day retention. This is a safety net used while
  the S3 store is degraded; CNPG backups remain the primary recovery path.

## 4. One Course stack

A Course is a package, not a shared tenant. Its resources are named
`fixbot-<slug>-*`. It contains:

- **Open WebUI** — a Deployment for `replicas: 1`, or a StatefulSet for
  `replicas > 1`. It serves chats, Pipes, and knowledge. It mounts its own QKB
  repository (plus the global sys-prompts and pipe-bases) read-only via
  `subPath` from the shared content volume.
- **Open WebUI DB** — a CNPG Postgres cluster holding application state.
  `postgres.instances` is normally 1; a large Course may use 2 instances with
  `zfs-csi` storage for HA (a one-way choice on a live Course).
- **Pipe State DB** — a second, single-instance CNPG Postgres cluster
  (`fixbot_state` database, chart-created schema) holding Pipe Sessions and
  valves.
- **Course data PVC** — Open WebUI uploads/cache on `nfs-csi`.
- **Optional Redis / Dragonfly** — sessions and WebSockets, present only when a
  Course has more than one WebUI replica.
- **Optional Chroma** — an HTTP vector store, also present for multi-replica
  Courses.
- **Networking** — a per-Course ingress with TLS, a Service, and NetworkPolicies
  that isolate each database. The DB policies are ingress-only (no egress
  rules); they admit the Course's WebUI pods, the CNPG operator, the db-dump
  CronJob, and Prometheus on the metrics port. Multi-instance DBs also admit
  peer DB pods.
- **Backups** — a CNPG `ScheduledBackup` plus continuous WAL archiving through
  the Barman Cloud Plugin to the Course's S3 path. The provisioning Job and
  the DB-dump CronJob access their targets over these policies.

A Course appears in `values.yaml` under `courses:`. The public site advertises
only `listed: true` Courses; unlisted Courses are internal and not advertised.

## 5. Content pipeline (how teacher content becomes live Pipes)

Content is git-versioned, not baked into images. Four kinds of repository live
under `fixbot` on GitLab the project:

- **QKB repos** (`fixbot/qkb/<slug>-<year>`): a bare JSON array of question
  objects per file, plus `<TYPE>/mapping.json` and a flat repo-root `assets/`
  directory for figures. Types are ILP, EP, CEP, PEP.
- **sys-prompts**: one `main.txt` per Pipe Type. Prompts are
  `string.Template`s rendered from the QKB mapping.
- **pipe-bases**: one `main.py` per Type, implementing Pipes and declaring
  valves.
- **qkb-schema** and **fixbot-content-ci**: the JSON schemas and the shared CI
  pipeline that every content repo includes thinly.

Flow:

1. A teacher pushes to a content repo.
2. The shared CI runs `validate` — schema/lint. A red pipeline stops here.
3. An optional `rehearse` stage runs Pipe Control with `test: true`. A
   propagate Job fetches the pushed sha into a throwaway tree, runs the CLI
   `--test`, and writes only slug-prefixed copies into the `test` Course. The
   green epoch is deliberately untouched.
4. On green, CI calls `POST /sync?wait=1` with the repository and pushed sha.
   All callers serialize in one queue.
5. Pipe Control spawns a **propagate Job**. The Job clones the caller's
   repository at the exact sha (read-only token; the proxy is exported
   only around the fetch), then publishes per-file to the shared content volume
   with replace-and-prune semantics, files first and `.git` last.
6. The Job runs the pipe_control CLI. The CLI walks the published tree and
   installs/updates Pipes in the target Courses' **Pipe State DB** and Open
   WebUI model configuration. Course scope restricts the walk with
   `--only-repo`.
7. WebUI replicas already mount the shared volume, so the published tree is
   what they read.

The important invariant: **a green pipeline means that exact sha is live
exactly where it scopes; a red pipeline means nothing of it shipped.** The
published per-repository HEADs on the volume are the green memory. Reconcile
never re-fetches; rollback is re-running an older green pipeline. If the volume
is lost, a reseed script replays the latest green shas in dependency order:
pipe-bases → sys-prompts → QKB.

Multilingual Pipes: English is canonical at root paths; translations live under
`<TYPE>/<lang>/` for QKB and sys-prompts, and in `texts.<lang>.json` for pipe
bases. A pipe never mixes languages; an incomplete layer skips that language.
Every pipe is named `[<TYPE>][<LANG>] <Name>`.

## 6. Data protection

- **CNPG + Barman Cloud Plugin** archive continuous WAL and a weekly full backup
  per database. Open WebUI DBs go to `s3://example-fixbot-pg-backups/<slug>`; Pipe State
  DBs go to `s3://example-fixbot-state-backups/<slug>` under a dedicated identity.
- **Interim dump** — daily `pg_dump -Fc` of both DBs to the
  `nfs-csi-backup` PVC, 14-day retention.
- **Deleting a Course** stops pruning its S3 paths but does not delete existing
  backups. The last State DB backup persists indefinitely; this is the
  semester-end archival strategy.
- **Loki** uses `s3://example-fixbot-loki` normally; during the S3 incident it writes to
  a filesystem PVC instead (30-day retention).

## 7. Identity, secrets, and external services

Secrets are **never created by the chart**. They are pre-created and referenced
by name:

- `fixbot-oidc-secret` / `fixbot-grafana-oidc-secret` — OIDC (the IdP).
  Open WebUI uses SSO; accounts merge by email.
- `<slug>-aiass-key` — the platform AIaaS LLM API key(s). A Course with
  `aiass.keyPoolSize > 1` uses a pool; its model ids are then prefixed
  `0.`, `1.`, … `N-1.`.
- `fixbot-s3-creds`, `fixbot-state-s3-creds`, `fixbot-loki-s3-creds` — S3
  service identities for the three buckets.
- `fixbot-gitlab-token` (bot/API), `fixbot-gitlab-readonly` (fetch/clone),
  `fixbot-registry-pull` (image pulls).
- `fixbot-pipe-control-token` — Bearer for `/sync`.
- `<slug>-pbloky-key` — optional pbloky integration env.
- `<release>-<slug>-provision` — Job-managed Open WebUI API key for the
  provisioning Job; not chart-managed.

External services: **id.example.com** (OIDC), **AIaaS** (LLM API),
**GitLab the project** (content repos, CI, registry), **S3 object storage**
(backups, Loki), and **Kubernetes** (platform, quota, DNS).

## 8. Optional and variant behaviour

- `openWebUI.replicas > 1` automatically adds Redis/Dragonfly, Chroma, and
  DB-migration gating on pod-0.
- `postgres.instances > 1` is an explicit HA choice; on a live Course the
  storage class (`nfs-csi` vs `zfs-csi`) cannot be changed without a one-way
  data migration.
- `aiass.keyPoolSize > 1` renames model ids with a numeric prefix.
- `openWebUI.selfSignup: true` enables self-service registration (and requires
  password auth); new users are `pending` until approved.
- `admin` lists are reconciled on every upgrade.

## 9. Visual encoding suggestion

If you turn this into a picture:

- **Left to right:** users → DNS/TLS/ingress → shared services → Course stack →
  external services.
- **One lane per concern:** serving, content delivery, data protection,
  observability. Do not collapse them into one tangle.
- **Shapes:** boxes for services, cylinders for databases/storage, rounded
  dashed boxes for optional components, a gate/checkmark for the CI validation
  step, a hub for Pipe Control, a conveyor for the propagate Job.
- **Line semantics:** solid = request/data; dashed = optional or backup; dotted
  = reconciliation/control.
- **Colors:** amber edge, blue shared services, green Course runtimes, purple
  databases, red external/backup targets, yellow GitLab content, grey humans.
- **Never draw** one shared database for all Courses, a direct GitLab-to-WebUI
  content arrow, or content inside the container image.
