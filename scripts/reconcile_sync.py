# FIxbot content reconciler — posts {"scope":"full"} to Pipe Control's
# /sync?wait=1, serialized in the single wait queue alongside CI-triggered
# runs. Deploy-only (the server runs the CLI against the published green
# epochs; nothing is re-fetched). Verdicts:
#   200 ok/completed      -> converged (or nothing to do)
#   202 timeout/queued    -> budget exhausted upstream; SKIP this tick —
#                            the next one retries (transient by design)
#   502/HTTPError/failed  -> propagation issues or platform failure -> exit 1,
#                            kube_job_status_failed lights the Grafana panel
import json
import os
import urllib.request

TOKEN = os.environ["PIPE_CONTROL_TOKEN"]
URL = os.environ.get(
    "PIPE_CONTROL_URL",
    "http://fixbot-fixbot-pipe-control-svc:80/sync?wait=1")
TIMEOUT = int(os.environ.get("TIMEOUT_SECONDS", "580"))


def main():
    req = urllib.request.Request(
        URL, data=b'{"scope":"full", "source":"reconcile"}', method="POST",
        headers={"Authorization": "Bearer " + TOKEN,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        print("reconcile: HTTPError %s: %s" % (e.code, e.read()[:400]))
        return 1
    status = body.get("status", "")
    print("reconcile: %s job=%s note=%s"
          % (status, body.get("job", ""), body.get("note", "")[:120]))
    for i in (body.get("issues") or [])[:10]:
        print("  issue:", (i.get("message") or "")[:200])
    if status in ("ok", "completed", "timeout", "queued-too-long"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
