# FIxbot Pipe Control — HTTP front (ADR 0006). Endpoints:
#   GET  /health   — liveness (no auth)
#   GET  /qkb-assets/<token>/<file> — PUBLIC (no auth) inline question figures:
#                    serves <file> from the requesting Course's QKB repo assets/.
#                    The token is sha256(<file>)[:16] (obfuscation; the ILP base
#                    adds it at render time), and a bare /qkb-assets/<file> is
#                    NOT served.
#                    The Course is picked from the request Host; each Course's
#                    ingress routes this root-relative prefix here (chat pages
#                    live at /c/<id> and Open WebUI itself owns /assets/, hence
#                    the dedicated prefix). Image files only, flat, never JSON.
#   GET  /repos    — dir listing of the synced content roots (Bearer)
#   GET  /targets  — Course API targets discovered from *-provision
#                    Secrets (Bearer; never leaks API keys)
#   POST /sync     — entry point for GitLab CI. Verifies Bearer, then:
#                    mode=stub: echo + current repo state;
#                    mode=real: spawns a fixbot-pipe-propagate-* Job that runs
#                    the real pipe_control CLI image against the shared
#                    content volume (verbose CLI logs land in Loki).
import json, os, re, ssl, threading, time, urllib.request, urllib.parse, hashlib, hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("PIPE_CONTROL_TOKEN", "")
NAMESPACE = os.environ.get("NAMESPACE", "fixbot")
RELEASE = os.environ.get("RELEASE", "fixbot")
MODE = os.environ.get("MODE", "stub")
ROOTS = {
    "sys_prompts": os.environ.get("FIXBOT_SYS_PROMPTS_PATH", "/fixbot/sys-prompts"),
    "pipe_bases": os.environ.get("FIXBOT_PIPE_BASE_PATH", "/fixbot/pipe-bases"),
    "qkb": os.environ.get("FIXBOT_QKB_PATH", "/fixbot/qkb"),
}
KUBE_SA = "/var/run/secrets/kubernetes.io/serviceaccount"

# Public inline-figure route. Each Course's ingress maps ASSET_PREFIX on the
# Course host to this service; the request Host selects the Course's QKB repo
# (QKB_REPO_BY_HOST, rendered from values.yaml as "<host>=<repo>,…"). Serving
# is deliberately narrow — a single image file living directly under the
# repo's assets/, addressed through an obfuscation token segment so the raw
# path is never advertised — so it can never expose question JSON (which holds
# the answers), .git, dotfiles, or anything outside assets/.
ASSET_PREFIX = "/qkb-assets/"
ASSET_MIME = {".png": "image/png", ".jpg": "image/jpeg",
              ".jpeg": "image/jpeg", ".webp": "image/webp"}
ASSET_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
ASSET_TOKEN_RE = re.compile(r"[0-9a-f]{16}")
ASSET_CACHE_SECONDS = os.environ.get("QKB_ASSET_CACHE_SECONDS", "600")


def _asset_token(name):
    """Obfuscation token for a bare asset file name: sha256(name)[:16].

    Deterministic and stateless — the ILP Pipe base computes the exact same
    value at render time and puts it in the request path. Keep in lockstep
    with pipe-bases/ILP/main.py::_asset_token; any drift 404s every figure.
    """
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


def _parse_host_repo_map(raw):
    out = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" not in pair:
            continue
        host, repo = pair.split("=", 1)
        if host.strip() and repo.strip():
            out[host.strip().lower()] = repo.strip()
    return out


QKB_REPO_BY_HOST = _parse_host_repo_map(os.environ.get("QKB_REPO_BY_HOST", ""))

