#!/usr/bin/env python3
"""Fixbot provisioning - reconcile admins + settings on a running Open WebUI.

Auth:
  - First run (fresh DB): signs up a dedicated service-account admin via
    ENABLE_INITIAL_ADMIN_SIGNUP, mints its API key and stores it in the
    per-Course Secret. Later runs reuse that key, so no password auth is
    ever opened on the instance (SSO stays the only login).
  - The service account is created ONLY while the DB has no users, so run the
    Job before anyone SSO-logs in.
"""
import base64
import json
import os
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.request

TARGET = os.environ["TARGET_URL"]
NS = os.environ["NAMESPACE"]
SECRET = os.environ["PROVISION_SECRET_NAME"]
SA_EMAIL = os.environ["SA_EMAIL"]
ADMIN_EMAILS = json.loads(os.environ.get("ADMIN_EMAILS", "[]"))
REENABLE_SIGNUP = os.environ.get("REENABLE_SIGNUP", "").lower() == "true"


def http(method, path, token=None, payload=None):
    url = TARGET.rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
            body = json.loads(raw) if "json" in ct.lower() else raw.decode()
            return resp.status, body
    except urllib.error.HTTPError as err:
        raw = err.read()
        ct = err.headers.get("Content-Type", "")
        try:
            body = json.loads(raw) if "json" in ct.lower() else raw.decode()
        except Exception:
            body = raw.decode(errors="replace")
        return err.code, body


def kube_request(method, path, payload=None, content_type="application/json"):
    base = "/var/run/secrets/kubernetes.io/serviceaccount"
    token = open(base + "/token").read().strip()
    ctx = ssl.create_default_context(cafile=base + "/ca.crt")
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        "https://%s:%s%s" % (host, port, path),
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": content_type},
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw)
        except Exception:
            return err.code, raw.decode(errors="replace")


def _secret_url():
    return "/api/v1/namespaces/%s/secrets/%s" % (NS, SECRET)


def _secret_body(apikey):
    encoded = base64.b64encode(apikey.encode()).decode()
    data = {}
    data["apikey"] = encoded
    meta = {}
    meta["name"] = SECRET
    meta["namespace"] = NS
    body = {}
    body["apiVersion"] = "v1"
    body["kind"] = "Secret"
    body["metadata"] = meta
    body["type"] = "Opaque"
    body["data"] = data
    return body


def get_apikey():
    status, body = kube_request("GET", _secret_url())
    if status == 200 and isinstance(body, dict):
        data = body.get("data") or {}
        val = data.get("apikey")
        if val:
            return base64.b64decode(val.encode()).decode()
    return ""


def store_apikey(apikey):
    payload = _secret_body(apikey)
    status, _ = kube_request(
        "PATCH",
        _secret_url(),
        payload=payload,
        content_type="application/strategic-merge-patch+json",
    )
    if status not in (200, 201):
        # Secret does not exist yet on first install - create it.
        kube_request(
            "POST",
            "/api/v1/namespaces/%s/secrets" % NS,
            payload=payload,
        )


def clear_apikey():
    data = {}
    data["apikey"] = ""
    payload = {}
    payload["data"] = data
    kube_request(
        "PATCH",
        _secret_url(),
        payload=payload,
        content_type="application/strategic-merge-patch+json",
    )


def wait_ready(timeout=600):
    start = time.time()
    while time.time() - start < timeout:
        try:
            status, _ = http("GET", "/health")
            if status == 200:
                return
        except Exception:
            pass
        time.sleep(5)
    print("Open WebUI did not become ready in time")
    sys.exit(1)


def bootstrap_apikey():
    password = secrets.token_urlsafe(24)
    status, body = http(
        "POST",
        "/api/v1/auths/signup",
        payload={"email": SA_EMAIL, "password": password, "name": "fixbot"},
    )
    if not (isinstance(body, dict) and body.get("token")):
        print("service-account signup failed (%s): %s" % (status, body))
        sys.exit(1)
    jwt = body["token"]
    status, kbody = http("POST", "/api/v1/auths/api_key", token=jwt)
    if not (isinstance(kbody, dict) and kbody.get("api_key")):
        print("minting api key failed (%s): %s" % (status, kbody))
        sys.exit(1)
    apikey = kbody["api_key"]
    store_apikey(apikey)
    print("bootstrapped service-account api key")
    return apikey


