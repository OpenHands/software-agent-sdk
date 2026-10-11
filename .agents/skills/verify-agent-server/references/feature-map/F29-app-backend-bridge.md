# Canvas App backend bridge (session, HTTP and WebSocket proxy)

How the Agent Canvas iframe reaches a Canvas App's own backend process from a
separate browser origin. When `app_backend_public_url` is configured, the
server advertises it in `/server_info`, and a Canvas page exchanges its
`X-Session-API-Key` for a five-minute, app-scoped, HttpOnly cookie with
`POST /app-backends/{name}/session` (sent to the ingress host, with an allowed
control `Origin` that is not the ingress origin). From then on every HTTP
method and WebSocket under `/app-backends/{name}` is forwarded to the app's
ready backend on loopback, authenticated only by that cookie: unsafe methods
and WebSockets must also carry `Origin` equal to the ingress origin, the
cookie, `Authorization`, `X-Session-API-Key` and key-bearing query parameters
are stripped going upstream, upstream redirects become 502 and upstream
`Set-Cookie` is dropped. Sessions live in memory only and die on revoke,
backend stop, disable, a backend or server restart, and at their TTL, which
also closes their bridged WebSockets. Installing, preparing and starting the
backend is the Canvas apps family (F28); this family arranges a ready backend
and proves the bridge.

Source: `openhands-agent-server/openhands/agent_server/canvas_extensions_bridge_router.py`, `openhands-agent-server/openhands/agent_server/canvas_extensions/bridge.py`, `openhands-agent-server/openhands/agent_server/docker_runtime/proxy.py`, `openhands-agent-server/openhands/agent_server/config.py`, `openhands-agent-server/openhands/agent_server/server_details_router.py`

Needs: `git`

Launch: `--canvas-ingress`

Routes: `POST /app-backends/{extension_name}/session`,
`DELETE /app-backends/{extension_name}/session`,
`DELETE /app-backends/{extension_name}/{path:path}`,
`GET /app-backends/{extension_name}/{path:path}`,
`PATCH /app-backends/{extension_name}/{path:path}`,
`POST /app-backends/{extension_name}/{path:path}`,
`PUT /app-backends/{extension_name}/{path:path}`,
`DELETE /app-backends/{extension_name}`, `GET /app-backends/{extension_name}`,
`PATCH /app-backends/{extension_name}`, `POST /app-backends/{extension_name}`,
`PUT /app-backends/{extension_name}`,
`WS /app-backends/{extension_name}/{path:path}`,
`WS /app-backends/{extension_name}`

## Sub-features

- `F29.ingress-advertised`: with `app_backend_public_url` set, `/server_info` reports it as `app_backend_ingress_url` and lists the `canvas_app_backend_bridge_v1` capability.
- `F29.ingress-config-errors`: on a server without `app_backend_public_url`, the session routes and the HTTP proxy answer 503 `Canvas App backend ingress is not configured`, the WebSocket handshake is refused with 403, and `/server_info` has neither the URL nor the capability; a value that is not an http(s) URL answers 503 `... is misconfigured`, and a plain-HTTP ingress on a non-loopback host refuses to mint a session (503 `... require HTTPS or a loopback secure context`).
- `F29.session-bootstrap`: `POST /app-backends/{name}/session` with the key and a control `Origin` returns `ingress_url`, `expires_at` about 300 s ahead and `iframe_sandbox`, and sets `oh_app_backend_session` with `HttpOnly; Max-Age=300; Path=/app-backends/{name}; SameSite=none; Secure; Partitioned`; a cookie jar replays it to the app's proxy, and the browser's CORS preflight for the bootstrap is allowed for the control origin with credentials.
- `F29.session-key-required`: the bootstrap answers 401 with no cookie to a missing or wrong `X-Session-API-Key`, to `Authorization: Bearer` and to an app session cookie.
- `F29.session-ingress-host`: a bootstrap or proxy request whose `Host` is not the ingress host answers 421, and `X-Forwarded-Host` does not change that while `trust_forwarded_headers` is off.
- `F29.session-forwarded-proto`: with `trust_forwarded_headers` off, a client-supplied `X-Forwarded-Proto: https` does not change the request origin, so a bootstrap on the plain-HTTP ingress still succeeds.
- `F29.session-origin-rules`: a missing, foreign or `null` `Origin` gets 403 and the ingress origin itself gets 409, both without a cookie; any `localhost` or `127.0.0.1` origin is accepted.
- `F29.session-configured-control-origin`: after a restart with `allow_cors_origins`, `allow_cors_origin_regex` and `DOCKER_HOST_ADDR`, a bootstrap from a listed origin, a regex match and the Docker host address succeeds (its CORS preflight is allowed too), while an unlisted origin, the listed host on another scheme or port, a partial regex match and another address get 403; a configured control origin does not count as the ingress `Origin` for unsafe proxied methods, and clearing the settings refuses those origins again.
- `F29.session-backend-not-ready`: bootstrapping or proxying an app whose backend is not ready (unknown app) answers 503 `Canvas App backend is not ready`; an invalid app name answers 422.
- `F29.proxy-get`: safe methods (GET, HEAD, OPTIONS) with only the cookie reach the backend with the method, sub-path and query, including `GET /session`; a CORS preflight is answered by the server and never reaches the backend.
- `F29.proxy-cookie-required`: proxied requests without a cookie, with a bogus cookie, with another app's cookie or with only the session key answer 401.
- `F29.proxy-unsafe-methods`: POST, PUT, PATCH and DELETE with the cookie and the ingress `Origin` reach the backend with their body; without that `Origin` (or with the control origin) they answer 403 before the cookie is checked.
- `F29.proxy-root`: `/app-backends/{name}` and `/app-backends/{name}/` reach the backend's `/` for all five methods, and on `/app-backends/{name}/` a `path` query parameter is an ordinary parameter.
- `F29.proxy-root-path-query`: on the bare root route (no trailing slash), a `path` query parameter is forwarded as a query parameter to the backend's `/`, like any other parameter.
- `F29.proxy-credentials-stripped`: the backend never sees `Cookie`, `Authorization`, `X-Session-API-Key` or the `session_api_key`, `X-Session-API-Key` and `Authorization` query parameters, while other headers and parameters pass.
- `F29.proxy-request-framing`: a proxied request keeps its framing: a GET reaches the backend without `Transfer-Encoding`, and a POST with a 13-byte JSON body reaches it with `Content-Length: 13`, as it does when sent to the backend directly.
- `F29.proxy-response-filter`: an upstream redirect becomes 502 `Upstream redirect is not allowed` with no `Location`, and an upstream `Set-Cookie` never reaches the client.
- `F29.proxy-response-headers`: a proxied response carries a single `Date` and a single `Server` header.
- `F29.proxy-path-traversal`: `.`/`..` segments and backslashes, percent-encoded at any depth, answer 400 `Invalid backend path` before any credential check, on HTTP and WebSocket.
- `F29.ws-bridge`: a WebSocket with the cookie and the ingress `Origin` is bridged to the backend, which echoes frames and receives no cookie, `Origin`, session key or `session_api_key` parameter; a path the backend does not upgrade (the root route) is accepted and then closed with 1011.
- `F29.ws-handshake-rejections`: a WebSocket without the ingress `Origin`, without a valid cookie for this app, or with only the session key is refused at the handshake with 403.
- `F29.ws-subprotocol`: a WebSocket subprotocol requested by the client reaches the backend and is negotiated through the bridge.
- `F29.session-revoke`: `DELETE /app-backends/{name}/session` answers 204 with a `Max-Age=0` cookie, closes that session's bridged WebSocket and makes its cookie 401, while other sessions of the app keep working; it needs the key and a control origin but no cookie.
- `F29.backend-lifecycle-revokes`: stopping or disabling the backend closes bridged WebSockets and makes the bridge answer 503; after the backend starts again on a new port, old cookies get 401 and a new session works. A backend that dies while ready is reported `unhealthy` (exited) by a status poll, after which the bridge answers 503, and once it starts again a session minted before the crash gets 401 because sessions are bound to the backend's port.
- `F29.backend-crash-detected`: once a ready backend's process has exited, the bridge answers 503 `Canvas App backend is not ready` without waiting for anyone to poll the backend's status.
- `F29.trusted-forwarded-headers`: with `trust_forwarded_headers` on, `X-Forwarded-Host` naming the ingress is accepted from a request whose `Host` is not, and a forwarded `https` that does not match the ingress answers 421; turned off again, the same header is ignored.
- `F29.sessions-not-persisted`: after a server restart the backend is stopped (the bridge answers 503), its prepared revision survives so it starts without a new prepare, and cookies minted before the restart get 401.
- `F29.session-ttl`: an idle bridged WebSocket is closed when its session expires 300 s after the bootstrap, and its cookie then gets 401.
- `F29.uninstall-revokes`: uninstalling the app closes its bridged WebSockets, stops its backend and makes the bridge answer 503 to old cookies and new bootstraps.

## How to get to it (agent POV)

- Config: `app_backend_public_url` (`OH_APP_BACKEND_PUBLIC_URL`), the
  separate browser origin of the ingress; `launch --canvas-ingress` sets it to
  the run's own URL (`http://127.0.0.1:PORT`), so the control origin used in
  recipes is `http://localhost:3000`. `trust_forwarded_headers`
  (`OH_TRUST_FORWARDED_HEADERS`) lets `X-Forwarded-Proto`/`X-Forwarded-Host`
  decide the request origin; `allow_cors_origins`,
  `allow_cors_origin_regex` and the `DOCKER_HOST_ADDR` environment variable
  extend the accepted control origins beyond `localhost` and `127.0.0.1`.
  The bridge checks them with its own code (`_allowed_control_origin`), not
  through the CORS middleware that reads the same settings
  (`F29.session-configured-control-origin` drives both).