# Propagation Job wiring (mode=real only, rendered from pipeControl.propagate).
PROPAGATE_IMAGE = os.environ.get("PROPAGATE_IMAGE", "")
PROPAGATE_PULL_POLICY = os.environ.get("PROPAGATE_IMAGE_PULL_POLICY", "Always")
PROPAGATE_PULL_SECRET = os.environ.get("PROPAGATE_IMAGE_PULL_SECRET", "")
PROPAGATE_SCRIPTS_CM = os.environ.get("PROPAGATE_SCRIPTS_CM", "")
PROPAGATE_CONTENT_PVC = os.environ.get("PROPAGATE_CONTENT_PVC", "")
PROPAGATE_SA_NAME = os.environ.get("PROPAGATE_SA_NAME", "")
COURSES = [c for c in os.environ.get("COURSES", "").split() if c]
# In-Job git fetch wiring (rendered from pipeControl.fetch + pipeControl.repos).
# rev 38+: the propagate Job is the SOLE WRITER of the shared content volume —
# it fetches every enabled repo itself (the git-sync sidecars are retired).
PROPAGATE_GIT_USERNAME = os.environ.get("PROPAGATE_GIT_USERNAME", "oauth2")
PROPAGATE_GIT_TOKEN_SECRET = os.environ.get("PROPAGATE_GIT_TOKEN_SECRET", "")
PROPAGATE_GIT_TOKEN_KEY = os.environ.get("PROPAGATE_GIT_TOKEN_KEY", "token")
PROPAGATE_GITLAB_BASE_URL = os.environ.get("PROPAGATE_GITLAB_BASE_URL", "")
PROPAGATE_GIT_DEPTH = os.environ.get("PROPAGATE_GIT_DEPTH", "50")
PROPAGATE_GIT_PROXY = os.environ.get("PROPAGATE_GIT_PROXY", "")
PROPAGATE_REPO_REFS = os.environ.get("PROPAGATE_REPO_REFS", "")
PROPAGATE_REPO_PATHS = os.environ.get("PROPAGATE_REPO_PATHS", "")
PROPAGATE_QKB_REPOS = os.environ.get("PROPAGATE_QKB_REPOS", "")
# /sync {"test": true}: REHEARSAL mode (rendered from pipeControl.test.enabled).
# The Job fetches the caller's repo at the sha into a THROWAWAY tree (never
# publishes → the per-repo green epoch is untouched) and runs the CLI with
# --test, which reads each real target read-only and writes slug-prefixed
# copies to the sandbox `test` Course only. Disabled => a test:true call is
# rejected 400 — NEVER silently downgraded to a real propagation.
TEST_ENABLED = os.environ.get(
    "TEST_ENABLED", "true").lower() in ("1", "true", "yes")
# /sync?wait=1 blocks until the propagation Job terminates (or this cap).
# Kept below the caller's curl --max-time (600s in fixbot-content-ci) so the
# HTTP response always arrives before the client gives up.
WAIT_MAX_SECONDS = int(os.environ.get("WAIT_MAX_SECONDS", "570"))
SUMMARY_PREFIX = "PIPE_CONTROL_SUMMARY "
# wait=1 calls SERIALIZE on this lock: each caller spawns its own fresh Job,
# waits on it, and gets ITS OWN scoped verdict — a Job is never killed, and
# concurrent pushes queue instead of racing. (Earlier latest-wins reaping
# was rejected: a displaced waiter only learned "timeout" after minutes and
# nobody graded its push.) The lock is a plain threading.Lock: contention is
# a handful of CI jobs per day.
SYNC_LOCK = threading.Lock()


