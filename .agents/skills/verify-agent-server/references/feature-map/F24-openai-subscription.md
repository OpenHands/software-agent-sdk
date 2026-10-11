# OpenAI ChatGPT subscription device flow

A ChatGPT Plus/Pro user can sign the agent server into their subscription so
that LLM profiles with `auth_type: "subscription"` run Codex models without an
API key. The server runs OpenAI's device-code flow: a client starts a login
(the server asks `auth.openai.com` for a one-time user code and returns an
opaque polling token), shows the user code and verification URL to a human,
and polls until the human approves; the server then stores the OAuth tokens
in `OH_PERSISTENCE_DIR/auth/openai_oauth.json` and never returns them. Status
says whether a usable login is stored (refreshing an expired one over the
network), logout deletes it and cancels pending logins, and a models route
lists the Codex models a subscription can use.

Source: `openhands-agent-server/openhands/agent_server/llm_router.py`, `openhands-sdk/openhands/sdk/llm/auth/openai.py`, `openhands-sdk/openhands/sdk/llm/auth/credentials.py`, `clients/typescript/src/client/llm-client.ts`

Needs: `node`, `network`

Routes: `GET /api/llm/subscription/openai/models`,
`GET /api/llm/subscription/openai/status`,
`POST /api/llm/subscription/openai/device/start`,
`POST /api/llm/subscription/openai/device/poll`,
`POST /api/llm/subscription/openai/logout`

## Sub-features

- `F24.models`: `GET /api/llm/subscription/openai/models` answers `vendor: openai` and the Codex ACP registry's model ids, sorted.
- `F24.status-disconnected`: with no stored login, status answers 200 `connected: false` with `account_email` and `expires_at` null, without network, and tightens the `auth/` directory to mode 700 without creating a credentials file.
- `F24.status-connected`: a stored, unexpired login shows `connected: true` with its `expires_at`; neither token appears in the response and `account_email` stays null.
- `F24.auth-required`: all five routes answer 401 without a key or with a wrong key, and a refused logout leaves the stored login in place.
- `F24.status-restart`: a stored login survives a server restart.
- `F24.logout`: logout deletes the stored credentials file and answers `connected: false`; status agrees, and a second logout with nothing stored is still 200.
- `F24.status-refresh-rejected`: a login that expires within 60 seconds makes status try a token refresh at `auth.openai.com`; when OpenAI rejects the refresh token, status answers 200 `connected: false` and keeps the file unchanged.
- `F24.status-invalid-file`: a credentials file that is not valid JSON or misses fields reads as `connected: false`, and status deletes it.
- `F24.device-poll-invalid`: a poll without a body, without `device_code` or with a non-string one answers 422.
- `F24.device-poll-unknown`: polling a token the server never issued answers 404 `Subscription device login not found or expired`.
- `F24.device-start`: `device/start` answers 200 with an opaque 43-character `device_code`, a `user_code`, `verification_uri` `https://auth.openai.com/codex/device`, `verification_uri_complete` null, `expires_at` 15 minutes ahead and `interval_seconds` of at least 1, and stores nothing on disk.
- `F24.device-poll-pending`: polling a started login before anyone approves it answers 200 `connected: false`, the token stays usable for the next poll, and no credentials are written.
- `F24.logout-cancels-pending`: after logout, polling a token issued before it answers 404.
- `F24.pending-lost-on-restart`: pending logins live in server memory, so after a restart a token that was pending answers 404.
- `F24.ts-client`: the TypeScript `LLMMetadataClient` subscription methods list models, read status, start, poll and log out, and surface 404 and 401 as `HttpError` statuses.
- `F24.offline-safe`: with no route to `auth.openai.com`, models, status without an expiring login, unknown-token polls and logout still answer normally.
- `F24.device-poll-outage`: a poll whose call to OpenAI fails (the network drops after `device/start`) answers a 5xx but keeps the login pending: once OpenAI is reachable again, the same token polls 200 `connected: false`.
- `F24.status-offline`: with an expired login and no route to `auth.openai.com`, status answers 200 `connected: false`, as it does for a rejected refresh.
- `F24.device-start-offline`: with no route to `auth.openai.com`, `device/start` and a poll of a login started before the outage answer a gateway error (502, 503 or 504), not an unhandled 500.
- `F24.login-complete`: after a human approves the user code with a ChatGPT account, a poll answers `connected: true` with `expires_at`, writes `auth/openai_oauth.json` with mode 600, consumes the token (the next poll is 404), and status shows the login.

## How to get to it (agent POV)

- REST: `GET /api/llm/subscription/openai/models` and `GET
  /api/llm/subscription/openai/status` (no body);
  `POST /api/llm/subscription/openai/device/start` (no body) returns
  `{device_code, user_code, verification_uri, verification_uri_complete,
  expires_at (ms), interval_seconds}`;
  `POST /api/llm/subscription/openai/device/poll` takes
  `{"device_code": "<token from start>"}`; `POST
  /api/llm/subscription/openai/logout` (no body). Status, poll and logout all
  answer the same `{vendor, connected, account_email, expires_at}` shape.
  Every route needs `X-Session-API-Key` like the rest of `/api/*`.