- REST: `GET /server_info` (`app_backend_ingress_url`, capability
  `canvas_app_backend_bridge_v1`; F01 owns the route).
- REST: `POST /app-backends/{extension_name}/session` and
  `DELETE /app-backends/{extension_name}/session`: `X-Session-API-Key`, a
  control `Origin`, the ingress `Host`; no body.
- REST (proxy, cookie only, `include_in_schema=False`):
  `GET|HEAD|OPTIONS|POST|PUT|PATCH|DELETE /app-backends/{extension_name}`
  and `/app-backends/{extension_name}/{path:path}`; unsafe methods need
  `Origin` equal to the ingress origin. The route table lists five methods;
  HEAD and OPTIONS ride on the same handler.
- WebSocket: `/app-backends/{extension_name}` and
  `/app-backends/{extension_name}/{path:path}`, with the cookie and the
  ingress `Origin` on the handshake (no first-frame auth).
- Backend lifecycle that revokes sessions (F28 owns these routes):
  `POST /api/canvas-extensions/installed/{name}/backend/stop`,
  `PATCH /api/canvas-extensions/installed/{name}` with `{"enabled": false}`,
  `DELETE /api/canvas-extensions/installed/{name}`.
- TypeScript client: `CanvasExtensionsClient.createAppBackendSession(name,
  signal)` and `revokeAppBackendSession(name, signal)`
  (`clients/typescript/src/client/canvas-extensions-client.ts`); they need
  `appBackendIngressUrl` from `/server_info`, which `OpenHandsClient` does not
  pass to its `canvasExtensions`, and they rely on the browser adding `Origin`
  and keeping the cookie (`credentials: 'include'`). Not driven here.
- SDK: none. Agent Canvas (context): loads the app in an iframe at
  `ingress_url` with the returned `iframe_sandbox`, re-bootstraps after each
  backend start and before the TTL.
- Recipes drive every route with the CLI's `api` verb (the cookie as a raw
  `--cookie` value or a `--jar`, `Origin` and `Host` as `--header`) and its
  `ws listen`/`ws start` verbs with `--auth none` and `--header` for the
  cookie and `Origin`; HEAD, OPTIONS and CORS preflights use `curl`, and the
  subprotocol check uses a `websockets` client through `exec`.

## Driving it with control-agent-server

Preconditions:

- A run launched with this family's `Launch:` flags (`--canvas-ingress`), so
  the ingress is the run's own URL. No LLM is needed.
- `qa-backend`: the CLI's `fixture canvas-app --backend ok` (an aiohttp
  backend: `/health`, a `/ws` text echo answering `echo:<frame>`, and a
  catch-all JSON echo of `app`, `method`, `path`, `query`, `body` and
  `cookie_forwarded`), installed, enabled, prepared and started.
- `qa-headers`: the same fixture with its backend replaced by a diagnostic
  one written here (echoes every request header, `/redirect` answers 302,
  `/set-cookie` sets a cookie, `/ws` accepts subprotocol `qa.v1` and reports
  which headers and query reached it), because the CLI fixture cannot show
  request headers, redirects or subprotocols. The script keeps the
  fixture's shebang (the checkout's `.venv` Python, which has aiohttp),
  repacks `backend/linux.tar.gz` and rewrites the manifest's `sha256`; the
  app is then installed, enabled, prepared and started.
- `S` is the ingress URL, `P` its port, `REV`/`REV2` the approved revisions.

```sh
control-agent-server api GET /server_info --auth none --check capabilities contains canvas_app_backend_bridge_v1 --quiet
S=$(control-agent-server api GET /server_info --auth none --field app_backend_ingress_url)
P=${S##*:}
APP=$(control-agent-server fixture canvas-app --name qa-backend --backend ok --print-path)
control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$APP\"}" --expect 200 --check name eq qa-backend --quiet
REV=$(control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --field revision)
control-agent-server api PATCH /api/canvas-extensions/installed/qa-backend --json '{"enabled": true}' --expect 200 --quiet
control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/prepare --json "{\"revision\": \"$REV\"}" --expect 200 --quiet
control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$REV\"}" --expect 200 --check state eq ready --quiet
HDR=$(control-agent-server fixture canvas-app --name qa-headers --backend ok --print-path)
mkdir -p "$HDR/backend/qa"
{ head -1 "$HDR/backend/src/server.py"; cat <<'PY'; } > "$HDR/backend/qa/server.py"
import sys

from aiohttp import WSMsgType, web


def seen(request):
    return {k.lower(): v for k, v in request.headers.items()}


async def health(request):
    return web.Response(text="ok")


async def redirect(request):
    raise web.HTTPFound("https://example.com/")


async def set_cookie(request):
    response = web.json_response({"set_cookie": "sent"})
    response.set_cookie("qa_upstream", "1")
    return response


async def socket(request):
    ws = web.WebSocketResponse(protocols=("qa.v1",))
    await ws.prepare(request)
    headers = seen(request)
    async for message in ws:
        if message.type == WSMsgType.TEXT:
            await ws.send_json({
                "kind": "qa_ws_echo",
                "echo": message.data,
                "protocol": ws.ws_protocol or "none",
                "cookie": "present" if "cookie" in headers else "absent",
                "origin": headers.get("origin", "absent"),
                "session_key": "present" if "x-session-api-key" in headers else "absent",
                "query": request.query_string or "none",
            })
    return ws


async def echo(request):
    return web.json_response({
        "method": request.method,
        "path": request.path,
        "query": dict(request.query),
        "headers": seen(request),
        "body": await request.text(),
    })


app = web.Application()
app.router.add_get("/health", health)
app.router.add_get("/redirect", redirect)
app.router.add_get("/set-cookie", set_cookie)
app.router.add_get("/ws", socket)
app.router.add_route("*", "/{tail:.*}", echo)
web.run_app(app, host="127.0.0.1", port=int(sys.argv[1]), print=None)
PY
chmod 755 "$HDR/backend/qa/server.py"
tar -czf "$HDR/backend/linux.tar.gz" -C "$HDR/backend/qa" server.py
sed -i "s/[0-9a-f]\{64\}/$(sha256sum "$HDR/backend/linux.tar.gz" | cut -d' ' -f1)/g" "$HDR/canvas-extension.json"
control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$HDR\"}" --expect 200 --check name eq qa-headers --quiet
REV2=$(control-agent-server api GET /api/canvas-extensions/installed/qa-headers/backend --field revision)
control-agent-server api PATCH /api/canvas-extensions/installed/qa-headers --json '{"enabled": true}' --expect 200 --quiet
control-agent-server api POST /api/canvas-extensions/installed/qa-headers/backend/prepare --json "{\"revision\": \"$REV2\"}" --expect 200 --quiet
control-agent-server api POST /api/canvas-extensions/installed/qa-headers/backend/start --json "{\"revision\": \"$REV2\"}" --expect 200 --check state eq ready --quiet
```

- **Ingress advertised (`F29.ingress-advertised`).** The run's own URL is
  the configured ingress.
  ```sh
  RUN_URL=$(control-agent-server config | python3 -c 'import json, sys; print(json.load(sys.stdin)["url"])')
  control-agent-server api GET /server_info --auth none --expect 200 \
    --check app_backend_ingress_url eq "$RUN_URL" \
    --check capabilities contains canvas_app_backend_bridge_v1 --save F29.ingress-advertised/server-info
  ```
  `app_backend_ingress_url` is `http://127.0.0.1:PORT` and the capability is
  listed.
