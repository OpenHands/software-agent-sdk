# Conversation runtime info, sandbox pause and credential bindings

What a sandbox orchestrator and a client see about the runtime behind a
conversation. `GET .../runtime` reports whether a conversation's runtime is
available and resumable (always `available` for the in-process local runtime,
which `reprovision` merely re-reports). Before a sandbox is frozen, the
orchestrator calls `POST /api/conversations/prepare-for-sandbox-pause`: every
loaded conversation is saved, any run in flight is cancelled to `paused`, its
ownership lease file is released, and it is dropped from memory; the catalog
keeps it, and the next event access loads it again. ACP Codex conversations
can take their `CODEX_AUTH_JSON` file credential from an external versioned
source: `PUT .../credential-bindings/CODEX_AUTH_JSON` probes the source, binds
it into a live, not yet initialized ACP agent (scrubbing the stored copies and
recording that the conversation now requires a runtime binding), or parks it
as pending for a conversation that is not loaded or not yet created. A
conversation that requires a binding refuses to load again (409, retryable;
WebSocket close 1013) after a pause or a restart until the binding is
activated again. The pause and binding routes are hidden from the OpenAPI
document. Two runtime-side behaviors complete the picture: with
`OH_CONVERSATION_RUNTIME=docker` the server owns one container per
conversation and its Docker router replaces the runtime routes, adds
`DELETE .../runtime` and `POST .../runtime/credentials`, proxies everything
else into the container and refuses the operations it cannot proxy (501);
and a sandbox whose control plane sets `OH_LLM_API_KEY_REFRESH_URL` and
`OH_LLM_API_KEY_REFRESH_BASE_URLS` re-fetches a managed LLM key once when
the provider rejects the stored one with a 401.

Source: `openhands-agent-server/openhands/agent_server/credential_binding.py`, `openhands-agent-server/openhands/agent_server/conversation_router.py`, `openhands-agent-server/openhands/agent_server/conversation_registry.py`, `openhands-agent-server/openhands/agent_server/conversation_service.py`, `openhands-agent-server/openhands/agent_server/event_service.py`, `openhands-agent-server/openhands/agent_server/conversation_lease.py`, `openhands-agent-server/openhands/agent_server/models.py`, `openhands-agent-server/openhands/agent_server/docker_runtime/`, `openhands-agent-server/openhands/agent_server/managed_llm_key.py`, `openhands-sdk/openhands/sdk/credential.py`, `openhands-sdk/openhands/sdk/agent/acp_file_credentials.py`

Needs: `llm`, `node`, `network`

Routes: `GET /api/conversations/{conversation_id}/runtime`,
`POST /api/conversations/{conversation_id}/runtime/reprovision`,
`POST /api/conversations/prepare-for-sandbox-pause`,
`PUT /api/conversations/{conversation_id}/credential-bindings/{secret_name}`,
`DELETE /api/conversations/{conversation_id}/runtime`,
`POST /api/conversations/{conversation_id}/runtime/credentials`

## Sub-features

- `F12.discovery`: the OpenAPI document lists the two runtime routes but not the pause and credential-binding routes, and `/server_info` advertises `credential_binding_v1`, `credential_binding_readiness_probe_v1` and `credential_binding_activation_guard_v1`.
- `F12.runtime-info`: `GET .../runtime` returns 200 `{"runtime_status": "available", "can_resume": true, "runtime_error": null}` for a local conversation, and the conversation's GET, search and batch-get rows embed the same object as `runtime_info`.
- `F12.runtime-reprovision`: `POST .../runtime/reprovision` returns the same runtime info, ignores a body, and changes nothing (status, event count and lease generation stay the same).
- `F12.runtime-errors`: both runtime routes answer 404 for an unknown id, 422 for a non-UUID id and 401 without a session key.
- `F12.ts-client`: the built TypeScript client's `ConversationClient.getRuntime` and `reprovisionRuntime` return the local runtime info, `getConversation()` embeds it as `runtime_info`, and both methods reject an unknown id with a 404 `HttpError` and a client without a key with 401.
- `F12.sandbox-pause-releases`: `prepare-for-sandbox-pause` answers 204 with an empty body, deletes every `owner_lease.json`, keeps every conversation listed and readable with its status, is idempotent, and is refused with 401 (leaving the leases) without a key.
- `F12.runtime-cold`: runtime info answers for a conversation that is not loaded, before and after a restart, without loading it (no lease file appears).
- `F12.sandbox-pause-rehydrate`: after a pause (no restart in between), the next event access loads the conversation again (a new lease, the same events), and a new message runs to `finished` on a real model, which acts again.
- `F12.sandbox-pause-running`: a conversation whose model call is in flight is cancelled to `paused` within the server's 10 s bound, with a `PauseEvent` and an `InterruptEvent`, and the status change is pushed on its events socket.
- `F12.sandbox-pause-websocket`: an events socket or a session socket that was open before the pause is either closed by the server or keeps receiving the conversation's new events; it is never left open and silent.
- `F12.binding-unsupported-secret`: only file credentials can be bound: any other secret name gets 404 without the source being contacted.
- `F12.binding-validation`: a body with extra keys, no `url`, an empty or over-long (4097+ characters) `url`, an empty or over-long (8193+ characters) header value or more than 16 headers, or a non-UUID id, gets 422 without the source being contacted, also for an unsupported secret name (the body is validated first); no key gets 401.
- `F12.binding-pending`: a binding for a conversation id the server does not know answers 204 after exactly one probe `GET` that carries the given headers; the id stays unknown (404).
- `F12.binding-source-statuses`: the source's answer maps to the activation status: 401/403 to 403, 400/404/422 to 422, 409 to 409, 501 to 501, a 200 that is not `{"value": str, "version": non-empty str}` to 422, and a URL that is not http(s) to 422, each after a single probe (none for the bad URL).
- `F12.binding-source-unavailable`: a source answering 5xx is probed three times with backoff (at least 0.75 s), then the activation fails with 502; an unreachable source also gives 502.
- `F12.binding-native-too-late`: binding a loaded regular (non-ACP) conversation is refused with 409 `Credential binding activation arrived after ACP initialization` and records nothing.
- `F12.binding-native-cold`: a binding sent while a regular (non-ACP) conversation is not loaded never makes that conversation demand a credential binding after the next pause.
- `F12.binding-live-acp`: binding a loaded ACP Codex conversation that has not run yet answers 204, records `required_runtime_credential_bindings: ["CODEX_AUTH_JSON"]` in `meta.json` and removes the stored `CODEX_AUTH_JSON` secret.
- `F12.binding-rebind`: a second activation with the same URL and new headers is accepted (the probe carries the new headers, which are never written to disk); a different URL is refused with 409.
- `F12.binding-required-after-pause`: after a pause, a conversation that requires a binding is still listed and reports runtime `available`, but event access and `/run` answer 409 `{"detail": "credential_binding_activation_required", "retryable": true}` and both conversation sockets close with 1013, until a new activation, after which events load again.
- `F12.binding-required-after-restart`: the requirement survives a restart (409 until a new activation, then 200), and deleting such a conversation needs no activation.
- `F12.binding-pending-on-create`: a pending binding is consumed when a conversation is created with that `conversation_id`, which then requires the binding from the start and stores no `CODEX_AUTH_JSON` secret.
- `F12.binding-pending-cleared`: a pause drops pending bindings, so a conversation created afterwards with that id keeps its own `CODEX_AUTH_JSON` secret and requires no binding.
- `F12.binding-profile-scope`: a conversation launched from an agent profile whose `secret_refs` exclude `CODEX_AUTH_JSON` refuses the binding with 403 `Credential binding authorization was rejected` and records nothing.
- `F12.runtime-launched-profile`: on a local server started with `OH_RUNTIME_LAUNCHED_PROFILE` (the launched profile a Docker parent passes into each conversation container), a conversation created without an agent profile records that profile as `launched_agent_profile` and its `secret_refs` scope the credential binding (403), while a conversation started from its own agent profile keeps its own profile (204).
- `F12.managed-key-refresh`: with `OH_LLM_API_KEY_REFRESH_URL`, `OH_LLM_API_KEY_REFRESH_BASE_URLS` naming the LLM's `base_url` and `OH_LLM_API_KEY_REFRESH_HEADERS` (values expanded from the server environment), a model call rejected with 401 makes exactly one `GET` of the refresh URL with the expanded headers and one retry, and the run finishes; the fetched key is not written to the server's state.
- `F12.managed-key-scope`: an LLM whose `base_url` is outside the allow-list, or any LLM once the allow-list is empty, surfaces the 401 (`LLMAuthenticationError`, status `error`) after one model call and no refresh request; the empty allow-list is logged as "stays off".
- `F12.managed-key-switch`: an in-scope LLM installed with `switch_llm` (or `switch_profile`) recovers from a 401 through the refresh URL like the LLM the conversation was loaded with.
- `F12.docker-runtime-contract`: on a server with `OH_CONVERSATION_RUNTIME=docker` and no Docker daemon, fork, `switch_llm`, `switch_profile` and credential bindings answer 501 before any lookup (the binding source is never contacted); the runtime routes, `DELETE .../runtime`, `POST .../runtime/credentials` and `/run` answer 404 `Conversation not found` for an unknown id, and the two Docker-only routes 401 without a key; both conversation sockets close with 1008 for an unknown id; create refuses a missing body (400), a non-object (422), a non-local workspace (422) and a bad `conversation_id` (400) without writing a runtime manifest; a well-formed create answers 502, leaving a manifest whose runtime info is 404, whose reprovision is 502 and whose socket closes with 1011, until `DELETE /api/conversations/{id}` removes it.
- `F12.docker-runtime`: with a Docker daemon, a create starts a container with the configured `--memory`, `--cpus` and `--pids-limit`, the launched profile in `OH_RUNTIME_LAUNCHED_PROFILE` and a startup bound of `conversation_container_startup_timeout`; `POST .../runtime/credentials` returns the container's session key, `DELETE .../runtime` stops it (204) while the conversation stays readable, reprovision starts it again, a request carrying `X-Expose-Secrets` is refused with 422 and an archived runtime answers 410 (blocked here: no Docker daemon).