- TypeScript client: `LLMMetadataClient` (`clients/typescript/src/client/llm-client.ts`,
  exported from `clients.ts`): `getOpenAISubscriptionModels()`,
  `getOpenAISubscriptionStatus()`, `startOpenAISubscriptionDeviceLogin()`,
  `pollOpenAISubscriptionDeviceLogin(deviceCode)`,
  `logoutOpenAISubscription()`. `F24.ts-client` drives all five.
- SDK: there is no `RemoteWorkspace` wrapper for these routes. In process,
  `OpenAISubscriptionAuth` and `LLM.subscription_login(vendor="openai",
  auth_method="device_code")` run the same OpenAI flow locally and use the
  same credential store. On the server, an LLM config with
  `auth_type: "subscription"` (a profile, a conversation's agent) loads the
  login this family stores through `create_subscription_llm_from_config`;
  profile validation of such a config is mapped in F20
  (`F20.validate-subscription-login`).
- Configuration: `OH_PERSISTENCE_DIR` decides where the login is stored
  (`<dir>/auth/openai_oauth.json`, else `~/.openhands/auth/`); a launched run
  keeps it at `home/.openhands/auth/` under the run. Outbound calls honor the
  server's `HTTPS_PROXY`/`https_proxy` and CA variables, which `launch`
  forwards.
- A frontend sign-in (Agent Canvas) is the intended caller: it starts a
  login, shows the code, polls, then saves a profile with
  `auth_type: "subscription"` (context only; see the comment in
  `profiles_router.py`'s validate handler).
- Recipes use `api` for every route, `state ls|cat` and `stat` for the stored
  login, `logs --grep` for the refresh attempt, `restart` for persistence, a
  second run launched with an unreachable proxy for the offline cases, a
  second run behind a run-owned CONNECT proxy (`netgate`) that can be cut
  and restored without a restart for the outage cases, and `exec -- node`
  for the TypeScript client.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported (`launch --new`), `doctor` is ok, and
  nothing has touched `home/.openhands/auth/` yet. No LLM profile is needed.
  `jq` and `node` are on `PATH`; the TypeScript client is built
  (`clients/typescript/dist/clients.js`, built in its bullet when missing).
- The server reaches `https://auth.openai.com` (through the proxy this shell
  exports, if any): `device/start`, pending polls and token refreshes are
  real calls to OpenAI. They create throwaway device codes that expire after
  15 minutes and never sign anyone in.
- A real sign-in needs a human with a ChatGPT account, so the stored login
  most bullets read is arranged: `write-login.sh RUN OFFSET_MS` writes
  `auth/openai_oauth.json` with fake tokens (`qa-f24-access-token`,
  `qa-f24-refresh-token`) expiring `OFFSET_MS` from now, exactly as a
  completed device login would leave it, and prints its `expires_at`.
  `ts_subscription.mjs` is the TypeScript-client program.
  `EXPECTED_MODELS` is the Codex ACP registry the models route mirrors.
- The offline bullets launch their own second run with
  `HTTPS_PROXY=https_proxy=http://127.0.0.1:9` (nothing listens there) and
  stop it when the bullet ends.
- The outage bullets need the network to drop while a login is pending, and a
  restart would lose the login. Their second run sends its HTTPS through
  `netgate.py`, a CONNECT proxy on a free loopback port that relays to this
  shell's `HTTPS_PROXY` (or connects directly when there is none).
  `netgate.sh up|down PORT` starts it and prints its port, or stops it so
  connections to that port are refused, the same failure as port 9.

  ```sh
  F24="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f24"
  AUTH_DIR="$AGENT_SERVER_VERIFY_RUN/home/.openhands/auth"
  mkdir -p "$F24"
  cat > "$F24/write-login.sh" <<'EOF'
  # write-login.sh RUN_DIR OFFSET_MS: store a ChatGPT login with fake tokens
  # that expires OFFSET_MS from now, where a completed device login leaves it.
  auth="$1/home/.openhands/auth"
  exp=$(( $(date +%s) * 1000 + $2 ))
  mkdir -p "$auth"
  printf '{"type": "oauth", "vendor": "openai", "access_token": "qa-f24-access-token", "refresh_token": "qa-f24-refresh-token", "expires_at": %s}\n' "$exp" > "$auth/openai_oauth.json"
  chmod 600 "$auth/openai_oauth.json"
  echo "$exp"
  EOF
  cat > "$F24/ts_subscription.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const { LLMMetadataClient } = await import(pathToFileURL(resolve('clients/typescript/dist/clients.js')).href);
  const host = process.env.AGENT_SERVER_URL;
  const client = new LLMMetadataClient({ host, apiKey: process.env.SESSION_API_KEY });
  const disconnected = { vendor: 'openai', connected: false, account_email: null, expires_at: null };
  assert.deepEqual(await client.getOpenAISubscriptionModels(), JSON.parse(process.argv[2]));
  assert.deepEqual(await client.getOpenAISubscriptionStatus(), disconnected);
  const start = await client.startOpenAISubscriptionDeviceLogin();
  assert.match(start.device_code, /^[A-Za-z0-9_-]{43}$/);
  assert.equal(start.verification_uri, 'https://auth.openai.com/codex/device');
  assert.ok(start.interval_seconds >= 1);
  assert.deepEqual(await client.pollOpenAISubscriptionDeviceLogin(start.device_code), disconnected);
  assert.deepEqual(await client.logoutOpenAISubscription(), disconnected);
  await assert.rejects(client.pollOpenAISubscriptionDeviceLogin(start.device_code), (err) => err.status === 404);
  const wrongKey = new LLMMetadataClient({ host, apiKey: 'qa-f24-wrong-key' });
  await assert.rejects(wrongKey.getOpenAISubscriptionStatus(), (err) => err.status === 401);
  wrongKey.close();
  client.close();
  console.log('QA_F24_TS_OK');
  JS
  cat > "$F24/netgate.py" <<'PY'
  # netgate.py PORT PORT_FILE: CONNECT proxy on 127.0.0.1:PORT (0 = free port)
  # that relays to $HTTPS_PROXY when set (no proxy credentials), else connects
  # directly; writes the bound port to PORT_FILE once it listens.
  import asyncio, os, sys
  from urllib.parse import urlsplit

  UPSTREAM = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")

  async def pipe(reader, writer):
      try:
          while data := await reader.read(65536):
              writer.write(data)
              await writer.drain()
      except OSError:
          pass
      finally:
          writer.close()

  async def handle(client_r, client_w):
      try:
          if UPSTREAM:
              url = urlsplit(UPSTREAM)
              up_r, up_w = await asyncio.open_connection(url.hostname, url.port or 80)
          else:
              head = await client_r.readuntil(b"\r\n\r\n")
              host, _, port = head.split()[1].decode().rpartition(":")
              up_r, up_w = await asyncio.open_connection(host, int(port))
              client_w.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
          await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))
      except (OSError, ValueError, asyncio.IncompleteReadError):
          client_w.close()

  async def main():
      server = await asyncio.start_server(handle, "127.0.0.1", int(sys.argv[1]), reuse_address=True)
      with open(sys.argv[2] + ".tmp", "w") as f:
          f.write(str(server.sockets[0].getsockname()[1]))
      os.replace(sys.argv[2] + ".tmp", sys.argv[2])
      async with server:
          await server.serve_forever()

  asyncio.run(main())
  PY
  cat > "$F24/netgate.sh" <<'SH'
  # netgate.sh up PORT | down PORT: start netgate.py on 127.0.0.1:PORT (0 picks
  # a free one) and print the port once it listens, or stop it and return once
  # connections to PORT are refused.
  dir=$(dirname "$0")
  if [ "$1" = up ]; then
    rm -f "$dir/netgate.port"
    .venv/bin/python -I "$dir/netgate.py" "$2" "$dir/netgate.port" >>"$dir/netgate.log" 2>&1 &
    echo $! >"$dir/netgate.pid"
    for _ in $(seq 100); do
      if [ -s "$dir/netgate.port" ]; then cat "$dir/netgate.port"; exit 0; fi
      sleep 0.1
    done
    exit 1
  fi
  kill "$(cat "$dir/netgate.pid" 2>/dev/null)" 2>/dev/null
  for _ in $(seq 100); do
    (exec 3<>"/dev/tcp/127.0.0.1/$2") 2>/dev/null || exit 0
    sleep 0.1
  done
  exit 1
  SH
  EXPECTED_MODELS=$(OPENHANDS_SUPPRESS_BANNER=1 .venv/bin/python -c 'import json; from openhands.sdk.settings.acp_providers import get_acp_provider; print(json.dumps(sorted(m.id for m in get_acp_provider("codex").available_models)))')
  test "$EXPECTED_MODELS" != '[]'
  ```

- **Subscription models (`F24.models`).** List the models a subscription
  can use.
  ```sh
  control-agent-server api GET /api/llm/subscription/openai/models --expect 200 --check vendor eq openai \
    --check models len-ge 1 --check models eq "$EXPECTED_MODELS" --save F24.models/models
  ```
  `models` equals the sorted ids of the `codex` ACP provider
  (`gpt-5.5`, `gpt-5.6-luna`, ... at the time of writing).
- **Status with no login (`F24.status-disconnected`).** Loosen the `auth/`
  directory first, so the read shows the server tightening it.
  ```sh
  mkdir -p "$AUTH_DIR" && chmod 755 "$AUTH_DIR"
  control-agent-server api GET /api/llm/subscription/openai/status --expect 200 --check vendor eq openai \
    --check connected eq false --check account_email eq null --check expires_at eq null --save F24.status-disconnected/status
  test "$(stat -c %a "$AUTH_DIR")" = 700
  control-agent-server state ls 'home/.openhands/auth/*' --expect-count 0
  ```
  The body is `{"vendor": "openai", "connected": false, "account_email":
  null, "expires_at": null}`, the directory is mode 700 afterwards and still
  empty.
- **Status with a stored login (`F24.status-connected`).** Store a login
  that is valid for an hour.
  ```sh
  EXP=$(bash "$F24/write-login.sh" "$AGENT_SERVER_VERIFY_RUN" 3600000)
  control-agent-server api GET /api/llm/subscription/openai/status --expect 200 --check connected eq true \
    --check expires_at eq "$EXP" --check account_email eq null \
    --check . not-contains qa-f24-access-token --check . not-contains qa-f24-refresh-token --save F24.status-connected/status
  ```
  `connected` is `true`, `expires_at` is the stored value in milliseconds,
  and neither token is in the body. No network call is made for an
  unexpired login.
- **Key required (`F24.auth-required`).** Every route without a key and with
  a wrong one, while the login from the previous bullet is stored.
  ```sh
  control-agent-server api GET /api/llm/subscription/openai/models --auth none --expect 401 --check detail eq Unauthorized
  control-agent-server api GET /api/llm/subscription/openai/models --auth bad --expect 401 --check detail eq Unauthorized
  control-agent-server api GET /api/llm/subscription/openai/status --auth none --expect 401 --check detail eq Unauthorized
  control-agent-server api GET /api/llm/subscription/openai/status --auth bad --expect 401 --check detail eq Unauthorized
  control-agent-server api POST /api/llm/subscription/openai/device/start --auth none --expect 401 --check detail eq Unauthorized
  control-agent-server api POST /api/llm/subscription/openai/device/start --auth bad --expect 401 --check detail eq Unauthorized
  control-agent-server api POST /api/llm/subscription/openai/device/poll --auth none --json '{"device_code": "qa-f24-unknown"}' --expect 401 --check detail eq Unauthorized
  control-agent-server api POST /api/llm/subscription/openai/device/poll --auth bad --json '{"device_code": "qa-f24-unknown"}' --expect 401 --check detail eq Unauthorized
  control-agent-server api POST /api/llm/subscription/openai/logout --auth none --expect 401 --check detail eq Unauthorized
  control-agent-server api POST /api/llm/subscription/openai/logout --auth bad --expect 401 --check detail eq Unauthorized \
    --save F24.auth-required/logout-bad-key
  control-agent-server state ls home/.openhands/auth/openai_oauth.json --expect-count 1
  control-agent-server api GET /api/llm/subscription/openai/status --expect 200 --check connected eq true --check expires_at eq "$EXP"
  ```
  All ten calls are 401 `{"detail": "Unauthorized"}`; the run's key still
  reads the login, so the refused logouts deleted nothing.
- **Login across a restart (`F24.status-restart`).** Restart and read the
  status again.
  ```sh
  control-agent-server restart
  control-agent-server api GET /api/llm/subscription/openai/status --expect 200 --check connected eq true \
    --check expires_at eq "$EXP" --save F24.status-restart/after-restart
  ```
  The login lives on disk, so the restarted server reports it unchanged.
- **Logout (`F24.logout`).** Log out, read back, log out again.
  ```sh
  control-agent-server api POST /api/llm/subscription/openai/logout --expect 200 --check vendor eq openai \
    --check connected eq false --check expires_at eq null --save F24.logout/logout
  control-agent-server state ls 'home/.openhands/auth/*' --expect-count 0
  control-agent-server api GET /api/llm/subscription/openai/status --expect 200 --check connected eq false --save F24.logout/status-after
  control-agent-server api POST /api/llm/subscription/openai/logout --expect 200 --check connected eq false --save F24.logout/logout-again
  ```
  The file is gone, status is disconnected, and the second logout with
  nothing stored is 200 with the same body.
- **Rejected refresh (`F24.status-refresh-rejected`).** Store a login that
  expires in 30 seconds (inside the 60-second safety margin, so it counts as
  expired) with a refresh token OpenAI does not know.
  ```sh
  N0=$(control-agent-server logs --grep 'Refreshing OpenAI access token' | jq .total_matching)
  EXP=$(bash "$F24/write-login.sh" "$AGENT_SERVER_VERIFY_RUN" 30000)
  control-agent-server api GET /api/llm/subscription/openai/status --expect 200 --check connected eq false \
    --check expires_at eq null --save F24.status-refresh-rejected/status
  N1=$(control-agent-server logs --grep 'Refreshing OpenAI access token' | jq .total_matching)
  test "$N1" -gt "$N0"
  control-agent-server state cat home/.openhands/auth/openai_oauth.json --check expires_at eq "$EXP" \
    --check access_token eq qa-f24-access-token --check refresh_token eq qa-f24-refresh-token
  control-agent-server api POST /api/llm/subscription/openai/logout --expect 200
  ```
  Status logs `Refreshing OpenAI access token`, OpenAI refuses the refresh
  token, and the answer is `connected: false`; the stored file is left as it
  was (no new tokens, same `expires_at`) until the logout removes it.
- **Invalid credentials file (`F24.status-invalid-file`).** A file that is
  missing required fields, then one that is not JSON.
  ```sh
  printf '{"type": "oauth", "vendor": "openai"}\n' > "$AUTH_DIR/openai_oauth.json"
  control-agent-server api GET /api/llm/subscription/openai/status --expect 200 --check connected eq false \
    --save F24.status-invalid-file/missing-fields
  control-agent-server state ls 'home/.openhands/auth/*' --expect-count 0
  printf 'qa-f24-not-json' > "$AUTH_DIR/openai_oauth.json"
  control-agent-server api GET /api/llm/subscription/openai/status --expect 200 --check connected eq false \
    --save F24.status-invalid-file/not-json
  control-agent-server state ls 'home/.openhands/auth/*' --expect-count 0
  ```
  Both reads are `connected: false` and each time the server deletes the
  unreadable file.
- **Malformed polls (`F24.device-poll-invalid`).** No body, an empty
  object, a number.
  ```sh
  control-agent-server api POST /api/llm/subscription/openai/device/poll --expect 422 --check detail.0.type eq missing \
    --check detail.0.loc eq '["body"]'
  control-agent-server api POST /api/llm/subscription/openai/device/poll --json '{}' --expect 422 --check detail.0.type eq missing \
    --check detail.0.loc eq '["body", "device_code"]' --save F24.device-poll-invalid/missing-code
  control-agent-server api POST /api/llm/subscription/openai/device/poll --json '{"device_code": 123}' --expect 422 \
    --check detail.0.type eq string_type --save F24.device-poll-invalid/number
  ```
  All three are 422 with the field named in `detail[0].loc`.
- **Unknown token (`F24.device-poll-unknown`).** Poll a token the server
  never issued.
  ```sh
  control-agent-server api POST /api/llm/subscription/openai/device/poll --json '{"device_code": "qa-f24-unknown"}' --expect 404 \
    --check detail eq 'Subscription device login not found or expired' --save F24.device-poll-unknown/unknown
  control-agent-server state ls 'home/.openhands/auth/*' --expect-count 0
  ```
  404 with that detail, without any call to OpenAI.
- **Start a login (`F24.device-start`).** Ask for a device code.
  ```sh
  T0=$(date +%s)
  START=$(control-agent-server api POST /api/llm/subscription/openai/device/start --expect 200 \
    --check device_code matches '^[A-Za-z0-9_-]{43}$' --check user_code matches '^[A-Z0-9]+-[A-Z0-9]+$' \
    --check verification_uri eq https://auth.openai.com/codex/device --check verification_uri_complete eq null \
    --check expires_at ge "$((T0 * 1000 + 900000))" --check expires_at le "$(((T0 + 60) * 1000 + 900000))" \
    --check interval_seconds ge 1 --save F24.device-start/start)
  DC=$(jq -r .response.body.device_code <<<"$START")
  INTERVAL=$(jq -r .response.body.interval_seconds <<<"$START")
  control-agent-server state ls 'home/.openhands/auth/*' --expect-count 0
  control-agent-server api GET /api/llm/subscription/openai/status --expect 200 --check connected eq false
  ```
  The `device_code` is the server's own random token (OpenAI's
  `device_auth_id` stays on the server), the `user_code` looks like
  `ABCD-12345`, `expires_at` is 15 minutes after the request, and
  `interval_seconds` is OpenAI's polling interval (5 today). Nothing is
  stored and status is still disconnected.
- **Pending login (`F24.device-poll-pending`).** Poll twice, waiting the
  interval the server returned in between, as the protocol asks.
  ```sh
  control-agent-server api POST /api/llm/subscription/openai/device/poll --json "{\"device_code\": \"$DC\"}" --expect 200 \
    --check connected eq false --check expires_at eq null --save F24.device-poll-pending/first
  sleep "$INTERVAL"
  control-agent-server api POST /api/llm/subscription/openai/device/poll --json "{\"device_code\": \"$DC\"}" --expect 200 \
    --check connected eq false --save F24.device-poll-pending/second
  control-agent-server state ls 'home/.openhands/auth/*' --expect-count 0
  ```
  Both polls are 200 `connected: false` (OpenAI answers "pending"), so the
  token was put back after the first one; no credentials appear.
- **Logout cancels pending logins (`F24.logout-cancels-pending`).** Log out
  while `DC` is pending.
  ```sh
  control-agent-server api POST /api/llm/subscription/openai/logout --expect 200 --check connected eq false
  control-agent-server api POST /api/llm/subscription/openai/device/poll --json "{\"device_code\": \"$DC\"}" --expect 404 \
    --check detail eq 'Subscription device login not found or expired' --save F24.logout-cancels-pending/poll-after-logout
  ```
  The token that was pending a moment ago is now 404.
- **Pending logins and restarts (`F24.pending-lost-on-restart`).** Start a
  login, see it pending, restart, poll again.
  ```sh
  DC2=$(control-agent-server api POST /api/llm/subscription/openai/device/start --expect 200 --field device_code)
  control-agent-server api POST /api/llm/subscription/openai/device/poll --json "{\"device_code\": \"$DC2\"}" --expect 200 \
    --check connected eq false
  control-agent-server restart
  control-agent-server api POST /api/llm/subscription/openai/device/poll --json "{\"device_code\": \"$DC2\"}" --expect 404 \
    --check detail eq 'Subscription device login not found or expired' --save F24.pending-lost-on-restart/poll-after-restart
  ```
  Before the restart the poll is pending; after it the token is unknown, so
  a client has to start over (and a human who approves the old code signs
  in nobody).
- **TypeScript client (`F24.ts-client`).** Every subscription method of
  `LLMMetadataClient` from the built client (built here when missing).
  ```sh
  test -f clients/typescript/dist/clients.js || (cd clients/typescript && npm ci && npm run build)
  control-agent-server exec --timeout 120 --expect-output QA_F24_TS_OK --save F24.ts-client/program -- node "$F24/ts_subscription.mjs" "$EXPECTED_MODELS"
  control-agent-server api GET /api/llm/subscription/openai/status --expect 200 --check connected eq false
  control-agent-server state ls 'home/.openhands/auth/*' --expect-count 0
  ```
  The program prints `QA_F24_TS_OK`: the models equal the registry, status
  is disconnected, a started login polls as `connected: false`, logout
  answers disconnected, the logged-out token rejects with `HttpError` status
  404 and a wrong key with 401.
- **Offline-safe routes (`F24.offline-safe`).** A second run whose only way
  out is a proxy on a closed port.
  ```sh
  OFF=$(control-agent-server launch --new --name f24-offline --env HTTPS_PROXY=http://127.0.0.1:9 --env https_proxy=http://127.0.0.1:9 --print-run)
  trap 'control-agent-server stop --run "$OFF" >/dev/null' EXIT
  if control-agent-server api POST /api/llm/subscription/openai/device/start --run "$OFF" --expect 200 --quiet; then false; fi
  control-agent-server api GET /api/llm/subscription/openai/models --run "$OFF" --expect 200 --check models eq "$EXPECTED_MODELS"
  control-agent-server api GET /api/llm/subscription/openai/status --run "$OFF" --expect 200 --check connected eq false \
    --save F24.offline-safe/status-no-login
  control-agent-server api POST /api/llm/subscription/openai/device/poll --run "$OFF" --json '{"device_code": "qa-f24-unknown"}' --expect 404
  bash "$F24/write-login.sh" "$OFF" 3600000 >/dev/null
  control-agent-server api GET /api/llm/subscription/openai/status --run "$OFF" --expect 200 --check connected eq true
  control-agent-server api POST /api/llm/subscription/openai/logout --run "$OFF" --expect 200 --check connected eq false \
    --save F24.offline-safe/logout
  control-agent-server state ls --run "$OFF" 'home/.openhands/auth/*' --expect-count 0
  ```
  `device/start` cannot succeed there (so the network really is cut), while
  the models, status with no login or an unexpired one, an unknown-token
  poll and logout all answer as on the online run.
- **Outage during a pending login (`F24.device-poll-outage`).** A second run
  behind `netgate`: start a login, cut the network, poll, restore it, poll
  the same token again.
  ```sh
  OUTAGE=""
  GP=$(bash "$F24/netgate.sh" up 0)
  trap 'if [ -n "$OUTAGE" ]; then control-agent-server stop --run "$OUTAGE" >/dev/null; fi; bash "$F24/netgate.sh" down "$GP"' EXIT
  OUTAGE=$(control-agent-server launch --new --name f24-outage --env "HTTPS_PROXY=http://127.0.0.1:$GP" --env "https_proxy=http://127.0.0.1:$GP" --print-run)
  START3=$(control-agent-server api POST /api/llm/subscription/openai/device/start --run "$OUTAGE" --expect 200)
  DC3=$(jq -r .response.body.device_code <<<"$START3")
  bash "$F24/netgate.sh" down "$GP"
  control-agent-server api POST /api/llm/subscription/openai/device/poll --run "$OUTAGE" --json "{\"device_code\": \"$DC3\"}" --expect 5xx \
    --save F24.device-poll-outage/poll-offline
  bash "$F24/netgate.sh" up "$GP" >/dev/null
  sleep "$(jq -r .response.body.interval_seconds <<<"$START3")"
  control-agent-server api POST /api/llm/subscription/openai/device/poll --run "$OUTAGE" --json "{\"device_code\": \"$DC3\"}" --expect 200 \
    --check connected eq false --save F24.device-poll-outage/poll-online
  control-agent-server state ls --run "$OUTAGE" 'home/.openhands/auth/*' --expect-count 0
  ```
  The poll during the outage fails (today a 500, `"exception": "All
  connection attempts failed"`; `F24.device-start-offline` asserts the
  gateway status it should be), and the router's `finally` puts the token
  back: after the network returns, the same token is still pending (200
  `connected: false`, not 404). The sleep honors `interval_seconds` before
  the next real call to OpenAI.
- **Status with an expired login offline (`F24.status-offline`), known bug.**
  An offline second run like `F24.offline-safe`'s. Two controls first: the
  network really is cut (`device/start` cannot succeed) and the same run
  reads an unexpired login as connected. Then a login that expired a second
  ago.
  ```sh
  OFF=$(control-agent-server launch --new --name f24-offline --env HTTPS_PROXY=http://127.0.0.1:9 --env https_proxy=http://127.0.0.1:9 --print-run)
  trap 'control-agent-server stop --run "$OFF" >/dev/null' EXIT
  if control-agent-server api POST /api/llm/subscription/openai/device/start --run "$OFF" --expect 200 --quiet; then false; fi
  EXP5=$(bash "$F24/write-login.sh" "$OFF" 3600000)
  control-agent-server api GET /api/llm/subscription/openai/status --run "$OFF" --expect 200 --check connected eq true \
    --check expires_at eq "$EXP5"
  bash "$F24/write-login.sh" "$OFF" -1000 >/dev/null
  control-agent-server api GET /api/llm/subscription/openai/status --run "$OFF" --expect 200 --check connected eq false \
    --save F24.status-offline/status  # bug
  control-agent-server logs --run "$OFF" --grep 'Refreshing OpenAI access token' --expect-min 1
  control-agent-server logs --run "$OFF" --grep 'Unhandled exception on GET' --expect-none
  ```
  Expected: 200 `connected: false` after a refresh attempt, the answer
  status gives when OpenAI rejects a refresh (`F24.status-refresh-rejected`),
  and no traceback for the GET (the `device/start` control leaves its own
  `Unhandled exception on POST`, `F24.device-start-offline`). Today the
  marked status call is 500 `{"detail": "Internal Server Error",
  "exception": "All connection attempts failed"}` with an `Unhandled
  exception on GET /api/llm/subscription/openai/status` traceback in the
  log: `get_openai_subscription_status` catches only `RuntimeError` around
  `refresh_if_needed()`, and `_refresh_access_token` lets httpx transport
  errors through (`llm_router.py`, `auth/openai.py`).
- **Start and poll a login offline (`F24.device-start-offline`), known bug.**
  A second run behind `netgate`: start a login while online and see it
  pending (the controls), cut the network, then start another and poll the
  first.
  ```sh
  OUTAGE=""
  GP=$(bash "$F24/netgate.sh" up 0)
  trap 'if [ -n "$OUTAGE" ]; then control-agent-server stop --run "$OUTAGE" >/dev/null; fi; bash "$F24/netgate.sh" down "$GP"' EXIT
  OUTAGE=$(control-agent-server launch --new --name f24-outage --env "HTTPS_PROXY=http://127.0.0.1:$GP" --env "https_proxy=http://127.0.0.1:$GP" --print-run)
  DC4=$(control-agent-server api POST /api/llm/subscription/openai/device/start --run "$OUTAGE" --expect 200 --field device_code)
  control-agent-server api POST /api/llm/subscription/openai/device/poll --run "$OUTAGE" --json "{\"device_code\": \"$DC4\"}" \
    --expect 200 --check connected eq false
  bash "$F24/netgate.sh" down "$GP"
  control-agent-server api POST /api/llm/subscription/openai/device/start --run "$OUTAGE" --expect 502,503,504 \
    --save F24.device-start-offline/start  # bug
  control-agent-server api POST /api/llm/subscription/openai/device/poll --run "$OUTAGE" --json "{\"device_code\": \"$DC4\"}" \
    --expect 502,503,504 --save F24.device-start-offline/poll  # bug
  control-agent-server logs --run "$OUTAGE" --grep 'Unhandled exception' --expect-none
  ```
  Expected: a gateway status (502, 503 or 504) that tells the client OpenAI
  is unreachable, as `credential_binding.py` and `git_provider_service.py`
  answer 502 or 504 for an unavailable upstream. Both marked calls encode
  the bug, so the bullet stays a known bug until both routes are fixed.
  Today the marked `device/start` is 500 `Internal Server Error` with
  `exception: "All connection attempts failed"` and an `Unhandled exception`
  traceback, and so is the poll (`F24.device-poll-outage`): neither
  `start_openai_subscription_device_login` nor
  `poll_openai_subscription_device_login` maps upstream errors, so
  transport errors and OpenAI's non-2xx answers (`RuntimeError` from
  `_request_device_code` and `_poll_device_code_once`) all surface as
  generic 500s.