- **Ingress not configured or unusable (`F29.ingress-config-errors`).** A
  second run without `--canvas-ingress`, restarted with two unusable ingress
  values and stopped when the bullet ends.
  ```sh
  NOI=$(control-agent-server launch --new --name f29-no-ingress --print-run)
  trap 'control-agent-server stop --run "$NOI" >/dev/null' EXIT
  control-agent-server api GET /server_info --run "$NOI" --auth none --expect 200 \
    --check app_backend_ingress_url missing --check capabilities not-contains canvas_app_backend_bridge_v1
  control-agent-server api POST /app-backends/qa-backend/session --run "$NOI" --header 'Origin: http://localhost:3000' \
    --expect 503 --check exception eq '503: Canvas App backend ingress is not configured' \
    --check-header set-cookie missing --save F29.ingress-config-errors/session
  control-agent-server api DELETE /app-backends/qa-backend/session --run "$NOI" --header 'Origin: http://localhost:3000' \
    --expect 503 --check exception eq '503: Canvas App backend ingress is not configured'
  control-agent-server api GET /app-backends/qa-backend/echo --run "$NOI" --auth none --cookie oh_app_backend_session=qa-f29-any \
    --expect 503 --check exception eq '503: Canvas App backend ingress is not configured' --save F29.ingress-config-errors/proxy
  control-agent-server ws listen /app-backends/qa-backend/ws --run "$NOI" --auth none --header 'Origin: http://localhost:3000' \
    --header 'Cookie: oh_app_backend_session=qa-f29-any' --duration 5 --expect-reject 403
  control-agent-server api POST /app-backends/qa-backend/session --run "$NOI" --auth none --header 'Origin: http://localhost:3000' --expect 401
  control-agent-server restart --run "$NOI" --config-json '{"app_backend_public_url": "ftp://apps.qa.test"}'
  control-agent-server api GET /server_info --run "$NOI" --auth none --check app_backend_ingress_url eq ftp://apps.qa.test
  control-agent-server api POST /app-backends/qa-backend/session --run "$NOI" --header 'Origin: http://localhost:3000' \
    --expect 503 --check exception eq '503: Canvas App backend ingress is misconfigured' --save F29.ingress-config-errors/misconfigured
  control-agent-server api GET /app-backends/qa-backend/echo --run "$NOI" --auth none --cookie oh_app_backend_session=qa-f29-any \
    --expect 503 --check exception eq '503: Canvas App backend ingress is misconfigured'
  NOP=$(control-agent-server config --run "$NOI" | python3 -c 'import json, sys; print(json.load(sys.stdin)["url"].rsplit(":", 1)[1])')
  control-agent-server restart --run "$NOI" --config-json "{\"app_backend_public_url\": \"http://apps.qa.test:$NOP\"}"
  NAPP=$(control-agent-server fixture canvas-app --run "$NOI" --name qa-backend --backend ok --print-path)
  control-agent-server api POST /api/canvas-extensions/install --run "$NOI" --json "{\"source\": \"$NAPP\"}" --expect 200 --quiet
  NREV=$(control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --run "$NOI" --field revision)
  control-agent-server api PATCH /api/canvas-extensions/installed/qa-backend --run "$NOI" --json '{"enabled": true}' --expect 200 --quiet
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/prepare --run "$NOI" \
    --json "{\"revision\": \"$NREV\"}" --expect 200 --quiet
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --run "$NOI" \
    --json "{\"revision\": \"$NREV\"}" --expect 200 --check state eq ready --quiet
  control-agent-server api POST /app-backends/qa-backend/session --run "$NOI" --header 'Origin: http://localhost:3000' \
    --header "Host: apps.qa.test:$NOP" --expect 503 \
    --check exception eq '503: Canvas App sessions require HTTPS or a loopback secure context' \
    --check-header set-cookie missing --save F29.ingress-config-errors/insecure-ingress
  control-agent-server api POST /app-backends/qa-backend/session --run "$NOI" --header 'Origin: http://localhost:3000' \
    --expect 421
  ```
  `/server_info` has no ingress URL and no capability; the session and proxy
  routes answer 503 with the reason in `exception`, the socket is refused
  with 403, and the key is still checked first (401 without it). After a
  restart with an `ftp://` ingress, `/server_info` advertises it verbatim and
  the bridge answers 503 `misconfigured`. After a restart with
  `http://apps.qa.test:PORT` (plain HTTP, not loopback) and a ready backend,
  a bootstrap addressed to that host (`Host: apps.qa.test:PORT`) passes the
  host, origin and readiness checks but is refused with 503 because a
  `Secure` cookie cannot be delivered over plain HTTP to a non-loopback
  host; the run's own loopback address is now the wrong host (421).
- **Session bootstrap (`F29.session-bootstrap`).** Mint a cookie for each app
  the way Canvas does, then let a browser-like jar replay one.
  ```sh
  SC=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 \
    --check ingress_url eq "$S/app-backends/qa-backend/" \
    --check iframe_sandbox eq 'allow-forms allow-modals allow-popups allow-same-origin allow-scripts' \
    --check-header set-cookie matches '^oh_app_backend_session=[A-Za-z0-9_-]{20,};' \
    --check-header set-cookie contains HttpOnly --check-header set-cookie contains 'Max-Age=300;' \
    --check-header set-cookie contains 'Path=/app-backends/qa-backend;' --check-header set-cookie contains 'SameSite=none' \
    --check-header set-cookie contains Secure --check-header set-cookie contains Partitioned \
    --check-header access-control-allow-origin eq http://localhost:3000 \
    --check-header access-control-allow-credentials eq true --save F29.session-bootstrap/mint --print-header set-cookie)
  TOKEN=$(printf '%s' "$SC" | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  test -n "$TOKEN"
  SC2=$(control-agent-server api POST /app-backends/qa-headers/session --header 'Origin: http://localhost:3000' --expect 200 \
    --check ingress_url eq "$S/app-backends/qa-headers/" \
    --check-header set-cookie contains 'Path=/app-backends/qa-headers;' --print-header set-cookie)
  T2=$(printf '%s' "$SC2" | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  test -n "$T2" && test "$T2" != "$TOKEN"
  EXP=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --jar qa-f29 --expect 200 --field expires_at)
  LEFT=$(( $(date -d "$EXP" +%s) - $(date +%s) ))
  test "$LEFT" -ge 290 && test "$LEFT" -le 300
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --jar qa-f29 --expect 200 \
    --check app eq qa-backend --save F29.session-bootstrap/jar-replay
  PRE=$(curl -s -D - -o /dev/null --noproxy '*' -X OPTIONS -H 'Origin: http://localhost:3000' \
    -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: x-session-api-key' \
    "$S/app-backends/qa-backend/session")
  grep -q '^HTTP/1.1 200' <<<"$PRE"
  grep -qi '^access-control-allow-origin: http://localhost:3000' <<<"$PRE"
  grep -qi '^access-control-allow-credentials: true' <<<"$PRE"
  grep -qi '^access-control-allow-headers: .*x-session-api-key' <<<"$PRE"
  ```
  Each app gets its own cookie on its own path; `expires_at` is about 300 s
  ahead, and a jar holding only the minted cookie (no key) reaches the
  backend. The browser's CORS preflight for the bootstrap (a cross-origin
  `POST` with `X-Session-API-Key`) is allowed for the control origin with
  credentials; it goes through `curl` because `map check` does not accept
  `api OPTIONS` on these routes.
- **Session key required (`F29.session-key-required`).** The bootstrap takes
  only the control header.
  ```sh
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 \
    --check-header set-cookie contains oh_app_backend_session=
  control-agent-server api POST /app-backends/qa-backend/session --auth none --header 'Origin: http://localhost:3000' \
    --expect 401 --check detail eq Unauthorized --check-header set-cookie missing --save F29.session-key-required/no-key
  control-agent-server api POST /app-backends/qa-backend/session --auth bad --header 'Origin: http://localhost:3000' \
    --expect 401 --check-header set-cookie missing --save F29.session-key-required/bad-key
  control-agent-server api POST /app-backends/qa-backend/session --auth bearer --header 'Origin: http://localhost:3000' \
    --expect 401 --check-header set-cookie missing
  control-agent-server api POST /app-backends/qa-backend/session --auth none --header 'Origin: http://localhost:3000' \
    --cookie "oh_app_backend_session=$TOKEN" --expect 401 --check-header set-cookie missing
  ```
  The run's key mints a cookie; no key, a wrong key, the key as a bearer
  token and an app session cookie each answer 401 and set no cookie.
- **Ingress host (`F29.session-ingress-host`).** The bootstrap and the proxy
  must arrive on the ingress host.
  ```sh
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --header "Host: localhost:$P" \
    --expect 421 --check detail eq 'Canvas App request must use the configured ingress origin' \
    --check-header set-cookie missing --save F29.session-ingress-host/session
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --header "Host: localhost:$P" \
    --header "X-Forwarded-Host: 127.0.0.1:$P" --expect 421 --save F29.session-ingress-host/forwarded-host
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --header 'Host: other.qa.test:1' --expect 421 --save F29.session-ingress-host/proxy
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --expect 200 --check app eq qa-backend
  ```
  `localhost:PORT` is the same server but not the ingress origin, so both
  routes answer 421, and `X-Forwarded-Host` naming the ingress does not help;
  the same cookie on the ingress host gets 200.
- **Forwarded proto (`F29.session-forwarded-proto`), known bug.** With
  `trust_forwarded_headers` off (the default), the request's own scheme should
  decide the origin (`bridge.py` `_request_origin`, `config.py`), so a
  client-supplied `X-Forwarded-Proto: https` must not change it.
  ```sh
  FP=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 \
    --check ingress_url eq "$S/app-backends/qa-backend/" --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  test -n "$FP"
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$FP" --expect 200 \
    --check app eq qa-backend
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' \
    --header 'X-Forwarded-Proto: https' --expect 200 --check ingress_url eq "$S/app-backends/qa-backend/" \
    --save F29.session-forwarded-proto/session  # bug
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$FP" \
    --header 'X-Forwarded-Proto: https' --expect 200 --check app eq qa-backend --save F29.session-forwarded-proto/proxy  # bug
  ```
  The same bootstrap and proxied GET without the header answer 200 first.
  Expected: 200 for both with the header too. Actual: 421 `Canvas App
  request must use the configured ingress origin`, because uvicorn's
  `proxy_headers` (on by
  default for loopback peers, `FORWARDED_ALLOW_IPS`) rewrites the scheme
  before the bridge looks at it. The same header lets a loopback client
  claim `https` against an HTTPS ingress. A run launched with
  `--env FORWARDED_ALLOW_IPS=192.0.2.1` (uvicorn then trusts no loopback
  peer) lets the same bootstrap past the host check, which pins the cause on
  the server's uvicorn configuration (`__main__.py`), not on the client.
  Only peers uvicorn trusts (loopback by default) can do this.
