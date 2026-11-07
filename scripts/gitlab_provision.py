"""FIxbot GitLab provisioning (post-install/post-upgrade hook).

Ensures the fixbot/qkb subgroup and one Question Knowledge Base (QKB) repo
per Course (<slug>-<year>) exist on GitLab, seeded with the Pipe-type
skeleton (ILP/EP/CEP/PEP — each with an empty mapping.json stub), a repo-root
assets/ inline-figure dir (.gitkeep) + the CI pipeline that validates and
triggers Pipe Control. Idempotent and NON-destructive: existing
groups/repos/files are never modified or deleted.

Env:
  GITLAB_URL     e.g. https://gitlab.example.com
  GITLAB_TOKEN   bot token (api scope, Maintainer on the fixbot group)
  GROUP_PATH     top-level group path (fixbot)
  QKB_SUBGROUP   subgroup path under GROUP_PATH (qkb)
  COURSES_JSON   [{"slug": "example", "repo": "example-2026"}, ...]
"""

import json
import os
import sys
import urllib.parse
import urllib.request

GITLAB_URL = os.environ["GITLAB_URL"].rstrip("/")
TOKEN = os.environ.get("GITLAB_TOKEN", "")
GROUP_PATH = os.environ.get("GROUP_PATH", "fixbot")
QKB_SUBGROUP = os.environ.get("QKB_SUBGROUP", "qkb")
COURSES = json.loads(os.environ.get("COURSES_JSON", "[]"))

if not TOKEN:
    sys.exit("GITLAB_TOKEN is empty — create the fixbot-gitlab-token Secret "
             "(see NOTES.txt) before enabling gitlabProvisioning.")


def api(method, path, payload=None):
    url = GITLAB_URL + "/api/v4" + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("PRIVATE-TOKEN", TOKEN)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def enc(path):
    return urllib.parse.quote(path, safe="")


def ensure_group(full_path, name, parent_id=None):
    status, g = api("GET", "/groups/" + enc(full_path))
    if status == 200:
        print(f"[ok] group {full_path} exists (id {g['id']})")
        return g["id"]
    payload = {"name": name, "path": name, "visibility": "private"}
    if parent_id:
        payload["parent_id"] = parent_id
    status, g = api("POST", "/groups", payload)
    if status not in (200, 201):
        sys.exit(f"failed to create group {full_path}: HTTP {status}")
    print(f"[created] group {full_path} (id {g['id']})")
    return g["id"]


def ensure_project(namespace_id, full_path, repo, slug):
    status, p = api("GET", "/projects/" + enc(full_path))
    created = False
    if status != 200:
        status, p = api("POST", "/projects", {
            "name": repo,
            "path": repo,
            "namespace_id": namespace_id,
            "visibility": "private",
            "default_branch": "main",
            "description": (
                f"FIxbot Question Knowledge Base (QKB) for course {slug}. "
                "Teacher-managed: each <TYPE>/<name>.json becomes a Pipe in "
                f"the {slug} Open WebUI. Pushes to main validate + propagate "
                "via Pipe Control."
            ),
        })
        if status not in (200, 201):
            sys.exit(f"failed to create repo {full_path}: HTTP {status}")
        created = True
        print(f"[created] repo {full_path} (id {p['id']})")
    else:
        print(f"[ok] repo {full_path} exists (id {p['id']})")
    _, tree = api("GET", f"/projects/{p['id']}/repository/tree")
    # An empty repo answers 404 {"message":"404 Tree Not Found"} (None), not
    # 200 [] — both are falsy. Seed only when there is genuinely no content.
    if not tree:
        seed_repo(p["id"], slug)
    ensure_pipeline_schedule(p["id"])
    return p["id"]


SCHEDULE_DESC = "weekly re-lint (B1+ backstop; content-ci lint drift detection)"


def ensure_pipeline_schedule(project_id):
    """Weekly re-lint backstop (readme-sync pushes carry [ci skip], so this
    catches content that no longer passes the latest lint). Idempotent."""
    status, schedules = api("GET", f"/projects/{project_id}/pipeline_schedules")
    if status == 200 and schedules:
        for s in schedules:
            if s.get("description") == SCHEDULE_DESC:
                print(f"[ok] repo {project_id}: weekly pipeline schedule exists "
                      f"(id {s['id']})")
                return
    # Stagger minutes deterministically by project id to avoid a Sunday herd.
    minute = project_id % 53
    status, s = api("POST", f"/projects/{project_id}/pipeline_schedules", {
        "description": SCHEDULE_DESC,
        "ref": "main",
        "cron": f"{minute} 3 * * 0",
        "cron_timezone": "UTC",
        "active": True,
    })
    if status not in (200, 201):
        print(f"[warn] repo {project_id}: could not create pipeline schedule "
              f"(HTTP {status}) — create it manually (Settings > CI/CD > Schedules)")
        return
    print(f"[created] repo {project_id}: weekly pipeline schedule "
          f"(id {s['id']}, Sundays 03:{minute:02d} UTC)")