- **Complete a login (`F24.login-complete`), blocked.** Needs a ChatGPT
  Plus/Pro account and a human who opens `verification_uri`, signs in and
  enters `user_code` within 15 minutes; this machine has neither. The
  commands, for a run where someone can approve:
  ```sh
  LOGIN=$(control-agent-server api POST /api/llm/subscription/openai/device/start --expect 200 --save F24.login-complete/start)
  LDC=$(jq -r .response.body.device_code <<<"$LOGIN")
  jq -r '.response.body | "Open \(.verification_uri) and enter \(.user_code)"' <<<"$LOGIN"
  for _ in $(seq 1 180); do
    if control-agent-server api POST /api/llm/subscription/openai/device/poll --json "{\"device_code\": \"$LDC\"}" --expect 200 --check connected eq true --quiet; then break; fi
    sleep 5
  done
  control-agent-server state cat home/.openhands/auth/openai_oauth.json --mode 600 --check vendor eq openai --check expires_at exists >/dev/null
  control-agent-server api POST /api/llm/subscription/openai/device/poll --json "{\"device_code\": \"$LDC\"}" --expect 404
  control-agent-server api GET /api/llm/subscription/openai/status --expect 200 --check connected eq true --check expires_at exists \
    --save F24.login-complete/status
  ```
  Expected: the poll turns `connected: true` with `expires_at`, the file is
  mode 600, the token is consumed (404), and status is connected. Logging
  out afterwards (`F24.logout`) removes the real login again.