- **Origin rules (`F29.session-origin-rules`).** Only a control origin may
  bootstrap, and it must differ from the ingress origin.
  ```sh
  control-agent-server api POST /app-backends/qa-backend/session --expect 403 --check detail eq 'Origin is not allowed' \
    --check-header set-cookie missing --save F29.session-origin-rules/no-origin
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: https://evil.qa.test' --expect 403 \
    --check detail eq 'Origin is not allowed' --check-header set-cookie missing --save F29.session-origin-rules/foreign
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: null' --expect 403 --check-header set-cookie missing
  control-agent-server api POST /app-backends/qa-backend/session --header "Origin: $S" --expect 409 \
    --check detail eq 'Canvas App ingress must use a separate browser origin' \
    --check-header set-cookie missing --save F29.session-origin-rules/ingress-origin
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://127.0.0.1:5173' --expect 200 \
    --check-header set-cookie contains oh_app_backend_session=
  ```
  403 for no, foreign and `null` origins, 409 for the ingress origin, all
  without a cookie; another loopback port is accepted.
- **Backend not ready (`F29.session-backend-not-ready`).** An app with no
  running backend, and a malformed name.
  ```sh
  control-agent-server api POST /app-backends/qa-nope/session --header 'Origin: http://localhost:3000' --expect 503 \
    --check exception eq '503: Canvas App backend is not ready' --check-header set-cookie missing \
    --save F29.session-backend-not-ready/session
  control-agent-server api GET /app-backends/qa-nope/echo --auth none --cookie "oh_app_backend_session=$TOKEN" --expect 503 \
    --check exception eq '503: Canvas App backend is not ready' --save F29.session-backend-not-ready/proxy
  control-agent-server api POST /app-backends/QA_Bad/session --header 'Origin: http://localhost:3000' --expect 422 \
    --check detail.0.loc.1 eq extension_name
  control-agent-server api GET /app-backends/QA_Bad/echo --auth none --expect 422 --check detail.0.loc.1 eq extension_name
  ```
  An unknown app looks exactly like a stopped one (503, no 404); a name
  outside `^[a-z0-9]+(?:-[a-z0-9]+)*$` is 422. A stopped installed backend
  is covered by `F29.backend-lifecycle-revokes`.
- **Proxied safe methods (`F29.proxy-get`).** Only the cookie, no key.
  ```sh
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --query view=main --expect 200 --check app eq qa-backend --check method eq GET --check path eq /echo \
    --check query.view eq main --check cookie_forwarded eq false --save F29.proxy-get/get
  control-agent-server api GET /app-backends/qa-backend/assets/app.js --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --expect 200 --check path eq /assets/app.js
  test "$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' -I -H "Cookie: oh_app_backend_session=$TOKEN" "$S/app-backends/qa-backend/echo")" = 200
  OPT=$(curl -s --noproxy '*' -X OPTIONS -H "Cookie: oh_app_backend_session=$TOKEN" "$S/app-backends/qa-backend/echo")
  grep -q '"method": "OPTIONS"' <<<"$OPT"
  test "$(curl -s --noproxy '*' -X OPTIONS -H "Cookie: oh_app_backend_session=$TOKEN" -H "Origin: $S" \
    -H 'Access-Control-Request-Method: POST' "$S/app-backends/qa-backend/echo")" = OK
  control-agent-server api GET /app-backends/qa-backend/session --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --expect 200 --check path eq /session
  ```
  The backend sees the method, the sub-path and the query, and no session
  cookie; HEAD answers 200 and a plain OPTIONS (not a CORS preflight)
  reaches the backend, while a CORS preflight (with
  `Access-Control-Request-Method`) is answered by the server's CORS
  middleware with a plain `OK` and never reaches the backend. HEAD and
  OPTIONS go through `curl` because
  `map check`'s route table lists only the five methods FastAPI reports for
  these routes. Only POST and DELETE on `/session` are the bridge's own
  routes; `GET /session` is the backend's.
- **Cookie required (`F29.proxy-cookie-required`).** The cookie is the only
  credential the proxy takes, and it is bound to one app.
  ```sh
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --expect 401 --check detail eq Unauthorized \
    --save F29.proxy-cookie-required/none
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie oh_app_backend_session=qa-f29-bogus --expect 401
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$T2" --expect 401 \
    --save F29.proxy-cookie-required/other-app
  control-agent-server api GET /app-backends/qa-backend/echo --expect 401 --save F29.proxy-cookie-required/key-only
  control-agent-server api GET /app-backends/qa-headers/echo --auth none --cookie "oh_app_backend_session=$T2" --expect 200 \
    --check path eq /echo
  ```
  401 without a cookie, with a bogus one, with `qa-headers`' cookie on
  `qa-backend`, and with only `X-Session-API-Key`; that cookie works on its
  own app.
- **Unsafe methods (`F29.proxy-unsafe-methods`).** The ingress `Origin` is a
  CSRF guard for anything but GET, HEAD and OPTIONS.
  ```sh
  control-agent-server api POST /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --header "Origin: $S" --json '{"qa": "f29"}' --expect 200 --check method eq POST --check path eq /echo \
    --check body eq '{"qa":"f29"}' --save F29.proxy-unsafe-methods/post
  control-agent-server api POST /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --json '{"qa": "f29"}' --expect 403 --check detail eq 'Origin is not allowed'
  control-agent-server api PUT /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --header "Origin: $S" --json '{"qa": "f29"}' --expect 200 --check method eq PUT --check path eq /echo \
    --check body eq '{"qa":"f29"}' --save F29.proxy-unsafe-methods/put
  control-agent-server api PUT /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --json '{"qa": "f29"}' --expect 403 --check detail eq 'Origin is not allowed'
  control-agent-server api PATCH /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --header "Origin: $S" --json '{"qa": "f29"}' --expect 200 --check method eq PATCH --check path eq /echo \
    --check body eq '{"qa":"f29"}' --save F29.proxy-unsafe-methods/patch
  control-agent-server api PATCH /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --json '{"qa": "f29"}' --expect 403 --check detail eq 'Origin is not allowed'
  control-agent-server api DELETE /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --header "Origin: $S" --json '{"qa": "f29"}' --expect 200 --check method eq DELETE --check path eq /echo \
    --check body eq '{"qa":"f29"}' --save F29.proxy-unsafe-methods/delete
  control-agent-server api DELETE /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --json '{"qa": "f29"}' --expect 403 --check detail eq 'Origin is not allowed'
  control-agent-server api POST /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --header 'Origin: http://localhost:3000' --expect 403 --save F29.proxy-unsafe-methods/control-origin
  control-agent-server api POST /app-backends/qa-backend/echo --auth none --cookie oh_app_backend_session=qa-f29-bogus \
    --expect 403
  control-agent-server api POST /app-backends/qa-backend/echo --auth none --header "Origin: $S" --expect 401
  ```
  Each method reaches the backend with its JSON body; without `Origin`, or
  with the Canvas control origin, it is 403, and that check runs before the
  cookie check (a bogus cookie and no `Origin` is 403, not 401).
- **App root (`F29.proxy-root`).** The route without a sub-path.
  ```sh
  control-agent-server api GET /app-backends/qa-backend --auth none --cookie "oh_app_backend_session=$TOKEN" --expect 200 \
    --check method eq GET --check path eq / --save F29.proxy-root/get
  control-agent-server api GET /app-backends/qa-backend/ --auth none --cookie "oh_app_backend_session=$TOKEN" --expect 200 \
    --check path eq /
  control-agent-server api GET /app-backends/qa-backend/ --query path=echo --auth none \
    --cookie "oh_app_backend_session=$TOKEN" --expect 200 --check path eq / --check query.path eq echo \
    --save F29.proxy-root/slash-path-query
  control-agent-server api POST /app-backends/qa-backend --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --header "Origin: $S" --json '{"root": 1}' --expect 200 --check method eq POST --check path eq / \
    --save F29.proxy-root/post
  control-agent-server api PUT /app-backends/qa-backend --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --header "Origin: $S" --json '{"root": 1}' --expect 200 --check method eq PUT --check path eq / \
    --save F29.proxy-root/put
  control-agent-server api PATCH /app-backends/qa-backend --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --header "Origin: $S" --json '{"root": 1}' --expect 200 --check method eq PATCH --check path eq / \
    --save F29.proxy-root/patch
  control-agent-server api DELETE /app-backends/qa-backend --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --header "Origin: $S" --json '{"root": 1}' --expect 200 --check method eq DELETE --check path eq / \
    --save F29.proxy-root/delete
  control-agent-server api POST /app-backends/qa-backend --auth none --cookie "oh_app_backend_session=$TOKEN" --expect 403
  control-agent-server api GET /app-backends/qa-backend --auth none --expect 401
  ```
  All five methods reach `/`; the same `Origin` and cookie rules apply. On
  `/app-backends/qa-backend/` (the form `ingress_url` uses) a `path` query
  parameter is an ordinary parameter of the backend's `/`.
