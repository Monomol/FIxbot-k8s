#!/bin/sh
# FIxbot Pipe Control — propagation Job entrypoint (ADR 0006, mode=real).
# Since rev 38 this Job is the SOLE WRITER of the shared content volume (the
# git-sync sidecars on the pipe-control pod are retired) — and since rev 43
# the volume IS the per-repo green memory (ADR 0006, volume-as-green-memory):
# epochs advance ONLY via green-signalled, sha-bearing runs (per-trigger:
# fetch+publish just the caller's repo at its validated-pipeline sha); the
# 5-minute reconcile and sha-less ops syncs go DEPLOY-ONLY here — they run
# the CLI against the published epochs, never re-fetching branch HEADs.
# Every run is serialized by the server's /sync?wait=1 queue.
set -eu

# REHEARSE=1 (a /sync {"test": true} call, from the server): fetch the trigger
# repo at the sha but DO NOT publish — the per-repo green epoch must not move
# on a rehearsal (a red rehearsal would otherwise leak into real Courses via
# the deploy-only reconcile). The CLI runs --test against a throwaway root
# built further down, writing slug-prefixed copies to the sandbox `test`
# Course only. Default 0 = the normal propagate path.
REHEARSE="${REHEARSE:-0}"

# pipe_control.py resolves .targets next to sys.argv[0]; /app inside the image
# is root-owned while the Job runs unprivileged (uid 65533), so copy the CLI
# into writable /work and run it from there.
mkdir -p /work/bin
cp -R /app/. /work/bin/

: > /work/bin/.targets
for slug in $COURSES; do
    if [ ! -s "/targets/key-$slug" ]; then
        # Course not provisioned yet (its <release>-<slug>-provision Secret
        # does not exist — projected optional; its webui cannot serve API
        # calls either). Skipping must not fail propagation for the rest.
        echo "WARN: skipping $slug: no provision key (not provisioned yet?)"
        continue
    fi
    key=$(cat "/targets/key-$slug")
    # Every Course has <release>-<slug>-svc:80 (load-balances into its webui
    # pods, incl. HA StatefulSets); the CLI matches targets by slug (QKB repo
    # dir '<slug>-<year>' -> slug, see loader.traverse).
    printf '%s; http://%s-%s-svc:80; %s\n' \
        "$slug" "$RELEASE" "$slug" "$key" >> /work/bin/.targets
done

FIXBOT_PIPE_BASE_PATH="${FIXBOT_PIPE_BASE_PATH:-/fixbot/pipe-bases}"

# --- Fetch phase -------------------------------------------------------------
# Git credentials ride in an env-driven credential helper: the token never
# appears in argv/URLs, so `git` error lines and pod logs never leak it.
# The Job runs as uid 65533 — the git-sync era's owner uid of the whole
# published tree — so publishes and prunes are OWNER-driven (a gid-map fluke
# of this NFS made group-based writes unreliable: runAsGroup surfaced as
# "nogroup"). umask 002 keeps the tree world-readable for the webui mounts.
umask 002
CLONES=/work/clones
mkdir -p "$CLONES"

export GITLAB_USERNAME="${GITLAB_USERNAME:-oauth2}"
# Env-driven git config: (1) credentials via helper — the token never appears
# in argv/URLs, so `git` error lines and pod logs never leak it; (2) blanket
# safe.directory — git>=2.35's "dubious ownership" refusal. Publishes are
# owner-driven (uid 65533) in the steady state, but legacy trees on the
# volume are NOT all 65533-owned (two Courses' repo dirs are uid-0-owned from
# the platform's fs-group fix pass history); the CLI resolves every content
# commit via `git log` and must never trip "Could not resolve a commit hash"
# on an ownership quirk again (that cost us a red full-walk on rev 39).
export GIT_CONFIG_COUNT=2
export GIT_CONFIG_KEY_0=credential.helper
export GIT_CONFIG_VALUE_0='!f() { echo "username=$GITLAB_USERNAME"; echo "password=$GITLAB_TOKEN"; }; f'
export GIT_CONFIG_KEY_1=safe.directory
export GIT_CONFIG_VALUE_1='*'

# Only the FETCH PHASE uses the optional proxy. The vars are exported right
# before the git fetches and UNSET right after (fetch_env_off below): leaving
# them set for the CLI phase can deflect in-cluster HTTP calls to the proxy.
# Keep the proxy strictly phase-scoped.
GIT_DEPTH="${GIT_DEPTH:-50}"