## Gotchas

- These routes call `https://auth.openai.com` from the server process:
  `device/start` and every poll of a pending token always, status only when
  the stored login expires within 60 seconds. On a machine without outbound
  HTTPS to it, `F24.device-start`, `F24.device-poll-pending`,
  `F24.logout-cancels-pending`, `F24.pending-lost-on-restart`,
  `F24.ts-client`, `F24.device-poll-outage` and
  `F24.status-refresh-rejected` fail with the 500s of the offline bugs, and
  `F24.device-start-offline` fails at its online `device/start` arrange step
  (a `fail`, not an `xfail`); `Needs: network` names that prerequisite.
  `netgate` relays to the shell's `HTTPS_PROXY` without credentials, so a
  proxy that needs them breaks the outage bullets.
- To cut the network, set both `HTTPS_PROXY` and `https_proxy`: Python's
  proxy lookup prefers the lowercase name, and `launch` forwards both from
  the caller's shell.
- Pending logins are held in the server process (`_PENDING_OPENAI_DEVICE_LOGINS`):
  a restart loses them, and they expire 15 minutes after `start`
  (`DEVICE_CODE_TIMEOUT_SECONDS`, pruned lazily on start and poll).
- `device_code` in the start response is the server's opaque polling token,
  not OpenAI's `device_auth_id`. `user_code` is a live one-time sign-in code
  for 15 minutes: it is in the `F24.device-start` evidence, so do not publish
  that evidence while it is valid (device codes are a phishing target).