- **Root `path` parameter (`F29.proxy-root-path-query`), known bug.** The
  root routes declare `path: str = ""` without a `{path}` segment, so FastAPI
  binds a `path` query parameter to it.
  ```sh
  RP=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  test -n "$RP"
  control-agent-server api GET /app-backends/qa-backend --query view=main --auth none \
    --cookie "oh_app_backend_session=$RP" --expect 200 --check path eq / --check query.view eq main
  control-agent-server api GET /app-backends/qa-backend/ --query path=echo --auth none \
    --cookie "oh_app_backend_session=$RP" --expect 200 --check path eq / --check query.path eq echo
  control-agent-server api GET /app-backends/qa-backend --query path=echo --auth none \
    --cookie "oh_app_backend_session=$RP" --expect 200 --check path eq / --check query.path eq echo \
    --save F29.proxy-root-path-query/get  # bug
  control-agent-server api GET /app-backends/qa-backend --query 'path=..' --auth none \
    --cookie "oh_app_backend_session=$RP" --expect 200 --check path eq / --check query.path eq ..  # bug
  control-agent-server ws listen /app-backends/qa-backend --query path=ws --auth none --header "Origin: $S" \
    --header "Cookie: oh_app_backend_session=$RP" --send qa-f29-root --duration 5 --expect-close 1011 --expect-frames 0  # bug
  ```
  The controls first: the bare root with another query parameter, and the
  trailing-slash root with `?path=echo`, both reach `/` with the query
  intact. Expected: the backend's `/` with `?path=echo` (and `?path=..`),
  and the root WebSocket closed with 1011 and no frame, like any root
  WebSocket (`F29.ws-bridge`). Actual: `?path=echo` is proxied to `/echo`
  (with `?path=echo` still in the query),
  `?path=..` is refused with 400 `Invalid backend path`, and `?path=ws`
  bridges to the backend's `/ws` echo. Only the bare root (no trailing slash)
  is affected: `ingress_url` ends with `/`, where `?path=` is an ordinary
  parameter (`F29.proxy-root`), and the bound value still goes through the
  traversal check, so the impact is limited to links that drop the slash.
- **Credentials stripped (`F29.proxy-credentials-stripped`).** `qa-headers`
  echoes every header it receives.
  ```sh
  control-agent-server api POST /app-backends/qa-headers/echo --cookie "oh_app_backend_session=$T2; qa_other=1" \
    --header "Origin: $S" --header 'Authorization: Bearer qa-f29-bearer' --header 'X-QA-Custom: kept' \
    --query view=main --query session_api_key=qa-leak --query X-Session-API-Key=qa-leak --query Authorization=qa-leak \
    --json '{"qa": "body"}' --expect 200 \
    --check headers.cookie missing --check headers.authorization missing --check headers.x-session-api-key missing \
    --check headers.x-qa-custom eq kept --check headers.origin eq "$S" --check query.view eq main \
    --check query.session_api_key missing --check query.X-Session-API-Key missing --check query.Authorization missing \
    --check body eq '{"qa":"body"}' --save F29.proxy-credentials-stripped/echo
  ```
  The request carried the cookie, the run's `X-Session-API-Key`, a bearer
  token and three key-named query parameters; the backend saw none of them,
  but did see `X-QA-Custom`, `Origin`, `view` and the body.
- **Request framing (`F29.proxy-request-framing`), known bug.** `qa-headers`
  echoes the framing headers it receives; direct calls to its loopback port
  are the control.
  ```sh
  FR=$(control-agent-server api POST /app-backends/qa-headers/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  test -n "$FR"
  HPORT=$(control-agent-server api GET /api/canvas-extensions/installed/qa-headers/backend --field port)
  DIRECT_GET=$(curl -s --noproxy '*' "http://127.0.0.1:$HPORT/echo")
  grep -q '"method": "GET"' <<<"$DIRECT_GET"
  if grep -qi '"transfer-encoding"' <<<"$DIRECT_GET"; then false; fi
  DIRECT_POST=$(curl -s --noproxy '*' -H 'Content-Type: application/json' --data '{"qa":"body"}' "http://127.0.0.1:$HPORT/echo")
  grep -q '"content-length": "13"' <<<"$DIRECT_POST"
  if grep -qi '"transfer-encoding"' <<<"$DIRECT_POST"; then false; fi
  control-agent-server api GET /app-backends/qa-headers/echo --auth none --cookie "oh_app_backend_session=$FR" --expect 200 \
    --check method eq GET --check body eq '' --check headers.transfer-encoding missing \
    --save F29.proxy-request-framing/get  # bug
  control-agent-server api POST /app-backends/qa-headers/echo --auth none --cookie "oh_app_backend_session=$FR" \
    --header "Origin: $S" --json '{"qa": "body"}' --expect 200 --check body eq '{"qa":"body"}' \
    --check headers.content-length eq 13 --check headers.transfer-encoding missing \
    --save F29.proxy-request-framing/post  # bug
  ```
  Directly, the backend sees no `Transfer-Encoding` on a GET and
  `Content-Length: 13` on the POST. Expected: the same through the bridge.
  Actual: both arrive with `Transfer-Encoding: chunked` and no
  `Content-Length` (the GET with an empty chunked body), because `proxy_http`
  (`docker_runtime/proxy.py`) drops `Content-Length` as hop-by-hop and
  always streams the request body through an async generator. aiohttp and
  uvicorn decode it, so the body still arrives here; a backend on a server
  without chunked request support (one built on Python's `http.server`,
  which reads `Content-Length`) sees an empty body.
- **Response filter (`F29.proxy-response-filter`).** Direct calls to the
  backend's loopback port are the control.
  ```sh
  HPORT=$(control-agent-server api GET /api/canvas-extensions/installed/qa-headers/backend --field port)
  test "$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' "http://127.0.0.1:$HPORT/redirect")" = 302
  control-agent-server api GET /app-backends/qa-headers/redirect --auth none --cookie "oh_app_backend_session=$T2" \
    --expect 502 --check exception eq '502: Upstream redirect is not allowed' --check-header location missing \
    --save F29.proxy-response-filter/redirect
  DIRECT=$(curl -s -D - -o /dev/null --noproxy '*' "http://127.0.0.1:$HPORT/set-cookie")
  grep -qi '^set-cookie: qa_upstream=1' <<<"$DIRECT"
  control-agent-server api GET /app-backends/qa-headers/set-cookie --auth none --cookie "oh_app_backend_session=$T2" \
    --expect 200 --check set_cookie eq sent --check-header set-cookie missing --save F29.proxy-response-filter/set-cookie
  ```
  The backend answers 302 and sets `qa_upstream`; through the bridge the
  redirect is 502 without `Location` and the body arrives without the cookie.
- **One Date and Server header (`F29.proxy-response-headers`), known bug.**
  The proxy copies the backend's `Date` and `Server` headers and uvicorn adds
  its own, so a proxied response carries two of each (a client joins them
  with `, `).
  ```sh
  control-agent-server api GET /server_info --auth none --expect 200 \
    --check-header date matches '^[A-Z][a-z]{2}, [0-9]{2} [A-Z][a-z]{2} [0-9]{4} [0-9:]{8} GMT$' --check-header server eq uvicorn
  BPORT=$(control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --field port)
  DIRECT_HEADERS=$(curl -s -D - -o /dev/null --noproxy '*' "http://127.0.0.1:$BPORT/echo")
  test "$(grep -ci '^date:' <<<"$DIRECT_HEADERS")" = 1 && test "$(grep -ci '^server:' <<<"$DIRECT_HEADERS")" = 1
  RH=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  test -n "$RH"
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$RH" --expect 200 \
    --check-header date matches '^[A-Z][a-z]{2}, [0-9]{2} [A-Z][a-z]{2} [0-9]{4} [0-9:]{8} GMT$' \
    --check-header server not-contains ', ' --all-headers --save F29.proxy-response-headers/echo  # bug
  ```
  The controls: a route the server answers itself carries one `Date` and
  `server: uvicorn`, and the backend answered directly sends one of each.
  Expected: one `Date` and one `Server` through the bridge too. Actual: `date: <uvicorn's>, <aiohttp's>`
  and `server: uvicorn, Python/3.x aiohttp/3.x` (RFC 9110 allows a single
  `Date`; the second `Server` also discloses the backend stack).
- **Path traversal (`F29.proxy-path-traversal`).** Checked first, so no
  credential is needed to see it.
  ```sh
  control-agent-server api GET '/app-backends/qa-backend/%2e%2e/secret' --auth none --expect 400 \
    --check detail eq 'Invalid backend path' --save F29.proxy-path-traversal/dotdot
  control-agent-server api GET '/app-backends/qa-backend/%252e%252e/secret' --auth none --expect 400 \
    --save F29.proxy-path-traversal/double-encoded
  control-agent-server api GET '/app-backends/qa-backend/%25252e%25252e/secret' --auth none --expect 400
  control-agent-server api GET '/app-backends/qa-backend/a%5cb' --auth none --expect 400
  control-agent-server api GET '/app-backends/qa-backend/%2e/x' --auth none --expect 400
  control-agent-server api POST '/app-backends/qa-backend/%2e%2e/x' --auth none --expect 400
  control-agent-server ws listen '/app-backends/qa-backend/%2e%2e/ws' --auth none --header "Origin: $S" \
    --header "Cookie: oh_app_backend_session=$TOKEN" --duration 5 --expect-reject 403
  control-agent-server api GET '/app-backends/qa-backend/app%2Ejs' --auth none --cookie "oh_app_backend_session=$TOKEN" \
    --expect 200 --check path eq /app.js
  ```
  `..`, `.` and backslashes are refused at every encoding depth (400 on
  HTTP, a 403 handshake on WebSocket); an encoded dot inside a name is
  forwarded.
