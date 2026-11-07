# FIxbot

FIxbot is a study assistant for courses: it helps students learn and practice and lets teachers examine them, through a specialised AI assistant built on a teacher-curated knowledge base. Technically it is an infrastructure platform that hosts isolated Open WebUI instances for courses, each behind its own subdomain at `fixbot.example.com`.

## Language

**Course**:
A unique course identifier (e.g., `course-a`, `course-b`) that maps to a subdomain and owns one Open WebUI instance with its own databases.
_Avoid_: Subject, tenant, project, instance

**Course Slug**:
The subdomain portion of a Course's URL — e.g., `course-a` in `course-a.fixbot.example.com`. Used as a prefix for all Kubernetes resource names belonging to that Course.
_Avoid_: Subdomain, identifier, id

**Open WebUI Instance**:
A deployment of Open WebUI (multi-replica) serving one Course. Students sign in via OIDC SSO and chat with Pipes there.
_Avoid_: Web UI, frontend, app

**Open WebUI DB**:
The per-Course database holding the Open WebUI application state (users, chats, config). Replaces the former generic "CNPG Cluster" term.
_Avoid_: Postgres instance, database server

**Pipe**:
A specialised teaching assistant exposed to students as one chat model inside a Course's Open WebUI instance. A Pipe encapsulates a unit of knowledge (e.g., one week's lecture content) and exists either to help the student study/practice or to examine them. Built by Pipe Control from a Pipe Base + System Prompt + QKB.
_Avoid_: Function, model, bot, agent

**Pipe Type**:
The purpose category of a Pipe: **ILP** (Interactive Learning Pipe — study/exercise sessions), **EP** (Examination Pipe), **CEP** (Code Examination Pipe), **PEP** (Personalized Examination Pipe).
_Avoid_: Variant, flavor, mode

**Pipe Base**:
A versioned code template, one per Pipe Type, that defines how a Pipe behaves (Conducts the session). Lives in the global Pipe Bases repo (one file per type).
_Avoid_: Function template, plugin

**System Prompt**:
The versioned instruction text, one per Pipe Type, given to the LLM behind every Pipe of that type. Lives in the global System Prompts repo (one file per type).
_Avoid_: Prompt template

**Question Knowledge Base (QKB)**:
The teacher-curated content of a Course: a set of questions with their associated metadata, available only to the LLM conducting the session (never shown raw to students). One versioned Git repo per Course and academic year (`<slug>-<year>`); each JSON file in it becomes one Pipe.
_Avoid_: Question bank, content database

**Question Figure**:
An optional image embedded in a QKB question, stored in the repo-root `assets/` directory (flat, images only) and referenced in `primary_question` by its bare file name. Pipes are text-only, so a figure is rendered for the student but never sent to the LLM. Each Course's ingress serves figures at the root-relative `/qkb-assets/<token>/<file>`, where `<token>` is a 16-hex obfuscation hash of the bare file name added by the ILP Pipe base at render time (the source file keeps the bare name; Open WebUI itself owns `/assets/`).
_Avoid_: QKB image, attachment, media

**Pipe Control**:
FIxbot's content-delivery service: it merges Pipe Bases + System Prompts + QKBs into Pipes and propagates them into Course Open WebUI instances, guaranteeing fresh content is pulled before propagation.
_Avoid_: pipe deployment, sync tool

**Pipe Session**:
One student's ongoing or completed conversation with one Pipe, identified by its chat. Its state (transcript, progress through questions, content commits, submitted flag) persists across messages so updates to Pipe content never lose ongoing work.
_Avoid_: Conversation state, session row

**Pipe State DB**:
The per-Course database holding Pipe Sessions — every Course's SECOND database, separate from the Open WebUI DB, backed up to its own dedicated S3 bucket with its own identity. Transcript data is never to be lost; the last backup survives Course teardown indefinitely.
_Avoid_: session store, state cache

**Pipe Language**:
The single language of everything a given Pipe shows to the student AND feeds the LLM: its QKB files, System Prompt, mapping, and UI texts. English is the canonical default (files at the type root); translations live in `<TYPE>/<lang>/` subtrees (`2-letter ISO 639-1` codes). The per-Type language set is declared via `languages.json` (repo-root Course default; `<TYPE>/languages.json` override; absence means English-only). A Course+Type with more than one declared language produces one Pipe per language. Every Pipe is named uniformly `[<TYPE>][<LANG>] <Name>` (e.g. `[ILP][CS] Week 01`); the model picker lists models name-sorted, so Pipes group by prefix. A Pipe NEVER mixes languages (emitted text lands in chat history = LLM context): an incomplete translation layer skips that Pipe loudly rather than shipping a hybrid. Pipe command names stay English in every language.
_Avoid_: locale, i18n, translation layer

**AIaaS Backend**:
The the platform AI-as-a-Service LLM API endpoint. Each Course's Open WebUI is configured to use this as its model backend.
_Avoid_: LLM provider, model API, chat API

**S3 Backup**:
The per-Course database backup mechanism that streams transaction logs continuously to an S3 bucket and takes weekly full base backups with 30-day retention. The Open WebUI DB and the Pipe State DB each back up to their own dedicated bucket/identity.
_Avoid_: pgdump, snapshot, dump

**Status Page (root site)**:
The public-facing web site served at `fixbot.example.com` from the `fixbot/site` container image (`site.py` + `assets/`; the chart deploys it and injects the Course set). Pages behind a shared navbar: Home (`/`, a navigational grid of clickable Course cards that link to each Course's subdomain, plus a "How to start" walkthrough with a screenshot served from `/assets/`), About (`/about`), Contacts (`/contacts`, maintainers and the project supervisor), Status (`/status`, green/red health indicators plus a 24h availability graph per Course with per-cell hover tooltips), and Report an issue (`/report`). A background poller probes each Course's `/health` on an interval to drive the indicators and graph. A Course with `listed: false` is excluded from all pages (internal Courses are not advertised). Operational metrics (DB status, replica count, resource usage) are restricted to the private Grafana instance.
_Avoid_: Dashboard, health check page, monitoring page

**Personal Monitoring**:
A the platform Rancher App that provisions per-namespace Prometheus + Grafana instances. Provides private dashboards and alerts for pod metrics, resource usage, and database health. **Deprecated** by the platform and not relied upon by this chart.
_Avoid_: Observability stack, monitoring platform

**Observability Stack**:
The self-hosted monitoring deployed in `fixbot` by this chart (per ADR 0004): Prometheus (metrics) + Grafana (the single web client for dashboards and logs) + Loki (log store on its own S3 bucket, 30-day compactor retention). Logs are collected by the logging operator and routed (syslog) into Loki. Exposed at a private subdomain (`monitoring.fixbot.example.com`), gated to the admin group via OIDC SSO; it is never shown on the public pages. Not to be confused with the public status page, which is only availability.
_Avoid_: Dashboard, obs tooling, monitoring platform