def seed_repo(project_id, slug):
    actions = []
    for pipe_type in ("ILP", "EP", "CEP", "PEP"):
        # An empty mapping.json per Pipe type. Optional (a system prompt is
        # only templated once its main.txt uses $placeholders; an absent or
        # empty mapping is then a no-op), but seeded so the file's place is
        # discoverable — see the canonical QKB authoring guide. Values are
        # teacher-curated in the QKB repos — the chart never seeds content.
        actions.append({"action": "create",
                        "file_path": f"{pipe_type}/mapping.json",
                        "content": "{}\n"})
    # Repo-root inline-figure store (flat, images only — see the canonical QKB
    # authoring guide). The .gitkeep tracks the still-empty dir in git so the
    # path exists for teachers from the first commit.
    actions.append({"action": "create", "file_path": "assets/.gitkeep",
                    "content": ""})
    actions.append({"action": "create", "file_path": "README.md",
                    "content": README.replace("__SLUG__", slug)})
    actions.append({"action": "create", "file_path": ".gitlab-ci.yml",
                    "content": GITLAB_CI})
    # [ci skip]: the seed is pushed by the group bot, which IS a real group
    # member (so the thin include would resolve and the pipeline would run),
    # but that pipeline races the Course's own provisioning Job: the freshly
    # seeded repo's `propagate` reaches Pipe Control before
    # <release>-<slug>-provision exists, failing with
    # "<slug>: not found in .targets (skipped 0 pipes)". The skeleton carries
    # zero question files, so there is nothing to propagate anyway; a
    # teacher's first real push (and the weekly re-lint) establish the epoch.
    status, _ = api("POST", f"/projects/{project_id}/repository/commits", {
        "branch": "main",
        "commit_message": "Seed FIxbot QKB skeleton (by the FIxbot helm chart) [ci skip]",
        "actions": actions,
    })
    if status not in (200, 201):
        sys.exit(f"failed to seed repo {project_id}: HTTP {status}")
    print(f"[seeded] repo {project_id} with Pipe-type skeleton (incl. mapping stubs) + CI")


README = """\
# FIxbot QKB — course __SLUG__

Question Knowledge Base for the course **__SLUG__**. Each JSON file
under a Pipe-type folder becomes one Pipe (an Open WebUI model) in the
course's instance:

| Folder | Pipe type |
|--------|-----------|
| `ILP/` | Interactive Learning Pipe — study/exercise sessions |
| `EP/`  | Examination Pipe |
| `CEP/` | Code Examination Pipe |
| `PEP/` | Personalized Examination Pipe |

Do not rename this repo: the FIxbot chart tracks it as `<slug>-<year>`.

## Authoring guide

The full, always-current authoring guide (file shape, JSON Schema, workflow,
and `mapping.json` templating) lives in the `fixbot/fixbot-content-ci` repo:

- https://gitlab.example.com/fixbot/fixbot-content-ci/-/blob/main/qkb/README.md

The canonical version of this file is synced from there by its `qkb-readme-sync`
CI job; this per-course copy is maintained automatically.
"""


GITLAB_CI = """\
# FIxbot QKB CI — shared pipeline from fixbot/fixbot-content-ci (ref: main).
# This file is managed by the FIxbot helm chart (scripts/gitlab_provision.py)
# and kept minimal on purpose: the actual jobs/scripts live in the shared
# fixbot-content-ci repo, so every QKB repo always runs the latest pipe.
# (Runtime clones in the shared pipeline authenticate as
# gitlab-ci-token:${CI_JOB_TOKEN} — see scripts/ in fixbot-content-ci.)
include:
  - project: fixbot/fixbot-content-ci
    ref: main
    file: /pipelines/qkb.yml
"""


def main():
    print(f"FIxbot GitLab provisioning: {len(COURSES)} course(s), "
          f"group {GROUP_PATH}/{QKB_SUBGROUP}")
    parent_id = ensure_group(GROUP_PATH, GROUP_PATH)
    qkb_id = ensure_group(f"{GROUP_PATH}/{QKB_SUBGROUP}", QKB_SUBGROUP, parent_id)
    for c in COURSES:
        ensure_project(qkb_id, f"{GROUP_PATH}/{QKB_SUBGROUP}/{c['repo']}",
                       c["repo"], c["slug"])
    print("done — nothing deleted, nothing overwritten")


if __name__ == "__main__":
    main()