def kube_api(method, path, obj=None):
    with open(KUBE_SA + "/token") as f:
        tok = f.read().strip()
    body = json.dumps(obj).encode() if obj is not None else None
    req = urllib.request.Request(
        "https://kubernetes.default.svc%s" % path, data=body, method=method,
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": "application/json"})
    ctx = ssl.create_default_context(cafile=KUBE_SA + "/ca.crt")
    with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
        return json.loads(r.read())


def list_dir(path, depth=2):
    out = {}
    if depth < 0 or not os.path.isdir(path):
        return out
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        if os.path.isdir(full):
            out[name + "/"] = list_dir(full, depth - 1)
        else:
            try:
                out[name] = os.path.getsize(full)
            except OSError:
                out[name] = None
    return out


def git_head(path):
    """HEAD sha of a published content dir (the per-repo green epoch — the
    volume IS the memory, ADR 0006 rev 43+). No git binary in this image:
    resolve .git/HEAD manually (loose ref, then packed-refs). None if the
    dir has no readable .git."""
    try:
        with open(os.path.join(path, ".git", "HEAD")) as f:
            ref_line = f.read().strip()
    except OSError:
        return None
    if not ref_line.startswith("ref:"):
        return ref_line or None  # detached HEAD (strict checkout)
    ref = ref_line.split(None, 1)[1].strip()
    try:
        with open(os.path.join(path, ".git", ref)) as f:
            return f.read().strip()
    except OSError:
        pass
    try:
        with open(os.path.join(path, ".git", "packed-refs")) as f:
            for line in f:
                line = line.rstrip()
                if line and not line.startswith(("#", "^")) and \
                        line.endswith(" " + ref):
                    return line.split()[0]
    except OSError:
        pass
    return None


def repo_epochs():
    """{content dir -> HEAD sha} for every published repo root."""
    out = {}
    for key, root in ROOTS.items():
        if key == "qkb":
            if os.path.isdir(root):
                for name in sorted(os.listdir(root)):
                    full = os.path.join(root, name)
                    if os.path.isdir(full):
                        out["qkb/" + name] = git_head(full)
        else:
            out[key] = git_head(root)
    return out


def discover_targets():
    try:
        data = kube_api("GET", "/api/v1/namespaces/%s/secrets" % NAMESPACE)
    except Exception as e:
        return {"error": str(e)}
    pat = re.compile(r"^%s-(.+)-provision$" % re.escape(RELEASE))
    targets = {}
    for item in data.get("items", []):
        m = pat.match(item.get("metadata", {}).get("name", ""))
        if m:
            slug = m.group(1)
            targets[slug] = {"url": "http://%s-%s-svc" % (RELEASE, slug)}
    return targets


def strict_pin(payload):
    """Strict pull-to-sha for sha-bearing calls (all CI callers — propagate.sh
    refuses to run without --sha). Returns (full_sha, repo_path) when the
    payload names a deterministic content-tree path to pin:
    scope=course -> /fixbot/qkb/<repo>; scope=global -> the named shared
    repo. ('', '') for sha-less calls (the 5-minute reconcile, ops syncs):
    those are DEPLOY-ONLY — the Job runs the CLI against the published epochs
    without re-fetching anything, because since rev 43 the published volume
    itself is the per-repo green memory (a fetch of an unpinned branch HEAD
    would import validate-RED content). A sha-bearing run fetches+advances
    ONLY the named epoch; a sha already superseded on its ref (moved past
    the fetch depth) is a GREEN skip — the newer push's own pipeline grades
    the newer content."""
    sha = re.sub(r"[^0-9a-f]", "", str(payload.get("sha", "")).lower())
    if not sha:
        return "", ""
    scope = payload.get("scope", "")
    if scope == "course":
        repo = re.sub(r"[^a-z0-9-]", "", str(payload.get("repo", "")).lower())
        if repo:
            return sha, "/fixbot/qkb/" + repo
    elif scope == "global":
        t = payload.get("type", "")
        if t in ("sys_prompts", "pipe_bases"):
            return sha, "/fixbot/" + t.replace("_", "-")
    return "", ""


def spawn_propagate_job(payload, test=False):
    """Create a one-shot propagation Job running the real pipe_control CLI.

    The Job mounts the shared content volume READ-WRITE (it is the sole
    writer: fetch+publish in run-propagate.sh), projects each Course's
    provision API key (minted by the provisioning Jobs) into /targets, and
    runs scripts/run-propagate.sh (in this same ConfigMap). Git credentials
    for the fetch phase ride a secretKeyRef (read_repository least-privilege
    token) + an env-driven credential helper, never argv/URLs. Job pod logs
    are picked up by the namespace-wide syslog flow -> Loki.

    `test=True` (a REHEARSAL /sync) exports REHEARSE=1: run-propagate.sh then
    fetches the named repo at the sha into a throwaway tree and runs the CLI
    with --test against that tree WITHOUT publishing — the green epoch never
    moves, and the only writes land on the sandbox `test` Course instance.
    """
    strict_sha, strict_path = strict_pin(payload)
    sha = strict_sha[:8] or re.sub(r"[^a-z0-9-]", "",
                                   str(payload.get("sha", ""))[:8].lower())
    repo = str(payload.get("repo", ""))[:80]
    name = "fixbot-pipe-propagate-%s%s%s-%d" % (
        "test-" if test else "", (sha + "-") if sha else "",
        "%x" % int(time.time()), os.getpid() % 100)
    name = name[:63]
    # optional=True: each Course's provision Secret only exists once its
    # (webui-dependent) provisioning Job first completes. Without this, ONE
    # stuck/failed/half-created Course blocks mounts of the Job for ALL
    # Courses; run-propagate.sh then skips the missing keys loudly (the Course
    # is not API-reachable anyway until provisioned).
    projected = [{
        "secret": {
            "name": "%s-%s-provision" % (RELEASE, slug),
            "optional": True,
            "items": [{"key": "apikey", "path": "key-" + slug}],
        }
    } for slug in COURSES]
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {
                "app.kubernetes.io/part-of": "fixbot",
                "fixbot.example.com/component": "pipe-propagate",
            },
            "annotations": {
                "fixbot.example.com/sync-sha": strict_sha or (sha or "none"),
                "fixbot.example.com/sync-repo": repo,
                "fixbot.example.com/sync-strict-repo": strict_path or "none",
                "fixbot.example.com/sync-mode": "test" if test else "propagate",
            },
        },
        "spec": {
            # Keep finished Jobs briefly for kubectl-debugging; run logs are
            # durable in Loki regardless (namespace syslog flow).
            "ttlSecondsAfterFinished": 3600,
            # 4: transient GitLab fetch slowness / mid-publish races of two
            # overlapping Jobs self-heal on retry — no manual Job deletes.
            "backoffLimit": 4,
            # Fetch+publish of all repos adds ~20-60s over the old read-only
            # Job (typical total 1-2min; heavy cold onboarding ~4min). 600
            # covers the full retry budget (5 attempts) without dead headroom
            # and aligns with the CI caller's curl --max-time budget (wait
            # cap is 570s server-side).
            "activeDeadlineSeconds": 600,
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/part-of": "fixbot",
                        "fixbot.example.com/component": "pipe-propagate",
                    },
                },
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": PROPAGATE_SA_NAME,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "fsGroup": 65533,
                        "fsGroupChangePolicy": "OnRootMismatch",
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [{
                        "name": "propagate",
                        "image": PROPAGATE_IMAGE,
                        "imagePullPolicy": PROPAGATE_PULL_POLICY,
                        "command": ["sh", "/scripts/run-propagate.sh"],
                        "env": ([
                            {"name": "COURSES",
                             "value": " ".join(COURSES)},
                            {"name": "RELEASE", "value": RELEASE},
                            # Fetch phase (rev 38+): repos/refs/paths +
                            # least-privilege git credentials (optional like
                            # the old sidecars: a missing Secret must not
                            # block pod creation while bootstrapping).
                            {"name": "GITLAB_BASE_URL",
                             "value": PROPAGATE_GITLAB_BASE_URL},
                            {"name": "REPO_REFS",
                             "value": PROPAGATE_REPO_REFS},
                            {"name": "REPO_PATHS",
                             "value": PROPAGATE_REPO_PATHS},
                            {"name": "QKB_REPOS",
                             "value": PROPAGATE_QKB_REPOS},
                            {"name": "GIT_DEPTH",
                             "value": PROPAGATE_GIT_DEPTH},
                            {"name": "GITLAB_USERNAME",
                             "value": PROPAGATE_GIT_USERNAME},
                            {"name": "GITLAB_TOKEN",
                             "valueFrom": {"secretKeyRef": {
                                 "name": PROPAGATE_GIT_TOKEN_SECRET,
                                 "key": PROPAGATE_GIT_TOKEN_KEY,
                                 "optional": True}}},
                        ] + ([
                            {"name": "GIT_PROXY", "value": PROPAGATE_GIT_PROXY},
                        ] if PROPAGATE_GIT_PROXY else []) + ([
                            {"name": "STRICT_SHA", "value": strict_sha},
                            {"name": "STRICT_REPO_PATH", "value": strict_path},
                        ] if strict_sha else []) + ([
                            # Rehearsal: no publish, CLI --test against the
                            # throwaway tree (see run-propagate.sh).
                            {"name": "REHEARSE", "value": "1"},
                        ] if test else [])),
                        "securityContext": {
                            # uid 65533: the git-sync era's owner uid of the
                            # whole published tree — publishes/prunes/swaps
                            # must be OWNER-driven. runAsGroup was tried and
                            # abandoned: this NFS maps the 65533 group as
                            # "nogroup", so group-based writes were refused
                            # (rev-38 publish EACCES). fsGroup 65533 below
                            # stays for the /work emptyDir on the node fs.
                            "runAsUser": 65533,
                            "allowPrivilegeEscalation": False,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        # Generous defaults for a short-lived batch run; the
                        # CLI is I/O bound (syncs content → HTTP upserts).
                        "resources": {
                            "requests": {"cpu": "50m", "memory": "128Mi"},
                            "limits": {"cpu": "500m", "memory": "512Mi"},
                        },
                        "volumeMounts": [
                            # RW — the Job is the sole writer of the content
                            # volume (fetch+publish before the CLI runs).
                            {"name": "content", "mountPath": "/fixbot"},
                            {"name": "work", "mountPath": "/work"},
                            {"name": "targets", "mountPath": "/targets",
                             "readOnly": True},
                            {"name": "scripts", "mountPath": "/scripts",
                             "readOnly": True},
                        ],
                    }],
                    # runAsGroup/fsGroup 65533 keeps the published tree
                    # group-writable across writers (see container secCtx).
                    "volumes": [
                        {"name": "content",
                         "persistentVolumeClaim": {
                             "claimName": PROPAGATE_CONTENT_PVC}},
                        {"name": "work", "emptyDir": {}},
                        {"name": "targets",
                         "projected": {"sources": projected}},
                        {"name": "scripts",
                         "configMap": {"name": PROPAGATE_SCRIPTS_CM,
                                       "defaultMode": 0o755}},
                    ],
                },
            },
        },
    }
    if PROPAGATE_PULL_SECRET:
        job["spec"]["template"]["spec"]["imagePullSecrets"] = [
            {"name": PROPAGATE_PULL_SECRET}]
    created = kube_api(
        "POST", "/apis/batch/v1/namespaces/%s/jobs" % NAMESPACE, job)
    return {"name": created["metadata"]["name"],
            "uid": created["metadata"].get("uid", "")}


