# FIxbot architecture diagrams

Logical, reusable views of the FIxbot infrastructure. They describe the
**generic structure** — what every Course gets and which shared services exist —
rather than a snapshot of current replica counts or pod placement.

The diagrams are generated from Graphviz sources in [`src/`](src/) and committed
as SVG (editable / zoomable) and PNG (slides / chat / LLM input).

## Views

| File | Answers |
|---|---|
| [`01-system-overview`](svg/01-system-overview.svg) | Who talks to what: users, ingress, shared services, one Course, external systems, monitoring. |
| [`02-course-stack`](svg/02-course-stack.svg) | What a single Course contains: Open WebUI, Open WebUI DB, Pipe State DB, optional Redis/Chroma, PVCs, provisioning, backups. |
| [`03-content-pipeline`](svg/03-content-pipeline.svg) | How teacher content becomes live Pipes: GitLab → CI validate → `/sync` → propagate Job → shared content PVC → target Courses; plus reconcile and rehearsal. |
| [`04-data-observability`](svg/04-data-observability.svg) | Backup paths (S3 + interim dump) and the self-hosted metrics/logs stack. |
| [`05-surfaces`](svg/05-surfaces.svg) | Build, credential and ownership surfaces: repos/images and named Secrets feeding runtime consumers. |
| [`poster`](svg/poster.svg) | Presentation poster: a curated, large-type landscape view of the whole system with a legend (best printed A2/A3). |
| [`poster-detail`](svg/poster-detail.svg) | Wall-chart version: panels A–D composed from the four detail diagrams, for screen use and zooming. |

PNG renders live in [`png/`](png/); SVG sources in [`src/`](src/).

## Reading conventions

- **Solid arrow** — request or data flow.
- **Dashed arrow** — optional/conditional path, or a backup/replication path.
- **Dotted arrow** — control/reconciliation (a Job or CronJob acting on something).
- **Cylinder** — database or persistent storage.
- **Dashed node** — component that exists only under a condition (for example
  Redis/Chroma when `openWebUI.replicas > 1`, or the interim dump during the S3
  incident).
- **Colors** — amber: edge/external platform; blue: shared namespace service;
  green: per-Course runtime; purple: data store; red: external service or backup target; grey: human actor; yellow: GitLab content/build surface.

The domain vocabulary follows [`CONTEXT.md`](../../CONTEXT.md): **Course**,
**Course Slug**, **Open WebUI DB**, **Pipe**, **Pipe State DB**, **QKB**,
**Pipe Control**. A Course is *not* a tenant, project, instance, or dashboard.

## Regenerating

Requirements: Graphviz (`dot`), a font with the Noto Sans family, and
`cairosvg` to rasterize the composed poster.

```sh
# Alpine / the OpenCode sandbox
apk add --no-cache graphviz font-noto py3-cairosvg

./scripts/render-architecture.sh
```

The script renders every `src/*.dot` to `svg/` and `png/` (200 dpi), then
composes `poster-detail.svg` from panels A–D and rasterizes `poster-detail.png`.
The curated `poster.svg` / `poster.png` comes from `src/poster.dot` like the other
views. If `cairosvg` is missing, the wall-chart SVG is still written and a warning
is shown.

## Notes

- `docs/` is excluded from the Helm package by `.helmignore`, so adding diagrams
  does not affect `helm install` / `helm upgrade` or the release-size limit.
- The diagrams are hand-authored logical models, not generated from `kubectl`.
  Update them in the same change wave as architectural changes to
  `values.yaml` / `templates/`.
- A detailed prose model of the same system (useful as LLM input) is in
  [`SYSTEM-DESCRIPTION.md`](SYSTEM-DESCRIPTION.md).
- For an illustrative (non-technical) render, use
  [`IMAGE_PROMPT.md`](IMAGE_PROMPT.md).