fetch_env_on() {
    [ -n "${GIT_PROXY:-}" ] || return 0
    export http_proxy="$GIT_PROXY" https_proxy="$GIT_PROXY"
    export HTTP_PROXY="$GIT_PROXY" HTTPS_PROXY="$GIT_PROXY"
    export no_proxy="localhost,127.0.0.1,10.0.0.0/8,.cluster.local,.svc"
    export NO_PROXY="$no_proxy"
}
fetch_env_off() {
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY no_proxy NO_PROXY
}

# REPO refs/paths arrive as comma maps: "sys-prompts=<ref>,pipe-bases=<ref>,…"
# and "sys-prompts=<gitlab path>,pipe-bases=<gitlab path>,qkb=qkb". QKB repo
# names (<slug>-<year>) come space-separated in QKB_REPOS. REPO_KEYS is the
# full fetch list: "sys-prompts pipe-bases qkb/<repo>…"
map_get() {  # $1 = comma map, $2 = key -> value or ''
    echo "$1" | tr ',' '\n' | while IFS='=' read -r k v; do
        [ "$k" = "$2" ] && echo "$v" && break
    done | head -n 1
}

# Map the strict pin (STRICT_REPO_PATH, a /fixbot/… content path) back to a
# fetch key.
strict_key=""
case "${STRICT_REPO_PATH:-}" in
    /fixbot/sys-prompts) strict_key="sys-prompts" ;;
    /fixbot/pipe-bases)  strict_key="pipe-bases" ;;
    /fixbot/qkb/*)       strict_key="qkb/${STRICT_REPO_PATH#/fixbot/qkb/}" ;;
    "")                  : ;;  # sha-less/manual call — its semantic IS "current"
    *) echo "WARN: unknown STRICT_REPO_PATH '$STRICT_REPO_PATH'; propagating at ref HEADs instead" ;;
esac

# Per-trigger epoch semantics (volume-as-green-memory, ADR 0006 rev 43+):
# the published tree is the green memory itself — content enters it ONLY via
# green-signalled runs: a CI call that passed validate and names the exact
# sha it graded. Therefore:
#   * sha-bearing runs fetch+publish ONLY the named repo (the caller's own
#     green sha advances that one epoch; every other repo renders from its
#     already-published — by construction last-green — epoch);
#   * sha-less runs (the 5-minute reconcile, ops full syncs) are DEPLOY-ONLY:
#     no fetch, no publish — they converge deployments to the published
#     epochs. Fetching branch HEADs unpinned would import validate-RED
#     commits into the green memory (the stale-branch failure mode, closed here).
REPO_KEYS=""
if [ -n "${STRICT_SHA:-}" ] && [ -n "$strict_key" ]; then
    REPO_KEYS=" $strict_key"
    echo "fetch+publish: advancing epoch $strict_key -> ${STRICT_SHA:0:12} (depth $GIT_DEPTH)"
else
    if [ -n "${STRICT_SHA:-}" ]; then
        echo "WARN: strict sha $STRICT_SHA names a repo outside values' set; deploy-only run"
    fi
    echo "deploy-only sync: no green sha named — converging deployments to published epochs (no fetch/publish)"
fi

# Strict pull-to-sha (sha-bearing /sync calls — every CI caller; see
# strict_pin in pipe_control_server.py). The Job fetches the named repo's
# tracked ref at depth GIT_DEPTH and checks out the EXACT pushed sha. If the
# sha is outside the window even after a direct sha-fetch, it was SUPERSEDED
# by a newer push — that push's own pipeline grades the newer content, so
# this call exits GREEN without touching anything (races recorded on
# 2026-09-22/23 made failing loud here self-healing but noisy; skipping is
# by design). Fetch/checkout failures (GitLab down, auth) are FATAL and the
# Job's backoffLimit retries.
SUPERSEDED=0
fetch_env_on
for key in $REPO_KEYS; do
    # key = sys-prompts | pipe-bases | qkb/<repo>; refs/paths are keyed by the
    # repo base (qkb), and the gitlab path of a QKB repo adds its repo name.
    base="${key%%/*}"
    ref="$(map_get "$REPO_REFS" "$base")"
    path="$(map_get "$REPO_PATHS" "$base")"
    if [ "$base" = "qkb" ]; then path="$path/${key#qkb/}"; fi
    if [ -z "$ref" ] || [ -z "$path" ]; then
        echo "FATAL: no ref/path for $key" >&2
        exit 1
    fi
    url="$GITLAB_BASE_URL/$path.git"
    dest="$CLONES/$key"
    mkdir -p "$dest"
    if ! git clone -q --depth "$GIT_DEPTH" --single-branch --branch "$ref" \
            "$url" "$dest"; then
        # A per-Course QKB repo that does not exist yet (pre-creation window
        # or torn-down Course) must not break propagation for all Courses —
        # skip it loudly. The caller's OWN repo and the shared repos are
        # platform-level: their failure reds this Job (retried).
        case "$key" in
            qkb/*)
                [ "$key" = "$strict_key" ] || {
                    echo "WARN: fetch of $url ($ref) failed — skipping $key" >&2
                    continue
                } ;;
        esac
        echo "FATAL: fetch of $url ($ref) failed" >&2
        exit 1
    fi
    if [ "$key" = "$strict_key" ] && [ -n "${STRICT_SHA:-}" ]; then
        if ! git -C "$dest" cat-file -e "$STRICT_SHA^{commit}" 2>/dev/null; then
            echo "strict pull-to-sha: $STRICT_SHA outside the depth-$GIT_DEPTH window of $key@$ref; trying a direct sha fetch"
            git -C "$dest" fetch -q --depth "$GIT_DEPTH" origin "$STRICT_SHA" || true
        fi
        if git -C "$dest" cat-file -e "$STRICT_SHA^{commit}" 2>/dev/null; then
            git -C "$dest" checkout -q "$STRICT_SHA"
            echo "strict pull-to-sha: $key pinned at $STRICT_SHA"
        else
            SUPERSEDED=1
            echo "strict pull-to-sha: $STRICT_SHA superseded on $key (ref moved past the depth window) — the newer push's pipeline grades the newer content; this call propagates nothing"
            break
        fi
    fi
done
fetch_env_off

[ "$SUPERSEDED" = 1 ] && exit 0

# --- Publish phase (SKIPPED on a rehearsal) ----------------------------------
# Files first, .git last (a consumer seeing .git at rev X can rely on the
# whole tree being X — the same invariant the old publish loop kept). Temp
# names carry $$ so two overlapping Jobs never corrupt each other's
# half-written file. All cleanup below is BEST-EFFORT against permission
# leftovers: every file THIS Job creates is owned by its own uid (65533), so
# once a tree is (re)published by a Job, every later Job can maintain it.
#
# REHEARSE=1 skips this block entirely: the green epoch (the published
# volume) is the memory the reconcile deploys from, so a rehearsal must never
# move it — see the throwaway-root build below.
if [ "$REHEARSE" != "1" ]; then
publish_repo() {  # $1 = clone src, $2 = /fixbot dst
    src="$1"; dst="$2"
    mkdir -p "$dst"
    # Leftovers of interrupted earlier swaps (incl. pre-P0 .git.old.N trees
    # that killed rev-38 attempts by appearing as prunable "content").
    rm -rf "$dst"/.git.old.* "$dst"/.git.new.* 2>/dev/null || true
    (cd "$src" && find . -name .git -prune -o -type f -print) | while read -r f; do
        mkdir -p "$dst/$(dirname "$f")"
        if ! cmp -s "$src/$f" "$dst/$f" 2>/dev/null; then
            cp "$src/$f" "$dst/$f.new.$$" && mv -f "$dst/$f.new.$$" "$dst/$f"
        fi
    done
    # Prune dst files gone upstream. The .git epoch dirs are excluded (they
    # are not content; the swap below owns them — NOTE: match exact names,
    # a repo may legitimately carry a .github/ content dir).
    (cd "$dst" && find . \( -name .git -o -name '.git.old.*' -o -name '.git.new.*' \) -prune -o -type f -print) | while read -r f; do
        [ -f "$src/$f" ] || { echo "publish: pruning stale $dst/$f"; rm -f "$dst/$f"; }
    done
    # Ship the real (shallow) .git next to the content: the CLI pins content
    # commits via `git log`. Files are already final (see above), so the
    # .git/new->old dance is the only swap that matters.
    rm -rf "$dst/.git.new.$$"
    cp -a "$src/.git" "$dst/.git.new.$$"
    rm -rf "$dst/.git.old.$$" 2>/dev/null || true
    if [ -e "$dst/.git" ]; then mv "$dst/.git" "$dst/.git.old.$$"; fi
    mv "$dst/.git.new.$$" "$dst/.git"
    rm -rf "$dst/.git.old.$$" 2>/dev/null || \
        echo "WARN: could not fully remove $dst/.git.old.$$ (left for the next run)" >&2
}

for key in $REPO_KEYS; do
    dest="$CLONES/$key"
    [ -d "$dest" ] || continue  # skipped above (missing per-Course repo)
    case "$key" in
        sys-prompts|pipe-bases) publish_repo "$dest" "/fixbot/$key" ;;
        qkb/*)                  publish_repo "$dest" "/fixbot/qkb/${key#qkb/}" ;;
    esac
done
fi

# Scoped CLI run (per-trigger): a course-scope sha-bearing call restricts
# the walk to the caller's repo via --only-repo (shipped in the CLI
# 2026-09-27 — auto-detected from --help; the detection stays as the graceful
# fallback: older images just full-walk against the frozen epochs, which is
# idempotent). Deploy-only runs always full-walk.
USE_SCOPED=0
SCOPED_REPO=""
case "${STRICT_REPO_PATH:-}" in
    /fixbot/qkb/*)
        if [ -n "${STRICT_SHA:-}" ]; then SCOPED_REPO="${STRICT_REPO_PATH#/fixbot/qkb/}"; fi ;;
esac
if [ -n "$SCOPED_REPO" ]; then
    if python /work/bin/pipe_control.py --help 2>&1 | grep -q -- '--only-repo'; then
        USE_SCOPED=1
        echo "scoped CLI run: --only-repo $SCOPED_REPO"
    else
        echo "CLI image has no --only-repo yet; falling back to a full walk (idempotent)"
    fi
fi

# --- CLI phase -----------------------------------------------------------------
CLI_ROOT=/fixbot
CLI_TEST=""
if [ "$REHEARSE" = "1" ]; then
    # The image MUST support --test: appending it to an older CLI would abort
    # argparse (Job Failed, safe red). We must NEVER fall back to a real
    # propagation here — that would defeat the whole gate.
    if ! python /work/bin/pipe_control.py --help 2>&1 | grep -q -- '--test'; then
        echo "FATAL: REHEARSE requested but the CLI image has no --test; refusing to run a real propagation" >&2
        exit 1
    fi
    CLI_TEST="--test"
    # Throwaway CLI root: the trigger repo is the FRESH clone (checked out at
    # the pushed sha); everything else renders from the published tree. The
    # CLI pins content commits via `git log`, so each entry needs a real .git
    # — both the clones and the published dirs carry one. Symlinks keep the
    # build O(1); the CLI's Path.glob/iterdir and `git -C` follow them.
    CLI_ROOT=/work/rehearse
    rm -rf "$CLI_ROOT"
    mkdir -p "$CLI_ROOT/qkb"
    for k in sys-prompts pipe-bases; do
        if [ -d "$CLONES/$k" ]; then ln -s "$CLONES/$k" "$CLI_ROOT/$k"
        else ln -s "/fixbot/$k" "$CLI_ROOT/$k"; fi
    done
    if [ -d /fixbot/qkb ]; then
        for d in /fixbot/qkb/*; do
            [ -d "$d" ] || continue
            r=$(basename "$d")
            if [ -d "$CLONES/qkb/$r" ]; then ln -s "$CLONES/qkb/$r" "$CLI_ROOT/qkb/$r"
            else ln -s "$d" "$CLI_ROOT/qkb/$r"; fi
        done
    fi
    # A brand-new (not-yet-published) QKB trigger repo exists only as a clone.
    if [ -n "$SCOPED_REPO" ] && [ -d "$CLONES/qkb/$SCOPED_REPO" ] \
            && [ ! -e "$CLI_ROOT/qkb/$SCOPED_REPO" ]; then
        ln -s "$CLONES/qkb/$SCOPED_REPO" "$CLI_ROOT/qkb/$SCOPED_REPO"
    fi
    echo "REHEARSAL: no publish; CLI --test against $CLI_ROOT (green epoch untouched)"
fi

# Diagnostics: prove the published tree resolves commits before the CLI
# starts (visible in Job logs / Loki).
if [ -d "$FIXBOT_PIPE_BASE_PATH/.git" ]; then
    git -C "$FIXBOT_PIPE_BASE_PATH" log -n 1 --format='diag: pipe-bases HEAD=%H' .
fi

if [ "$USE_SCOPED" = 1 ]; then
    exec python /work/bin/pipe_control.py "$CLI_ROOT" -v --only-repo "$SCOPED_REPO" $CLI_TEST
fi
exec python /work/bin/pipe_control.py "$CLI_ROOT" -v $CLI_TEST
