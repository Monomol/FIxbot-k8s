# Version Pipe Content in Git Repos and Deliver via git-sync + Pipe Control API

All three Pipe content sources (Pipe Bases, System Prompts, QKBs) live in
git repos on gitlab.example.com and reach the cluster through git-sync plus
the Pipe Control API — never through hand-editing in the Open WebUI UI.

## Status

Accepted. Pipe Control deploys as a stub until its server mode ships
(the Pipe Control tasks, see `the internal road map`); the content repos are seeded
per `gitlab/README.md`.

## Context

Pipe content has two author groups: teachers write Question Knowledge Bases
(per-Course JSON question sets) and admins maintain the Pipe Bases (python
function templates) and System Prompts. Both groups need auditable,
version-controlled authoring — who changed which question, when, with the
ability to review and roll back. Open WebUI's alternative is hand-editing
Pipe code in the Admin UI of each Course: unauditable, unrepeatable, and
impossible to propagate consistently across Courses.

## Decision

- **Three repo types on gitlab.example.com.** `fixbot/pipes/sys-prompts` and
  `fixbot/pipes/pipe-bases` are global (type-a): one push propagates to ALL
  Pipes of that type in every Course. `fixbot/qkb/<slug>-<year>` is
  per-Course (type-b): a push reaches only that Course's Pipes. Every repo
  uses one folder per Pipe type (ILP/EP/CEP/PEP).
- **CI triggers propagation.** Each repo's pipeline validates on every push
  and, on the default branch, calls Pipe Control's `POST /sync` with the
  commit SHA (Bearer-token auth, token in the masked group CI variable
  `PIPE_CONTROL_TOKEN`). `/sync` guarantees pull-to-expected-SHA BEFORE
  propagating, so CI and the cluster can never disagree about which content
  is live.
- ~~**git-sync into one shared volume.** The Pipe Control Pod git-syncs every
  repo and a publish loop copies the worktrees into plain directories on
  the shared RWX `fixbot-pipe-content` volume (`nfs-csi`) — plain dirs, NOT
  git-sync's symlink mounts, because kubelet subPath mounts go stale on
  git-sync's symlink swap. Pipe Control is the single writer; webui Pods
  mount the volume read-only, each with a per-Course QKB subPath so a
  Course sees ONLY its own QKB at `FIXBOT_QKB_PATH=/fixbot/qkb`.~~
  **SUPERSEDED (2026-09-26, see addendum 3):** the git-sync sidecars + the
  publish mirror loop are retired; the `/sync` Job is now the SOLE writer
  (fetch → publish → CLI inside one Job pod). The mount topology (webui
  read-only, per-Course QKB subPath at `FIXBOT_QKB_PATH`) is unchanged.
- **Provenance in the Pipe State DB.** Every `pipe_sessions` row records
  `qkb_commit`, `pipe_base_commit` and `sys_prompt_commit`, so any student
  interaction can be traced back to the exact content that produced it.

## Alternatives considered

- **Push fully rendered Pipes into the webui DB via its API.** Rejected:
  Pipe code reading live QKB files at runtime keeps content out of the
  webui DB and lets a teacher's push take effect without touching Pipe
  objects at all.
- **git-sync sidecar in every webui Pod.** Rejected: a single-writer shared
  volume is simpler and runs far fewer sidecars (one Pipe Control Pod
  instead of a sidecar per webui replica per Course).
- **CI calls the k8s API to spawn Jobs.** Rejected: k8s credentials in
  GitLab CI have a far worse blast radius than a scoped `/sync` Bearer
  token.

## Consequences

- QKB repos are auto-created by the gitlabProvisioning Helm hook and NEVER
  deleted; academic-year rollover = new repo + a `qkb.year` bump in
  values.yaml. Old repos stay addressable, so old-year content survives.
