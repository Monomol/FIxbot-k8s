#!/usr/bin/env python3
"""FIxbot green-memory reseed (volume rebuild / disaster recovery).

Under volume-as-green-memory (ADR 0006, rev 43+) the published content
volume's per-repo HEADs ARE the only "latest validate-green" epochs. Losing
the `fixbot-fixbot-pipe-content` PVC wipes that memory; this script reseeds
it deterministically WITHOUT hand-re-running pipelines:

  1. For every tracked repo (fixbot/pipes/sys-prompts, fixbot/pipes/pipe-bases
     and every project in the fixbot/qkb subgroup) find the SHA of the latest
     SUCCESSFUL pipeline on the default branch via the GitLab API — that is
     the newest commit whose content provably passed validate.
  2. Re-propagate each repo at that sha through Pipe Control's production
     strict-pull-to-sha path (POST /sync?wait=1), in the SEEDING ORDER that
     guarantees renders never see a missing epoch:
        pipe_bases -> sys_prompts -> qkb repos (slug order).
     QKB runs render against the published globals, so globals MUST land
     first; global runs full-walk empty/absent QKB dirs and create no Pipes
     from them, which is exactly right at seed time.

Needs: GITLAB_TOKEN (read_api or api; fixbot-gitlab-token today) and
PIPE_CONTROL_TOKEN (the /sync bearer). Run from any host that can reach BOTH
gitlab.example.com and pipe-control.fixbot.example.com (from any host that
can reach both; honors http(s)_proxy env vars).

Usage:
  python3 reseed_green_memory.py                  # DRY-RUN plan only
  python3 reseed_green_memory.py --apply          # executes the reseed
  python3 reseed_green_memory.py --only example-2026 --apply
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

GITLAB = os.environ.get("GITLAB_API", "https://gitlab.example.com/api/v4")
PIPE_CONTROL = os.environ.get(
    "PIPE_CONTROL_URL",
    "https://pipe-control.fixbot.example.com/sync")
GROUP = os.environ.get("FIXBOT_GITLAB_GROUP", "fixbot")

SHARED = [("pipe_bases", "pipes/pipe-bases"), ("sys_prompts", "pipes/sys-prompts")]


def die(msg, code=2):
    sys.exit(f"reseed: {msg}")


def gitlab(token, path, qs=None):
    url = f"{GITLAB}/projects/{path}" + ("?" + urllib.parse.urlencode(qs) if qs else "")
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        die(f"GitLab {path} -> HTTP {e.code}: {e.read()[:200]!r}")


def enc(path):
    return urllib.parse.quote(path, safe="")


def qkb_repos(token):
    """All projects in the fixbot/qkb subgroup (path only, slug-sorted)."""
    url = f"{GITLAB}/groups/{enc(GROUP + '/qkb')}/projects?per_page=100"
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token})
    with urllib.request.urlopen(req, timeout=30) as r:
        projects = json.load(r)
    return sorted(p["path"] for p in projects)


def last_green_sha(token, project_path):
    """SHA of the latest successful pipeline on the default branch, with the
    pipeline id for logging. None when the repo never had a green pipeline
    (nothing green to seed — that is a loud skip, not an error)."""
    proj = gitlab(token, enc(project_path))
    branch = proj.get("default_branch") or "main"
    pls = gitlab(token, enc(project_path) + "/pipelines",
                 {"ref": branch, "status": "success", "per_page": "1"})
    if not pls:
        return None, None
    return pls[0]["sha"], pls[0]["id"]


def sync(token, payload, timeout):
    req = urllib.request.Request(
        PIPE_CONTROL + "?wait=1", method="POST",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def epochs(token):
    url = PIPE_CONTROL.replace("/sync", "/repos")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("epochs", {})


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually post the /sync calls (default: dry-run)")
    ap.add_argument("--only", metavar="REPO",
                    help="limit to one repo (qkb name, pipe_bases or sys_prompts)")
    ap.add_argument("--timeout", type=int, default=580,
                    help="per-/sync wait=1 HTTP timeout seconds (server cap 570s)")
    args = ap.parse_args()
    gl = os.environ.get("GITLAB_TOKEN") or die("GITLAB_TOKEN unset")
    pc = os.environ.get("PIPE_CONTROL_TOKEN") or die("PIPE_CONTROL_TOKEN unset")

    print("== before:")
    try:
        for k, v in epochs(pc).items():
            print(f"   {k}: {str(v)[:12]}")
    except Exception as e:
        print(f"   (epochs unavailable: {e})")

    plan = []
    for typ, path in SHARED:
        if args.only and args.only != typ:
            continue
        plan.append((f"shared {typ}", {"scope": "global", "type": typ}, path))
    qkbs = ([args.only] if args.only and args.only.endswith(("-2026", "-2025", "-2027"))
            else qkb_repos(gl))
    for repo in qkbs:
        plan.append((f"qkb {repo}", {"scope": "course", "repo": repo}, f"qkb/{repo}"))

    print("== plan (last validate-green shas):")
    actions = []
    for label, payload, path in plan:
        sha, pid = last_green_sha(gl, f"{GROUP}/{path}")
        if not sha:
            print(f"   {label}: NO successful pipeline on {path} — skipping (seed via its next push)")
            continue
        actions.append((label, {**payload, "sha": sha}))
        print(f"   {label}: {sha[:12]} (pipeline #{pid})")
    if not args.apply:
        print(f"\ndry-run: {len(actions)} reseed /sync call(s) planned; re-run with --apply to execute.")
        return 0

    fail = 0
    for label, payload in actions:
        print(f"== reseed {label} @ {payload['sha'][:12]} ...", flush=True)
        st, body = sync(pc, payload, args.timeout)
        status = body.get("status", "?")
        issues = body.get("issues") or []
        print(f"   -> HTTP {st} status={status} job={body.get('job','')} issues={len(issues)}")
        for i in issues[:5]:
            print(f"      -", (i.get("message") or "")[:180])
        if st != 200 or issues:
            fail = 1
    print("== after:")
    for k, v in epochs(pc).items():
        print(f"   {k}: {str(v)[:12]}")
    print("reseed:", "FAILURES present" if fail else "all green")
    return fail


if __name__ == "__main__":
    raise SystemExit(main())
