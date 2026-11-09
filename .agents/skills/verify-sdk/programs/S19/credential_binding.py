"""A versioned file-credential source, served over HTTP on localhost, and the
SDK's HttpVersionedCredentialBinding loading and replacing it the way an ACP
agent syncs a rotated login file.

The source keeps one value with a version, requires a Bearer token, answers
409 on a stale version, and has fixed paths that answer a given status.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sdk_verify import emit

from openhands.sdk.credential import (
    CredentialAuthorizationRejected,
    CredentialBindingUnsupported,
    CredentialConflict,
    CredentialInvalidResponse,
    CredentialNeedsReauthentication,
    CredentialSyncError,
    HttpVersionedCredentialBinding,
)


state = {"value": "auth-v1", "version": "1", "puts": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, status, body):
        data = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def guard(self) -> bool:
        if self.path.startswith("/status/"):
            code = int(self.path.rsplit("/", 1)[1])
            self.reply(code, {"error": code})
            return False
        if self.path == "/garbage":
            self.reply(200, b"not json")
            return False
        if self.headers.get("authorization") != "Bearer good":
            self.reply(401, {"error": "auth"})
            return False
        return True

    def do_GET(self):  # noqa: N802
        if self.guard():
            self.reply(200, {"value": state["value"], "version": state["version"]})

    def do_PUT(self):  # noqa: N802
        if not self.guard():
            return
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if body["expected_version"] != state["version"]:
            self.reply(409, {"error": "conflict"})
            return
        state["value"] = body["value"]
        state["version"] = str(int(state["version"]) + 1)
        state["puts"] += 1
        self.reply(200, {"version": state["version"]})


httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{httpd.server_address[1]}"


async def scenario() -> dict:
    out: dict = {"ok": True}
    good = HttpVersionedCredentialBinding(
        f"{base}/cred", {"Authorization": "Bearer good"}
    )
    first = await good.load()
    out["load"] = {"value": first.value, "version": first.version}
    new_version = await good.replace(first.version, "auth-v2")
    out["replace_version"] = new_version
    out["after_replace"] = (await good.load()).value
    try:
        await good.replace(first.version, "stale-write")
        out["stale"] = None
    except CredentialConflict:
        out["stale"] = "CredentialConflict"

    bad = HttpVersionedCredentialBinding(
        f"{base}/cred", {"Authorization": "Bearer bad"}
    )
    try:
        await bad.load()
        out["bad_auth"] = None
    except CredentialAuthorizationRejected:
        out["bad_auth"] = "CredentialAuthorizationRejected"
    revision = bad.authorization_revision
    bad.reauthorize(
        HttpVersionedCredentialBinding(f"{base}/cred", {"Authorization": "Bearer good"})
    )
    out["reauthorized_value"] = (await bad.load()).value
    out["revision_bumped"] = bad.authorization_revision == revision + 1
    try:
        bad.reauthorize(HttpVersionedCredentialBinding(f"{base}/other", {}))
        out["reauthorize_other_url"] = None
    except ValueError as exc:
        out["reauthorize_other_url"] = str(exc)

    errors = {}
    for code in (404, 400, 422, 409, 401, 403, 501, 500):
        b = HttpVersionedCredentialBinding(f"{base}/status/{code}", {})
        try:
            await b.load()
            errors[str(code)] = None
        except (
            CredentialNeedsReauthentication,
            CredentialConflict,
            CredentialAuthorizationRejected,
            CredentialBindingUnsupported,
            CredentialSyncError,
        ) as exc:
            errors[str(code)] = type(exc).__name__
    out["status_errors"] = errors
    for label, url in (
        ("invalid_url", "not a url"),
        ("garbage", f"{base}/garbage"),
        ("unreachable", "http://127.0.0.1:9/cred"),
    ):
        try:
            await HttpVersionedCredentialBinding(url, {}).load()
            out[label] = None
        except CredentialSyncError as exc:
            out[label] = type(exc).__name__
    out["invalid_url_is_invalid_response"] = (
        out["invalid_url"] == CredentialInvalidResponse.__name__
    )
    out["server_puts"] = state["puts"]
    return out


result = asyncio.run(scenario())
httpd.shutdown()
emit(result)