def reconcile_admins(apikey):
    status, body = http("GET", "/api/v1/users/all", token=apikey)
    if status not in (200, 201):
        print("listing users failed (%s): %s" % (status, body))
        sys.exit(1)
    if isinstance(body, dict):
        users = body.get("users", [])
    elif isinstance(body, list):
        users = body
    else:
        print("unexpected users payload: %s" % (body,))
        sys.exit(1)
    by_email = {}
    for u in users:
        by_email[u.get("email", "").lower()] = u

    # The service account must stay admin (its API key is how we authenticate),
    # so keep it in the desired admin set along with the values.yaml admins.
    desired = set(e.lower() for e in ADMIN_EMAILS if e and e.strip())
    sa_lower = SA_EMAIL.lower()
    desired.add(sa_lower)

    for email in sorted(desired):
        user = by_email.get(email)
        if user:
            if user.get("role") != "admin":
                st, rb = http(
                    "POST",
                    "/api/v1/users/%s/update" % user["id"],
                    token=apikey,
                    payload={"role": "admin"},
                )
                if st not in (200, 201):
                    print("promote failed for %s (%s): %s" % (email, st, rb))
                else:
                    print("promoted admin:", email)
        else:
            name = email.split("@")[0]
            st, rb = http(
                "POST",
                "/api/v1/auths/add",
                token=apikey,
                payload={
                    "email": email,
                    "password": secrets.token_urlsafe(24),
                    "name": name,
                    "role": "admin",
                },
            )
            if st not in (200, 201):
                print("create admin failed for %s (%s): %s" % (email, st, rb))
            else:
                print("created admin:", email)

    for user in users:
        email = user.get("email", "").lower()
        if user.get("role") == "admin" and email not in desired:
            st, _ = http(
                "POST",
                "/api/v1/users/%s/update" % user["id"],
                token=apikey,
                payload={"role": "user"},
            )
            if st == 403:
                print("skipped protected primary/other admin:", user.get("email"))


def reenable_signup(apikey):
    """Re-enable self-service signup (openWebUI.selfSignup).

    OWUI auto-disables signup after the FIRST user signs up (auths.py) - and
    this Job's bootstrap service account IS that first user - so for Courses
    that want public signup we restore the env-declared state. With
    ENABLE_PERSISTENT_CONFIG=false this rewrites the running process's config
    (env is authoritative at the next boot anyway); harmless when already on.
    """
    if not REENABLE_SIGNUP:
        return
    status, body = http(
        "POST",
        "/api/v1/configs/import",
        token=apikey,
        payload={"config": {"ui.enable_signup": True}},
    )
    if status not in (200, 201):
        print("re-enabling signup failed (%s): %s" % (status, body))
    else:
        print("self-signup re-enabled")


def apply_settings(apikey):
    cfg = {}

    def parse_json(value):
        if not value:
            return None
        try:
            return json.loads(value)
        except Exception:
            return None

    if os.environ.get("DEFAULT_MODELS") is not None:
        cfg["DEFAULT_MODELS"] = os.environ.get("DEFAULT_MODELS", "")
    if os.environ.get("DEFAULT_PINNED_MODELS") is not None:
        cfg["DEFAULT_PINNED_MODELS"] = os.environ.get("DEFAULT_PINNED_MODELS", "")
    order = parse_json(os.environ.get("MODEL_ORDER_LIST", ""))
    if order is not None:
        cfg["MODEL_ORDER_LIST"] = order
    metadata = parse_json(os.environ.get("DEFAULT_MODEL_METADATA", ""))
    if metadata is not None:
        cfg["DEFAULT_MODEL_METADATA"] = metadata
    params = parse_json(os.environ.get("DEFAULT_MODEL_PARAMS", ""))
    if params is not None:
        cfg["DEFAULT_MODEL_PARAMS"] = params

    if cfg:
        status, body = http("POST", "/api/v1/configs/models", token=apikey, payload=cfg)
        if status not in (200, 201):
            print("settings apply failed (%s): %s" % (status, body))
        else:
            print("settings applied")


def main():
    wait_ready()
    apikey = get_apikey()
    if apikey:
        # The stored key may be stale (its user is gone) after a Course DB was
        # reset - detect it and fall back to a fresh bootstrap.
        status, _ = http("GET", "/api/v1/users/all", token=apikey)
        if status == 401:
            print("stored API key is stale after a DB reset; re-bootstrapping")
            clear_apikey()
            apikey = ""
    if not apikey:
        apikey = bootstrap_apikey()
    reconcile_admins(apikey)
    apply_settings(apikey)
    reenable_signup(apikey)
    print("provisioning complete")


if __name__ == "__main__":
    main()