- Honor `interval_seconds` between polls. Fast polls work today, but any
  OpenAI answer other than success, 403 or 404 (a 429, a 5xx), and any
  transport error, becomes a 500 from `poll` while the token stays pending
  (`F24.device-poll-outage` shows it for a dropped network). A poll that
  arrives while another poll of the same token is in flight answers
  `connected: false` instead of 404.
- Logout bumps an epoch: a poll that was already talking to OpenAI when
  logout ran finishes with `connected: false` and does not store the
  credentials it obtained.
- A login that expires within 60 seconds counts as expired. When OpenAI
  rejects its refresh, the dead file stays, so every status call goes to the
  network again until logout. A successful refresh rewrites the file with
  the new tokens and `expires_in` (default 3600 seconds).
- `account_email` is always `null`; the server never decodes the token.
- The login is stored in plain JSON (not encrypted with `OH_SECRET_KEY`);
  the server makes the file mode 600 when it writes it and the `auth/`
  directory mode 700 on every access, including a plain status read. An
  unreadable file is deleted silently by the next status read.
- The routes emit no WebSocket events. While a deferred-init server is
  dormant they answer 503 like every `/api/*` route (F03).
- `F24.ts-client` imports the built `clients/typescript/dist` and builds it
  only when it is missing: after changing `clients/typescript/src`, run
  `npm run build` there first, or the bullet proves the old build.
- 5xx bodies are rewritten by the server's exception handler to
  `{"detail": "Internal Server Error", "exception": ..., "error_id": ...}`,
  so clients can only tell an OpenAI outage from a server crash by status.