def job_terminal_state(job):
    """('Complete'|'Failed', reason) once the Job is terminal, else (None, '')."""
    for c in (job.get("status") or {}).get("conditions", []):
        if c.get("type") in ("Complete", "Failed") and c.get("status") == "True":
            return c["type"], c.get("reason") or ""
    return None, ""


def job_logs(name, tail_lines=40):
    """Log text of the Job's most recent pod (restartPolicy=Never => 1 pod
    per attempt; backoffLimit>0 may leave several). '' when unavailable."""
    try:
        pods = kube_api(
            "GET", "/api/v1/namespaces/%s/pods?labelSelector=job-name=%s"
            % (NAMESPACE, name))
        items = pods.get("items", [])
        if not items:
            return ""
        items.sort(key=lambda p: p["metadata"].get("creationTimestamp", ""))
        pod = items[-1]["metadata"]["name"]
        req_path = ("/api/v1/namespaces/%s/pods/%s/log?tailLines=%d"
                    % (NAMESPACE, pod, tail_lines * 4))
        # raw text, not JSON — inline the request
        with open(KUBE_SA + "/token") as f:
            tok = f.read().strip()
        req = urllib.request.Request(
            "https://kubernetes.default.svc" + req_path,
            headers={"Authorization": "Bearer " + tok})
        ctx = ssl.create_default_context(cafile=KUBE_SA + "/ca.crt")
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            text = r.read().decode("utf-8", "replace")
        lines = text.splitlines()
        return "\n".join(lines[-tail_lines:])
    except Exception as e:
        return "(log fetch failed: %s)" % e