- Pipe Control runs in mode=real (since rev 17, 2026-09-11): the python HTTP
  front keeps `/health` + `/sync` (Bearer) and on `/sync` spawns a
  `fixbot-pipe-propagate-*` Job running the real pipe_control CLI image
  (registry `registry.example.com`, pulled via `fixbot-registry-pull`).
  `mode: stub` remains a full rollback. The published content dirs ship the
  REAL shallow `.git` (the sidecar's gitdir, not the worktree pointer file)
  so the CLI pins exact content commits in Pipe valves; the Job trusts the
  read-only cross-uid tree via git `safe.directory` env config.
- A bad push to a global (type-a) repo propagates to every Course at once;
  the validation CI on those repos is the only gate before `/sync`.

## Addendum (2026-09-21): promotion channel + CI feedback loop

The last Consequences bullet aged out: lint used to be a *sibling* of
propagation, not a gate — every `/sync` re-published the whole content
volume's `main` HEAD, so a lint-red push (`example` Week 01) could be swept
into instances by an unrelated `/sync`, and teachers got no CI verdict about
propagation itself.

Two mechanisms now close that, in line with this ADR's pull-model:

1. ~~**`validated` promotion ref.** Every content repo's pipeline runs
   `validate → promote → propagate`: `promote` (push-only) fast-forwards
   `main` → `validated` …~~ **RETIRED (2026-09-26, addendum 3):** under the
   per-trigger model the pipeline's sha IS the promotion — pipelines only
   ever call `/sync` after validate passed, and the published volume keeps
   per-repo HEADs as the green memory. The `promote` stage was deleted from
   all three pipeline templates, the `validated` branches deleted, and the
   `ci_push_repository_for_job_token_allowed` knobs reverted (they existed
   only for that push; qkb-schema keeps its knob for CI tag pushes).
2. **`/sync?wait=1` feedback mode.** The HTTP front serializes waiting
   callers on one lock (each caller always spawns its own fresh Job and
   receives its own verdict — superseding/reaping was explicitly rejected),
   parses the CLI's `PIPE_CONTROL_SUMMARY` line from the Job log, and grades
   scoped: 502 only for issues whose scope matches the caller's repo
   (cross-course reds are impossible — loader isolates per-repo/per-target
   fatals into scoped issues); shared pipes repos are graded globally;
   platform-level Job failure reds everyone. The CLI prints the summary
   unconditionally; `--fail-with-body` surfaces it in the teacher's job log.

~~Residual race (accepted, same class as before): ≤30s git-sync lag means a
propagate Job can read predecessor content for fresh `validated` updates —
visible in the verdict's commit pins and self-healing on the next `/sync`.~~
RETIRED 2026-09-23 by strict pull-to-sha: sha-bearing `/sync` payloads map
to a content repo path (`strict_pin` in `pipe_control_server.py`), and the
spawned Job gates the CLI on `git rev-parse HEAD == STRICT_SHA` in that
published tree (≤120s, Job retries); only then does propagation run. A
superseded sha never converges → loud red (the newer push's own pipeline
grades the newer content). Sha-less manual calls keep volume-HEAD
semantics.

## Addendum (2026-09-26): Job-as-writer + per-trigger epochs + volume-as-green-memory

The git-sync sidecar set was retired after repeated operational pain (7
sidecars crash-looping on every GitLab wobble, three different stale/racey
convergence bugs between promote→sync→publish, and the anti-bot throttling
window of 2026-09). The model that replaced it was designed by stress-
testing it against three attack questions from the maintainer, each of which
changed the design; the record keeps the questions because they are the
easiest way to re-derive every mechanism.

1. **The propagate Job is the SOLE WRITER of the content volume.** On
   `/sync` the Job shallow-fetches repo(s) from GitLab itself (no sidecars;
   credentials via env `credential.helper`, never in argv; fetches phase-
   scoped through the platform proxy), publishes trees
   (per-file replace + prune + the real shallow `.git`, self-cleaning
   `.git.*` epochs, uid 65533 = the tree's historical owner uid), then runs
   the pipe_control CLI. The pipe-control pod keeps only the HTTP front and
   a **skeleton+prune loop** (keep dst dirs alive for webui subPath mounts;
   prune qkb dirs of removed Courses).
2. **Runs are per-trigger, not global-converge.** Q: *"So it fetches and
   propagates all repos, not only the one that triggered /sync?"* — the
   global-fetch-per-trigger behavior shipped first had a fatal coupling: an
   unrelated green pipeline (QKB Course A) would sweep another repo's
   never-greened commit into instances (the example Week 01 class) once
   `validated` stopped being the fetch ref. Per-trigger: a push's run acts
    ONLY on its own repo — QKB course-scope: fetch+publish just that repo at
    the pushed sha, CLI walks it (`--only-repo`, shipped in the CLI
    2026-09-27; the Job auto-detects it from `--help`, with a full-walk
    fallback for older images); shared repo: same for that shared repo, CLI
    full-walks everything
   against the OTHER repos' published (by construction last-green) frozen
   epochs.
3. **The verdict is truthful to deployment, per the maintainer's invariant:**
   **green pipeline ⇔ that sha is live exactly where it scopes; red ⇔
   nothing of it shipped.** CI already enforces `wait=1` everywhere; ALL
   syncs (CI, reconcile, ops) serialize through the single server lock, so
   nothing races concurrently.
4. **The volume IS the per-repo green memory.** The 5-minute reconcile
   CronJob (`POST /sync {"scope":"full"}` + `wait=1`) converges deployments
   (onboarding, DB resets, Admin-UI drift, missed hooks) — a sha-less run is
   DEPLOY-ONLY (no fetch/publish; fetching unpinned branch HEADs would
   import validate-RED content). Q: *"is the `validated` branch still really
   needed?"* — assessed options: ① `validated` branch (native, ff-ordered,
   but CI ceremony + job-token knobs forever), ② server-side call-stream
   memory (everything-01 has plus server-restart gaps — dominated), ③
   **volume-as-green-memory (chosen)**, ④ GitLab pipeline-API queries
   (drags api-scope token + throttled API into the convergence path —
   dominated). Green-ness enters the volume only via CI: reaching `/sync`
   post-validate demonstrates the carried sha is green, so fetch+publish of
   exactly that sha writes only validate-green epochs. Costs accepted:
   green memory shares the PVC's durability (reseed protocol below);
   regressions are not ff-refused — re-running an old green pipeline
   intentionally rolls back (sanctioned feature; visibility via `/repos`
   HEADs instead of refusal machinery).
5. **Recovery protocol (PVC loss):** `scripts/reseed_green_memory.py`
   discovers each repo's latest *successful* pipeline sha via the GitLab API
   and re-syncs in the only safe order — **pipe_bases → sys_prompts → QKB
   (slug order)**. Global runs full-walk and create no Pipes from absent
   QKB content; QKB runs render against the published globals, so globals
   must land first. Manual-sha `/sync` remains a *trusted ops override*
   (unverifiable-by-construction greenness; documented).
6. **2026-09-26 failure lessons folded into the mechanism:** published tree
   writes must be OWNER-driven (NFS maps group 65533 as `nogroup`; the
   group-based Write attempt EACCE'd — Job runs as uid 65533); git needs the
   env-`safe.directory` overlay for legacy uid-0 dirs; proxy env MUST be
   phase-scoped to git fetch (whole-Job export deflected in-cluster HTTP to
   squid, answering 503s for `/api/v1` calls).

Rollback path: mode=stub; model rollback would be re-adding the sidecar set
(chart history), with `pipeControl.repos.*.ref` back on a re-created
`validated`.

## Addendum 4 (2026-10-13): `rehearse` gate — deploy-to-`test` before real propagation

**Context.** `validate` is schema/compile lint only; it cannot catch a Pipe
that fails at the Open WebUI API (a valve the base's Valves model rejects, a
prompt that renders but the model create chokes on, an unreachable target).
the rehearsal task asked for a validate/`--dry-run` mode that makes the loader
stage GATING. The colleague shipped `pipe_control.py --test` (`de289659`):
for every real target it reads state read-only, then rehearses the would-be
create/update as slug-prefixed copies (`<slug>__<pipe id>`, `[<SLUG>] …`) on
the sandbox `test` Course — a genuine deploy attempt, not a report.

**Decision.** Content CI gains a `rehearse` stage between `validate` and
`propagate` (QKB repos first; globals follow). It POSTs `/sync?wait=1` with
the sha plus `{"test":true}`; the server (new REHEARSE mode, gated by
`pipeControl.test.enabled`) spawns the Job with `REHEARSE=1`. `run-propagate.sh`
then fetches the caller's repo at the sha into a **throwaway tree**
(`/work/rehearse`; trigger repo = fresh clone, everything else symlinked from
the published volume) and runs the CLI `--test` **without publishing**. A red
rehearsal (502 from scoped issues) fails the stage → GitLab skips `propagate`.

**Why no-publish is load-bearing.** A rehearsal must never advance the green
epoch: the volume IS the memory (addendum 3), and the deploy-only 5-minute
reconcile converges real Courses to it — so a rehearsal that published would
leak unrehearsed (possibly red) content into real Courses even though its own
pipeline red. Fetch-to-throwaway keeps the invariant "red ⇔ nothing shipped".

**Ordering invariant.** The chart must understand `test:true` before any CI
sends it (an old server would treat it as an ordinary sha-bearing sync → a
real propagation). `pipeControl.test.enabled: false` makes the server reject
such calls `400` rather than downgrade. Rollback: drop the CI `rehearse`
stage; the chart REHEARSE mode is inert without `test:true`.

**Accepted cost.** The CLI never deletes, so prefixed `[<SLUG>] …` Pipes
accumulate on the sandbox `test` instance (cleanup is a follow-up). The
sandbox must stay provisioned or every rehearsal reds.