- **WebSocket bridge (`F29.ws-bridge`).** Cookie and ingress `Origin` on the
  handshake.
  ```sh
  control-agent-server ws listen /app-backends/qa-backend/ws --auth none --header "Origin: $S" \
    --header "Cookie: oh_app_backend_session=$TOKEN" --send qa-f29-hello --until-kind str --until .=echo:qa-f29-hello \
    --duration 10 --save F29.ws-bridge/echo
  control-agent-server ws listen /app-backends/qa-headers/ws --auth header --header "Origin: $S" \
    --header "Cookie: oh_app_backend_session=$T2; qa_other=1" --query view=main --query session_api_key=qa-leak \
    --send qa-f29-headers --until-kind qa_ws_echo --until echo=qa-f29-headers --until cookie=absent \
    --until origin=absent --until session_key=absent --until query=view=main --duration 10 --save F29.ws-bridge/headers
  control-agent-server ws listen /app-backends/qa-backend --auth none --header "Origin: $S" \
    --header "Cookie: oh_app_backend_session=$TOKEN" --send qa-f29-root --duration 10 --expect-close 1011 \
    --expect-frames 0 --save F29.ws-bridge/root-refused
  control-agent-server ws listen /app-backends/qa-backend/no-socket-here --auth none --header "Origin: $S" \
    --header "Cookie: oh_app_backend_session=$TOKEN" --duration 10 --expect-close 1011 --expect-frames 0
  ```
  `qa-backend` answers `echo:qa-f29-hello`; `qa-headers` reports that its
  handshake carried no cookie, no `Origin`, no `X-Session-API-Key` and only
  `view=main` as the query. The root route `/app-backends/qa-backend` targets
  the backend's `/`, which is plain HTTP: the handshake succeeds (the bridge
  accepts before dialing the backend), then the socket closes with 1011 and
  no frame, as does any sub-path the backend does not upgrade.
- **Handshake rejections (`F29.ws-handshake-rejections`).** Each refused
  before accept (server close 1008, which the client sees as HTTP 403).
  ```sh
  control-agent-server ws listen /app-backends/qa-backend/ws --auth none --header "Cookie: oh_app_backend_session=$TOKEN" \
    --duration 5 --expect-reject 403 --save F29.ws-handshake-rejections/no-origin
  control-agent-server ws listen /app-backends/qa-backend/ws --auth none --header 'Origin: http://localhost:3000' \
    --header "Cookie: oh_app_backend_session=$TOKEN" --duration 5 --expect-reject 403 --save F29.ws-handshake-rejections/control-origin
  control-agent-server ws listen /app-backends/qa-backend/ws --auth none --header "Origin: $S" --duration 5 --expect-reject 403
  control-agent-server ws listen /app-backends/qa-backend/ws --auth none --header "Origin: $S" \
    --header 'Cookie: oh_app_backend_session=qa-f29-bogus' --duration 5 --expect-reject 403
  control-agent-server ws listen /app-backends/qa-backend/ws --auth none --header "Origin: $S" \
    --header "Cookie: oh_app_backend_session=$T2" --duration 5 --expect-reject 403 --save F29.ws-handshake-rejections/other-app
  control-agent-server ws listen /app-backends/qa-backend/ws --auth header --header "Origin: $S" --duration 5 --expect-reject 403
  control-agent-server ws listen /app-backends/qa-backend/ws --auth query --header "Origin: $S" --duration 5 --expect-reject 403
  ```
  403 without `Origin`, with the control origin, without a cookie, with a
  bogus or another app's cookie, and with only the session key (header or
  query). The accepted handshake is in the previous bullet.