def wait_for_job(name, deadline):
    """Poll until terminal or `deadline` (time.time()-based). Returns
    (state, reason, logs); state in 'Complete'|'Failed'|'Timeout'."""
    state, reason = None, ""
    while time.time() < deadline:
        try:
            job = kube_api(
                "GET", "/apis/batch/v1/namespaces/%s/jobs/%s" % (NAMESPACE, name))
            state, reason = job_terminal_state(job)
            if state:
                break
        except Exception as e:
            print("wait_for_job poll failed: %s" % e)
        time.sleep(3)
    if not state:
        return "Timeout", "", ""
    return state, reason, job_logs(name)


def caller_scope(repo):
    """Scope prefixes for issue attribution. '<slug>-<year>' -> both the repo
    name and the bare slug; issue messages always start with one of them
    ('<repo>/…' from the loader, '<slug>: …' from the CLI).
    Empty repo => empty set => caller gets GLOBAL grading (ops/manual use)."""
    repo = re.sub(r"[^a-z0-9-]", "", str(repo).lower())
    terms = {t for t in (repo, repo.rsplit("-", 1)[0]) if t} if repo else set()
    return tuple(t for t in terms if t)


def issue_in_scope(message, scope):
    """True when the issue belongs to this caller. Loader issues read
    '<repo>/TYPE…', CLI issues '<slug>: …' — fenced prefix match on the
    first path segment / before the first colon."""
    if not scope:
        return True
    head = re.split(r"[/:]", message, 1)[0]
    return head in scope


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = (json.dumps(obj, indent=2) + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if not TOKEN:
            self._send(503, {"error": "auth not configured: "
                             "PIPE_CONTROL_TOKEN unset (create "
                             "secret fixbot-pipe-control-token)"})
            return False
        auth = self.headers.get("Authorization", "")
        if auth != "Bearer " + TOKEN:
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    def _serve_asset(self):
        """Public, unauthenticated inline-figure GET
        (ASSET_PREFIX + <token>/<file>).

        Resolves the Course's QKB repo from the request Host and streams one
        image file from <repo>/assets/. The path MUST carry the obfuscation
        token (sha256 of the bare file name) as its first segment; the bare
        legacy form is no longer served. Rejects (404) anything that is not a
        flat, well-formed image file name with a matching token resolving
        inside assets/ — nested paths, dotfiles, traversal (%2e%2e), non-image
        extensions (notably *.json, which holds the answers), and forged
        tokens are never served.
        """
        path = urllib.parse.urlsplit(self.path).path
        rest = urllib.parse.unquote(path[len(ASSET_PREFIX):])
        token, sep, name = rest.partition("/")
        ext = os.path.splitext(name)[1].lower()
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        repo = QKB_REPO_BY_HOST.get(host)
        if (not sep or not repo or ext not in ASSET_MIME
                or not ASSET_TOKEN_RE.fullmatch(token)
                or not hmac.compare_digest(token, _asset_token(name))
                or not ASSET_NAME_RE.fullmatch(name)):
            self._send(404, {"error": "not found"})
            return
        assets_dir = os.path.realpath(os.path.join(ROOTS["qkb"], repo, "assets"))
        real = os.path.realpath(os.path.join(assets_dir, name))
        if not real.startswith(assets_dir + os.sep):  # confinement + symlink guard
            self._send(404, {"error": "not found"})
            return
        try:
            with open(real, "rb") as f:
                data = f.read()
        except OSError:
            self._send(404, {"error": "not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", ASSET_MIME[ext])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control",
                         "public, max-age=%s" % ASSET_CACHE_SECONDS)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok", "mode": MODE})
            return
        if self.path.startswith(ASSET_PREFIX):
            self._serve_asset()
            return
        if not self._authed():
            return
        if self.path == "/repos":
            self._send(200, {"epochs": repo_epochs(),
                             "tree": {k: list_dir(v)
                                      for k, v in ROOTS.items()}})
        elif self.path == "/targets":
            self._send(200, {"targets": discover_targets()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path != "/sync":
            self._send(404, {"error": "not found"})
            return
        if not self._authed():
            return
        wait = urllib.parse.parse_qs(url.query).get("wait", [""])[0] in ("1", "true")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            body = {}
        if body.get("test") is True and not TEST_ENABLED:
            self._send(400, {"error": "test mode disabled "
                             "(pipeControl.test.enabled=false)"})
            return
        if MODE == "real":
            test = body.get("test") is True
            note = ("sha-bearing sync: the Job fetches+publishes ONLY the "
                    "named repo at the pushed sha (strict pull-to-sha; "
                    "superseded sha = green skip) and propagates; sha-less "
                    "sync (reconcile/ops full) is deploy-only over the "
                    "published green epochs; valves pin commits")
            if test:
                note = ("REHEARSAL (test): the Job fetches the named repo at "
                        "the sha into a throwaway tree and runs the CLI --test "
                        "WITHOUT publishing (green epoch UNCHANGED); writes "
                        "land on the sandbox 'test' instance only. " + note)
            if not wait:
                # Fire-and-forget (legacy/manual callers): spawn and return.
                # No lock, no reaping — overlapping Jobs were tolerated for
                # months and the upserts are idempotent / commit-pinned.
                try:
                    job = spawn_propagate_job(body, test=test)
                except Exception as e:
                    self._send(500, {"status": "error", "mode": "real",
                                     "error": "failed to spawn propagation "
                                              "Job: %s" % e})
                    return
                self._send(202, {
                    "status": "accepted",
                    "mode": "real",
                    "job": job["name"],
                    "received": body,
                    # A sha-bearing call pins the caller's repo itself (the
                    # Job fetches it); other repos propagate from their
                    # freshly fetched ref HEADs, and the CLI records content
                    # commit pins in Pipe valves — a missed push self-heals
                    # on the next /sync.
                    "note": note,
                })
                return
            # wait=1: CI feedback mode. Serialized through SYNC_LOCK — this
            # caller spawns its own fresh Job and gets ITS OWN verdict, even
            # when pushes arrive concurrently; queued callers that cannot get
            # in before the shared deadline get 202 (never red).
            scope = caller_scope(body.get("repo", ""))
            deadline = time.time() + WAIT_MAX_SECONDS
            if not SYNC_LOCK.acquire(timeout=max(0.0, deadline - time.time())):
                self._send(202, {
                    "status": "queued-too-long",
                    "note": "another propagation is running; queued past %ds "
                            "budget — propagation will still land via the "
                            "running/next /sync; " % WAIT_MAX_SECONDS + note,
                })
                return
            try:
                try:
                    job = spawn_propagate_job(body, test=test)
                except Exception as e:
                    self._send(500, {"status": "error", "mode": "real",
                                     "error": "failed to spawn propagation "
                                              "Job: %s" % e})
                    return
                state, reason, logs = wait_for_job(
                    job["name"], deadline)
            finally:
                SYNC_LOCK.release()
            report = None
            for line in reversed(logs.splitlines()):
                if line.startswith(SUMMARY_PREFIX):
                    try:
                        report = json.loads(line[len(SUMMARY_PREFIX):])
                    except Exception:
                        report = None
                    break
            if state == "Timeout":
                self._send(202, {
                    "status": "timeout",
                    "job": job["name"],
                    "note": "still running after %ds; result lands in Loki; "
                            % WAIT_MAX_SECONDS + note,
                })
                return
            if state == "Failed":
                # Failed (exit!=0): platform-level only (content volume,
                # shared bases/prompts) — per-repo/target breakage is
                # isolated into scoped summary issues by the CLI instead.
                # Ops-owned and rare -> correctly red for every caller.
                self._send(502, {
                    "status": "failed",
                    "job": job["name"],
                    "reason": reason,
                    "log_tail": logs,
                    "note": note,
                })
                return
            if report is None:
                self._send(200, {
                    "status": "completed",
                    "job": job["name"],
                    "note": "no PIPE_CONTROL_SUMMARY in logs (pre-summary "
                            "CLI image?); " + note,
                })
                return
            # Scoped grading (cross-course CI safety): redness is attributed
            # by the caller's repo — only issues that START with this
            # caller's scope ('<repo>/…' loader, '<slug>: …' CLI) red this
            # pipeline; other Courses' issues are reported but stay green.
            issues = report.get("issues") or []
            mine = [i for i in issues
                    if issue_in_scope(i.get("message", ""), scope)]
            others = [i for i in issues
                      if not issue_in_scope(i.get("message", ""), scope)]
            code = 200 if not mine else 502
            self._send(code, {
                "status": "ok" if not mine else "issues",
                "test": test,
                "job": job["name"],
                "repo": body.get("repo", ""),
                "issues": mine,
                "other_issues_report_only": others,
                "targets": report.get("targets", {}),
                "note": note,
            })
            return
        self._send(200, {
            "status": "stub",
            "received": body,
            "propagation": "not implemented (set pipeControl.mode: real)",
            "repos": {k: list(list_dir(v).keys()) for k, v in ROOTS.items()},
        })


if __name__ == "__main__":
    # Threading: wait=1 requests hold a handler for the Job's lifetime —
    # /health probes and concurrent syncs must keep being served meanwhile
    # (single-threaded HTTPServer failed liveness probes mid-wait).
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))),
                        Handler).serve_forever()