## How to get to it (agent POV)

- REST: `GET /api/conversations/{conversation_id}/runtime` and
  `POST /api/conversations/{conversation_id}/runtime/reprovision` (both in
  the OpenAPI document; the local registry always answers `available`).
  `runtime_info` is also embedded in `GET /api/conversations/{id}`,
  `GET /api/conversations/search` and `GET /api/conversations?ids=...`.
- REST, Docker runtime mode (`OH_CONVERSATION_RUNTIME=docker` or config
  `conversation_runtime: docker`; `docker_runtime/routers.py`): the Docker
  router is mounted after the catalog's read routes (`GET
  /api/conversations/{id}`, search, count stay local) and ahead of the local
  conversation routes, so it serves `POST /api/conversations` (create
  through a container), `GET .../runtime` and `POST .../runtime/reprovision`
  (container state),
  two routes the local app does not have, `DELETE .../runtime` (stop the
  container, keep the conversation; 204) and `POST .../runtime/credentials`
  (the container's `session_api_key`), `DELETE /api/conversations/{id}`, and
  a catch-all proxy `/{conversation_id}/{tail}` that forwards into the
  container except `fork`, `switch_llm`, `switch_profile` and
  `credential-bindings` (501). `map routes` lists the routes that exist only
  in this mode (marked `config: conversation_runtime=docker`); the last two
  `Routes:` entries are the two runtime-control ones, and the container
  proxies are listed as not mapped in the README.
- REST, hidden (`include_in_schema=False`, so neither the generated
  TypeScript schema nor OpenAPI tooling sees them; used by the sandbox
  orchestrator): `POST /api/conversations/prepare-for-sandbox-pause` (no body,
  204) and
  `PUT /api/conversations/{conversation_id}/credential-bindings/{secret_name}`
  with `{"url": "...", "headers": {"Name": "value"}}` (204). The credential
  source must answer `GET url` with `{"value": "<credential>", "version":
  "<opaque>"}` and later `PUT url` with `{"expected_version", "value"}`
  (refresh sync; not reachable without a running Codex).
- WebSocket: `/sockets/events/{conversation_id}` and
  `/sockets/session/{conversation_id}` close with 1013
  `credential_binding_activation_required` for a conversation that needs a
  binding. In Docker mode both are bridged into the container and close with
  1008 for an unknown conversation and 1011 when its container cannot start.
- TypeScript client: `ConversationClient.getRuntime(conversationId)` and
  `reprovisionRuntime(conversationId)`; `ConversationInfo.runtime_info`
  (`F12.ts-client` runs them with `node` against the built client). No
  client method for the hidden routes.
- SDK: no Python consumer of these routes in this repository;
  `RemoteWorkspace` calls the Docker-only `.../runtime` lifecycle routes
  (`DELETE .../runtime`, `POST .../runtime/credentials`).
- Configuration: `OH_LEASE_TTL_SECONDS` (default 45; `0` disables the lease
  files that the pause releases); `OH_CONVERSATION_RUNTIME` with
  `conversation_image`, `conversation_container_memory`,
  `conversation_container_cpus`, `conversation_container_pids_limit` and
  `conversation_container_startup_timeout`; `OH_RUNTIME_LAUNCHED_PROFILE`
  (a `LaunchedAgentProfile` JSON the Docker parent sets in each container,
  read by any server at create); `OH_LLM_API_KEY_REFRESH_URL`,
  `OH_LLM_API_KEY_REFRESH_BASE_URLS` (comma-separated `base_url` allow-list,
  required) and `OH_LLM_API_KEY_REFRESH_HEADERS` (JSON object, `$VAR` and
  `${VAR}` expanded from the server environment), read when a conversation
  is loaded (`managed_llm_key.py`, registered in `EventService.start`). The
  refresh URL answers the current key as the plain-text body.
- `/server_info` `capabilities` advertises `credential_binding_v1`,
  `credential_binding_readiness_probe_v1` and
  `credential_binding_activation_guard_v1` (F01 owns the route).
- Recipes: `api` drives every route; `fixture http-sink` plays the credential
  source and the managed-key refresh URL (one sink per reply status, since a
  sink answers every request the same way) and `sink read` counts its probes
  and their headers; a second server (`launch --new --env ...`) carries the
  Docker mode, the launched profile and the refresh variables; `state ls`
  and `state cat` read `owner_lease.json` and `meta.json`; `fixture llm-stub`
  holds a model call in flight and plays the provider that rejects a stale
  key with 401; `ws start`/`ws listen` observe the sockets. An
  ACP Codex conversation is created with an inline
  `{"kind": "ACPAgent", "acp_command": ["codex-acp"], "acp_server": "codex"}`
  agent: the ACP subprocess only starts on the first run, so no Codex binary
  is needed for anything here.

## Driving it with control-agent-server

Preconditions:

- A baseline run (`launch --new`), `doctor` ok, `$DEEPSEEK_API_KEY` set; the
  block below saves the DeepSeek profiles.
- The block creates, in order: an unknown id `NOPE`, the inline ACP Codex
  agent `ACP_AGENT`, the healthy credential source `SRC` (an HTTP sink
  answering `{"value": "{}", "version": "qa-v1"}`), a second healthy source
  `NEVER` that no request may reach, an `llm-stub` that hangs every model call
  for 300 s with an inline agent on it (`HANG_AGENT`), a regular conversation
  `N` on DeepSeek flash that never runs, and a conversation `R` on DeepSeek
  flash that answers one tiny prompt and finishes. No tool needs `tmux`
  (`--tools none`).
- Several bullets call `prepare-for-sandbox-pause`, which unloads every
  conversation of the run, so each bullet loads what it needs first. `N` is
  left requiring a credential binding by `F12.binding-native-cold` (a known
  bug), so later bullets do not use it.
- The bullets from `F12.runtime-launched-profile` on each launch and stop
  their own second server (a local one with `OH_RUNTIME_LAUNCHED_PROFILE`
  or the managed-key variables, or one with
  `OH_CONVERSATION_RUNTIME=docker`); their sinks and stubs are fixtures of
  the baseline run, so `sink read` needs no `--run`. Those bullets need no
  DeepSeek key: their models are `llm-stub` scripts, since a real provider
  cannot be told to reject a key once.

```sh
control-agent-server llm preset deepseek
NOPE=$(python3 -c 'import uuid; print(uuid.uuid4())')
ACP_AGENT='{"kind": "ACPAgent", "acp_command": ["codex-acp"], "acp_server": "codex"}'
SRC=$(control-agent-server fixture http-sink --name qa-f12-cred --status 200 --body '{"value": "{}", "version": "qa-v1"}' --print-path)
NEVER=$(control-agent-server fixture http-sink --name qa-f12-never --status 200 --body '{"value": "{}", "version": "qa-v1"}' --print-path)
HANG=$(control-agent-server fixture llm-stub --name qa-f12-hang --step hang:300 --print-path)
HANG_AGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$HANG/v1\", \"num_retries\": 0}, \"tools\": []}"
N=$(control-agent-server conversation start --tools none --no-autotitle --print-id)
R=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Reply with the single word ok, then finish.' --wait --until finished --timeout 180 --print-id)
control-agent-server api GET "/api/conversations/$R" --check execution_status eq finished
```

- **Discovery (`F12.discovery`).** Read the OpenAPI document and the
  capability flags.
  ```sh
  control-agent-server api GET /openapi.json --auth none --expect 200 --max-chars 200 \
    --check 'paths./api/conversations/{conversation_id}/runtime.get' exists \
    --check 'paths./api/conversations/{conversation_id}/runtime/reprovision.post' exists \
    --check 'paths./api/conversations/prepare-for-sandbox-pause' missing \
    --check 'paths./api/conversations/{conversation_id}/credential-bindings/{secret_name}' missing \
    --save F12.discovery/openapi
  control-agent-server api GET /server_info --auth none --expect 200 \
    --check capabilities contains credential_binding_v1 \
    --check capabilities contains credential_binding_readiness_probe_v1 \
    --check capabilities contains credential_binding_activation_guard_v1 --save F12.discovery/server-info
  ```
  The runtime routes are documented; the orchestrator routes are not, but
  the server advertises the binding contract through its capabilities.
- **Runtime info (`F12.runtime-info`).** Read the runtime of `N`, then the
  embedded copies.
  ```sh
  control-agent-server api GET "/api/conversations/$N/runtime" --expect 200 \
    --check runtime_status eq available --check can_resume eq true --check runtime_error eq null \
    --save F12.runtime-info/runtime
  control-agent-server api GET "/api/conversations/$N" --expect 200 \
    --check runtime_info.runtime_status eq available --check runtime_info.can_resume eq true \
    --save F12.runtime-info/conversation
  control-agent-server api GET /api/conversations/search --query limit=100 --expect 200 --quiet \
    --check items.0.runtime_info.runtime_status eq available
  control-agent-server api GET /api/conversations --query "ids=$N" --expect 200 \
    --check 0.runtime_info.runtime_status eq available --check 0.runtime_info.can_resume eq true
  ```
  Every view reports `available`, resumable, no error.
- **Reprovision (`F12.runtime-reprovision`).** Local mode has nothing to
  provision; the call re-reports and touches nothing.
  ```sh
  EN=$(control-agent-server api GET "/api/conversations/$N/events/count" --field .)
  control-agent-server state cat owner_lease.json --conversation "$N" --check generation eq 1
  control-agent-server api POST "/api/conversations/$N/runtime/reprovision" --expect 200 \
    --check runtime_status eq available --check can_resume eq true --check runtime_error eq null \
    --save F12.runtime-reprovision/reprovision
  control-agent-server api POST "/api/conversations/$N/runtime/reprovision" --json '{"qa": "ignored"}' --expect 200 \
    --check runtime_status eq available
  control-agent-server api GET "/api/conversations/$N" --check execution_status eq idle
  control-agent-server api GET "/api/conversations/$N/events/count" --check . eq "$EN"
  control-agent-server state cat owner_lease.json --conversation "$N" --check generation eq 1
  ```
  Both calls return the same object as `GET .../runtime`; the status, the
  event count and the lease generation are unchanged.
- **Runtime errors (`F12.runtime-errors`).** Unknown id, malformed id, no
  key.
  ```sh
  control-agent-server api GET "/api/conversations/$NOPE/runtime" --expect 404 --check detail eq 'Not Found' --save F12.runtime-errors/unknown
  control-agent-server api POST "/api/conversations/$NOPE/runtime/reprovision" --expect 404 --check detail eq 'Not Found' --save F12.runtime-errors/unknown-reprovision
  control-agent-server api GET /api/conversations/not-a-uuid/runtime --expect 422 --check detail.0.type eq uuid_parsing
  control-agent-server api POST /api/conversations/not-a-uuid/runtime/reprovision --expect 422 --check detail.0.type eq uuid_parsing
  control-agent-server api GET "/api/conversations/$N/runtime" --auth none --expect 401 --check detail eq Unauthorized
  control-agent-server api POST "/api/conversations/$N/runtime/reprovision" --auth none --expect 401 --check detail eq Unauthorized
  ```
  404 `Not Found`, 422 `uuid_parsing` and 401 `Unauthorized`, for both
  routes.
- **TypeScript client (`F12.ts-client`).** `ConversationClient` from the
  built client in `clients/typescript/dist` (built here when missing).
  ```sh
  test -f clients/typescript/dist/clients.js || (cd clients/typescript && npm ci && npm run build)
  F="$AGENT_SERVER_VERIFY_RUN/fixtures"
  cat > "$F/qa-f12-ts.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const { ConversationClient } = await import(pathToFileURL(resolve('clients/typescript/dist/clients.js')).href);
  const [cid, nope] = process.argv.slice(2);
  const host = process.env.AGENT_SERVER_URL;
  const client = new ConversationClient({ host, apiKey: process.env.SESSION_API_KEY });
  const available = { runtime_status: 'available', can_resume: true, runtime_error: null };
  assert.deepEqual(await client.getRuntime(cid), available);
  assert.deepEqual(await client.reprovisionRuntime(cid), available);
  assert.deepEqual((await client.getConversation(cid)).runtime_info, available);
  await assert.rejects(client.getRuntime(nope), (e) => e.status === 404);
  await assert.rejects(client.reprovisionRuntime(nope), (e) => e.status === 404);
  await assert.rejects(new ConversationClient({ host }).getRuntime(cid), (e) => e.status === 401);
  console.log('TS_RUNTIME_OK');
  JS
  control-agent-server exec --expect-output TS_RUNTIME_OK --save F12.ts-client/program -- node "$F/qa-f12-ts.mjs" "$N" "$NOPE"
  ```
  Every assertion holds: the same `available` object three ways, a 404
  `HttpError` for the unknown id from both methods, and 401 without a key.
- **Pause releases everything (`F12.sandbox-pause-releases`).** Both `N` and
  `R` are loaded and hold leases; a refused call first, then the real one.
  ```sh
  control-agent-server api GET "/api/conversations/$R/events/count" --expect 200
  control-agent-server state ls 'server/workspace/conversations/*/owner_lease.json' --expect-min 2
  ER=$(control-agent-server api GET "/api/conversations/$R/events/count" --field .)
  COUNT=$(control-agent-server api GET /api/conversations/count --field .)
  control-agent-server api POST /api/conversations/prepare-for-sandbox-pause --auth none --expect 401 --check detail eq Unauthorized
  control-agent-server state ls 'server/workspace/conversations/*/owner_lease.json' --expect-min 2
  control-agent-server api POST /api/conversations/prepare-for-sandbox-pause --expect 204 --save F12.sandbox-pause-releases/pause \
    | jq -e '.response.body == ""'
  control-agent-server state ls 'server/workspace/conversations/*/owner_lease.json' --expect-count 0
  control-agent-server api GET "/api/conversations/$N" --expect 200 --check execution_status eq idle
  control-agent-server api GET "/api/conversations/$R" --expect 200 --check execution_status eq finished --save F12.sandbox-pause-releases/after
  control-agent-server api GET /api/conversations/count --check . eq "$COUNT"
  control-agent-server api POST /api/conversations/prepare-for-sandbox-pause --expect 204
  ```
  The 401 leaves both leases in place; the real call returns 204 with no
  body, every lease file is gone, both conversations are still listed with
  their statuses (served from the catalog), and a second call is a no-op 204.
- **Runtime of an unloaded conversation (`F12.runtime-cold`).** Read the
  runtime while nothing is loaded, also after a restart.
  ```sh
  control-agent-server api GET "/api/conversations/$N/runtime" --expect 200 --check runtime_status eq available --save F12.runtime-cold/after-pause
  control-agent-server api POST "/api/conversations/$R/runtime/reprovision" --expect 200 --check runtime_status eq available
  control-agent-server state ls 'server/workspace/conversations/*/owner_lease.json' --expect-count 0
  control-agent-server restart
  control-agent-server api GET "/api/conversations/$R/runtime" --expect 200 --check runtime_status eq available --check can_resume eq true --save F12.runtime-cold/after-restart
  control-agent-server state ls 'server/workspace/conversations/*/owner_lease.json' --expect-count 0
  ```
  The runtime routes read the catalog: no lease file appears, before or after
  the restart.
- **Rehydrate after a pause (`F12.sandbox-pause-rehydrate`).** Load `R`,
  pause (the previous bullet's restart is not in between), read its events,
  then run it again on DeepSeek flash.
  ```sh
  ER=$(control-agent-server api GET "/api/conversations/$R/events/count" --field .)
  AR=$(control-agent-server conversation events "$R" --kinds ActionEvent,MessageEvent --contains '"source": "agent"' --expect-min 1 | jq -e '.matching')
  control-agent-server api POST /api/conversations/prepare-for-sandbox-pause --expect 204
  control-agent-server state ls --conversation "$R" owner_lease.json --expect-count 0
  control-agent-server api GET "/api/conversations/$R/events/count" --expect 200 --check . eq "$ER" --save F12.sandbox-pause-rehydrate/events
  control-agent-server state cat owner_lease.json --conversation "$R" --check generation eq 1 --check owner_instance_id exists
  control-agent-server conversation send "$R" --text 'Reply with the single word again, then finish.' --wait --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$R" --check execution_status eq finished --save F12.sandbox-pause-rehydrate/after-run
  control-agent-server api GET "/api/conversations/$R/events/count" --check . gt "$ER"
  control-agent-server conversation events "$R" --kinds ActionEvent,MessageEvent --contains '"source": "agent"' \
    | jq -e --argjson before "$AR" '.matching > $before'
  ```
  The pause removes `R`'s lease; the first event read loads it again (a new
  lease file) with the event log intact, and the conversation runs to
  `finished` again with at least one new agent action or message, so the
  model really ran on the reloaded conversation.
- **Pause a running conversation (`F12.sandbox-pause-running`).** `H`'s
  model call hangs, so the run is in flight when the pause arrives.
  ```sh
  H=$(control-agent-server conversation start --body-json "{\"agent\": $HANG_AGENT}" --no-autotitle --print-id)
  control-agent-server ws start "/sockets/events/$H" --name f12-running --duration 300
  control-agent-server ws read f12-running --expect-kind ConversationStateUpdateEvent --wait 15
  control-agent-server conversation send "$H" --text 'QA_F12_RUNNING'
  control-agent-server conversation wait "$H" --until running --timeout 30
  control-agent-server sink read --name qa-f12-hang --expect-min 1 --wait 15
  control-agent-server api POST /api/conversations/prepare-for-sandbox-pause --expect 204 --expect-max-ms 10000 --save F12.sandbox-pause-running/pause
  control-agent-server api GET "/api/conversations/$H" --check execution_status eq paused --save F12.sandbox-pause-running/after
  control-agent-server ws read f12-running --contains '"key": "execution_status", "value": "paused"' --wait 10
  control-agent-server ws stop f12-running --kinds PauseEvent,InterruptEvent --expect-min 2 --save F12.sandbox-pause-running/frames
  control-agent-server conversation events "$H" --kinds PauseEvent --expect-count 1
  control-agent-server conversation events "$H" --kinds InterruptEvent --expect-count 1
  ```
  The pause returns 204 well within 10 s although the model call would hang
  for 300 s; the conversation is `paused`, its log has one `PauseEvent` and
  one `InterruptEvent`, and the socket received both and the `paused` status.
- **Open sockets across a pause (`F12.sandbox-pause-websocket`), known bug.**
  An events socket and a session socket opened before the pause, a message
  posted after it, and fresh sockets of both kinds as the positive control.
  ```sh
  control-agent-server api GET "/api/conversations/$N/events/count" --expect 200
  control-agent-server ws start "/sockets/events/$N" --name f12-before --duration 300
  control-agent-server ws start "/sockets/session/$N" --name f12-before-session --duration 300
  control-agent-server ws read f12-before --expect-kind ConversationStateUpdateEvent --wait 15
  control-agent-server ws read f12-before-session --kinds sync --expect-min 1 --wait 15
  control-agent-server api POST /api/conversations/prepare-for-sandbox-pause --expect 204
  control-agent-server ws start "/sockets/events/$N" --name f12-after --duration 300
  control-agent-server ws start "/sockets/session/$N" --name f12-after-session --duration 300
  control-agent-server ws read f12-after --expect-kind ConversationStateUpdateEvent --wait 15
  control-agent-server ws read f12-after-session --kinds sync --expect-min 1 --wait 15
  control-agent-server conversation send "$N" --text 'QA_F12_AFTER_PAUSE' --no-run
  control-agent-server ws stop f12-after --contains QA_F12_AFTER_PAUSE --expect-min 1 --wait 15 --save F12.sandbox-pause-websocket/fresh-socket
  control-agent-server ws stop f12-after-session --contains QA_F12_AFTER_PAUSE --expect-min 1 --wait 15 --save F12.sandbox-pause-websocket/fresh-session-socket
  OLD=$(control-agent-server ws stop f12-before --wait 5 --save F12.sandbox-pause-websocket/old-socket)
  OLD_SESSION=$(control-agent-server ws stop f12-before-session --wait 5 --save F12.sandbox-pause-websocket/old-session-socket)
  printf '%s\n%s\n' "$OLD" "$OLD_SESSION" | jq -c '{close, kinds}'
  echo "$OLD" | jq -e '.close != null or ((.kinds.MessageEvent // 0) >= 1)'  # bug
  echo "$OLD_SESSION" | jq -e '.close != null or ((.kinds["durable:MessageEvent"] // 0) >= 1)'  # bug
  ```
  Correct behavior: a socket that was open before the pause is closed by
  the server (so the client reconnects) or keeps streaming. Today both stay
  open and silent: `close` is `null` and neither receives the
  `QA_F12_AFTER_PAUSE` message that the fresh sockets get (the events socket
  keeps its one initial `ConversationStateUpdateEvent`, the session socket
  its `sync` frame and initial state; still the same 20 s later). The pause
  closes the conversation's pub/sub without closing the socket subscribers
  (`_WebSocketSubscriber` and the session socket's `_SessionSubscriber`
  inherit the no-op `Subscriber.close()`), leaving the clients attached to a
  discarded `EventService`. Idle eviction avoids this by never evicting a
  conversation with an external subscriber; the pause has no such guard.
  Both captures are saved before the assertions, so the evidence holds both.
- **Unsupported secret names (`F12.binding-unsupported-secret`).** Only
  `CODEX_AUTH_JSON` is a file credential.
  ```sh
  control-agent-server api PUT "/api/conversations/$NOPE/credential-bindings/GITHUB_TOKEN" --json "{\"url\": \"$NEVER/cred\"}" \
    --expect 404 --check detail eq 'Not Found' --save F12.binding-unsupported-secret/github-token
  control-agent-server api PUT "/api/conversations/$NOPE/credential-bindings/codex_auth_json" --json "{\"url\": \"$NEVER/cred\"}" --expect 404
  control-agent-server sink read --name qa-f12-never --expect-max 0
  ```
  Both are 404 `Not Found` (the name is case-sensitive) and the source never
  saw a request.
- **Body validation (`F12.binding-validation`).** Malformed requests never
  reach the source.
  ```sh
  B="/api/conversations/$NOPE/credential-bindings/CODEX_AUTH_JSON"
  control-agent-server api PUT "$B" --json "{\"url\": \"$NEVER/cred\", \"qa\": 1}" --expect 422 --check detail.0.type eq extra_forbidden --save F12.binding-validation/extra-key
  control-agent-server api PUT "$B" --json '{"headers": {}}' --expect 422 --check detail.0.type eq missing
  control-agent-server api PUT "$B" --json '{"url": ""}' --expect 422 --check detail.0.type eq string_too_short
  control-agent-server api PUT "$B" --json "{\"url\": \"$NEVER/cred\", \"headers\": {\"X-Qa\": \"\"}}" --expect 422 \
    --check detail.0.msg contains 'Invalid credential binding headers' --save F12.binding-validation/empty-header
  H17=$(python3 -c 'import json; print(json.dumps({f"X-Qa-{i}": "v" for i in range(17)}))')
  control-agent-server api PUT "$B" --json "{\"url\": \"$NEVER/cred\", \"headers\": $H17}" --expect 422 --check detail.0.msg contains 'Invalid credential binding headers'
  LONG_VALUE=$(python3 -c 'print("v" * 8193)')
  control-agent-server api PUT "$B" --json "{\"url\": \"$NEVER/cred\", \"headers\": {\"X-Qa\": \"$LONG_VALUE\"}}" --expect 422 --quiet \
    --check detail.0.msg contains 'Invalid credential binding headers'
  LONG_URL="$NEVER/$(python3 -c 'print("x" * 4096)')"
  control-agent-server api PUT "$B" --json "{\"url\": \"$LONG_URL\"}" --expect 422 --quiet --check detail.0.type eq string_too_long
  control-agent-server api PUT "/api/conversations/$NOPE/credential-bindings/GITHUB_TOKEN" --json '{}' --expect 422 --check detail.0.type eq missing
  control-agent-server api PUT /api/conversations/not-a-uuid/credential-bindings/CODEX_AUTH_JSON --json "{\"url\": \"$NEVER/cred\"}" --expect 422 --check detail.0.type eq uuid_parsing
  control-agent-server api PUT "$B" --auth none --json "{\"url\": \"$NEVER/cred\"}" --expect 401 --check detail eq Unauthorized
  control-agent-server sink read --name qa-f12-never --expect-max 0
  ```
  Each malformed body or id is a 422 with the pydantic error type (a bad
  body under the unsupported `GITHUB_TOKEN` name is 422, not 404), no key is
  401, and the source still has no request.
- **Pending binding for an unknown id (`F12.binding-pending`).** Activation
  may precede the conversation.
  ```sh
  U1=$(python3 -c 'import uuid; print(uuid.uuid4())')
  control-agent-server api PUT "/api/conversations/$U1/credential-bindings/CODEX_AUTH_JSON" \
    --json "{\"url\": \"$SRC/cred\", \"headers\": {\"Authorization\": \"Bearer qa-f12-pending\"}}" --expect 204 --save F12.binding-pending/put \
    | jq -e '.response.body == ""'
  control-agent-server sink read --name qa-f12-cred --contains 'Bearer qa-f12-pending' --expect-min 1 --expect-max 1 --save F12.binding-pending/probe
  control-agent-server sink read --name qa-f12-cred --path /cred --contains '"method": "GET"' --expect-min 1 --expect-max 1
  control-agent-server sink read --name qa-f12-cred --expect-max 1
  control-agent-server api GET "/api/conversations/$U1" --expect 404
  ```
  204 with an empty body after one `GET /cred` carrying
  `Authorization: Bearer qa-f12-pending`; the conversation still does not
  exist.
- **Source answers (`F12.binding-source-statuses`).** One sink per reply,
  since a sink answers every request the same way.
  ```sh
  B="/api/conversations/$NOPE/credential-bindings/CODEX_AUTH_JSON"
  S401=$(control-agent-server fixture http-sink --name qa-f12-src-401 --status 401 --body '{}' --print-path)
  S403=$(control-agent-server fixture http-sink --name qa-f12-src-403 --status 403 --body '{}' --print-path)
  S400=$(control-agent-server fixture http-sink --name qa-f12-src-400 --status 400 --body '{}' --print-path)
  S404=$(control-agent-server fixture http-sink --name qa-f12-src-404 --status 404 --body '{}' --print-path)
  S422=$(control-agent-server fixture http-sink --name qa-f12-src-422 --status 422 --body '{}' --print-path)
  S409=$(control-agent-server fixture http-sink --name qa-f12-src-409 --status 409 --body '{}' --print-path)
  S501=$(control-agent-server fixture http-sink --name qa-f12-src-501 --status 501 --body '{}' --print-path)
  SNOTJSON=$(control-agent-server fixture http-sink --name qa-f12-src-notjson --status 200 --body 'not-json' --print-path)
  SNOVERSION=$(control-agent-server fixture http-sink --name qa-f12-src-noversion --status 200 --body '{"value": "{}"}' --print-path)
  SEMPTY=$(control-agent-server fixture http-sink --name qa-f12-src-emptyversion --status 200 --body '{"value": "{}", "version": ""}' --print-path)
  SINT=$(control-agent-server fixture http-sink --name qa-f12-src-intvalue --status 200 --body '{"value": 1, "version": "v1"}' --print-path)
  control-agent-server api PUT "$B" --json "{\"url\": \"$S401/cred\"}" --expect 403 --check detail eq 'Credential binding authorization was rejected' --save F12.binding-source-statuses/source-401
  control-agent-server api PUT "$B" --json "{\"url\": \"$S403/cred\"}" --expect 403 --check detail eq 'Credential binding authorization was rejected' --save F12.binding-source-statuses/source-403
  control-agent-server api PUT "$B" --json "{\"url\": \"$S400/cred\"}" --expect 422 --check detail eq 'Credential binding source is invalid' --save F12.binding-source-statuses/source-400
  control-agent-server api PUT "$B" --json "{\"url\": \"$S404/cred\"}" --expect 422 --check detail eq 'Credential binding source is invalid' --save F12.binding-source-statuses/source-404
  control-agent-server api PUT "$B" --json "{\"url\": \"$S422/cred\"}" --expect 422 --check detail eq 'Credential binding source is invalid' --save F12.binding-source-statuses/source-422
  control-agent-server api PUT "$B" --json "{\"url\": \"$S409/cred\"}" --expect 409 --check detail eq 'Credential binding source changed' --save F12.binding-source-statuses/source-409
  control-agent-server api PUT "$B" --json "{\"url\": \"$S501/cred\"}" --expect 501 --check exception eq '501: Credential binding source is unsupported' --save F12.binding-source-statuses/source-501
  control-agent-server api PUT "$B" --json "{\"url\": \"$SNOTJSON/cred\"}" --expect 422 --check detail eq 'Credential binding source is invalid' --save F12.binding-source-statuses/source-not-json
  control-agent-server api PUT "$B" --json "{\"url\": \"$SNOVERSION/cred\"}" --expect 422 --check detail eq 'Credential binding source is invalid' --save F12.binding-source-statuses/source-no-version
  control-agent-server api PUT "$B" --json "{\"url\": \"$SEMPTY/cred\"}" --expect 422 --check detail eq 'Credential binding source is invalid'
  control-agent-server api PUT "$B" --json "{\"url\": \"$SINT/cred\"}" --expect 422 --check detail eq 'Credential binding source is invalid'
  for name in 401 403 400 404 422 409 501 notjson noversion emptyversion intvalue; do
    control-agent-server sink read --name "qa-f12-src-$name" --path /cred --expect-min 1 --expect-max 1
  done
  control-agent-server api PUT "$B" --json '{"url": "ftp://127.0.0.1/cred"}' --expect 422 --check detail eq 'Credential binding source is invalid' --save F12.binding-source-statuses/ftp-url
  control-agent-server api PUT "$B" --json '{"url": "not a url"}' --expect 422 --check detail eq 'Credential binding source is invalid'
  ```
  Every source status maps as listed, each after exactly one probe (no
  retry for a definite answer); a 501 is reported as
  `{"detail": "Internal Server Error", "exception": "501: ..."}` like every
  5xx; malformed 200 bodies and non-http(s) URLs are 422.
- **Unavailable source (`F12.binding-source-unavailable`).** A 503 source,
  then a closed port.
  ```sh
  S=$(control-agent-server fixture http-sink --name qa-f12-src-503 --status 503 --body '{}' --print-path)
  control-agent-server api PUT "/api/conversations/$NOPE/credential-bindings/CODEX_AUTH_JSON" --json "{\"url\": \"$S/cred\"}" \
    --expect 502 --check exception eq '502: Credential binding source is unavailable' --expect-max-ms 5000 \
    --save F12.binding-source-unavailable/source-503 | jq -e '.elapsed_ms >= 750'
  control-agent-server sink read --name qa-f12-src-503 --expect-min 3 --expect-max 3 --save F12.binding-source-unavailable/probes
  control-agent-server api PUT "/api/conversations/$NOPE/credential-bindings/CODEX_AUTH_JSON" --json '{"url": "http://127.0.0.1:9/cred"}' \
    --expect 502 --check exception eq '502: Credential binding source is unavailable' --expect-max-ms 5000 \
    --save F12.binding-source-unavailable/closed-port | jq -e '.elapsed_ms >= 750'
  ```
  Three probes (sleeping 0.25 s, then 0.5 s, between them), then 502 within
  5 s; a refused connection is retried the same way (it also takes at least
  0.75 s).
- **Regular conversation, loaded (`F12.binding-native-too-late`).** Load `N`,
  then try to bind it.
  ```sh
  control-agent-server api GET "/api/conversations/$N/events/count" --expect 200
  control-agent-server state ls --conversation "$N" owner_lease.json --expect-count 1
  control-agent-server api PUT "/api/conversations/$N/credential-bindings/CODEX_AUTH_JSON" --json "{\"url\": \"$SRC/cred\"}" \
    --expect 409 --check detail eq 'Credential binding activation arrived after ACP initialization' --save F12.binding-native-too-late/put
  control-agent-server state cat meta.json --conversation "$N" --check required_runtime_credential_bindings len-eq 0
  ```
  409 and no recorded requirement.
- **Regular conversation, not loaded (`F12.binding-native-cold`), known bug.**
  The same binding sent while `N` is unloaded, then a load and a pause.
  ```sh
  control-agent-server api POST /api/conversations/prepare-for-sandbox-pause --expect 204
  control-agent-server api PUT "/api/conversations/$N/credential-bindings/CODEX_AUTH_JSON" --json "{\"url\": \"$SRC/cred\"}" --expect 204,409 \
    --save F12.binding-native-cold/put
  control-agent-server api GET "/api/conversations/$N/events/count" --expect 200
  control-agent-server api POST /api/conversations/prepare-for-sandbox-pause --expect 204
  control-agent-server api GET "/api/conversations/$N/events/count" --expect 200 --save F12.binding-native-cold/events-after-pause  # bug
  control-agent-server state cat meta.json --conversation "$N" --check required_runtime_credential_bindings len-eq 0  # bug
  ```
  Correct behavior: a regular conversation either refuses the binding (409,
  as it does when loaded) or ignores it, and stays readable after the next
  pause. Today the unloaded PUT is accepted as pending (204), consumed when
  `N` loads, and written to `meta.json` as
  `required_runtime_credential_bindings: ["CODEX_AUTH_JSON"]`: after the
  pause every event read is 409 `credential_binding_activation_required` and
  the sockets close with 1013, for a conversation that never uses Codex.
  The same happens when a regular conversation is created with an id that
  has a pending binding, and then its own `CODEX_AUTH_JSON` secret is also
  dropped from `meta.json` (`secrets: {}`), although the create path strips
  that secret only for Codex sources. `_resolve_credential_bindings` filters
  pending bindings by profile only, not by agent kind, and
  `EventService.start` records every HTTP binding as required. Low severity:
  only an orchestrator that binds conversations without knowing their agent
  kind hits it.
- **Bind a loaded ACP Codex conversation (`F12.binding-live-acp`).** `A` has
  never run, so its ACP agent is not initialized yet.
  ```sh
  A=$(control-agent-server conversation start --body-json "{\"agent\": $ACP_AGENT}" --secret CODEX_AUTH_JSON=qa-f12-request-copy --no-autotitle --print-id)
  control-agent-server api GET "/api/conversations/$A" --check agent.kind eq ACPAgent --check agent.acp_server eq codex
  control-agent-server state cat meta.json --conversation "$A" --check secrets.CODEX_AUTH_JSON exists --check required_runtime_credential_bindings len-eq 0
  control-agent-server api PUT "/api/conversations/$A/credential-bindings/CODEX_AUTH_JSON" \
    --json "{\"url\": \"$SRC/cred\", \"headers\": {\"Authorization\": \"Bearer qa-f12-first\"}}" --expect 204 --save F12.binding-live-acp/put
  control-agent-server sink read --name qa-f12-cred --contains 'Bearer qa-f12-first' --expect-min 1 --expect-max 1
  control-agent-server state cat meta.json --conversation "$A" \
    --check required_runtime_credential_bindings eq '["CODEX_AUTH_JSON"]' --check secrets.CODEX_AUTH_JSON missing
  control-agent-server state grep qa-f12-request-copy --glob 'server/**/*' --expect-none
  control-agent-server state grep qa-f12-request-copy --glob 'home/**/*' --expect-none
  control-agent-server api GET "/api/conversations/$A/events/count" --expect 200
  ```
  204; `meta.json` now records the requirement and no longer holds the
  conversation's own `CODEX_AUTH_JSON` secret (stored encrypted before, so
  the plaintext greps hold either way; the `secrets` check is the proof of
  the scrub), and the conversation stays readable.
- **Re-bind (`F12.binding-rebind`).** Same URL with new headers, then a
  different URL.
  ```sh
  control-agent-server api PUT "/api/conversations/$A/credential-bindings/CODEX_AUTH_JSON" \
    --json "{\"url\": \"$SRC/cred\", \"headers\": {\"Authorization\": \"Bearer qa-f12-second\"}}" --expect 204 --save F12.binding-rebind/same-url
  control-agent-server sink read --name qa-f12-cred --contains 'Bearer qa-f12-second' --expect-min 1 --expect-max 1
  control-agent-server api PUT "/api/conversations/$A/credential-bindings/CODEX_AUTH_JSON" --json "{\"url\": \"$SRC/other\"}" \
    --expect 409 --check detail eq 'Credential binding activation arrived after ACP initialization' --save F12.binding-rebind/other-url
  control-agent-server state grep qa-f12-second --glob 'fixtures/**/*'
  control-agent-server state grep qa-f12-second --glob 'server/**/*' --expect-none
  control-agent-server state grep qa-f12-second --glob 'home/**/*' --expect-none
  control-agent-server state grep qa-f12-first --glob 'server/**/*' --expect-none
  control-agent-server state grep qa-f12-first --glob 'home/**/*' --expect-none
  control-agent-server logs --grep "$A" --expect-min 1
  control-agent-server logs --grep 'qa-f12-first|qa-f12-second' --expect-none
  ```
  The same URL re-authorizes the live binding (204, probed with the new
  header); another URL is 409 with the generic "too late" message; no header
  value is written to the server's state, its `HOME` or its log (which does
  name the conversation).
- **Required after a pause (`F12.binding-required-after-pause`).** Pause,
  then every way in.
  ```sh
  control-agent-server api POST /api/conversations/prepare-for-sandbox-pause --expect 204
  control-agent-server api GET "/api/conversations/$A" --expect 200 --check id eq "$A"
  control-agent-server api GET "/api/conversations/$A/runtime" --expect 200 --check runtime_status eq available
  control-agent-server api GET "/api/conversations/$A/events/search" --expect 409 \
    --check detail eq credential_binding_activation_required --check retryable eq true --save F12.binding-required-after-pause/events
  control-agent-server api POST "/api/conversations/$A/run" --expect 409 --check detail eq credential_binding_activation_required
  control-agent-server ws listen "/sockets/events/$A" --expect-close 1013 --duration 10 --save F12.binding-required-after-pause/events-socket \
    | jq -e '.close.reason == "credential_binding_activation_required"'
  control-agent-server ws listen "/sockets/session/$A" --query after_seq=-1 --expect-close 1013 --duration 10
  control-agent-server state ls --conversation "$A" owner_lease.json --expect-count 0
  control-agent-server api PUT "/api/conversations/$A/credential-bindings/CODEX_AUTH_JSON" --json "{\"url\": \"$SRC/cred\"}" --expect 204 --save F12.binding-required-after-pause/reactivate
  control-agent-server api GET "/api/conversations/$A/events/search" --expect 200 --save F12.binding-required-after-pause/events-after
  control-agent-server state ls --conversation "$A" owner_lease.json --expect-count 1
  ```
  The catalog and runtime views still answer, but loading is refused with a
  retryable 409 and both sockets close with 1013 (no lease is taken); after a
  new activation the conversation loads.
- **Required after a restart (`F12.binding-required-after-restart`).** The
  requirement is in `meta.json`, so it survives; deletion is exempt.
  ```sh
  control-agent-server restart
  control-agent-server api GET "/api/conversations/$A/events/count" --expect 409 --check retryable eq true --save F12.binding-required-after-restart/events
  control-agent-server api PUT "/api/conversations/$A/credential-bindings/CODEX_AUTH_JSON" --json "{\"url\": \"$SRC/cred\"}" --expect 204
  control-agent-server api GET "/api/conversations/$A/events/count" --expect 200 --save F12.binding-required-after-restart/events-after
  control-agent-server state cat meta.json --conversation "$A" --check required_runtime_credential_bindings eq '["CODEX_AUTH_JSON"]'
  control-agent-server api POST /api/conversations/prepare-for-sandbox-pause --expect 204
  control-agent-server api GET "/api/conversations/$A/events/count" --expect 409
  control-agent-server api DELETE "/api/conversations/$A" --expect 200 --save F12.binding-required-after-restart/delete
  control-agent-server api GET "/api/conversations/$A" --expect 404
  control-agent-server state ls --conversation "$A" meta.json --expect-count 0
  ```
  409 after the restart, 200 after re-activation, and the requirement stays
  recorded. A conversation that needs a binding can still be deleted without
  one (its directory is removed).
- **Pending binding consumed at create (`F12.binding-pending-on-create`).**
  Bind first, then create the conversation with that id.
  ```sh
  U2=$(python3 -c 'import uuid; print(uuid.uuid4())')
  control-agent-server api PUT "/api/conversations/$U2/credential-bindings/CODEX_AUTH_JSON" --json "{\"url\": \"$SRC/cred\"}" --expect 204
  C2=$(control-agent-server conversation start --body-json "{\"conversation_id\": \"$U2\", \"agent\": $ACP_AGENT}" --secret CODEX_AUTH_JSON=qa-f12-create-copy --no-autotitle --print-id)
  test "$C2" = "$U2"
  control-agent-server state cat meta.json --conversation "$U2" \
    --check required_runtime_credential_bindings eq '["CODEX_AUTH_JSON"]' --check secrets.CODEX_AUTH_JSON missing
  control-agent-server state grep qa-f12-create-copy --glob 'server/**/*' --expect-none
  control-agent-server state grep qa-f12-create-copy --glob 'home/**/*' --expect-none
  ```
  The conversation is born bound: the requirement is recorded and the
  request's own `CODEX_AUTH_JSON` secret is not stored.
- **A pause drops pending bindings (`F12.binding-pending-cleared`).** Bind,
  pause, then create.
  ```sh
  U3=$(python3 -c 'import uuid; print(uuid.uuid4())')
  control-agent-server api PUT "/api/conversations/$U3/credential-bindings/CODEX_AUTH_JSON" --json "{\"url\": \"$SRC/cred\"}" --expect 204
  control-agent-server api POST /api/conversations/prepare-for-sandbox-pause --expect 204
  C3=$(control-agent-server conversation start --body-json "{\"conversation_id\": \"$U3\", \"agent\": $ACP_AGENT}" --secret CODEX_AUTH_JSON=qa-f12-kept-copy --no-autotitle --print-id)
  test "$C3" = "$U3"
  control-agent-server state cat meta.json --conversation "$U3" \
    --check required_runtime_credential_bindings len-eq 0 --check secrets.CODEX_AUTH_JSON exists
  ```
  Compare with `F12.binding-pending-on-create`: the pending binding is gone,
  so the conversation keeps its own (encrypted) secret and needs no binding.
- **Profile excludes the secret (`F12.binding-profile-scope`).** An ACP Codex
  agent profile with `secret_refs: []`.
  ```sh
  control-agent-server api POST /api/agent-profiles/qa-f12-noscope --json '{"agent_kind": "acp", "acp_server": "codex", "secret_refs": []}' --expect 201
  P=$(control-agent-server conversation start --agent-profile qa-f12-noscope --no-autotitle --print-id)
  control-agent-server state cat meta.json --conversation "$P" --check launched_agent_profile.secret_refs len-eq 0
  control-agent-server api PUT "/api/conversations/$P/credential-bindings/CODEX_AUTH_JSON" --json "{\"url\": \"$SRC/cred\"}" \
    --expect 403 --check detail eq 'Credential binding authorization was rejected' --save F12.binding-profile-scope/put
  control-agent-server state cat meta.json --conversation "$P" --check required_runtime_credential_bindings len-eq 0
  control-agent-server api DELETE /api/agent-profiles/qa-f12-noscope --expect 200
  ```
  403 (after the source was probed) and nothing recorded; the profile is
  removed afterwards.
- **Launched profile from the runtime (`F12.runtime-launched-profile`).** A
  second, local server started with `OH_RUNTIME_LAUNCHED_PROFILE`, as a
  Docker parent starts each conversation container. Translated: the
  variable is read by any server at create, so no container is needed.
  ```sh
  LPID=$(python3 -c 'import uuid; print(uuid.uuid4())')
  LSRC=$(control-agent-server fixture http-sink --name qa-f12-lp-cred --status 200 --body '{"value": "{}", "version": "qa-v1"}' --print-path)
  LP=$(control-agent-server launch --new --name f12-launched-profile --print-run \
    --env "OH_RUNTIME_LAUNCHED_PROFILE={\"agent_profile_id\": \"$LPID\", \"revision\": 7, \"secret_refs\": [\"QA_F12_ALLOWED\"]}")
  trap 'control-agent-server stop --run "$LP" >/dev/null 2>&1 || true' EXIT
  L=$(control-agent-server conversation start --run "$LP" --body-json "{\"agent\": $ACP_AGENT}" --no-autotitle --print-id)
  control-agent-server api GET "/api/conversations/$L" --run "$LP" --check launched_agent_profile.agent_profile_id eq "$LPID" \
    --check launched_agent_profile.revision eq 7 --check launched_agent_profile.secret_refs eq '["QA_F12_ALLOWED"]' \
    --save F12.runtime-launched-profile/conversation
  control-agent-server state cat meta.json --run "$LP" --conversation "$L" --check launched_agent_profile.agent_profile_id eq "$LPID"
  control-agent-server api POST /api/agent-profiles/qa-f12-own --run "$LP" --json '{"agent_kind": "acp", "acp_server": "codex", "secret_refs": ["CODEX_AUTH_JSON"]}' --expect 201
  O=$(control-agent-server conversation start --run "$LP" --agent-profile qa-f12-own --no-autotitle --print-id)
  control-agent-server api GET "/api/conversations/$O" --run "$LP" --check launched_agent_profile.agent_profile_id ne "$LPID" \
    --check launched_agent_profile.secret_refs eq '["CODEX_AUTH_JSON"]'
  control-agent-server api PUT "/api/conversations/$O/credential-bindings/CODEX_AUTH_JSON" --run "$LP" --json "{\"url\": \"$LSRC/cred\"}" --expect 204
  control-agent-server api PUT "/api/conversations/$L/credential-bindings/CODEX_AUTH_JSON" --run "$LP" --json "{\"url\": \"$LSRC/cred\"}" \
    --expect 403 --check detail eq 'Credential binding authorization was rejected' --save F12.runtime-launched-profile/binding
  control-agent-server state cat meta.json --run "$LP" --conversation "$L" --check required_runtime_credential_bindings len-eq 0
  control-agent-server stop --run "$LP"
  ```
  The profile-less conversation records the runtime's profile (id, revision
  7, `secret_refs`), so the binding is refused with 403 and nothing is
  recorded; the conversation started from its own agent profile records
  that profile and accepts the binding (204). Secrets in the create request
  itself are not filtered here: in Docker mode the parent scopes them before
  it forwards the request (`forward_to_runtime`).
- **Managed key refresh on a 401 (`F12.managed-key-refresh`).** A second
  server with the refresh variables; the stub plays a managed proxy that
  rejects the stored key once (`status:401`) and then answers.
  ```sh
  FINISH_STEP='tool:finish:{"message": "done"}'
  KSRC=$(control-agent-server fixture http-sink --name qa-f12-keysrc --status 200 --body 'qa-f12-fresh-key' --print-path)
  KSTUB=$(control-agent-server fixture llm-stub --name qa-f12-keystub --step status:401 --step "$FINISH_STEP" --print-path)
  K=$(control-agent-server launch --new --name f12-keyrefresh --print-run \
    --env "OH_LLM_API_KEY_REFRESH_URL=$KSRC/key" --env "OH_LLM_API_KEY_REFRESH_BASE_URLS=$KSTUB/v1" \
    --env 'OH_LLM_API_KEY_REFRESH_HEADERS={"X-QA": "${QA_F12_REFRESH_TOKEN}"}' --env QA_F12_REFRESH_TOKEN=qa-f12-refresh-hdr)
  trap 'control-agent-server stop --run "$K" >/dev/null 2>&1 || true' EXIT
  KAGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-f12-stale-key\", \"base_url\": \"$KSTUB/v1\", \"num_retries\": 0}, \"tools\": []}"
  KC=$(control-agent-server conversation start --run "$K" --body-json "{\"agent\": $KAGENT}" --no-autotitle --prompt 'QA_F12_KEY' \
    --wait --until finished --timeout 60 --print-id)
  control-agent-server api GET "/api/conversations/$KC" --run "$K" --check execution_status eq finished --save F12.managed-key-refresh/after
  control-agent-server sink read --name qa-f12-keysrc --path /key --contains '"X-QA": "qa-f12-refresh-hdr"' --expect-min 1 --expect-max 1 \
    --save F12.managed-key-refresh/key-fetch
  control-agent-server sink read --name qa-f12-keysrc --expect-max 1
  control-agent-server sink read --name qa-f12-keystub --contains '"call": 0, "step": {"status": 401}' --expect-min 1 --expect-max 1
  control-agent-server sink read --name qa-f12-keystub --expect-min 2 --expect-max 2
  control-agent-server logs --run "$K" --grep 'Authentication error; refreshing' --expect-min 1
  control-agent-server state grep qa-f12-fresh-key --run "$K" --glob 'server/**/*' --expect-none
  control-agent-server state grep qa-f12-fresh-key --run "$K" --glob 'home/**/*' --expect-none
  control-agent-server stop --run "$K"
  ```
  The first model call gets 401; the server fetches the key once (`GET
  /key` carrying `X-QA: qa-f12-refresh-hdr`, expanded from
  `${QA_F12_REFRESH_TOKEN}` in its environment), retries the call once and
  the run finishes; the fetched key is not written to the server's state or
  `HOME`. The stub does not record request headers, so that the retry
  carried the fetched key is proven by the SDK's contract (it retries only
  with a key that differs from the stored one), not observed.
- **Refresh scope (`F12.managed-key-scope`).** The same setup with a second
  stub outside the allow-list, then an empty allow-list.
  ```sh
  FINISH_STEP='tool:finish:{"message": "done"}'
  SSRC=$(control-agent-server fixture http-sink --name qa-f12-keysrc-scope --status 200 --body 'qa-f12-fresh-key' --print-path)
  MSTUB=$(control-agent-server fixture llm-stub --name qa-f12-managed --step status:401 --step "$FINISH_STEP" \
    --step status:401 --step "$FINISH_STEP" --print-path)
  BYOK=$(control-agent-server fixture llm-stub --name qa-f12-byok --step status:401 --step "$FINISH_STEP" --print-path)
  K=$(control-agent-server launch --new --name f12-keyscope --print-run \
    --env "OH_LLM_API_KEY_REFRESH_URL=$SSRC/key" --env "OH_LLM_API_KEY_REFRESH_BASE_URLS=$MSTUB/v1")
  trap 'control-agent-server stop --run "$K" >/dev/null 2>&1 || true' EXIT
  MAGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-f12-stale-key\", \"base_url\": \"$MSTUB/v1\", \"num_retries\": 0}, \"tools\": []}"
  BAGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-f12-byok-key\", \"base_url\": \"$BYOK/v1\", \"num_retries\": 0}, \"tools\": []}"
  control-agent-server conversation start --run "$K" --body-json "{\"agent\": $MAGENT}" --no-autotitle --prompt 'QA_F12_MANAGED' \
    --wait --until finished --timeout 60
  control-agent-server sink read --name qa-f12-keysrc-scope --expect-min 1 --expect-max 1
  BC=$(control-agent-server conversation start --run "$K" --body-json "{\"agent\": $BAGENT}" --no-autotitle --prompt 'QA_F12_BYOK' \
    --wait --until error --timeout 60 --print-id)
  control-agent-server api GET "/api/conversations/$BC" --run "$K" --check execution_status eq error --save F12.managed-key-scope/byok
  control-agent-server conversation events "$BC" --run "$K" --kinds ConversationErrorEvent --contains LLMAuthenticationError --expect-count 1
  control-agent-server sink read --name qa-f12-byok --expect-min 1 --expect-max 1
  control-agent-server sink read --name qa-f12-keysrc-scope --expect-max 1
  control-agent-server restart --run "$K" --env 'OH_LLM_API_KEY_REFRESH_BASE_URLS='
  EC=$(control-agent-server conversation start --run "$K" --body-json "{\"agent\": $MAGENT}" --no-autotitle --prompt 'QA_F12_EMPTY' \
    --wait --until error --timeout 60 --print-id)
  control-agent-server conversation events "$EC" --run "$K" --kinds ConversationErrorEvent --contains LLMAuthenticationError --expect-count 1
  control-agent-server sink read --name qa-f12-managed --expect-min 3 --expect-max 3
  control-agent-server sink read --name qa-f12-keysrc-scope --expect-max 1 --save F12.managed-key-scope/no-fetch
  control-agent-server logs --run "$K" --grep 'stays off' --expect-min 1
  control-agent-server stop --run "$K"
  ```
  The in-scope LLM refreshes (the positive control: one fetch, finished).
  The LLM on the other `base_url` ends in `error` with
  `LLMAuthenticationError` after one model call and no fetch, although its
  stub would answer a retry. After the restart with an empty allow-list the
  same managed `base_url` gets no hook (fail closed, logged as "managed LLM
  key refresh stays off"): one more 401, no fetch, `error`.
- **Refresh after a model switch (`F12.managed-key-switch`), known bug.** An
  in-scope conversation that refreshes once, then `switch_llm` to another
  LLM on the same managed `base_url`.
  ```sh
  FINISH_STEP='tool:finish:{"message": "done"}'
  WSRC=$(control-agent-server fixture http-sink --name qa-f12-keysrc-switch --status 200 --body 'qa-f12-fresh-key' --print-path)
  WSTUB=$(control-agent-server fixture llm-stub --name qa-f12-switch --step status:401 --step "$FINISH_STEP" \
    --step status:401 --step "$FINISH_STEP" --print-path)
  K=$(control-agent-server launch --new --name f12-keyswitch --print-run \
    --env "OH_LLM_API_KEY_REFRESH_URL=$WSRC/key" --env "OH_LLM_API_KEY_REFRESH_BASE_URLS=$WSTUB/v1")
  trap 'control-agent-server stop --run "$K" >/dev/null 2>&1 || true' EXIT
  WAGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-f12-stale-key\", \"base_url\": \"$WSTUB/v1\", \"num_retries\": 0}, \"tools\": []}"
  WC=$(control-agent-server conversation start --run "$K" --body-json "{\"agent\": $WAGENT}" --no-autotitle --prompt 'QA_F12_SWITCH_1' \
    --wait --until finished --timeout 60 --print-id)
  control-agent-server sink read --name qa-f12-keysrc-switch --expect-min 1 --expect-max 1
  control-agent-server api POST "/api/conversations/$WC/switch_llm" --run "$K" \
    --json "{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-f12-switched-key\", \"base_url\": \"$WSTUB/v1\", \"num_retries\": 0, \"usage_id\": \"qa-f12-switched\"}}" \
    --expect 200 --save F12.managed-key-switch/switch
  control-agent-server api GET "/api/conversations/$WC" --run "$K" --check agent.llm.usage_id eq qa-f12-switched --check agent.llm.base_url eq "$WSTUB/v1"
  control-agent-server conversation send "$WC" --run "$K" --text 'QA_F12_SWITCH_2'
  control-agent-server sink read --name qa-f12-switch --expect-min 3 --wait 30
  control-agent-server conversation wait "$WC" --run "$K" --until finished,error --timeout 60
  control-agent-server sink read --name qa-f12-keysrc-switch --expect-min 2 --expect-max 2 --save F12.managed-key-switch/key-fetch  # bug
  control-agent-server api GET "/api/conversations/$WC" --run "$K" --check execution_status eq finished  # bug
  control-agent-server stop --run "$K"
  ```
  Correct behavior: the switched LLM sits on an allow-listed `base_url`, so
  its 401 is refreshed like the first one (a second fetch, then
  `finished`). Today the first run refreshes (the positive control), but
  after `switch_llm` the 401 surfaces: no second fetch, `error` with
  `LLMAuthenticationError`, and the stub saw no retry.
  `register_managed_llm_key_refresh` runs only in `EventService.start`, and
  `LocalConversation.switch_llm` (which `switch_profile` also uses) installs
  a new LLM without the hook. A restart reloads the conversation and
  registers the hook on the switched LLM, after which the same 401 is
  refreshed and the run finishes (seen while mapping), so the hook is lost
  only until the next load. Low severity: it reproduces the "first call
  fails once" symptom (#5189) the hook exists for, after any model switch.
- **Docker runtime mode without a daemon (`F12.docker-runtime-contract`).**
  A second server with `OH_CONVERSATION_RUNTIME=docker`. Translated: no
  Docker daemon is needed for the router's own answers (startup only warns
  that it cannot list containers); everything that needs a running
  container is `F12.docker-runtime`. `DELETE .../runtime` and
  `POST .../runtime/credentials` exist only in this mode.
  ```sh
  DK=$(control-agent-server launch --new --name f12-docker --env OH_CONVERSATION_RUNTIME=docker --print-run)
  trap 'control-agent-server stop --run "$DK" >/dev/null 2>&1 || true' EXIT
  control-agent-server api GET /server_info --run "$DK" --auth none --expect 200 --check conversation_runtime eq docker
  Z=$(python3 -c 'import uuid; print(uuid.uuid4())')
  U=$(python3 -c 'import uuid; print(uuid.uuid4())')
  DOCKER_501='501: This operation is unavailable in Docker runtime mode'
  control-agent-server api POST "/api/conversations/$Z/fork" --run "$DK" --json '{}' --expect 501 --check exception eq "$DOCKER_501" \
    --save F12.docker-runtime-contract/fork
  control-agent-server api POST "/api/conversations/$Z/switch_llm" --run "$DK" --json '{}' --expect 501 --check exception eq "$DOCKER_501"
  control-agent-server api POST "/api/conversations/$Z/switch_profile" --run "$DK" --json '{}' --expect 501 --check exception eq "$DOCKER_501"
  control-agent-server api PUT "/api/conversations/$Z/credential-bindings/CODEX_AUTH_JSON" --run "$DK" --json "{\"url\": \"$NEVER/cred\"}" \
    --expect 501 --check exception eq "$DOCKER_501" --save F12.docker-runtime-contract/credential-binding
  control-agent-server api PUT "/api/conversations/$Z/credential-bindings/GITHUB_TOKEN" --run "$DK" --json '{}' --expect 501
  control-agent-server sink read --name qa-f12-never --expect-max 0
  control-agent-server api POST "/api/conversations/$Z/run" --run "$DK" --expect 404 --check detail eq 'Conversation not found'
  control-agent-server api GET "/api/conversations/$Z/runtime" --run "$DK" --expect 404 --check detail eq 'Conversation not found' \
    --save F12.docker-runtime-contract/runtime-unknown
  control-agent-server api POST "/api/conversations/$Z/runtime/reprovision" --run "$DK" --expect 404 --check detail eq 'Conversation not found'
  control-agent-server api DELETE "/api/conversations/$Z/runtime" --run "$DK" --expect 404 --check detail eq 'Conversation not found' --save F12.docker-runtime-contract/release-unknown
  control-agent-server api POST "/api/conversations/$Z/runtime/credentials" --run "$DK" --expect 404 --check detail eq 'Conversation not found' \
    --save F12.docker-runtime-contract/credentials-unknown
  control-agent-server api DELETE "/api/conversations/$Z/runtime" --run "$DK" --auth none --expect 401 --check detail eq Unauthorized
  control-agent-server api POST "/api/conversations/$Z/runtime/credentials" --run "$DK" --auth none --expect 401 --check detail eq Unauthorized
  control-agent-server ws listen "/sockets/events/$Z" --run "$DK" --expect-close 1008 --duration 10
  control-agent-server ws listen "/sockets/session/$Z" --run "$DK" --expect-close 1008 --duration 10
  control-agent-server api POST /api/conversations --run "$DK" --expect 400 --check detail eq 'Invalid JSON body'
  control-agent-server api POST /api/conversations --run "$DK" --json '[1]' --expect 422 --check detail eq 'Expected a JSON object'
  control-agent-server api POST /api/conversations --run "$DK" --json '{"workspace": {"kind": "RemoteWorkspace", "host": "http://127.0.0.1:9"}}' \
    --expect 422 --check detail eq 'Docker conversations require a local workspace'
  control-agent-server api POST /api/conversations --run "$DK" --json '{"conversation_id": "not-a-uuid"}' --expect 400 --check detail eq 'Invalid conversation_id'
  control-agent-server state ls --run "$DK" 'home/.openhands/runtime-control/*.json' --expect-count 0
  control-agent-server api POST /api/conversations --run "$DK" \
    --json "{\"conversation_id\": \"$U\", \"agent\": {\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-f12-docker-key\"}, \"tools\": []}}" \
    --expect 502 --check exception eq '502: Could not create conversation container' --save F12.docker-runtime-contract/create
  control-agent-server state ls --run "$DK" "home/.openhands/runtime-control/${U//-/}.json" --expect-count 1
  control-agent-server api GET "/api/conversations/$U/runtime" --run "$DK" --expect 404 --check detail eq 'Conversation not found'
  control-agent-server api POST "/api/conversations/$U/runtime/reprovision" --run "$DK" --expect 502 \
    --check exception eq '502: Could not start conversation container'
  control-agent-server ws listen "/sockets/events/$U" --run "$DK" --expect-close 1011 --duration 10
  control-agent-server api DELETE "/api/conversations/$U" --run "$DK" --expect 200 --save F12.docker-runtime-contract/delete
  control-agent-server state ls --run "$DK" "home/.openhands/runtime-control/${U//-/}.json" --expect-count 0
  control-agent-server api DELETE "/api/conversations/$U" --run "$DK" --expect 404 --check detail eq 'Conversation not found'
  control-agent-server stop --run "$DK"
  ```
  The four unproxyable operations are 501 (in `exception`, like every 5xx)
  for an id that does not exist and even for a body the local route would
  reject, and the binding source is never contacted. Unknown ids are 404
  `Conversation not found` (the Docker router's wording; the local runtime
  routes say `Not Found`) and the sockets close with 1008. Malformed creates
  are refused before anything is written. A well-formed create writes the
  runtime manifest, fails to start the container (502) and leaves the
  manifest: the conversation has no `meta.json` yet, so runtime info is 404,
  while reprovision and the socket try to start the container (502, close
  1011). Deleting the conversation removes the manifest.
- **Docker runtime with a daemon (`F12.docker-runtime`), blocked.**
  Needs a Docker daemon and the `conversation_image`
  (`ghcr.io/openhands/agent-server:...-python`) pulled;
  `control-agent-server capabilities` reports `docker`. It would prove the
  container limits, the startup bound, `DELETE .../runtime` (204),
  `POST .../runtime/credentials` (200), reprovision, the `X-Expose-Secrets`
  refusal (422) and the launched profile inside the container.
  ```sh
  DK=$(control-agent-server launch --new --name f12-docker-live --print-run \
    --config-json '{"conversation_runtime": "docker", "conversation_container_memory": "1g", "conversation_container_cpus": 1, "conversation_container_pids_limit": 256}')
  CID=$(control-agent-server api POST /api/conversations --run "$DK" \
    --json '{"agent": {"llm": {"model": "openai/qa-stub", "api_key": "qa"}, "tools": []}}' --expect 200,201 --field id)
  docker inspect --format '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}} {{.HostConfig.PidsLimit}}' \
    "$(docker ps -q --filter label=ai.openhands.runtime-owner)" | grep -qx '1073741824 1000000000 256'
  control-agent-server api GET "/api/conversations/$CID/runtime" --run "$DK" --expect 200 --check runtime_status eq available --check can_resume eq true
  control-agent-server api POST "/api/conversations/$CID/runtime/credentials" --run "$DK" --expect 200 --check session_api_key exists
  control-agent-server api GET "/api/conversations/$CID/events/search" --run "$DK" --header 'X-Expose-Secrets: plaintext' \
    --expect 422 --check detail eq 'Runtime secret exposure is unsupported'
  control-agent-server api DELETE "/api/conversations/$CID/runtime" --run "$DK" --expect 204
  control-agent-server api GET "/api/conversations/$CID/runtime" --run "$DK" --expect 200 --check runtime_status eq missing --check can_resume eq true
  control-agent-server api GET "/api/conversations/$CID" --run "$DK" --expect 200
  control-agent-server api POST "/api/conversations/$CID/runtime/reprovision" --run "$DK" --expect 200 --check runtime_status eq available
  control-agent-server stop --run "$DK"
  ```
  With a daemon, the container runs with `--memory 1g --cpus 1
  --pids-limit 256`, `--cap-drop ALL` and `OH_RUNTIME_LAUNCHED_PROFILE` in
  its environment; credentials return its session key, the release stops
  it (runtime `missing`, still resumable) without touching the
  conversation, and reprovision starts it again. A `conversation_image`
  that never answers `/health` makes the create fail with 502 after
  `conversation_container_startup_timeout` seconds; a runtime archived by
  retention answers 410 `Conversation runtime was archived; its history is
  read-only`; a second `DELETE /api/conversations/{id}` while the first is
  still stopping the container answers 409 `Conversation deletion is already
  in progress` (without a daemon the stop is too quick to race).

## Gotchas

- `GET /api/conversations/{id}` and the runtime routes read the catalog and
  never load a conversation; event routes, `/run`, message posts and the
  sockets do. Lease files (`owner_lease.json`, in the conversation directory
  named by the id without dashes) exist only for loaded conversations, so they
  are the visible "loaded" signal. Leases themselves (renewal, takeover,
  second instances) are F31.
- The local runtime always reports `available` / `can_resume: true`, even for
  a conversation whose every load is refused until a credential binding
  arrives; the runtime view is not a readiness signal for bindings.
- `prepare-for-sandbox-pause` unloads every conversation of the server at
  once, under an exclusive lifecycle lock; it waits up to 10 s per in-flight
  run. A failure closing one conversation surfaces as a 500 after the others
  closed, and pending bindings are then kept. Sockets that were open stay
  attached to the closed service (`F12.sandbox-pause-websocket`), so a client
  must reconnect on its own after a pause.
- `sink read --wait` polls until `--expect-min` holds; the in-flight model
  call in `F12.sandbox-pause-running` and the switched LLM's call in
  `F12.managed-key-switch` are awaited that way before anything is
  asserted.
- The binding route probes the source before it looks at the conversation,
  so even a 403 (profile scope) or 409 (too late) costs one source request.
  It validates the body before the secret name (a bad body with an
  unsupported name is 422, not 404).
- A 204 from the binding route never means the conversation exists: unknown
  ids get a pending binding, kept in memory until a create or load with that
  id, a pause, or the server stops.
- The "too late" 409 message (`... arrived after ACP initialization`) is also
  used for a different URL on an existing binding and for regular agents.
- 5xx answers are rewritten by the server's exception handler to
  `{"detail": "Internal Server Error", "exception": "<status>: <detail>"}`,
  so 501 and 502 put the reason in `exception`.
- The source is reached with `httpx` from the server process; launched runs
  put `127.0.0.1` and `localhost` in `NO_PROXY`, which a loopback source
  needs behind a proxy.
- The http sink answers every request with the same status, so each source
  behavior needs its own sink, and sequences (503 then 200) cannot be
  scripted.
- Refreshing the credential (`PUT url` with `expected_version`) only happens
  inside a running Codex ACP session; without the Codex ACP binary and an
  OpenAI login it is not reachable, so this family proves activation, scrub,
  persistence and the reload guard only.
- Docker runtime mode is chosen at startup: the Docker router is mounted
  ahead of the local conversation routes (`api.py`), so on such a server
  the local runtime, binding, fork and switch handlers are unreachable
  (shadowed, not removed from the code). `map routes` adds the routes that
  exist only in Docker runtime mode, so the two runtime-control routes sit
  on this family's `Routes:` line and recipes call them by literal path.
- Without a daemon, a Docker-mode create still writes
  `runtime-control/<id>.json` (encrypted session and cipher keys) before the
  container start fails; only `DELETE /api/conversations/{id}` removes it.
  The Docker router's 404s say `Conversation not found`, the local ones
  `Not Found`.
- The managed-key variables are read from the server's environment each
  time a conversation is loaded (create, first event access after a pause or
  restart), so `restart --env` changes them for every conversation loaded
  afterwards. Only an LLM whose resolved `base_url` (without a trailing
  slash) is on the allow-list gets the hook; the refresh URL must answer the
  bare key as text, and a refresh that returns the stored key does not
  retry. `llm-stub` answers by call number, not by key, and does not record
  request headers: the stub's 401 stands for "stale key", and the recipes
  count fetches and calls instead of reading the retried key.