- **Subprotocol (`F29.ws-subprotocol`), known bug.** `qa-headers`' `/ws`
  negotiates `qa.v1` when a client asks for it; the direct connection is the
  control. Both sides use a subprotocol-aware client (`websockets` with
  `subprotocols=[...]`), because `ws listen --header 'Sec-WebSocket-Protocol: ...'`
  rejects any subprotocol the server selects and so could never see a fix.
  ```sh
  SP=$(control-agent-server api POST /app-backends/qa-headers/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  test -n "$SP"
  HPORT=$(control-agent-server api GET /api/canvas-extensions/installed/qa-headers/backend --field port)
  control-agent-server exec --expect-output 'subprotocol=qa.v1' -- "$PWD/.venv/bin/python" -c \
    'import sys; from websockets.sync.client import connect; ws = connect(sys.argv[1], subprotocols=["qa.v1"], proxy=None); print("subprotocol=" + str(ws.subprotocol)); ws.close()' \
    "ws://127.0.0.1:$HPORT/ws"
  control-agent-server exec --expect-output 'subprotocol=None' --expect-output '"echo": "qa-f29-plain"' -- "$PWD/.venv/bin/python" -c \
    'import sys; from websockets.sync.client import connect; ws = connect(sys.argv[1], origin=sys.argv[2], additional_headers={"Cookie": "oh_app_backend_session=" + sys.argv[3]}, proxy=None); print("subprotocol=" + str(ws.subprotocol)); ws.send("qa-f29-plain"); print(ws.recv(timeout=10)); ws.close()' \
    "ws://127.0.0.1:$P/app-backends/qa-headers/ws" "$S" "$SP"
  control-agent-server exec --expect-output 'subprotocol=qa.v1' --expect-output '"protocol": "qa.v1"' \
    --save F29.ws-subprotocol/bridged -- "$PWD/.venv/bin/python" -c \
    'import sys; from websockets.sync.client import connect; ws = connect(sys.argv[1], subprotocols=["qa.v1"], origin=sys.argv[2], additional_headers={"Cookie": "oh_app_backend_session=" + sys.argv[3]}, proxy=None); print("subprotocol=" + str(ws.subprotocol)); ws.send("qa-f29-proto"); print(ws.recv(timeout=10)); ws.close()' \
    "ws://127.0.0.1:$P/app-backends/qa-headers/ws" "$S" "$SP"  # bug
  ```
  The controls: directly, the backend negotiates `qa.v1`; through the
  bridge, the same client without a subprotocol gets its echo with a fresh
  cookie. Expected: the bridge negotiates `qa.v1` and the backend reports
  `protocol: qa.v1`, as it does directly. Actual: `subprotocol=None` and
  `protocol: none` (the echo frame still arrives); the bridge neither
  forwards `Sec-WebSocket-Protocol` upstream nor selects one when it
  accepts. Browsers fail such a handshake (Chrome: "Sent non-empty
  'Sec-WebSocket-Protocol' header but no response was received"), so
  clients that require a subprotocol (graphql-ws, many terminal protocols)
  cannot connect through the bridge.
- **Revoke a session (`F29.session-revoke`).** Two sessions of one app; one
  has a bridged socket open.
  ```sh
  RA=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  RB=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  test -n "$RA" && test -n "$RB"
  control-agent-server ws start /app-backends/qa-backend/ws --name f29-revoke --auth none --header "Origin: $S" \
    --header "Cookie: oh_app_backend_session=$RA" --send qa-f29-before-revoke --duration 120
  control-agent-server ws read f29-revoke --contains echo:qa-f29-before-revoke --wait 10 --expect-open
  control-agent-server api DELETE /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' \
    --cookie "oh_app_backend_session=$RA" --expect 204 --check-header set-cookie contains 'oh_app_backend_session=""' \
    --check-header set-cookie contains 'Max-Age=0;' --check-header set-cookie contains 'Path=/app-backends/qa-backend;' \
    --save F29.session-revoke/delete
  control-agent-server ws stop f29-revoke --wait 10 --expect-close 1000 --save F29.session-revoke/socket-closed
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$RA" --expect 401 \
    --save F29.session-revoke/revoked-cookie
  control-agent-server ws listen /app-backends/qa-backend/ws --auth none --header "Origin: $S" \
    --header "Cookie: oh_app_backend_session=$RA" --duration 5 --expect-reject 403
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$RB" --expect 200 \
    --check app eq qa-backend
  control-agent-server api DELETE /app-backends/qa-backend/session --auth none --header 'Origin: http://localhost:3000' \
    --cookie "oh_app_backend_session=$RB" --expect 401
  control-agent-server api DELETE /app-backends/qa-backend/session --header 'Origin: https://evil.qa.test' \
    --cookie "oh_app_backend_session=$RB" --expect 403
  control-agent-server api DELETE /app-backends/qa-backend/session --header "Origin: $S" \
    --cookie "oh_app_backend_session=$RB" --expect 409
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$RB" --expect 200
  control-agent-server api DELETE /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 204 \
    --check-header set-cookie contains 'Max-Age=0;'
  ```
  The revoked cookie's socket closes (1000) and the cookie gets 401 and a
  403 handshake; the second session still works, also after DELETEs refused
  for a missing key (401), a foreign origin (403) and the ingress origin
  (409). A DELETE with no cookie still answers 204 and clears the cookie.
- **Backend stop and disable revoke (`F29.backend-lifecycle-revokes`).**
  F28's lifecycle routes, seen through the bridge.
  ```sh
  LC=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  test -n "$LC"
  PORT1=$(control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --field port)
  control-agent-server ws start /app-backends/qa-backend/ws --name f29-stop --auth none --header "Origin: $S" \
    --header "Cookie: oh_app_backend_session=$LC" --send qa-f29-before-stop --duration 120
  control-agent-server ws read f29-stop --contains echo:qa-f29-before-stop --wait 10 --expect-open
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/stop --expect 200 --check state eq stopped
  control-agent-server ws stop f29-stop --wait 10 --expect-close 1000 --save F29.backend-lifecycle-revokes/stop-socket
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$LC" --expect 503 \
    --check exception eq '503: Canvas App backend is not ready' --save F29.backend-lifecycle-revokes/stopped-proxy
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 503 \
    --check exception eq '503: Canvas App backend is not ready' --check-header set-cookie missing
  control-agent-server ws listen /app-backends/qa-backend/ws --auth none --header "Origin: $S" \
    --header "Cookie: oh_app_backend_session=$LC" --duration 5 --expect-reject 403
  control-agent-server api DELETE /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' \
    --cookie "oh_app_backend_session=$LC" --expect 204
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$REV\"}" \
    --expect 200 --check state eq ready --check port ne "$PORT1"
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$LC" --expect 401 \
    --save F29.backend-lifecycle-revokes/after-restart
  LD=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  control-agent-server ws start /app-backends/qa-backend/ws --name f29-disable --auth none --header "Origin: $S" \
    --header "Cookie: oh_app_backend_session=$LD" --send qa-f29-before-disable --duration 120
  control-agent-server ws read f29-disable --contains echo:qa-f29-before-disable --wait 10 --expect-open
  control-agent-server api PATCH /api/canvas-extensions/installed/qa-backend --json '{"enabled": false}' --expect 200 \
    --check enabled eq false
  control-agent-server ws stop f29-disable --wait 10 --expect-close 1000 --save F29.backend-lifecycle-revokes/disable-socket
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$LD" --expect 503 \
    --check exception eq '503: Canvas App backend is not ready' --save F29.backend-lifecycle-revokes/disabled-proxy
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --expect 200 --check state eq stopped
  control-agent-server api PATCH /api/canvas-extensions/installed/qa-backend --json '{"enabled": true}' --expect 200
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$REV\"}" \
    --expect 200 --check state eq ready
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$LD" --expect 401
  LE=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$LE" --expect 200 \
    --check app eq qa-backend
  CRASH_PID=$(control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --field pid)
  kill -TERM "$CRASH_PID"
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --until-ok 15 --expect 200 \
    --check state eq unhealthy --check detail contains 'exited' --check pid missing --save F29.backend-lifecycle-revokes/crash-status
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$LE" --expect 503 \
    --check exception eq '503: Canvas App backend is not ready' --save F29.backend-lifecycle-revokes/after-poll
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 503 \
    --check exception eq '503: Canvas App backend is not ready' --check-header set-cookie missing
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$REV\"}" \
    --expect 200 --check state eq ready
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$LE" --expect 401 \
    --save F29.backend-lifecycle-revokes/crash-restart-old-cookie
  LF=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$LF" --expect 200 \
    --check app eq qa-backend
  ```
  Stop and disable each close the open socket (1000) and turn the bridge to
  503 (a DELETE of the session still answers 204); after the backend starts
  again on a new port the old cookies get 401 and a fresh session works.
  Killing the ready backend's process (a crash) is reported by the next
  status poll as `unhealthy` (`Backend exited with code ...`, no `pid`),
  after which the bridge answers 503 to the old cookie and to a new
  bootstrap. A crash revokes nothing, so `LE` is still a live session; it
  gets 401 once the backend restarts only because sessions are bound to the
  backend's port, and a new session works. What the bridge answers between
  the crash and that poll is `F29.backend-crash-detected`. The backend ends
  enabled and ready.
- **Crash seen by the bridge (`F29.backend-crash-detected`), known bug.**
  The same crash, but nobody polls the backend's status before the next
  proxied request.
  ```sh
  CC=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  test -n "$CC"
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$CC" --expect 200 \
    --check app eq qa-backend
  CRASH_PID=$(control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --expect 200 --check state eq ready --field pid)
  kill -TERM "$CRASH_PID"
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$CC" --until-ok 15 \
    --expect 503 --check exception eq '503: Canvas App backend is not ready' --save F29.backend-crash-detected/crashed  # bug
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --expect 200 --check state ne ready
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$REV\"}" \
    --expect 200 --check state eq ready
  ```
  The cookie works until the kill. Expected: within a few seconds of the
  process exiting, the bridge answers 503 `Canvas App backend is not ready`,
  as for any backend that is not running. Actual: for the whole 15 s it
  answers 502 `Conversation container unreachable` (the shared Docker
  runtime proxy's message, which names a conversation container that does
  not exist here), because `ready_endpoint` (`canvas_extensions/backend.py`)
  trusts the stored `ready` state and only a call to the F28 status route
  notices the exit (`F29.backend-lifecycle-revokes`). While the bug lasts,
  the bullet stops there and leaves the dead backend marked `ready`; the
  next bullet's server restart clears it.
- **Configured control origins (`F29.session-configured-control-origin`).**
  A hosted Canvas runs on its own origin, which the operator lists in
  `allow_cors_origins` or matches with `allow_cors_origin_regex`; a Canvas in
  Docker reaches the server from `DOCKER_HOST_ADDR`. The bridge checks these
  with its own code, so the bullet drives the bootstrap and its CORS
  preflight together. A restart stops the backends, so the bullet starts
  them again, and it clears the settings before it ends.
  ```sh
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: https://qa-f29.example' --expect 403 \
    --check detail eq 'Origin is not allowed' --check-header set-cookie missing
  control-agent-server restart --config-json '{"allow_cors_origins": ["https://qa-f29.example"], "allow_cors_origin_regex": "https://[a-z]+\\.qa-f29re\\.example"}' \
    --env DOCKER_HOST_ADDR=10.254.0.9
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$REV\"}" \
    --expect 200 --check state eq ready --quiet
  CO=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: https://qa-f29.example' --expect 200 \
    --check ingress_url eq "$S/app-backends/qa-backend/" --check-header access-control-allow-origin eq https://qa-f29.example \
    --check-header access-control-allow-credentials eq true --save F29.session-configured-control-origin/listed \
    --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  test -n "$CO"
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: https://app.qa-f29re.example' --expect 200 \
    --check-header set-cookie contains oh_app_backend_session= \
    --check-header access-control-allow-origin eq https://app.qa-f29re.example --save F29.session-configured-control-origin/regex
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://10.254.0.9:3000' --expect 200 \
    --check-header set-cookie contains oh_app_backend_session= \
    --check-header access-control-allow-origin eq http://10.254.0.9:3000 --save F29.session-configured-control-origin/docker-host
  for ORIGIN in https://qa-f29.example https://app.qa-f29re.example http://10.254.0.9:3000; do
    PRE=$(curl -s -D - -o /dev/null --noproxy '*' -X OPTIONS -H "Origin: $ORIGIN" -H 'Access-Control-Request-Method: POST' \
      -H 'Access-Control-Request-Headers: x-session-api-key' "$S/app-backends/qa-backend/session")
    grep -q '^HTTP/1.1 200' <<<"$PRE"
    grep -qi "^access-control-allow-origin: $ORIGIN" <<<"$PRE"
  done
  for ORIGIN in https://evil.qa.test https://qa-f29.example:8443 http://qa-f29.example https://app.qa-f29re.example.evil.test \
    https://qa-f29re.example http://10.254.0.90:3000; do
    control-agent-server api POST /app-backends/qa-backend/session --header "Origin: $ORIGIN" --expect 403 \
      --check detail eq 'Origin is not allowed' --check-header set-cookie missing --quiet
  done
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$CO" --expect 200 \
    --check app eq qa-backend
  control-agent-server api POST /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$CO" \
    --header 'Origin: https://qa-f29.example' --json '{"qa": "f29"}' --expect 403 --check detail eq 'Origin is not allowed' \
    --save F29.session-configured-control-origin/not-app-origin
  control-agent-server api DELETE /app-backends/qa-backend/session --header 'Origin: https://app.qa-f29re.example' \
    --cookie "oh_app_backend_session=$CO" --expect 204 --check-header set-cookie contains 'Max-Age=0;'
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$CO" --expect 401
  control-agent-server restart --config-json '{"allow_cors_origins": [], "allow_cors_origin_regex": null}' --env DOCKER_HOST_ADDR=
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$REV\"}" \
    --expect 200 --check state eq ready --quiet
  control-agent-server api POST /api/canvas-extensions/installed/qa-headers/backend/start --json "{\"revision\": \"$REV2\"}" \
    --expect 200 --check state eq ready --quiet
  for ORIGIN in https://qa-f29.example https://app.qa-f29re.example http://10.254.0.9:3000; do
    control-agent-server api POST /app-backends/qa-backend/session --header "Origin: $ORIGIN" --expect 403 \
      --check-header set-cookie missing --quiet
  done
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 \
    --check-header set-cookie contains oh_app_backend_session= --save F29.session-configured-control-origin/cleared
  ```
  Before the restart the hosted origin is refused (403). With the settings,
  the listed origin, a full regex match and the Docker host address (any
  port) each mint a cookie, and the CORS middleware allows each one's
  preflight with credentials. The listed host on another port or on
  `http`, a regex match with a suffix, the regex's bare domain and another
  address are refused with 403 and no cookie. The minted cookie reaches the
  backend on a GET, but the control origin is not the ingress origin, so an
  unsafe proxied method with it is still 403; a DELETE from another
  configured origin revokes the session. With the settings cleared
  (`DOCKER_HOST_ADDR` set empty) the three origins are refused again and
  `localhost` still works. Both backends end ready.
- **Trusted forwarded headers (`F29.trusted-forwarded-headers`).** Restart
  with `trust_forwarded_headers` on, as behind a TLS-terminating proxy, then
  back off. A restart stops the backends, so the bullet starts them again.
  ```sh
  control-agent-server restart --config-json '{"trust_forwarded_headers": true}'
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$REV\"}" \
    --expect 200 --check state eq ready --quiet
  FH=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' \
    --header "Host: localhost:$P" --header "X-Forwarded-Host: 127.0.0.1:$P" --expect 200 \
    --check ingress_url eq "$S/app-backends/qa-backend/" --check-header set-cookie contains Secure \
    --save F29.trusted-forwarded-headers/session --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  test -n "$FH"
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$FH" \
    --header "Host: localhost:$P" --header "X-Forwarded-Host: 127.0.0.1:$P" --expect 200 --check path eq /echo \
    --save F29.trusted-forwarded-headers/proxy
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$FH" \
    --header "Host: localhost:$P" --expect 421
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' \
    --header 'X-Forwarded-Proto: https' --expect 421
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' \
    --header 'X-Forwarded-Host: apps.qa.test' --expect 421
  control-agent-server restart --config-json '{"trust_forwarded_headers": false}'
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$REV\"}" \
    --expect 200 --check state eq ready --quiet
  control-agent-server api POST /api/canvas-extensions/installed/qa-headers/backend/start --json "{\"revision\": \"$REV2\"}" \
    --expect 200 --check state eq ready --quiet
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' \
    --header "Host: localhost:$P" --header "X-Forwarded-Host: 127.0.0.1:$P" --expect 421 \
    --save F29.trusted-forwarded-headers/untrusted-again
  ```
  Trusted, `X-Forwarded-Host: 127.0.0.1:PORT` makes a `localhost:PORT`
  request count as the ingress for the bootstrap (a `Secure` cookie) and the
  proxy, while the same request without it is still 421, and a forwarded
  `https` or foreign host does not match the `http` ingress (421). After the
  second restart the header is ignored again. Both backends end ready.
- **Server restart (`F29.sessions-not-persisted`).** Sessions and running
  backends are in memory.
  ```sh
  RS=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$RS" --expect 200
  control-agent-server restart
  control-agent-server api GET /server_info --auth none --check app_backend_ingress_url eq "$S"
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --expect 200 --check state eq stopped \
    --check prepared_revision eq "$REV" --save F29.sessions-not-persisted/backend-after-restart
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$RS" --expect 503 \
    --check exception eq '503: Canvas App backend is not ready'
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$REV\"}" \
    --expect 200 --check state eq ready
  control-agent-server api POST /api/canvas-extensions/installed/qa-headers/backend/start --json "{\"revision\": \"$REV2\"}" \
    --expect 200 --check state eq ready
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$RS" --expect 401 \
    --save F29.sessions-not-persisted/old-cookie
  RT=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$RT" --expect 200 \
    --check app eq qa-backend
  ```
  After the restart both backends are `stopped` and the bridge answers 503;
  they start again without a new prepare, the pre-restart cookie gets 401
  and a new session works.
- **Session expiry (`F29.session-ttl`).** The server's own 300 s timer is the
  thing under test, so this bullet waits for it (about five minutes).
  ```sh
  T0=$SECONDS
  TTL=$(control-agent-server api POST /app-backends/qa-headers/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  test -n "$TTL"
  control-agent-server ws start /app-backends/qa-headers/ws --name f29-ttl --auth none --header "Origin: $S" \
    --header "Cookie: oh_app_backend_session=$TTL" --send qa-f29-ttl --duration 400
  control-agent-server ws read f29-ttl --contains qa-f29-ttl --wait 10 --expect-open
  control-agent-server api GET /app-backends/qa-headers/echo --auth none --cookie "oh_app_backend_session=$TTL" --expect 200
  control-agent-server ws read f29-ttl --wait 330 --expect-close 1000 --save F29.session-ttl/socket-closed
  ELAPSED=$((SECONDS - T0))
  test "$ELAPSED" -ge 295 && test "$ELAPSED" -le 320
  control-agent-server ws stop f29-ttl
  control-agent-server api GET /app-backends/qa-headers/echo --auth none --cookie "oh_app_backend_session=$TTL" --expect 401 \
    --save F29.session-ttl/expired-cookie
  ```
  The idle socket closes (1000) between 295 and 320 s after the bootstrap,
  with no traffic in between, and the cookie then gets 401.
- **Uninstall (`F29.uninstall-revokes`).** F28's uninstall route, seen
  through the bridge. Last, because it removes `qa-backend`.
  ```sh
  UC=$(control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 200 --print-header set-cookie | sed -n 's/^oh_app_backend_session=\([^;]*\);.*/\1/p')
  test -n "$UC"
  control-agent-server ws start /app-backends/qa-backend/ws --name f29-uninstall --auth none --header "Origin: $S" \
    --header "Cookie: oh_app_backend_session=$UC" --send qa-f29-before-uninstall --duration 120
  control-agent-server ws read f29-uninstall --contains echo:qa-f29-before-uninstall --wait 10 --expect-open
  control-agent-server api DELETE /api/canvas-extensions/installed/qa-backend --expect 200
  control-agent-server ws stop f29-uninstall --wait 10 --expect-close 1000 --save F29.uninstall-revokes/socket-closed
  control-agent-server api GET /app-backends/qa-backend/echo --auth none --cookie "oh_app_backend_session=$UC" --expect 503 \
    --check exception eq '503: Canvas App backend is not ready' --save F29.uninstall-revokes/proxy
  control-agent-server api POST /app-backends/qa-backend/session --header 'Origin: http://localhost:3000' --expect 503 \
    --check exception eq '503: Canvas App backend is not ready' --check-header set-cookie missing
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --expect 200 --check state eq missing \
    --check pid missing --save F29.uninstall-revokes/backend
  ```
  Uninstalling closes the open socket (1000), stops the backend (`missing`,
  no `pid`), and the bridge answers 503 to the old cookie and to a new
  bootstrap, exactly as for an app that was never installed.

## Gotchas

- Two credentials, two hosts' worth of rules: the bootstrap
  (`POST`/`DELETE .../session`) takes `X-Session-API-Key` and a control
  `Origin` that differs from the ingress origin, while the proxy routes take
  only the cookie and, for unsafe methods and WebSockets, `Origin` equal to
  the ingress origin. Both must arrive on the ingress host. With
  `--canvas-ingress` the ingress is the run's own `http://127.0.0.1:PORT`, so
  use `http://localhost:3000` (any loopback origin on another port) as the
  control origin; the ingress origin itself gets 409.
- 5xx answers from the bridge go through the server's generic 5xx handler:
  `detail` is `Internal Server Error` and the reason is in `exception`
  (`503: Canvas App backend is not ready`). Assert on `exception`.
- An unknown app and an installed app whose backend is stopped, starting or
  unhealthy look the same (503 not ready); the bridge never reveals which
  apps exist. Only `state == ready` counts, and that state changes only when
  someone calls the F28 status, stop or start routes, so a backend that
  crashed while ready is still dialed and the bridge answers 502 (worded
  `Conversation container unreachable`, the shared Docker-runtime proxy's
  message) until a status poll notices: the known bug
  `F29.backend-crash-detected`.
- The session is bound to the app name and to the backend's current
  loopback port: any backend restart (new port) invalidates every cookie, as
  do stop, disable, uninstall, `DELETE .../session` and a server restart
  (sessions are in memory). Canvas must bootstrap again after each start and
  before the 300 s TTL; the TTL is a constant, not configurable.
- Revocation and expiry close bridged sockets with code 1000 (normal
  closure), not a policy code, and a revoking `DELETE` waits for those
  sockets to finish closing (it can take about a second).
- Sent from a shell, the cookie must be passed explicitly (`--cookie`) or
  through a CLI `--jar`; the jar, like browsers, treats `127.0.0.1` as a
  secure context and replays `Secure` cookies to it. `curl`'s own cookie jar
  may not. The jar matches paths by prefix, so it also replays the cookie to
  `/app-backends/<name>-other`; browsers do not.
- The proxied request body always reaches the backend with
  `Transfer-Encoding: chunked` and no `Content-Length` (even a GET), because
  the proxy streams the body and drops `Content-Length` (the known bug
  `F29.proxy-request-framing`). Backends on servers that ignore chunked
  request bodies (Python's `http.server`) see empty POST bodies; aiohttp and
  uvicorn decode them.
- Upstream `Set-Cookie` is dropped and upstream redirects become 502, so an
  app backend can use neither; pages must use URLs relative to
  `ingress_url` (which ends with `/`, unlike the bare root route).
- `POST` and `DELETE` on `/app-backends/<name>/session` are the bridge's
  session routes and never reach the backend; `GET`, `PUT`, `PATCH`, `HEAD`
  and `OPTIONS` on that path are proxied. A CORS preflight (`OPTIONS` with
  `Origin` and `Access-Control-Request-Method`) is answered by the CORS
  middleware and never reaches the backend either.
- HTTP clients normalize literal `.` and `..` segments before sending
  (`/app-backends/<name>/./x` arrives as `/x`), so traversal recipes use
  percent-encoded forms; the bridge decodes until the value stops changing.
- uvicorn trusts `X-Forwarded-Proto` (and `X-Forwarded-For`) from loopback
  peers by default (`FORWARDED_ALLOW_IPS`), independently of
  `trust_forwarded_headers`, which is why `F29.session-forwarded-proto`
  fails; `X-Forwarded-Host` is honored only with `trust_forwarded_headers`.
- Control origins from `allow_cors_origins` must match the listed origin
  exactly after normalization (scheme, host and port; a default port may be
  omitted), `allow_cors_origin_regex` must match the whole `Origin` value,
  and `DOCKER_HOST_ADDR` matches the host on `http` or `https` and any
  port. They take effect at startup, so a recipe changes them with
  `restart`, which also stops every backend and drops every session. To
  clear `DOCKER_HOST_ADDR` on a run, restart with `--env DOCKER_HOST_ADDR=`
  (empty).
- Not driven here: binary WebSocket frames (the CLI sends text frames
  only), and the TypeScript client's session methods, which need a browser
  to add `Origin` and keep the cookie.
- Evidence saved by these recipes contains live `oh_app_backend_session`
  values (the CLI redacts the run's keys, not app cookies). They expire
  within five minutes and only reach the fixture backends.
