# TypeScript client (@openhands/typescript-client)

The browser and Node consumer lane: what a program built on the TypeScript
client in `clients/typescript` observes when it drives the Agent Server.
`ConversationManager` (or the aggregate `AgentServerClient`) reads server
status and lists, loads, updates and deletes conversations; `RemoteWorkspace`
runs commands, moves files and mints the workspace cookie, globally or scoped
to one conversation; `RemoteConversation` creates or reattaches a server
conversation, sends messages, runs it and reads its state and events through
`RemoteState` and `RemoteEventsList`; `ConversationEventStream`,
`WebSocketCallbackClient` and `BashWebSocketClient` stream events; small
clients wrap hooks, MCP tests and the workspace registry, gated on the
server's version; and `HttpError` carries every refusal. The client is pinned
to a released server (`package.json` `config.agentServerImage`), so its own
endpoint audit is replayed here against this checkout's contract. Every
recipe runs a real program on the built package (`clients/typescript/dist`)
through `control-agent-server exec` and checks the server's own view through
the REST API, the WebSocket or the disk afterwards.

Source: `clients/typescript/src/`, `clients/typescript/scripts/endpoint-audit.mjs`, `clients/typescript/scripts/generate-agent-server-api.mjs`, `clients/typescript/endpoint-audit.config.json`, `clients/typescript/package.json`

Needs: `llm`, `node`, `uvx`, `git`, `tmux`, `network`

Routes: none

## Sub-features

- `F34.server-client`: `ServerClient` (through `ConversationManager.server` and `AgentServerClient.server`) reads `/alive`, `/health`, `/ready`, `/server_info` and `/` with the server's own values, the probes without a key; the version-gated clients (`HooksClient`, `WorkspacesClient`, `MCPClient`) read `/server_info` once per transport, accept this server and return its data.
- `F34.http-errors`: a missing or wrong key rejects with an `HttpError` (`status` 401, `detail` `Unauthorized`, recognized by `isHttpError`), a 404 and a 422 carry the server's `detail` and `validationErrors`, while an unreachable host and a client timeout reject with plain errors.
- `F34.workspace-exec`: `RemoteWorkspace.executeCommand` returns stdout, stderr and the exit code of a command run in the given `cwd`, and exit `-1` once the command's own timeout kills it.
- `F34.workspace-files`: `RemoteWorkspace.uploadText`/`fileUpload` and `downloadAsText`, and `FileClient.downloadFile`/`uploadTextFile`, round-trip files (bytes exact through `FileClient`); a missing file, a relative path and a directory reject with the server's 404 and 400.
- `F34.workspace-binary-download`: `RemoteWorkspace.downloadAsBlob` returns a binary file byte for byte.
- `F34.workspace-scoped`: a `RemoteWorkspace` with `conversationId` routes through `/api/conversations/{id}/bash|file/...`: commands default to the conversation's workspace and land in its own bash history, paths and `cwd` outside it get 422, an unknown conversation 404.
- `F34.workspace-git`: `RemoteWorkspace.gitChanges` and `gitDiff` return the server's changes and diff for a repository, globally and through `/api/conversations/{id}/git/...` for a scoped workspace, and wrap a 422 (path outside the conversation workspace) or 401 in an `Error` whose `cause` is the `HttpError`.
- `F34.workspace-git-types`: the statuses `gitChanges` returns type-check as the exported `GitChange['status']`, as they do against the client's generated contract.
- `F34.bash-client`: `RemoteWorkspace.bash` starts a background command, finds its output with `searchEvents`, reads events one at a time and in batches (unknown ids are `null`), stops a running command, treats an unknown id as already stopped, and `clearEvents` empties the history.
- `F34.bash-websocket`: `BashWebSocketClient` in Node delivers the bash events of a command (replayed with `resendMode: 'all'`) to its callback without errors.
- `F34.workspace-session`: `startWorkspaceSession(id)` answers with the workspace base URL that serves the conversation's files, `deleteWorkspaceSession()` succeeds, a mismatched scoped id is refused before any request, and no key is 401.
- `F34.conversation-create`: `RemoteConversation.start()` creates the server conversation with the client's agent (LLM, tools), working directory and `max_iterations`, and rebinds its workspace to the new conversation.
- `F34.conversation-run`: a reattached `RemoteConversation` stores a `sendMessage` without running, `run()` drives a real model to `finished`, the `hookConfig` given at creation runs before the tool call and is reported by `getHookConfig()`, the client reads the agent's file, stats, final response and trajectory ZIP, and `RemoteState` reports the server's status, agent, workspace, policy and persistence directory.
- `F34.events-list`: `RemoteEventsList` counts, searches (source, fully-qualified kind, body, pages), fetches events by id and iterates the same events the server stores.
- `F34.events-unknown-id`: `RemoteEventsList.getEventById` rejects an unknown event id with a 404 `HttpError` and `getEventsById` puts `null` in its slot.
- `F34.events-search-kind`: `RemoteEventsList.search({kind: 'MessageEvent'})` and `count({kind: 'MessageEvent'})`, the short kind its own docs show (`ActionEvent, MessageEvent`), find the same events as the fully-qualified kind.
- `F34.event-stream`: `ConversationEventStream` on Node's global `WebSocket` authenticates with the first frame, replays the stored events with `resend_mode: 'all'`, sends a user message that runs the agent to `finished` on the same socket, and reports a wrong key as a 4001 close.
- `F34.ws-callback-client`: `RemoteConversation.startWebSocketClient()` in Node delivers the conversation's events to the conversation callback without errors.
- `F34.conversation-controls`: `setTitle` and `setConfirmationPolicy` change the server's title and policy.
- `F34.conversation-confirm`: under `AlwaysConfirm`, `sendConfirmationResponse(false, reason)` records one `UserRejectObservation` with that reason and leaves the command unrun, `sendConfirmationResponse(true)` runs the pending command, and `updateSecrets` (a string and a function value) puts both values into that command's shell without any event containing them.
- `F34.conversation-branching`: `fork({title})` returns a `RemoteConversation` bound to the fork (its id, the fork's workspace and agent, the copied events), `navigateTo` refreshes the cached state to the new `leaf_event_id`, and `askAgent` answers without adding events to the source.
- `F34.manager`: `ConversationManager` searches with paging, counts by status, batch-gets (unknown ids are `null`), lists all, loads a conversation with its server working directory, creates and deletes one, and an unknown id rejects with 404.
- `F34.manager-update-result`: `ConversationManager.updateConversation` stores the new title and resolves with the updated `ConversationInfo` it is typed to return.
- `F34.manager-create-options`: `ConversationManager.createConversation(agent, {maxIterations, stuckDetection})` creates the conversation with those settings.
- `F34.manager-acp`: `ConversationManager.acp` creates a conversation for an inline ACP agent (with its `maxIterations`) and gets, batch-gets, lists and counts it with its ACP agent fields.
- `F34.hooks-client`: `HooksClient.loadHooks` and `RemoteConversation.loadHooks` read a project's `.openhands/hooks.json` as a snake_case `HookConfig` the client's hook helpers understand, and `null` for a directory without hooks.
- `F34.mcp-client`: `MCPClient.testServer` lists a stdio MCP server's tools and runs one tool call, reports a server that fails to start as `{ok: false}` with an `error_kind`, and an empty command rejects with 422.
- `F34.restart-reload`: after a server restart a new `ConversationManager` loads the same conversation with its status, title, events, hook config and workspace files.
- `F34.event-stream-reconnect`: a `ConversationEventStream` with `reconnect: {enabled: true}` and `queryParams: {resend_mode: 'all'}`, on the `ws` package's constructor, reconnects by itself after a server restart, replays the history again with the same query, and delivers a message posted after the restart.
- `F34.event-stream-reconnect-native`: the same stream on Node's global `WebSocket` also reconnects after a server restart.
- `F34.endpoint-audit`: the client's endpoint audit against this checkout's public OpenAPI contract finds no client call the server lacks, and regenerating the client's types from that contract removes no exported type.
- `F34.version-gate-old`: against a real 1.22.0 agent-server release, the gated methods reject with `AgentServerVersionError` (required and actual versions) and never call the gated route.

## How to get to it (agent POV)

- Package: `@openhands/typescript-client` (ESM only), main entry
  `dist/index.js` (`ConversationManager`, `RemoteConversation`/`Conversation`,
  `RemoteWorkspace`/`Workspace`, `RemoteState`, `RemoteEventsList`, `Agent`,
  `HttpClient`, `HttpError`, `isHttpError`, `WebSocketCallbackClient`,
  `BashWebSocketClient`, `HooksClient`, `MCPClient`, `WorkspacesClient`,
  `assertAgentServerSupports`, the hook helpers) and the secondary entry
  `dist/clients.js` (`ServerClient`, `FileClient`, `ConversationEventStream`,
  `buildConversationEventStreamUrl`, `AgentServerClient` and every endpoint
  client). Recipes import the built files by path from the repository root.
- Server status: `ServerClient.getAlive|getHealth|getReady|getServerInfo|getRoot`
  (`GET /alive`, `/health`, `/ready`, `/server_info`, `/`).
- Conversations: `RemoteConversation.start()` (`POST /api/conversations`, or
  `GET /api/conversations/{id}` when constructed with `conversationId`),
  `sendMessage` (`POST .../events` with `run: false`), `run`, `pause`,
  `setTitle` (`PATCH`), `setConfirmationPolicy`, `sendConfirmationResponse`
  (`POST .../events/respond_to_confirmation`), `updateSecrets` (`POST
  .../secrets`, every value wrapped as a `StaticSecret`, a function value
  called first), `fork` (`POST .../fork`, returns a new `RemoteConversation`
  bound to the fork), `navigateTo` (`POST .../navigate`, then refreshes the
  cached state), `askAgent` (`POST .../ask_agent`), `conversationStats`,
  `getAgentFinalResponse`, `downloadTrajectory`, `getHookConfig`, `loadHooks`
  (`POST /api/hooks`), `startWebSocketClient`; `ConversationManager`
  `searchConversations`, `countConversations`, `getConversations` (`GET
  /api/conversations?ids=`), `getAllConversations`, `getConversation`,
  `loadConversation`, `createConversation`, `updateConversation`,
  `deleteConversation`, and the `acp` namespace (`createConversation`,
  `getConversation`, `getConversations`, `getAllConversations`,
  `countConversations`, `searchConversations`) for ACP agents. `pause`,
  condense, goals and profile or model switching are thin wrappers whose
  routes are driven by their own families (F05, F09, F10, F11); this family
  does not call them.
- State and events: `RemoteState` (`GET /api/conversations/{id}`, cached for
  2 seconds), `RemoteEventsList.search|count|getEventById|getEventsById|getEvents`
  (`GET .../events/search`, `.../events/count`, `.../events/{event_id}`).
- Workspace: `RemoteWorkspace.executeCommand` (`POST
  /api/bash/execute_bash_command`), `uploadText`/`fileUpload` (`POST
  /api/file/upload`), `downloadAsText`/`downloadAsBlob`/`fileDownload` (`GET
  /api/file/download`), `gitChanges`/`gitDiff` (`GET /api/git/changes`,
  `/api/git/diff`), `bash.*` (`BashClient`: start, search, get, batch get,
  stop, clear), `startWorkspaceSession`/`deleteWorkspaceSession`
  (`/api/auth/workspace-session`). With `conversationId` the bash, file and
  git calls go to `/api/conversations/{id}/bash/...`, `.../file/...` and
  `.../git/...` when
  `/server_info` advertises `conversation_runtime_routes_v1` (else a `cid`
  query parameter). `FileClient` downloads as `ArrayBuffer`.
- WebSockets: `ConversationEventStream` (`/sockets/events/{id}`, first-frame
  auth, `queryParams`, optional reconnect) uses the global `WebSocket` unless
  given `createWebSocket`; `WebSocketCallbackClient` (used by
  `RemoteConversation.startWebSocketClient`) and `BashWebSocketClient`
  (`/sockets/bash-events`, deprecated `session_api_key` query auth) pick
  `window.WebSocket` or `require('ws')` when their module loads.
- Version gate: `HooksClient`, `WorkspacesClient` and `MCPClient` call
  `assertAgentServerSupports` (hooks, workspaces and MCP test need 1.23.0, MCP
  OAuth 1.31.0), which caches one `/server_info` per `HttpClient`.
- Contract tooling: `npm run audit:endpoints` (`scripts/endpoint-audit.mjs`)
  and `npm run generate:agent-server-api`, which take
  `AGENT_SERVER_OPENAPI_PATH` (and `AGENT_SERVER_GENERATED_OUTPUT`) to run
  offline against a contract exported from source by
  `.github/scripts/export_agent_server_openapi.py`.
- Agent Canvas (`OpenHands/OpenHands`) consumes this package; context only.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported (`launch --new`), `doctor` is ok,
  `node` (22 or later, for the global `WebSocket`) is on `PATH` and
  `$DEEPSEEK_API_KEY` is set. The block installs the client's dependencies
  when they are missing (needs `npm`) and rebuilds `clients/typescript/dist`
  when it is missing or older than any file in `src/` or `package.json`, so
  a checkout with client changes is never verified against a stale build.
- The block saves the DeepSeek profiles, creates the `qa-f34` fixture
  directory with `lib.mjs` (imports both entry points and exports `host`,
  `apiKey`, `F34`, `NOPE`, `sleep`, `waitFor` and `assert` for every
  program), `reconnect.mjs` (the two reconnect bullets' watcher: one
  `ConversationEventStream` per mode given, `ws` through the `ws` package's
  constructor or `native` on Node's global `WebSocket`, which records
  whether each went down, came back and saw a message posted after the
  restart, and never throws on a stream that does not come back), a
  project with `.openhands/hooks.json`, a stdio MCP server, and `SCID`: a
  conversation that never runs (placeholder agent, workspace
  `$F34/scoped`, one queued user message), which the scoped-workspace
  bullets and every known-bug bullet use, so `map run --only` on a known
  bug needs nothing but this block.
  Programs are written into `$F34` by each bullet and run with
  `control-agent-server exec`, which injects `AGENT_SERVER_URL` and
  `SESSION_API_KEY`; values that later bullets need (conversation ids) are
  written to files under `$F34`. A known-bug program only observes: it
  asserts its positive controls, writes what the client returned to a JSON
  file under `$F34` and prints it, and the line marked `# bug` (a `jq -e`
  or `tsc`) asserts the correct value, so `map run` reports the bug only
  when that assertion is the one that fails.
- `F34.version-gate-old` downloads the 1.22.0 agent-server release from PyPI
  with `uvx` (about 200 packages, cached after the first time).

  ```sh
  test -d clients/typescript/node_modules/typescript || (cd clients/typescript && npm ci --silent)
  if [ ! -f clients/typescript/dist/index.js ] || \
    [ -n "$(find clients/typescript/src clients/typescript/package.json -newer clients/typescript/dist/index.js -print -quit)" ]; then
    (cd clients/typescript && npm run build --silent)
  fi
  test -z "$(find clients/typescript/src -newer clients/typescript/dist/index.js -print -quit)"
  control-agent-server llm preset deepseek
  F34="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f34"
  mkdir -p "$F34/work" "$F34/files" "$F34/scoped"
  SCID=$(control-agent-server conversation start --no-run --placeholder-agent --tools none --no-autotitle --workspace "$F34/scoped" \
    --prompt 'qa-f34-scoped: never run' --print-id)
  control-agent-server api GET "/api/conversations/$SCID" --expect 200 --check execution_status eq idle --check workspace.working_dir eq "$F34/scoped"
  cat > "$F34/lib.mjs" <<'JS'
  import assert from 'node:assert/strict';
  const root = 'file://' + process.cwd() + '/';
  export const m = await import(new URL('clients/typescript/dist/index.js', root).href);
  export const c = await import(new URL('clients/typescript/dist/clients.js', root).href);
  export const host = process.env.AGENT_SERVER_URL;
  export const apiKey = process.env.SESSION_API_KEY;
  export const F34 = process.env.AGENT_SERVER_VERIFY_RUN + '/fixtures/qa-f34';
  export const NOPE = '00000000-0000-0000-0000-000000000000';
  export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  export async function waitFor(fn, ms = 60000, step = 250) {
    const end = Date.now() + ms;
    for (;;) {
      const value = await fn();
      if (value) return value;
      if (Date.now() > end) throw new Error('waitFor timed out');
      await sleep(step);
    }
  }
  export { assert };
  JS
  cat > "$F34/reconnect.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { c, host, apiKey, F34, sleep } from './lib.mjs';
  const { WebSocket: WS } = await import(new URL('clients/typescript/node_modules/ws/wrapper.mjs', 'file://' + process.cwd() + '/').href);
  const [cid, out, ...modes] = process.argv.slice(2);
  const until = async (ok, ms) => { for (const end = Date.now() + ms; !ok() && Date.now() < end;) await sleep(50); return ok(); };
  const watch = (mode) => {
    const r = { mode, frames: [], states: [] };
    r.stream = new c.ConversationEventStream({ url: c.buildConversationEventStreamUrl(host, cid), sessionApiKey: apiKey,
      queryParams: { resend_mode: 'all' }, reconnect: { enabled: true },
      ...(mode === 'ws' ? { createWebSocket: (url) => new WS(url) } : {}),
      onMessage: (f) => r.frames.push({ at: r.states.length, frame: JSON.parse(f.data) }),
      onStateChange: (s) => r.states.push(s) });
    r.stream.start();
    return r;
  };
  const seen = (r, text, after = -1) => r.frames.filter((f) => f.at > after && f.frame.kind === 'MessageEvent' &&
    JSON.stringify(f.frame).includes(text)).length;
  const streams = modes.map(watch);
  if (!(await until(() => streams.every((r) => seen(r, 'qa-f34-reconnect: never run')), 15000))) throw new Error('no first replay');
  writeFileSync(`${F34}/reconnect-ready`, '');
  const results = await Promise.all(streams.map(async (r) => {
    const down = () => r.states.findIndex((s) => !s.isConnected && s.isReconnecting);
    const up = () => (down() < 0 ? -1 : r.states.findIndex((s, i) => i > down() && s.isConnected));
    await until(() => down() >= 0, 60000);
    await until(() => up() >= 0 && seen(r, 'qa-f34-after-restart', up()) > 0, 60000);
    const last = r.states.at(-1);
    r.stream.stop();
    return [r.mode, { went_down: down() >= 0, reconnected: up() >= 0, attempts: Math.max(...r.states.map((s) => s.attemptCount)),
      replayed_after: up() < 0 ? 0 : seen(r, 'qa-f34-reconnect: never run', up()),
      sent_after: up() < 0 ? 0 : seen(r, 'qa-f34-after-restart', up()),
      last_state: { ...last, error: last.error && last.error.message } }];
  }));
  writeFileSync(out, JSON.stringify(Object.fromEntries(results)));
  console.log('QA_F34_RECONNECT_WATCHED');
  JS
  PROJECT=$(control-agent-server fixture project --name qa-f34-project --print-path)
  test -f "$PROJECT/.openhands/hooks.json"
  control-agent-server fixture mcp-server --name qa-f34-mcp
  test -f "$AGENT_SERVER_VERIFY_RUN/fixtures/mcp/qa-f34-mcp.py"
  ```

- **Server status and version gate (`F34.server-client`).** The probes
  without a key, server info with one, through both entry points; then the
  gated clients, which this server is new enough for.
  ```sh
  VERSION=$(control-agent-server api GET /server_info --auth none --field version)
  cat > "$F34/server.mjs" <<'JS'
  import { m, c, host, apiKey, assert } from './lib.mjs';
  const version = process.argv[2];
  const anonymous = new c.ServerClient({ host });
  assert.deepEqual(await anonymous.getAlive(), { status: 'ok' });
  assert.deepEqual(await anonymous.getHealth(), { status: 'ok' });
  assert.deepEqual(await anonymous.getReady(), { status: 'ready' });
  assert.equal((await anonymous.getRoot()).version, version);
  const manager = new m.ConversationManager({ host, apiKey });
  const info = await manager.server.getServerInfo();
  assert.equal(info.version, version);
  assert.ok(info.capabilities.includes('conversation_runtime_routes_v1'));
  assert.ok(info.usable_tools.includes('file_editor'));
  const aggregate = new c.AgentServerClient({ host, apiKey });
  assert.deepEqual(await aggregate.server.getHealth(), { status: 'ok' });
  console.log('QA_F34_SERVER_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_SERVER_OK --save F34.server-client/program -- node "$F34/server.mjs" "$VERSION"
  N=$(control-agent-server api GET /api/workspaces --field workspaces | jq length)
  cat > "$F34/gate.mjs" <<'JS'
  import { m, host, apiKey, assert } from './lib.mjs';
  const [version, count] = process.argv.slice(2);
  const http = new m.HttpClient({ baseUrl: host, apiKey });
  for (const requirement of Object.values(m.AgentServerFeatureRequirements)) {
    assert.equal((await m.assertAgentServerSupports(http, requirement)).version, version);
    assert.ok(m.compareAgentServerVersions(version, requirement.minVersion) >= 0);
  }
  assert.equal(await m.getCachedAgentServerInfo(http), await m.getCachedAgentServerInfo(http));
  const listed = await new m.WorkspacesClient({ host, apiKey }).listWorkspaces();
  assert.equal(listed.workspaces.length, Number(count));
  assert.ok(Array.isArray(listed.workspaceParents));
  assert.deepEqual(await new m.HooksClient({ host, apiKey }).loadHooks({ project_dir: '/qa-f34-no-project' }), { hook_config: null });
  console.log('QA_F34_GATE_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_GATE_OK --save F34.server-client/gate -- node "$F34/gate.mjs" "$VERSION" "$N"
  ```
  The first program prints `QA_F34_SERVER_OK`: the probes answer
  `ok`/`ready`, and `/` and `/server_info` report the version the CLI read.
  The second prints `QA_F34_GATE_OK`: every requirement (`workspaces`,
  `hooks`, `mcp-test` 1.23.0, `mcp-oauth` 1.31.0) passes for this server's
  version, the cached `/server_info` is the same object twice, and the gated
  calls return the server's data (the registry size REST reports, no hooks
  for a missing project).
- **Errors (`F34.http-errors`).** Every refusal the client surfaces, with a
  positive control on the same server.
  ```sh
  cat > "$F34/errors.mjs" <<'JS'
  import { m, host, apiKey, NOPE, assert } from './lib.mjs';
  const manager = new m.ConversationManager({ host, apiKey });
  assert.equal(typeof (await manager.countConversations()), 'number');
  await assert.rejects(new m.ConversationManager({ host }).countConversations(), (e) =>
    m.isHttpError(e) && e.status === 401 && e.detail === 'Unauthorized' &&
    e.message === 'HTTP request failed (401 Unauthorized): {"detail":"Unauthorized"}');
  await assert.rejects(new m.ConversationManager({ host, apiKey: 'qa-f34-wrong-key' }).countConversations(),
    (e) => m.isHttpError(e) && e.status === 401 && e.detail === 'Unauthorized');
  await assert.rejects(manager.getConversation(NOPE), (e) => e.status === 404 && e.detail === 'Not Found');
  await assert.rejects(manager.getConversation('qa-f34-not-a-uuid'), (e) =>
    e.status === 422 && e.validationErrors.length === 1 && e.validationErrors[0].type === 'uuid_parsing' &&
    e.validationErrors[0].loc.join('.') === 'path.conversation_id' && e.detail === e.validationErrors[0].msg);
  const http = new m.HttpClient({ baseUrl: host, apiKey });
  await assert.rejects(http.post('/api/file/upload', {}), (e) =>
    e.status === 422 && e.validationErrors.length === 2 &&
    e.detail === e.validationErrors.map((v) => `${v.loc.at(-1)}: ${v.msg}`).join('; '));
  await assert.rejects(new m.HttpClient({ baseUrl: 'http://127.0.0.1:9' }).get('/alive'),
    (e) => !m.isHttpError(e) && e.message.startsWith('Request failed: '));
  const slow = new m.HttpClient({ baseUrl: host, apiKey, timeout: 500 });
  const started = Date.now();
  await assert.rejects(slow.post('/api/bash/execute_bash_command', { command: 'sleep 2', timeout: 10 }),
    (e) => !m.isHttpError(e) && e.message === 'Request timeout after 500ms');
  assert.ok(Date.now() - started < 1800, `timeout took ${Date.now() - started} ms`);
  console.log('QA_F34_ERRORS_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_ERRORS_OK --save F34.http-errors/program -- node "$F34/errors.mjs"
  control-agent-server api GET /api/conversations/count --auth none --expect 401 --check detail eq Unauthorized
  ```
  The program prints `QA_F34_ERRORS_OK`: both 401s are `HttpError`s with
  `detail` `Unauthorized` and the transport `message`; the unknown id is 404
  `Not Found`; a malformed id is a 422 whose single `uuid_parsing` entry is
  the `detail`, and a body missing two fields joins them as
  `path: Field required; file: Field required`; a closed port and the
  500 ms client timeout reject with plain `Error`s, the timeout well before
  the 2 s command ends.
- **Workspace commands (`F34.workspace-exec`).** Output, `cwd`, exit codes
  and the command's own timeout.
  ```sh
  cat > "$F34/exec.mjs" <<'JS'
  import { m, host, apiKey, F34, assert } from './lib.mjs';
  const dir = `${F34}/work`;
  const workspace = new m.RemoteWorkspace({ host, apiKey, workingDir: dir });
  const command = 'echo qa-f34-out; echo qa-f34-err >&2; pwd; printf qa-f34-exec > qa-f34-exec.txt';
  assert.deepEqual(await workspace.executeCommand(command, dir), {
    command, exit_code: 0, stdout: `qa-f34-out\n${dir}\n`, stderr: 'qa-f34-err\n', timeout_occurred: false,
  });
  assert.equal((await workspace.executeCommand('exit 3')).exit_code, 3);
  const started = Date.now();
  const killed = await workspace.executeCommand('sleep 5', dir, 1);
  assert.equal(killed.exit_code, -1);
  assert.ok(Date.now() - started < 4500, `took ${Date.now() - started} ms`);
  console.log('QA_F34_EXEC_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_EXEC_OK --save F34.workspace-exec/program -- node "$F34/exec.mjs"
  test "$(cat "$F34/work/qa-f34-exec.txt")" = qa-f34-exec
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand --query sort_order=TIMESTAMP_DESC --query limit=3 \
    --check items.0.command eq 'sleep 5' --check items.0.timeout eq 1 --check items.1.command eq 'exit 3' \
    --check items.2.command contains qa-f34-out --check items.2.cwd eq "$F34/work" --save F34.workspace-exec/history
  ```
  The first result is exact; `exit 3` reports 3; `sleep 5` with a 1 s timeout
  returns after about a second with exit `-1` (`timeout_occurred` stays
  `false`, see Gotchas). The file the command wrote is on disk and the
  server's bash history lists the three commands with their `cwd` and
  timeout.
- **Workspace files (`F34.workspace-files`).** Text and bytes, then the
  refusals.
  ```sh
  cat > "$F34/files.mjs" <<'JS'
  import { m, c, host, apiKey, F34, assert } from './lib.mjs';
  const dir = `${F34}/files`;
  const workspace = new m.RemoteWorkspace({ host, apiKey, workingDir: dir });
  const uploaded = await workspace.uploadText('qa-f34 text\n', `${dir}/up.txt`);
  assert.equal(uploaded.success, true);
  assert.equal(uploaded.destination_path, `${dir}/up.txt`);
  assert.equal(await workspace.downloadAsText(`${dir}/up.txt`), 'qa-f34 text\n');
  await workspace.fileUpload(new Blob([new Uint8Array([0xff, 0x00, 0x80, 0xfe, 0x41])]), `${dir}/nested/bin.dat`);
  const files = new c.FileClient({ host, apiKey });
  assert.deepEqual([...new Uint8Array(await files.downloadFile(`${dir}/nested/bin.dat`))], [255, 0, 128, 254, 65]);
  assert.equal(await files.downloadTextFile(`${dir}/up.txt`), 'qa-f34 text\n');
  assert.deepEqual(await files.uploadTextFile('qa-f34 via FileClient', `${dir}/fc.txt`), { success: true });
  await assert.rejects(workspace.downloadAsText(`${dir}/missing.txt`), (e) => e.status === 404 && e.detail === 'File not found');
  await assert.rejects(workspace.uploadText('x', 'qa-f34-relative.txt'), (e) => e.status === 400 && e.detail === 'Path must be absolute');
  await assert.rejects(workspace.downloadAsText(dir), (e) => e.status === 400 && e.detail === 'Path is not a file');
  console.log('QA_F34_FILES_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_FILES_OK --save F34.workspace-files/program -- node "$F34/files.mjs"
  test "$(cat "$F34/files/up.txt")" = 'qa-f34 text'
  test "$(od -An -tx1 "$F34/files/nested/bin.dat" | tr -d ' \n')" = ff0080fe41
  control-agent-server api GET /api/file/download --query "path=$F34/files/fc.txt" --expect 200 --raw-out "$F34/files/fc.copy"
  test "$(cat "$F34/files/fc.copy")" = 'qa-f34 via FileClient'
  ```
  The text round-trips, the upload created the missing `nested/` directory,
  the five bytes on disk are exactly the ones sent and `FileClient` reads them
  back unchanged; the server's own download serves the `FileClient` upload.
- **Binary download through RemoteWorkspace (`F34.workspace-binary-download`), known bug.**
  `RemoteWorkspace.fileDownload` lets the transport parse the
  `application/octet-stream` body as text, so `downloadAsBlob` and
  `fileDownload` return UTF-8-decoded bytes (`clients/typescript/src/workspace/remote-workspace.ts:183`,
  no `responseType`); `FileClient.downloadFile` asks for an `arrayBuffer`
  and is right. The bullet writes its own five bytes, and the program first
  reads them back exactly through `FileClient` (positive control), then
  records what `downloadAsBlob` and `fileDownload` returned.
  ```sh
  printf '\377\000\200\376A' > "$F34/files/qa-f34-bin.dat"
  test "$(od -An -tx1 "$F34/files/qa-f34-bin.dat" | tr -d ' \n')" = ff0080fe41
  cat > "$F34/binary.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, c, host, apiKey, F34, assert } from './lib.mjs';
  const path = `${F34}/files/qa-f34-bin.dat`;
  assert.deepEqual([...new Uint8Array(await new c.FileClient({ host, apiKey }).downloadFile(path))], [255, 0, 128, 254, 65]);
  const workspace = new m.RemoteWorkspace({ host, apiKey, workingDir: `${F34}/files` });
  const blob = [...new Uint8Array(await (await workspace.downloadAsBlob(path)).arrayBuffer())];
  const { file_size: fileSize } = await workspace.fileDownload(path);
  writeFileSync(`${F34}/binary.json`, JSON.stringify({ blob, file_size: fileSize }));
  console.log('QA_F34_BINARY_READ');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_BINARY_READ --save F34.workspace-binary-download/program -- node "$F34/binary.mjs"
  cat "$F34/binary.json"
  jq -e '.blob == [255, 0, 128, 254, 65] and .file_size == 5' "$F34/binary.json"  # bug
  ```
  Expected: the same five bytes and `file_size` 5. Today the blob holds 11
  bytes (`[239,191,189,0,239,191,189,239,191,189,65]`: each invalid byte
  became U+FFFD) and `file_size` is 11, while the file on disk has 5.
- **Conversation-scoped workspace (`F34.workspace-scoped`).** A workspace
  bound to `SCID`, the conversation from `Preconditions:` that never runs.
  ```sh
  cat > "$F34/scoped.mjs" <<'JS'
  import { m, host, apiKey, F34, NOPE, assert } from './lib.mjs';
  const scid = process.argv[2];
  const workspace = new m.RemoteWorkspace({ host, apiKey, workingDir: '/qa-f34-ignored', conversationId: scid });
  assert.equal((await workspace.executeCommand('pwd')).stdout, `${F34}/scoped\n`);
  await workspace.uploadText('qa-f34-scoped', `${F34}/scoped/qa-f34-scoped.txt`);
  assert.equal(await workspace.downloadAsText(`${F34}/scoped/qa-f34-scoped.txt`), 'qa-f34-scoped');
  await assert.rejects(workspace.uploadText('x', `${F34}/qa-f34-outside.txt`),
    (e) => e.status === 422 && e.detail === 'path must be inside the conversation workspace');
  await assert.rejects(workspace.executeCommand('pwd', '/tmp'),
    (e) => e.status === 422 && e.detail === 'cwd must be inside the conversation workspace');
  await assert.rejects(workspace.client.get('/api/file/download?path=/etc/hostname'), /separate query parameters/);
  const unknown = new m.RemoteWorkspace({ host, apiKey, workingDir: '/x', conversationId: NOPE });
  await assert.rejects(unknown.executeCommand('pwd'), (e) => e.status === 404);
  console.log('QA_F34_SCOPED_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_SCOPED_OK --save F34.workspace-scoped/program -- node "$F34/scoped.mjs" "$SCID"
  test "$(cat "$F34/scoped/qa-f34-scoped.txt")" = qa-f34-scoped
  test ! -e "$F34/qa-f34-outside.txt"
  control-agent-server api GET "/api/conversations/$SCID/bash/bash_events/search" --query kind__eq=BashCommand \
    --check items len-eq 1 --check items.0.command eq pwd --check items.0.cwd eq "$F34/scoped" --save F34.workspace-scoped/history
  ```
  The workspace's own `workingDir` is ignored: `pwd` prints the
  conversation's workspace, the upload lands there, the outside path and
  `cwd` are 422 and nothing is written outside, a query string in the path is
  refused by the client before any request, and the unknown conversation is
  404. Only `pwd` is in the conversation's own bash history.
- **Git through RemoteWorkspace (`F34.workspace-git`).** The `git-repo`
  fixture (one modified, one untracked file), globally and through a copy
  inside `SCID`'s workspace.
  ```sh
  REPO=$(control-agent-server fixture git-repo --name qa-f34-repo --print-path)
  test -d "$F34/scoped/qa-f34-repo" || cp -r "$REPO" "$F34/scoped/qa-f34-repo"
  cat > "$F34/git.mjs" <<'JS'
  import { m, host, apiKey, F34, assert } from './lib.mjs';
  const [repo, scid] = process.argv.slice(2);
  const fixture = [{ status: 'UPDATED', path: 'README.md' }, { status: 'ADDED', path: 'notes.txt' }];
  const workspace = new m.RemoteWorkspace({ host, apiKey, workingDir: repo });
  assert.deepEqual(await workspace.gitChanges(repo), fixture);
  assert.deepEqual(await workspace.gitChanges(repo, { ref: 'HEAD' }), fixture);
  assert.deepEqual(await workspace.gitDiff(`${repo}/README.md`), {
    original: '# QA fixture repo\n\nCreated by control-agent-server.',
    modified: '# QA fixture repo\n\nModified, not committed.',
  });
  const scoped = new m.RemoteWorkspace({ host, apiKey, workingDir: '/qa-f34-ignored', conversationId: scid });
  assert.deepEqual(await scoped.gitChanges(`${F34}/scoped/qa-f34-repo`), fixture);
  await assert.rejects(scoped.gitChanges(repo), (e) => !m.isHttpError(e) && e.message.startsWith('Failed to get git changes: ') &&
    m.isHttpError(e.cause) && e.cause.status === 422 && e.cause.detail === 'path must be inside the conversation workspace');
  await assert.rejects(new m.RemoteWorkspace({ host, workingDir: repo }).gitDiff(`${repo}/README.md`),
    (e) => e.message.startsWith('Failed to get git diff: ') && m.isHttpError(e.cause) && e.cause.status === 401);
  console.log('QA_F34_GIT_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_GIT_OK --save F34.workspace-git/program -- node "$F34/git.mjs" "$REPO" "$SCID"
  control-agent-server api GET /api/git/changes --query path="$REPO" --expect 200 --check . len-eq 2 \
    --check 0.status eq UPDATED --check 0.path eq README.md --check 1.status eq ADDED --check 1.path eq notes.txt --save F34.workspace-git/changes
  control-agent-server api GET "/api/conversations/$SCID/git/diff" --query path="$F34/scoped/qa-f34-repo/README.md" --expect 200 \
    --check modified contains 'Modified, not committed.' --check original contains 'Created by control-agent-server.'
  ```
  `gitChanges` returns the server's list unchanged (`UPDATED README.md`,
  `ADDED notes.txt`, the same with `ref: 'HEAD'`), `gitDiff` the original
  and modified text, and the scoped workspace reads the copy through
  `/api/conversations/{id}/git/...` (only that route answers 422 for the
  fixture's own path, outside the conversation workspace). Both methods wrap a refusal in a plain `Error` (`Failed to get git changes:
  ...`) whose `cause` is the `HttpError`: 422 for a path outside the
  conversation workspace, 401 without a key. REST agrees.
- **Git status type (`F34.workspace-git-types`), known bug.** The exported
  `GitChange` type declares `status: 'added' | 'modified' | 'deleted' |
  'renamed'` (`clients/typescript/src/models/workspace.ts:31`), while the
  server and the client's own generated contract (`GitChangeStatus`) use
  `'MOVED' | 'ADDED' | 'DELETED' | 'UPDATED'`, so a TypeScript consumer's
  `change.status === 'modified'` compiles and never matches. The program
  writes what `gitChanges` returned into three tiny `.mts` files and the
  client's own `tsc` type-checks them: the statuses against the generated
  contract and the paths against the exported `GitChange` (positive
  controls: the same compiler, flags and import resolve), then the statuses
  against the exported `GitChange`.
  ```sh
  REPO=$(control-agent-server fixture git-repo --name qa-f34-repo --print-path)
  cat > "$F34/git-types.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, host, apiKey, F34, assert } from './lib.mjs';
  const repo = process.argv[2];
  const changes = await new m.RemoteWorkspace({ host, apiKey, workingDir: repo }).gitChanges(repo);
  const statuses = changes.map((c) => c.status);
  assert.deepEqual(statuses, ['UPDATED', 'ADDED']);
  const dist = process.cwd() + '/clients/typescript/dist';
  writeFileSync(`${F34}/git-contract.mts`, `import type { GitChangeStatus } from '${dist}/generated/agent-server-schema.js';\n` +
    `export const statuses: GitChangeStatus[] = ${JSON.stringify(statuses)};\n`);
  writeFileSync(`${F34}/git-paths.mts`, `import type { GitChange } from '${dist}/index.js';\n` +
    `export const paths: Array<GitChange['path']> = ${JSON.stringify(changes.map((c) => c.path))};\n`);
  writeFileSync(`${F34}/git-types.mts`, `import type { GitChange } from '${dist}/index.js';\n` +
    `export const statuses: Array<GitChange['status']> = ${JSON.stringify(statuses)};\n`);
  console.log('QA_F34_GIT_TYPES_WRITTEN');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_GIT_TYPES_WRITTEN --save F34.workspace-git-types/program -- node "$F34/git-types.mjs" "$REPO"
  TSC="node clients/typescript/node_modules/typescript/bin/tsc --noEmit --strict --skipLibCheck --module nodenext --moduleResolution nodenext --target es2022"
  $TSC "$F34/git-contract.mts"
  $TSC "$F34/git-paths.mts"
  $TSC "$F34/git-types.mts"  # bug
  ```
  Expected: both files type-check. Today the contract file compiles and
  the exported type fails with `Type '"UPDATED"' is not assignable to type
  '"added" | "modified" | "deleted" | "renamed"'`.
- **Bash client (`F34.bash-client`).** Background command, lookups, stop and
  clear, with the socket and the REST history as second views.
  ```sh
  cat > "$F34/bash.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, host, apiKey, F34, NOPE, assert, waitFor } from './lib.mjs';
  const bash = new m.RemoteWorkspace({ host, apiKey, workingDir: '/tmp' }).bash;
  const done = (id) => async () => (await bash.searchEvents({ command_id__eq: id, kind__eq: 'BashOutput' }))
    .items.find((e) => e.exit_code !== null && e.exit_code !== undefined);
  const command = await bash.startCommand('echo qa-f34-bash', `${F34}/work`);
  assert.equal(command.kind, 'BashCommand');
  const output = await waitFor(done(command.id), 30000);
  assert.equal(output.stdout, 'qa-f34-bash\n');
  assert.equal(output.exit_code, 0);
  assert.equal((await bash.getEvent(command.id)).command, 'echo qa-f34-bash');
  assert.deepEqual((await bash.batchGetEvents([output.id, NOPE.replaceAll('-', '')])).map((e) => e && e.kind), ['BashOutput', null]);
  assert.deepEqual((await bash.getEvents([command.id, NOPE.replaceAll('-', '')])).map((e) => e && e.kind), ['BashCommand', null]);
  const started = Date.now();
  const sleeper = await bash.startCommand('sleep 30');
  await bash.stopCommand(sleeper.id);
  const stopped = await waitFor(done(sleeper.id), 15000);
  assert.notEqual(stopped.exit_code, 0);
  assert.ok(Date.now() - started < 15000);
  await bash.stopCommand(NOPE);
  await bash.stopCommand(command.id);
  writeFileSync(`${F34}/bash-command-id`, command.id);
  console.log('QA_F34_BASH_OK');
  JS
  control-agent-server exec --timeout 90 --expect-output QA_F34_BASH_OK --save F34.bash-client/program -- node "$F34/bash.mjs"
  BCMD=$(cat "$F34/bash-command-id")
  control-agent-server api GET "/api/bash/bash_events/$BCMD" --expect 200 --check command eq 'echo qa-f34-bash' --check cwd eq "$F34/work"
  control-agent-server ws listen /sockets/bash-events --query resend_mode=all --expect-kind BashCommand --expect-kind BashOutput \
    --duration 3 --save F34.bash-client/socket
  cat > "$F34/bash-clear.mjs" <<'JS'
  import { m, host, apiKey, assert } from './lib.mjs';
  const { cleared_count } = await new m.RemoteWorkspace({ host, apiKey, workingDir: '/tmp' }).bash.clearEvents();
  assert.ok(cleared_count >= 4, `cleared ${cleared_count}`);
  console.log('QA_F34_BASH_CLEAR_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_BASH_CLEAR_OK -- node "$F34/bash-clear.mjs"
  control-agent-server api GET /api/bash/bash_events/search --check items len-eq 0 --save F34.bash-client/cleared
  control-agent-server api GET "/api/bash/bash_events/$BCMD" --expect 404
  ```
  The command's output arrives with exit 0, the lookups return the command
  and its output with `null` for an unknown id, the 30 s `sleep` is stopped
  within seconds with a non-zero exit, and stopping an unknown or finished
  command resolves. REST serves the command with its `cwd`, the bash socket
  replays both kinds, and after `clearEvents` the history is empty and the
  command is 404.
- **Bash events socket from Node (`F34.bash-websocket`), known bug.**
  `BashWebSocketClient` resolves its constructor with `require('ws')` when
  the module loads, and `require` does not exist in the package's ESM build,
  so it never opens a socket in Node 22 although `ws` is a dependency and a
  global `WebSocket` exists (`clients/typescript/src/events/bash-websocket-client.ts:19`).
  The bullet runs its own command, and the CLI first replays its output on
  the same socket (positive control); the program then gives the client
  10 seconds and records the outputs and errors it saw.
  ```sh
  control-agent-server api POST /api/bash/execute_bash_command --json '{"command": "echo qa-f34-bash-ws", "cwd": "/tmp"}' \
    --expect 200 --check stdout contains qa-f34-bash-ws
  control-agent-server ws start /sockets/bash-events --query resend_mode=all --name qa-f34-bash-ws --duration 30
  control-agent-server ws stop qa-f34-bash-ws --kinds BashOutput --contains qa-f34-bash-ws --expect-kind BashOutput --wait 10 \
    --save F34.bash-websocket/cli
  cat > "$F34/bash-ws.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, host, apiKey, F34, sleep } from './lib.mjs';
  const events = [];
  const errors = [];
  const client = new m.BashWebSocketClient({ host, apiKey, resendMode: 'all',
    callback: (e) => events.push(e), onError: (e) => errors.push(e.message) });
  const seen = () => events.some((e) => e.kind === 'BashOutput' && e.stdout === 'qa-f34-bash-ws\n');
  client.start();
  for (const end = Date.now() + 10000; Date.now() < end && !seen();) await sleep(100);
  client.stop();
  writeFileSync(`${F34}/bash-ws.json`, JSON.stringify({ saw_output: seen(), frames: events.length, errors: [...new Set(errors)] }));
  console.log('QA_F34_BASH_WS_DONE');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_BASH_WS_DONE --save F34.bash-websocket/program -- node "$F34/bash-ws.mjs"
  cat "$F34/bash-ws.json"
  jq -e '.saw_output and .errors == []' "$F34/bash-ws.json"  # bug
  ```
  Expected: the replayed `BashOutput` reaches the callback and `onError` is
  never called. Today `saw_output` is false with no frame, and `errors`
  holds "Failed to create bash WebSocket connection: WebSocket
  implementation not available. Install the `ws` package, ..." (once per
  reconnect attempt).
- **Workspace session (`F34.workspace-session`).** The cookie mint and its
  base URL; Node's `fetch` keeps no cookie jar, so the URL is fetched with the
  header (F02 proves the cookie itself).
  ```sh
  cat > "$F34/session.mjs" <<'JS'
  import { m, host, apiKey, NOPE, assert } from './lib.mjs';
  const scid = process.argv[2];
  const workspace = new m.RemoteWorkspace({ host, apiKey, workingDir: '/qa-f34-ignored' });
  const base = await workspace.startWorkspaceSession(scid);
  assert.equal(base, `${host}/api/conversations/${scid}/workspace/`);
  const file = await fetch(base + 'qa-f34-scoped.txt', { headers: { 'X-Session-API-Key': apiKey } });
  assert.equal(file.status, 200);
  assert.equal(await file.text(), 'qa-f34-scoped');
  await workspace.deleteWorkspaceSession();
  const scoped = new m.RemoteWorkspace({ host, apiKey, workingDir: '/x', conversationId: scid });
  await assert.rejects(scoped.startWorkspaceSession(NOPE), /Workspace session must belong to the selected runtime/);
  assert.equal(await scoped.startWorkspaceSession(scid), base);
  await assert.rejects(new m.RemoteWorkspace({ host, workingDir: '/x' }).startWorkspaceSession(scid), (e) => e.status === 401);
  await assert.rejects(new m.RemoteWorkspace({ host, workingDir: '/x' }).deleteWorkspaceSession(), (e) => e.status === 401);
  console.log('QA_F34_SESSION_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_SESSION_OK --save F34.workspace-session/program -- node "$F34/session.mjs" "$SCID"
  control-agent-server api GET "/api/conversations/$SCID/workspace/qa-f34-scoped.txt" --expect 200 --raw-out "$F34/session.copy"
  test "$(cat "$F34/session.copy")" = qa-f34-scoped
  ```
  The mint resolves with `<host>/api/conversations/<id>/workspace/`, which
  serves the file; the delete resolves on 204; a scoped workspace refuses
  another conversation's id without a request; both routes are 401 without a
  key.
- **Create a conversation (`F34.conversation-create`).** A real DeepSeek
  agent with one tool and a hook config, created but not run.
  ```sh
  cat > "$F34/create.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, host, apiKey, F34, assert } from './lib.mjs';
  const agent = new m.Agent({
    llm: { model: 'deepseek/deepseek-flash', api_key: process.env.DEEPSEEK_API_KEY, usage_id: 'agent' },
    tools: [{ name: 'file_editor' }],
  });
  const hookConfig = { pre_tool_use: [{ matcher: '*', hooks: [{ type: 'command', command: 'echo qa-f34-hook' }] }] };
  const conversation = new m.RemoteConversation(agent, new m.RemoteWorkspace({ host, apiKey, workingDir: `${F34}/work` }), { hookConfig });
  assert.throws(() => conversation.id, /Call start\(\)/);
  await conversation.start({ maxIterations: 20 });
  assert.match(conversation.id, /^[0-9a-f-]{36}$/);
  assert.equal(conversation.workspace.conversationId, conversation.id);
  assert.equal(conversation.workspace.workingDir, `${F34}/work`);
  writeFileSync(`${F34}/cid`, conversation.id);
  console.log('QA_F34_CREATE_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_CREATE_OK --save F34.conversation-create/program -- node "$F34/create.mjs"
  CID=$(cat "$F34/cid")
  control-agent-server api GET "/api/conversations/$CID" --expect 200 --max-chars 300 --check execution_status eq idle \
    --check workspace.working_dir eq "$F34/work" --check agent.llm.model eq deepseek/deepseek-flash \
    --check agent.tools.0.name eq file_editor --check max_iterations eq 20 --save F34.conversation-create/server
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . eq 0
  ```
  `id` throws before `start()`; afterwards the conversation exists `idle`
  with the client's model, tool, working directory and iteration limit, and
  has no events yet. The hook config is stored too but only shows in the
  conversation once the agent starts (checked in the next bullet).
- **Send and run (`F34.conversation-run`).** A second program reattaches by
  id, sends one tiny task and runs it on deepseek-flash.
  ```sh
  cat > "$F34/run.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, host, apiKey, F34, assert, waitFor } from './lib.mjs';
  const cid = process.argv[2];
  const placeholder = new m.Agent({ llm: { model: 'qa-f34/unused' } });
  const conversation = new m.RemoteConversation(placeholder,
    new m.RemoteWorkspace({ host, apiKey, workingDir: '/qa-f34-ignored' }), { conversationId: cid });
  await conversation.start();
  assert.equal(conversation.workspace.workingDir, `${F34}/work`);
  await conversation.sendMessage('Use the file_editor tool to create the file qa-f34.txt in the current working directory containing exactly: hi. Then finish.');
  assert.equal((await conversation.state.refresh()).execution_status, 'idle');
  await conversation.run();
  const status = await waitFor(async () => {
    const value = (await conversation.state.refresh()).execution_status;
    return ['finished', 'error', 'stuck'].includes(value) && value;
  }, 240000, 1000);
  assert.equal(status, 'finished');
  assert.equal((await conversation.getHookConfig()).pre_tool_use[0].hooks[0].command, 'echo qa-f34-hook');
  assert.equal((await conversation.workspace.downloadAsText(`${F34}/work/qa-f34.txt`)).trim(), 'hi');
  const stats = await conversation.conversationStats();
  assert.ok(stats.usage_to_metrics.agent.accumulated_token_usage.prompt_tokens > 0);
  const response = await conversation.getAgentFinalResponse();
  assert.equal(typeof response, 'string');
  writeFileSync(`${F34}/final-client.json`, JSON.stringify({ response }));
  const zip = await conversation.downloadTrajectory();
  assert.deepEqual([...new Uint8Array(await zip.slice(0, 2).arrayBuffer())], [0x50, 0x4b]);
  console.log('QA_F34_RUN_OK');
  JS
  control-agent-server exec --timeout 300 --expect-output QA_F34_RUN_OK --save F34.conversation-run/program -- node "$F34/run.mjs" "$CID"
  control-agent-server api GET "/api/conversations/$CID" --max-chars 300 --check execution_status eq finished \
    --check hook_config.pre_tool_use.0.hooks.0.command eq 'echo qa-f34-hook' --save F34.conversation-run/server
  grep -qx hi "$F34/work/qa-f34.txt"
  control-agent-server api GET "/api/conversations/$CID/agent_final_response" --expect 200 --raw-out "$F34/final-rest.json"
  jq -e --slurpfile client "$F34/final-client.json" '.response == $client[0].response' "$F34/final-rest.json"
  control-agent-server api GET "/api/conversations/$CID/events/count" --query source=user --check . eq 1
  control-agent-server conversation events "$CID" --kinds ObservationEvent --contains qa-f34.txt
  control-agent-server conversation events "$CID" --kinds HookExecutionEvent --contains qa-f34-hook
  cat > "$F34/state.mjs" <<'JS'
  import { m, host, apiKey, F34, assert } from './lib.mjs';
  const cid = process.argv[2];
  const conversation = new m.RemoteConversation(new m.Agent({ llm: { model: 'qa-f34/unused' } }),
    new m.RemoteWorkspace({ host, apiKey, workingDir: '/x' }), { conversationId: cid });
  await conversation.start();
  const state = conversation.state;
  assert.equal(state.id, cid);
  assert.equal(await state.getExecutionStatus(), 'finished');
  assert.equal(await state.getAgentStatus(), 'finished');
  assert.equal((await state.getAgent()).llm.model, 'deepseek/deepseek-flash');
  assert.deepEqual(await state.getWorkspace(), { kind: 'LocalWorkspace', working_dir: `${F34}/work` });
  assert.deepEqual(await state.getConfirmationPolicy(), { kind: 'NeverConfirm' });
  assert.ok((await state.getPersistenceDir()).endsWith(cid.replaceAll('-', '')));
  assert.equal(JSON.parse(await state.modelDumpJson()).id, cid);
  assert.ok(Array.isArray(await state.getActivatedKnowledgeSkills()));
  await assert.rejects(state.setAgentStatus('running'), /has no effect/);
  console.log('QA_F34_STATE_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_STATE_OK --save F34.conversation-run/state -- node "$F34/state.mjs" "$CID"
  control-agent-server api GET "/api/conversations/$CID" --max-chars 300 --check execution_status eq finished --check confirmation_policy.kind eq NeverConfirm
  ```
  `start()` with an id rebinds the workspace to the server's working
  directory; the message alone leaves the conversation `idle`; `run()`
  reaches `finished`; the client reads the hook config it sent at creation,
  `hi` from the agent's file, token usage for the `agent` LLM, the final
  response (whatever its form: the `finish` message or the last agent
  message, identical to what REST serves) and a ZIP trajectory (`PK`).
  The server agrees: `finished`, the
  hook config, one user message, a `file_editor` observation naming the file
  and a `HookExecutionEvent` for `echo qa-f34-hook`. A third program reads
  every `RemoteState` accessor back (status, agent model, workspace,
  `NeverConfirm`, a persistence directory named after the id) and
  `setAgentStatus` rejects with `Setting execution_status on RemoteState has
  no effect`.
- **Events list (`F34.events-list`).** Count, filters, pages and lookups
  against the stored log.
  ```sh
  TOTAL=$(control-agent-server api GET "/api/conversations/$CID/events/count" --field .)
  cat > "$F34/events.mjs" <<'JS'
  import { m, host, apiKey, assert } from './lib.mjs';
  const [cid, total] = process.argv.slice(2);
  const events = new m.RemoteEventsList({ baseUrl: host, apiKey }, cid);
  assert.equal(await events.count(), Number(total));
  const all = await events.getEvents();
  assert.equal(all.length, Number(total));
  assert.equal(new Set(all.map((e) => e.id)).size, all.length);
  const users = await events.search({ source: 'user' });
  assert.deepEqual(users.items.map((e) => e.kind), ['MessageEvent']);
  const actions = await events.search({ kind: 'openhands.sdk.event.llm_convertible.action.ActionEvent' });
  assert.ok(actions.items.length >= 1 && actions.items.every((e) => e.kind === 'ActionEvent'));
  assert.ok((await events.search({ body: 'QA-F34.TXT' })).items.length >= 1);
  const first = await events.search({ limit: 2 });
  assert.equal(first.items.length, 2);
  const second = await events.search({ limit: 2, page_id: first.next_page_id });
  assert.ok(!first.items.some((e) => second.items.some((s) => s.id === e.id)));
  assert.equal((await events.getEventById(all[0].id)).id, all[0].id);
  assert.deepEqual((await events.getEventsById([all[1].id, all[2].id])).map((e) => e.id), [all[1].id, all[2].id]);
  let iterated = 0;
  for await (const event of events) iterated += event.id ? 1 : 0;
  assert.equal(iterated, Number(total));
  console.log('QA_F34_EVENTS_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_EVENTS_OK --save F34.events-list/program -- node "$F34/events.mjs" "$CID" "$TOTAL"
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . eq "$TOTAL"
  ```
  `count`, `getEvents` and the async iterator all see `TOTAL` distinct
  events; the user filter returns the one message, the fully-qualified kind
  only actions, the body filter is case-insensitive, pages do not overlap,
  and the lookups by id return the stored events.
- **Unknown event id (`F34.events-unknown-id`), known bug.** The same server
  bug as `F06.unknown-event`, reached through the client: `getEventById`
  expects the documented 404 and `getEventsById` maps it to `null`
  (`clients/typescript/src/events/remote-events-list.ts:110`), but the
  server answers 500 (`Unknown event_id`). Runs on `SCID`, whose first
  event is read back by id first (positive control); the program records
  how each call settled.
  ```sh
  cat > "$F34/unknown-event.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, host, apiKey, F34, NOPE, assert } from './lib.mjs';
  const events = new m.RemoteEventsList({ baseUrl: host, apiKey }, process.argv[2]);
  const [first] = (await events.search({ limit: 1 })).items;
  assert.equal((await events.getEventById(first.id)).id, first.id);
  const settle = (promise) => promise.then((value) => ({ value }),
    (e) => ({ http_error: m.isHttpError(e), status: e.status, response: e.response }));
  const single = await settle(events.getEventById(NOPE).then((e) => e.id));
  const batch = await settle(events.getEventsById([first.id, NOPE]).then((list) => list.map((e) => e && e.id)));
  writeFileSync(`${F34}/unknown-event.json`, JSON.stringify({ first: first.id, single, batch }));
  console.log('QA_F34_UNKNOWN_EVENT_READ');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_UNKNOWN_EVENT_READ --save F34.events-unknown-id/program -- node "$F34/unknown-event.mjs" "$SCID"
  cat "$F34/unknown-event.json"
  jq -e '.single.http_error and .single.status == 404' "$F34/unknown-event.json"  # bug
  jq -e '.batch.value == [.first, null]' "$F34/unknown-event.json"  # bug
  ```
  Expected: a 404 `HttpError`, and `[<event>, null]`. Today both calls
  reject with a 500 `HttpError` whose `response.exception` is
  `'Unknown event_id: 00000000-0000-0000-0000-000000000000'`, so one missing
  id fails the whole `getEventsById`.
- **Short event kinds (`F34.events-search-kind`), known bug.** The same
  server bug as `F06.search-kind-short`, reached through the client: the
  service compares `kind` with `<module>.<ClassName>`
  (`openhands-agent-server/openhands/agent_server/event_service.py:555`)
  while the route and `EventSearchOptions.kind`
  (`clients/typescript/src/events/remote-events-list.ts:23`) document
  `ActionEvent, MessageEvent`. Runs on `SCID`'s queued user message; the
  fully-qualified kind finds it first (positive control); the bug is the
  same for every kind (`F06.search-kind-short` shows it on the REST route).
  ```sh
  cat > "$F34/kind.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, host, apiKey, F34, assert } from './lib.mjs';
  const events = new m.RemoteEventsList({ baseUrl: host, apiKey }, process.argv[2]);
  const qualified = 'openhands.sdk.event.llm_convertible.message.MessageEvent';
  const full = await events.search({ kind: qualified });
  assert.deepEqual(full.items.map((e) => [e.kind, e.source]), [['MessageEvent', 'user']]);
  assert.equal(await events.count({ kind: qualified }), 1);
  const short = (await events.search({ kind: 'MessageEvent' })).items.map((e) => e.id);
  writeFileSync(`${F34}/kind.json`, JSON.stringify({ full: full.items.map((e) => e.id), short,
    short_count: await events.count({ kind: 'MessageEvent' }) }));
  console.log('QA_F34_KIND_READ');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_KIND_READ --save F34.events-search-kind/program -- node "$F34/kind.mjs" "$SCID"
  cat "$F34/kind.json"
  jq -e '.short == .full and .short_count == 1' "$F34/kind.json"  # bug
  ```
  Expected: the short name finds the same message. Today it returns no items
  and a count of 0.
- **Event stream on Node's WebSocket (`F34.event-stream`).** Replay, a turn
  sent over the socket, and a wrong key.
  ```sh
  TOTAL=$(control-agent-server api GET "/api/conversations/$CID/events/count" --field .)
  cat > "$F34/stream.mjs" <<'JS'
  import { m, c, host, apiKey, assert, waitFor } from './lib.mjs';
  const [cid, total] = process.argv.slice(2);
  const stored = (await new m.RemoteEventsList({ baseUrl: host, apiKey }, cid).getEvents()).map((e) => e.id);
  assert.equal(stored.length, Number(total));
  const url = c.buildConversationEventStreamUrl(host, cid);
  assert.equal(url, `${host.replace('http', 'ws')}/sockets/events/${cid}`);
  const frames = [];
  let state = {};
  const stream = new c.ConversationEventStream({ url, sessionApiKey: apiKey, queryParams: { resend_mode: 'all' },
    onMessage: (frame) => frames.push(JSON.parse(frame.data)), onStateChange: (next) => { state = next; } });
  assert.throws(() => stream.send('{}'), /not open/);
  stream.start();
  await waitFor(() => state.isConnected, 10000, 50);
  await waitFor(() => { const seen = new Set(frames.map((f) => f.id)); return stored.every((id) => seen.has(id)); }, 15000, 100);
  stream.send(JSON.stringify({ role: 'user', content: [{ type: 'text', text: 'qa-f34-stream: reply with the single word ok, then finish.' }] }));
  const sent = await waitFor(() => frames.findIndex((f) => f.kind === 'MessageEvent' && f.source === 'user' &&
    JSON.stringify(f).includes('qa-f34-stream')) + 1, 15000, 100);
  await waitFor(() => frames.slice(sent).some((f) => f.kind === 'ConversationStateUpdateEvent' &&
    f.key === 'execution_status' && f.value === 'finished'), 240000, 250);
  stream.stop();
  assert.equal(state.isConnected, false);
  const errors = [];
  const bad = new c.ConversationEventStream({ url, sessionApiKey: 'qa-f34-wrong-key',
    onStateChange: (next) => { if (next.error) errors.push(next.error.message); } });
  bad.start();
  await waitFor(() => errors.length, 10000, 50);
  bad.stop();
  assert.equal(errors[0], 'WebSocket closed with code 4001: Authentication failed');
  const leaky = new c.ConversationEventStream({ url: `${url}?session_api_key=${apiKey}`,
    onStateChange: (next) => { if (next.error) errors.push(next.error.message); } });
  leaky.start();
  leaky.stop();
  assert.equal(errors.at(-1), 'Use sessionApiKey for first-frame authentication, not URL credentials');
  console.log('QA_F34_STREAM_OK');
  JS
  control-agent-server exec --timeout 300 --expect-output QA_F34_STREAM_OK --save F34.event-stream/program -- node "$F34/stream.mjs" "$CID" "$TOTAL"
  control-agent-server api GET "/api/conversations/$CID/events/search" --query body=qa-f34-stream --check items len-ge 1 \
    --check items.0.source eq user --save F34.event-stream/message
  control-agent-server api GET "/api/conversations/$CID" --max-chars 300 --check execution_status eq finished
  control-agent-server api GET "/api/conversations/$CID/events/count" --query source=user --check . eq 2
  ```
  `send` throws before the socket opens; once open, every stored event id
  (read through `RemoteEventsList`) is replayed, the sent message comes back as a user `MessageEvent` and the run
  it starts reaches `finished` on the same socket. A wrong key ends in
  `WebSocket closed with code 4001: Authentication failed`, and a key in the
  URL is refused before connecting. REST shows the second user message and
  `finished`.
- **Conversation callback socket from Node (`F34.ws-callback-client`), known bug.**
  `WebSocketCallbackClient`, which `RemoteConversation.startWebSocketClient()`
  uses, resolves its constructor with `require('ws')` at module load, which
  fails in the ESM build, so in Node it never connects
  (`clients/typescript/src/events/websocket-client.ts:29`). The server sends
  a state snapshot to every new subscriber, as the CLI shows first on
  `SCID` (positive control); the program then gives the client 10 seconds
  and records the event kinds and errors it saw.
  ```sh
  control-agent-server ws listen "/sockets/events/$SCID" --expect-kind ConversationStateUpdateEvent --duration 3 --save F34.ws-callback-client/cli
  cat > "$F34/callback.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, host, apiKey, F34, sleep } from './lib.mjs';
  const events = [];
  const errors = [];
  const conversation = new m.RemoteConversation(new m.Agent({ llm: { model: 'qa-f34/unused' } }),
    new m.RemoteWorkspace({ host, apiKey, workingDir: '/x' }),
    { conversationId: process.argv[2], callback: (e) => events.push(e), onError: (e) => errors.push(e.message) });
  await conversation.start();
  await conversation.startWebSocketClient();
  const seen = () => events.some((e) => e.kind === 'ConversationStateUpdateEvent');
  for (const end = Date.now() + 10000; Date.now() < end && !seen();) await sleep(100);
  await conversation.close();
  writeFileSync(`${F34}/callback.json`, JSON.stringify({ kinds: [...new Set(events.map((e) => e.kind))], errors: [...new Set(errors)] }));
  console.log('QA_F34_CALLBACK_DONE');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_CALLBACK_DONE --save F34.ws-callback-client/program -- node "$F34/callback.mjs" "$SCID"
  cat "$F34/callback.json"
  jq -e '(.kinds | index("ConversationStateUpdateEvent")) != null and .errors == []' "$F34/callback.json"  # bug
  ```
  Expected: the snapshot reaches the conversation callback and `onError` is
  never called. Today `kinds` is empty and `errors` holds "WebSocket
  implementation not available. Install the `ws` package, or run in an
  environment with a global WebSocket constructor." (once per retry).
- **Title and confirmation policy (`F34.conversation-controls`).** Two
  mutations, each read back through REST; the policy is restored.
  ```sh
  cat > "$F34/controls.mjs" <<'JS'
  import { m, host, apiKey, assert } from './lib.mjs';
  const conversation = new m.RemoteConversation(new m.Agent({ llm: { model: 'qa-f34/unused' } }),
    new m.RemoteWorkspace({ host, apiKey, workingDir: '/x' }), { conversationId: process.argv[2] });
  await conversation.start();
  await conversation.setTitle('qa-f34-title');
  await conversation.setConfirmationPolicy({ kind: 'AlwaysConfirm' });
  assert.deepEqual((await conversation.state.refresh()).confirmation_policy, { kind: 'AlwaysConfirm' });
  console.log('QA_F34_CONTROLS_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_CONTROLS_OK --save F34.conversation-controls/program -- node "$F34/controls.mjs" "$CID"
  control-agent-server api GET "/api/conversations/$CID" --max-chars 300 --check title eq qa-f34-title \
    --check confirmation_policy.kind eq AlwaysConfirm --save F34.conversation-controls/server
  control-agent-server api POST "/api/conversations/$CID/confirmation_policy" --json '{"policy": {"kind": "NeverConfirm"}}' --expect 200
  control-agent-server api GET "/api/conversations/$CID" --max-chars 300 --check confirmation_policy.kind eq NeverConfirm
  ```
  The server reports the new title and `AlwaysConfirm`; the REST restore
  puts `NeverConfirm` back.
- **Conversation manager (`F34.manager`).** Listing, lookups, loading,
  creating and deleting.
  ```sh
  COUNT=$(control-agent-server api GET /api/conversations/count --field .)
  FINISHED=$(control-agent-server api GET /api/conversations/count --query status=finished --field .)
  cat > "$F34/manager.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, host, apiKey, F34, NOPE, assert } from './lib.mjs';
  const [cid, scid, count, finished] = process.argv.slice(2);
  const manager = new m.ConversationManager({ host, apiKey });
  assert.equal(await manager.countConversations(), Number(count));
  assert.equal(await manager.countConversations({ status: 'finished' }), Number(finished));
  const first = await manager.searchConversations({ limit: 1 });
  assert.equal(first.items.length, 1);
  assert.ok(first.next_page_id);
  assert.deepEqual((await manager.getAllConversations()).map((x) => x.id).sort(), [cid, scid].sort());
  assert.deepEqual((await manager.getConversations([cid, NOPE, scid])).map((x) => x && x.id), [cid, null, scid]);
  assert.equal((await manager.getConversation(cid)).execution_status, 'finished');
  const loaded = await manager.loadConversation(cid);
  assert.equal(loaded.id, cid);
  assert.equal(loaded.workspace.workingDir, `${F34}/work`);
  await assert.rejects(manager.loadConversation(NOPE), (e) => e.status === 404);
  const created = await manager.createConversation(new m.Agent({ llm: { model: 'qa-f34/unreachable' } }), { workingDir: `${F34}/scoped` });
  assert.equal((await manager.getConversation(created.id)).execution_status, 'idle');
  assert.equal(await manager.countConversations(), Number(count) + 1);
  await manager.deleteConversation(created.id);
  await assert.rejects(manager.getConversation(created.id), (e) => e.status === 404);
  writeFileSync(`${F34}/deleted-id`, created.id);
  console.log('QA_F34_MANAGER_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_MANAGER_OK --save F34.manager/program -- node "$F34/manager.mjs" "$CID" "$SCID" "$COUNT" "$FINISHED"
  control-agent-server api GET "/api/conversations/$(cat "$F34/deleted-id")" --expect 404 --save F34.manager/deleted
  control-agent-server api GET /api/conversations/count --check . eq "$COUNT"
  ```
  The counts match REST, the first page of one has a `next_page_id`,
  `getAllConversations` pages through both conversations, the batch keeps
  input order with `null` for the unknown id, the loaded conversation carries
  its server working directory, and the created conversation is gone after
  the delete (404, count back to `COUNT`).
- **Update result (`F34.manager-update-result`), known bug.** `PATCH
  /api/conversations/{id}` answers `Success` (`{"success": true}`, also in
  the client's generated contract), but `ConversationManager.updateConversation`
  and `ConversationClient.updateConversation` are typed to resolve with the
  updated `ConversationInfo` (`clients/typescript/src/conversation/conversation-manager.ts:384`,
  `clients/typescript/src/client/conversation-client.ts:430`).
  The program checks that the title was stored (positive control, also
  through REST) and records what the call resolved with.
  ```sh
  cat > "$F34/update.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, host, apiKey, F34, assert } from './lib.mjs';
  const scid = process.argv[2];
  const manager = new m.ConversationManager({ host, apiKey });
  const result = await manager.updateConversation(scid, { title: 'qa-f34-renamed' });
  assert.equal((await manager.getConversation(scid)).title, 'qa-f34-renamed');
  writeFileSync(`${F34}/update.json`, JSON.stringify(result));
  console.log('QA_F34_UPDATE_READ');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_UPDATE_READ --save F34.manager-update-result/program -- node "$F34/update.mjs" "$SCID"
  control-agent-server api GET "/api/conversations/$SCID" --max-chars 300 --check title eq qa-f34-renamed
  cat "$F34/update.json"
  jq -e --arg id "$SCID" '.id == $id and .title == "qa-f34-renamed"' "$F34/update.json"  # bug
  ```
  Expected: the stored title and a `ConversationInfo` with the conversation's
  `id` and new title. Today the title is stored but the promise resolves with
  `{success: true}`, so `result.id` and `result.title` are `undefined`.
- **Create options (`F34.manager-create-options`), known bug.**
  `ConversationManager.createConversation` hands `maxIterations` and
  `stuckDetection` to the `RemoteConversation` constructor, which ignores
  them, and calls `start()` without them, so the server always gets
  `max_iterations` 500 and `stuck_detection: true`
  (`clients/typescript/src/conversation/conversation-manager.ts:221`).
  `RemoteConversation.start({maxIterations, stuckDetection})` does forward
  both, as the program shows first (positive control). It records what the
  server stored for the manager's conversation, deletes both conversations,
  and REST confirms they are gone.
  ```sh
  cat > "$F34/create-options.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, host, apiKey, F34, assert } from './lib.mjs';
  const manager = new m.ConversationManager({ host, apiKey });
  const agent = new m.Agent({ llm: { model: 'qa-f34/unreachable' } });
  const direct = new m.RemoteConversation(agent, new m.RemoteWorkspace({ host, apiKey, workingDir: `${F34}/scoped` }));
  await direct.start({ maxIterations: 7, stuckDetection: false });
  const control = await manager.getConversation(direct.id);
  await manager.deleteConversation(direct.id);
  assert.deepEqual([control.max_iterations, control.stuck_detection], [7, false]);
  const created = await manager.createConversation(agent, { workingDir: `${F34}/scoped`, maxIterations: 7, stuckDetection: false });
  const info = await manager.getConversation(created.id);
  await manager.deleteConversation(created.id);
  writeFileSync(`${F34}/create-options.json`, JSON.stringify({ ids: [direct.id, created.id],
    max_iterations: info.max_iterations, stuck_detection: info.stuck_detection }));
  console.log('QA_F34_CREATE_OPTIONS_READ');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_CREATE_OPTIONS_READ --save F34.manager-create-options/program -- node "$F34/create-options.mjs"
  for ID in $(jq -r '.ids[]' "$F34/create-options.json"); do control-agent-server api GET "/api/conversations/$ID" --expect 404; done
  cat "$F34/create-options.json"
  jq -e '.max_iterations == 7 and .stuck_detection == false' "$F34/create-options.json"  # bug
  ```
  Expected: `max_iterations` 7 and `stuck_detection: false`, as the
  direct `start()` gets. Today the manager's conversation has
  `{"max_iterations":500,"stuck_detection":true}`. Both conversations are
  deleted either way.
- **ACP conversations (`F34.manager-acp`).** `ConversationManager.acp` with
  the inline ACP Codex agent F12 uses; the ACP subprocess only starts on
  the first run, so nothing runs and no Codex binary is needed.
  ```sh
  COUNT=$(control-agent-server api GET /api/conversations/count --field .)
  mkdir -p "$F34/acp"
  cat > "$F34/acp.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, host, apiKey, F34, NOPE, assert } from './lib.mjs';
  const count = Number(process.argv[2]);
  const manager = new m.ConversationManager({ host, apiKey });
  const agent = { kind: 'ACPAgent', acp_command: ['codex-acp'], acp_server: 'codex' };
  const created = await manager.acp.createConversation(agent, { workingDir: `${F34}/acp`, maxIterations: 9 });
  writeFileSync(`${F34}/acp-id`, created.id);
  assert.deepEqual([created.agent.kind, created.agent.acp_server, created.max_iterations], ['ACPAgent', 'codex', 9]);
  const got = await manager.acp.getConversation(created.id);
  assert.deepEqual(got.agent.acp_command, ['codex-acp']);
  assert.equal(got.workspace.working_dir, `${F34}/acp`);
  assert.deepEqual((await manager.acp.getConversations([created.id, NOPE])).map((x) => x && x.agent.kind), ['ACPAgent', null]);
  assert.ok((await manager.acp.getAllConversations()).some((x) => x.id === created.id && x.agent.kind === 'ACPAgent'));
  assert.equal(await manager.acp.countConversations(), count + 1);
  assert.equal((await manager.acp.searchConversations({ limit: 100 })).items.filter((x) => x.agent.kind === 'ACPAgent').length, 1);
  console.log('QA_F34_ACP_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_ACP_OK --save F34.manager-acp/program -- node "$F34/acp.mjs" "$COUNT"
  ACPID=$(cat "$F34/acp-id")
  control-agent-server api GET "/api/conversations/$ACPID" --max-chars 300 --check agent.kind eq ACPAgent --check agent.acp_server eq codex \
    --check max_iterations eq 9 --check execution_status eq idle --save F34.manager-acp/server
  control-agent-server api DELETE "/api/conversations/$ACPID" --expect 200
  control-agent-server api GET /api/conversations/count --check . eq "$COUNT"
  ```
  The namespace creates the conversation with the ACP agent and its
  `max_iterations` 9 (unlike `createConversation`, it forwards the option),
  and the get, batch get (`null` for the unknown id), list, count and search
  all return it with its ACP fields; REST agrees, and the delete puts the
  count back.
- **Fork, navigate and ask (`F34.conversation-branching`).** On `CID`, the
  finished DeepSeek conversation; the fork is deleted at the end.
  ```sh
  TOTAL=$(control-agent-server api GET "/api/conversations/$CID/events/count" --field .)
  cat > "$F34/branching.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, host, apiKey, F34, assert } from './lib.mjs';
  const [cid, total] = process.argv.slice(2);
  const conversation = new m.RemoteConversation(new m.Agent({ llm: { model: 'qa-f34/unused' } }),
    new m.RemoteWorkspace({ host, apiKey, workingDir: '/x' }), { conversationId: cid });
  await conversation.start();
  const fork = await conversation.fork({ title: 'qa-f34-fork' });
  writeFileSync(`${F34}/fork-id`, fork.id);
  assert.ok(fork instanceof m.RemoteConversation && fork.id !== cid);
  assert.equal(fork.workspace.conversationId, fork.id);
  assert.equal(fork.workspace.workingDir, `${F34}/work`);
  assert.equal(fork.agent.llm.model, 'deepseek/deepseek-flash');
  assert.equal(await fork.state.events.count(), Number(total));
  const before = await fork.state.refresh();
  assert.equal(before.title, 'qa-f34-fork');
  const [first] = (await fork.state.events.search({ limit: 1 })).items;
  assert.notEqual(before.leaf_event_id, first.id);
  await fork.navigateTo(first.id);
  assert.equal((await fork.state.modelDump()).leaf_event_id, first.id);
  writeFileSync(`${F34}/fork-leaf`, first.id);
  assert.equal(typeof (await conversation.askAgent('In one short sentence: which file did you create?')), 'string');
  assert.equal(await conversation.state.events.count(), Number(total));
  console.log('QA_F34_BRANCHING_OK');
  JS
  control-agent-server exec --timeout 180 --expect-output QA_F34_BRANCHING_OK --save F34.conversation-branching/program -- node "$F34/branching.mjs" "$CID" "$TOTAL"
  FORK=$(cat "$F34/fork-id")
  control-agent-server api GET "/api/conversations/$FORK" --max-chars 300 --check forked_from_conversation_id eq "$CID" \
    --check title eq qa-f34-fork --check leaf_event_id eq "$(cat "$F34/fork-leaf")" --save F34.conversation-branching/fork
  control-agent-server api GET "/api/conversations/$CID" --max-chars 300 --check execution_status eq finished --check leaf_event_id ne "$(cat "$F34/fork-leaf")"
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . eq "$TOTAL"
  control-agent-server api DELETE "/api/conversations/$FORK" --expect 200
  ```
  `fork` returns a `RemoteConversation` bound to the fork: its own id,
  a workspace scoped to it in the source's working directory, the source's
  agent model, and all `TOTAL` copied events. With the state cached right
  before, `navigateTo(<first event>)` leaves the cached state on the new
  HEAD, which GET confirms (the source's HEAD did not move). `askAgent`
  resolves with a string and adds no event to the source, which is still
  `finished`.
- **Confirmation and secrets (`F34.conversation-confirm`).** A DeepSeek
  conversation with the `terminal` tool under `AlwaysConfirm`; two runtime
  secrets (a string and a function value) whose hashes the first command
  writes. The program accepts every proposal of the first task until the
  agent finishes, then rejects the proposal of a second, harmless task (a
  real model asked to retry a rejected command often refuses, so the
  reject comes last). It only waits on statuses and observation counts,
  never on the model's wording.
  ```sh
  export QA_F34_SECRET="qa-f34-secret-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
  mkdir -p "$F34/confirm"
  cat > "$F34/confirm.mjs" <<'JS'
  import { writeFileSync } from 'node:fs';
  import { m, host, apiKey, F34, assert, waitFor } from './lib.mjs';
  const secret = process.env.QA_F34_SECRET;
  const REJECT = 'openhands.sdk.event.llm_convertible.observation.UserRejectObservation';
  const OBSERVATION = 'openhands.sdk.event.llm_convertible.observation.ObservationEvent';
  const agent = new m.Agent({
    llm: { model: 'deepseek/deepseek-flash', api_key: process.env.DEEPSEEK_API_KEY, usage_id: 'agent' },
    tools: [{ name: 'terminal' }],
  });
  const conversation = new m.RemoteConversation(agent, new m.RemoteWorkspace({ host, apiKey, workingDir: `${F34}/confirm` }));
  await conversation.start({ maxIterations: 20 });
  writeFileSync(`${F34}/confirm-id`, conversation.id);
  await conversation.setConfirmationPolicy({ kind: 'AlwaysConfirm' });
  await conversation.updateSecrets({ QA_F34_TOKEN: secret, QA_F34_TOKEN_FN: () => `${secret}-fn` });
  const settled = (wanted) => waitFor(async () => {
    const status = (await conversation.state.refresh()).execution_status;
    if (['error', 'stuck'].includes(status)) throw new Error(`conversation ${status}`);
    return wanted.includes(status) && status;
  }, 240000, 500);
  const observations = () => conversation.state.events.count({ kind: OBSERVATION });
  const command = 'printf %s "$QA_F34_TOKEN" | sha256sum | cut -c1-16 > qa-f34-confirm.txt; ' +
    'printf %s "$QA_F34_TOKEN_FN" | sha256sum | cut -c1-16 >> qa-f34-confirm.txt';
  await conversation.sendMessage(`Use the terminal tool to run exactly this one command, then finish: ${command}`);
  await conversation.run();
  let accepted = 0;
  while ((await settled(['waiting_for_confirmation', 'finished'])) === 'waiting_for_confirmation' && accepted < 8) {
    const observed = await observations();
    await conversation.sendConfirmationResponse(true);
    accepted += 1;
    await waitFor(async () => (await observations()) > observed, 120000, 500);
  }
  assert.ok(accepted >= 1, 'the agent never asked for confirmation');
  assert.equal((await conversation.state.refresh()).execution_status, 'finished');
  const before = await observations();
  await conversation.sendMessage('Use the terminal tool to run exactly this one command, then finish: echo qa-f34-rejected > qa-f34-rejected.txt');
  await conversation.run();
  assert.equal(await settled(['waiting_for_confirmation', 'finished']), 'waiting_for_confirmation');
  await conversation.sendConfirmationResponse(false, 'qa-f34 rejected');
  assert.equal(await settled(['idle']), 'idle');
  const rejected = await conversation.state.events.search({ kind: REJECT });
  assert.deepEqual(rejected.items.map((e) => e.rejection_reason), ['qa-f34 rejected']);
  assert.equal(await observations(), before);
  console.log('QA_F34_CONFIRM_OK');
  JS
  control-agent-server exec --timeout 600 --env QA_F34_SECRET="$QA_F34_SECRET" --expect-output QA_F34_CONFIRM_OK \
    --save F34.conversation-confirm/program -- node "$F34/confirm.mjs"
  CFID=$(cat "$F34/confirm-id")
  control-agent-server api GET "/api/conversations/$CFID" --max-chars 300 --check execution_status eq idle \
    --check confirmation_policy.kind eq AlwaysConfirm --save F34.conversation-confirm/server
  control-agent-server api GET "/api/conversations/$CFID/events/search" \
    --query kind=openhands.sdk.event.llm_convertible.observation.UserRejectObservation \
    --check items len-eq 1 --check items.0.rejection_reason eq 'qa-f34 rejected' --check items.0.tool_name eq terminal \
    --save F34.conversation-confirm/rejected
  control-agent-server conversation events "$CFID" --kinds ActionEvent --contains qa-f34-rejected.txt --expect-min 1
  test ! -e "$F34/confirm/qa-f34-rejected.txt"
  test "$(wc -l < "$F34/confirm/qa-f34-confirm.txt")" -eq 2
  test "$(sed -n 1p "$F34/confirm/qa-f34-confirm.txt")" = "$(printf %s "$QA_F34_SECRET" | sha256sum | cut -c1-16)"
  test "$(sed -n 2p "$F34/confirm/qa-f34-confirm.txt")" = "$(printf %s "$QA_F34_SECRET-fn" | sha256sum | cut -c1-16)"
  control-agent-server conversation events "$CFID" --kinds ActionEvent --contains QA_F34_TOKEN_FN --expect-min 1
  control-agent-server conversation events "$CFID" --contains "$QA_F34_SECRET" --expect-count 0
  control-agent-server state grep --env-value QA_F34_SECRET --glob 'server/**/*' --expect-none
  control-agent-server state grep --env-value QA_F34_SECRET --glob 'home/**/*' --expect-none
  ```
  Every accept runs the pending command and the agent finishes; the file
  holds the 16-character SHA-256 prefixes of both secret values, so the
  string and the function value both reached the shell, while the proposal
  names only the variables: no event and no file the server persisted
  contains the value. The second task's proposal waits for confirmation;
  the reject records one `UserRejectObservation` with `qa-f34 rejected`
  (REST agrees: terminal tool), runs nothing (no new observation, no
  `qa-f34-rejected.txt`) and leaves the conversation `idle`.
- **Hooks (`F34.hooks-client`).** Project hooks through `HooksClient` and
  `RemoteConversation.loadHooks`.
  ```sh
  cat > "$F34/hooks.mjs" <<'JS'
  import { m, host, apiKey, F34, assert } from './lib.mjs';
  const [cid, project] = process.argv.slice(2);
  const hooks = new m.HooksClient({ host, apiKey });
  const { hook_config: config } = await hooks.loadHooks({ project_dir: project });
  assert.ok(m.hasHooksForEvent(config, m.HookEventType.PRE_TOOL_USE));
  assert.ok(!m.hasHooksForEvent(config, m.HookEventType.STOP));
  assert.deepEqual(m.getHooksForEvent(config, m.HookEventType.PRE_TOOL_USE, 'terminal').map((h) => h.command), ['echo QA_PROJECT_HOOK']);
  assert.deepEqual(await hooks.loadHooks({ project_dir: `${F34}/files` }), { hook_config: null });
  const conversation = new m.RemoteConversation(new m.Agent({ llm: { model: 'qa-f34/unused' } }),
    new m.RemoteWorkspace({ host, apiKey, workingDir: '/x' }), { conversationId: cid });
  await conversation.start();
  assert.deepEqual(await conversation.loadHooks(project), config);
  console.log('QA_F34_HOOKS_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_HOOKS_OK --save F34.hooks-client/program -- node "$F34/hooks.mjs" "$CID" "$PROJECT"
  control-agent-server api POST /api/hooks --json "{\"project_dir\": \"$PROJECT\"}" --expect 200 \
    --check hook_config.pre_tool_use.0.hooks.0.command eq 'echo QA_PROJECT_HOOK'
  ```
  The `PreToolUse` entry of `hooks.json` comes back under `pre_tool_use`,
  which `hasHooksForEvent` and `getHooksForEvent` read; a directory without
  hooks gives `{hook_config: null}`; the conversation's `loadHooks` returns
  the same config.
- **MCP test (`F34.mcp-client`).** The stdio fixture, a tool call, a server
  that dies and a bad body.
  ```sh
  MCPN=$(control-agent-server api GET /api/settings --field agent_settings.mcp_config | jq length)
  cat > "$F34/mcp.mjs" <<'JS'
  import { m, host, apiKey, assert } from './lib.mjs';
  const script = process.env.AGENT_SERVER_VERIFY_RUN + '/fixtures/mcp/qa-f34-mcp.py';
  const server = { type: 'stdio', command: process.cwd() + '/.venv/bin/python', args: [script] };
  const mcp = new m.MCPClient({ host, apiKey });
  assert.deepEqual(await mcp.testServer({ name: 'qa-f34-mcp', server, timeout: 60 }), { ok: true, tools: ['qa_echo'] });
  const called = await mcp.testServer({ server, timeout: 60, tool_call: { name: 'qa_echo', arguments: { text: 'qa-f34' } } });
  assert.deepEqual(called.tool_result, { is_error: false, text: 'QA_MCP_ECHO:qa-f34' });
  const failed = await mcp.testServer({ server: { type: 'stdio', command: '/bin/false', args: [] }, timeout: 20 });
  assert.equal(failed.ok, false);
  assert.ok(['timeout', 'connection', 'unknown'].includes(failed.error_kind) && failed.error.length > 0);
  await assert.rejects(mcp.testServer({ server: { type: 'stdio', command: '' } }),
    (e) => e.status === 422 && e.detail === 'String should have at least 1 character');
  console.log('QA_F34_MCP_OK');
  JS
  control-agent-server exec --timeout 180 --expect-output QA_F34_MCP_OK --save F34.mcp-client/program -- node "$F34/mcp.mjs"
  control-agent-server api GET /api/settings --max-chars 300 --check agent_settings.mcp_config.qa-f34-mcp missing \
    --check agent_settings.mcp_config len-eq "$MCPN"
  ```
  The fixture lists `qa_echo` and the tool call returns its text; `/bin/false`
  is a 200 `{ok: false}` with an `error_kind`; an empty command is a 422
  `HttpError` with the validator's message. The test persists nothing: the
  settings' flat `mcp_config` server map has no `qa-f34-mcp` entry and the
  same size as before.
- **Restart (`F34.restart-reload`).** A restart, then a fresh client.
  ```sh
  TOTAL=$(control-agent-server api GET "/api/conversations/$CID/events/count" --field .)
  control-agent-server restart
  cat > "$F34/restart.mjs" <<'JS'
  import { m, host, apiKey, F34, assert } from './lib.mjs';
  const [cid, total] = process.argv.slice(2);
  const manager = new m.ConversationManager({ host, apiKey });
  const conversation = await manager.loadConversation(cid);
  assert.equal(await conversation.state.getExecutionStatus(), 'finished');
  assert.equal((await manager.getConversation(cid)).title, 'qa-f34-title');
  assert.equal(await conversation.state.events.count(), Number(total));
  assert.equal((await conversation.getHookConfig()).pre_tool_use[0].hooks[0].command, 'echo qa-f34-hook');
  assert.equal((await conversation.workspace.downloadAsText(`${F34}/work/qa-f34.txt`)).trim(), 'hi');
  console.log('QA_F34_RESTART_OK');
  JS
  control-agent-server exec --timeout 60 --expect-output QA_F34_RESTART_OK --save F34.restart-reload/program -- node "$F34/restart.mjs" "$CID" "$TOTAL"
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . eq "$TOTAL"
  ```
  After the restart the conversation is still `finished` with its title,
  all `TOTAL` events, its hook config and the agent's file.
- **Event stream reconnect (`F34.event-stream-reconnect`).** A
  `ConversationEventStream` with `reconnect: {enabled: true}` and
  `resend_mode: 'all'`, on the `ws` package's constructor (`createWebSocket`,
  the adapter the stream takes for environments without a usable native
  socket), watching its own conversation that never runs. The watcher from
  `Preconditions:` runs in the background; once the stream has replayed the
  queued message, the CLI restarts the server and posts a second message
  (`run: false`).
  ```sh
  RCID=$(control-agent-server conversation start --no-run --placeholder-agent --tools none --no-autotitle --workspace "$F34/scoped" \
    --prompt 'qa-f34-reconnect: never run' --print-id)
  rm -f "$F34/reconnect-ready"
  control-agent-server exec --timeout 300 --expect-output QA_F34_RECONNECT_WATCHED --save F34.event-stream-reconnect/program \
    -- node "$F34/reconnect.mjs" "$RCID" "$F34/reconnect-ws.json" ws &
  RPID=$!
  for _ in $(seq 150); do if [ -e "$F34/reconnect-ready" ]; then break; fi; sleep 0.2; done
  test -e "$F34/reconnect-ready"
  control-agent-server restart
  control-agent-server api POST "/api/conversations/$RCID/events" \
    --json '{"role": "user", "content": [{"type": "text", "text": "qa-f34-after-restart"}], "run": false}' --expect 200
  wait "$RPID"
  cat "$F34/reconnect-ws.json"
  jq -e '.ws | .went_down and .attempts >= 1 and .reconnected and .replayed_after >= 1 and .sent_after >= 1' "$F34/reconnect-ws.json"
  control-agent-server api GET "/api/conversations/$RCID/events/count" --query source=user --check . eq 2 --save F34.event-stream-reconnect/server
  control-agent-server api GET "/api/conversations/$RCID" --max-chars 300 --check execution_status eq idle
  ```
  The restart closes the socket (1012) and the stream goes to
  reconnecting by itself; refused attempts close with 1006 and back off
  (1, 2, 4, 8 s plus jitter) until the server is back, then it is
  connected again. The reconnect sent `resend_mode=all` again, so the
  queued message was replayed on the new socket, and the message posted
  after the restart arrived on it (live or in that replay). REST shows both
  user messages and the conversation still `idle`.
- **Reconnect on Node's own WebSocket (`F34.event-stream-reconnect-native`), known bug.**
  The same restart with two streams side by side: the `ws`-backed one
  (positive control) and one on Node's global `WebSocket`, which
  `F34.event-stream` uses and the README presents as all the stream
  needs. In Node 22 (undici 6), a handshake to a port that refuses fires
  `error` but never `close`, and the stream schedules its retries only
  from `onclose` (`clients/typescript/src/client/conversation-event-stream.ts:145`);
  its own 10 s handshake abort only calls `close()`, which fires no
  `close` either. So the first refused attempt during the restart is the
  last one.
  ```sh
  RNID=$(control-agent-server conversation start --no-run --placeholder-agent --tools none --no-autotitle --workspace "$F34/scoped" \
    --prompt 'qa-f34-reconnect: never run' --print-id)
  rm -f "$F34/reconnect-ready"
  control-agent-server exec --timeout 300 --expect-output QA_F34_RECONNECT_WATCHED --save F34.event-stream-reconnect-native/program \
    -- node "$F34/reconnect.mjs" "$RNID" "$F34/reconnect-native.json" ws native &
  RPID=$!
  for _ in $(seq 150); do if [ -e "$F34/reconnect-ready" ]; then break; fi; sleep 0.2; done
  test -e "$F34/reconnect-ready"
  control-agent-server restart
  control-agent-server api POST "/api/conversations/$RNID/events" \
    --json '{"role": "user", "content": [{"type": "text", "text": "qa-f34-after-restart"}], "run": false}' --expect 200
  wait "$RPID"
  cat "$F34/reconnect-native.json"
  jq -e '.ws.reconnected and .ws.sent_after >= 1 and .native.went_down' "$F34/reconnect-native.json"
  jq -e '.native.reconnected and .native.sent_after >= 1' "$F34/reconnect-native.json"  # bug
  ```
  Expected: both streams come back and see the message posted after the
  restart. Today the `ws`-backed stream does, while the native one stays
  `{"isConnected": false, "isReconnecting": true, "attemptCount": 1}`
  for the whole minute the watcher gives it, never retrying.
- **Endpoint audit against this checkout (`F34.endpoint-audit`).** The
  client's own drift tooling, offline, in a fixture directory (nothing is
  written into `clients/typescript`).
  ```sh
  A="$F34/audit"
  mkdir -p "$A/home"
  HOME="$A/home" OPENHANDS_SUPPRESS_BANNER=1 .venv/bin/python .github/scripts/export_agent_server_openapi.py --output "$A/openapi.json" > "$A/export.log" 2>&1
  jq -e '.paths["/api/conversations"].post and .paths["/api/bash/execute_bash_command"].post' "$A/openapi.json" > /dev/null
  for f in package.json src .prettierrc; do ln -sfn "$PWD/clients/typescript/$f" "$A/$f"; done
  rm -f "$A/endpoint-audit.config.json"
  jq '.allowClientOnly |= map(select(.endpoints | index("GET /alive")))' clients/typescript/endpoint-audit.config.json > "$A/endpoint-audit.config.json"
  control-agent-server exec --cwd "$A" --env AGENT_SERVER_OPENAPI_PATH="$A/openapi.json" --timeout 120 \
    --expect-output 'endpoint audit passed' --save F34.endpoint-audit/audit -- node "$PWD/clients/typescript/scripts/endpoint-audit.mjs"
  jq -e '.clientOnly == [] and .client > 100' "$A/.audit/endpoint-audit.json"
  jq -e '[.allowedClientOnly[].endpoint] == ["GET /", "GET /alive", "GET /health", "GET /ready", "GET /server_info"]' "$A/.audit/endpoint-audit.json"
  jq -r '.serverOnly[]' "$A/.audit/endpoint-audit.json" > "$A/server-only.txt"
  control-agent-server exec --env AGENT_SERVER_OPENAPI_PATH="$A/openapi.json" --env AGENT_SERVER_GENERATED_OUTPUT="$A/agent-server-schema.ts" \
    --timeout 180 --save F34.endpoint-audit/generate -- node clients/typescript/scripts/generate-agent-server-api.mjs
  node clients/typescript/scripts/summarize-agent-server-api.mjs --before clients/typescript/src/generated/agent-server-schema.ts \
    --after "$A/agent-server-schema.ts" --output "$A/summary.md"
  grep -Eqx 'No generated TypeScript contract changes\.|- Removed exported types: none' "$A/summary.md"
  ```
  `.github/scripts/export_agent_server_openapi.py` writes the same public
  contract CI uses for unreleased versions. The audit runs with a copy of
  `endpoint-audit.config.json` whose `allowClientOnly` keeps only the five
  operational routes the public contract omits (F01 proves them), so its
  client-ahead exceptions (meta-profiles, profile validation) are checked
  too; the report-only gate never fails on client-only calls, which is why
  the recipe asserts `clientOnly == []` on the JSON itself. It reports no
  client-only call (every handwritten call exists on this checkout);
  `server-only.txt` lists the operations without a literal client
  call (43 on the baseline: the conversation-scoped bash, file, git and VS
  Code aliases the client reaches by rewriting paths at runtime, the canvas
  extension routes, whose backend calls a helper builds, provider
  connections, `/api/init`, the `GET .../events` batch, archives, commits,
  `create_directory` and `load_plugin`). Regenerating the client's types
  from this checkout removes no exported type (on the baseline the diff is
  `+13 / -1` lines: two new fields of the canvas backend artifact).
- **Old server version (`F34.version-gate-old`).** A real 1.22.0 release of
  the agent server, installed with `uvx`, attached and stopped inside the
  bullet.
  ```sh
  OLD="$F34/old-server"
  mkdir -p "$OLD/home" "$OLD/persist"
  OLDPORT=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
  (cd "$OLD" && HOME="$OLD/home" OH_PERSISTENCE_DIR="$OLD/persist" OH_SESSION_API_KEYS_0=qa-f34-old-key COLUMNS=400 exec timeout 600 \
    uvx --python 3.13 --from 'openhands-agent-server==1.22.0' --with 'openhands-sdk==1.22.0' --with 'openhands-tools==1.22.0' \
    --with 'openhands-workspace==1.22.0' python -m openhands.agent_server --host 127.0.0.1 --port "$OLDPORT" > "$OLD/server.log" 2>&1) &
  OLDPID=$!
  OLDRUN=""
  trap 'if [ -n "$OLDRUN" ]; then control-agent-server stop --run "$OLDRUN" > /dev/null 2>&1; fi; kill -TERM -- "-$OLDPID" 2>/dev/null || kill -TERM "$OLDPID" 2>/dev/null || true' EXIT
  export QA_F34_OLD_KEY=qa-f34-old-key
  OLDRUN=$(control-agent-server attach --url "http://127.0.0.1:$OLDPORT" --key-env QA_F34_OLD_KEY --name f34-old --print-run)
  curl -s --noproxy '*' --retry 150 --retry-connrefused --retry-delay 2 --retry-max-time 300 -o /dev/null "http://127.0.0.1:$OLDPORT/alive"
  control-agent-server api GET /server_info --run "$OLDRUN" --auth none --expect 200 --check version eq 1.22.0 --save F34.version-gate-old/info
  cat > "$F34/gate-old.mjs" <<'JS'
  import { m, assert } from './lib.mjs';
  const host = process.argv[2];
  const apiKey = process.env.QA_F34_OLD_KEY;
  const old = (min) => (e) => m.isAgentServerVersionError(e) && e.code === 'AGENT_SERVER_VERSION_TOO_OLD' &&
    e.requiredVersion === min && e.actualVersion === '1.22.0' && !m.isHttpError(e);
  await assert.rejects(new m.HooksClient({ host, apiKey }).loadHooks({}), old('1.23.0'));
  await assert.rejects(new m.WorkspacesClient({ host, apiKey }).listWorkspaces(), old('1.23.0'));
  const mcp = new m.MCPClient({ host, apiKey });
  await assert.rejects(mcp.testServer({ server: { type: 'stdio', command: '/bin/true' } }), old('1.23.0'));
  await assert.rejects(mcp.getOAuthStatus('qa-f34-job'), old('1.31.0'));
  const ungated = new m.ConversationManager({ host, apiKey });
  assert.equal(await ungated.countConversations(), 0);
  console.log('QA_F34_GATE_OLD_OK');
  JS
  control-agent-server exec --run "$OLDRUN" --timeout 60 --env QA_F34_OLD_KEY=qa-f34-old-key --expect-output QA_F34_GATE_OLD_OK \
    --save F34.version-gate-old/program -- node "$F34/gate-old.mjs" "http://127.0.0.1:$OLDPORT"
  grep -q '"GET /server_info HTTP' "$OLD/server.log"
  grep -q '"GET /api/conversations/count HTTP' "$OLD/server.log"
  if grep -Eq '/api/hooks|/api/workspaces|/api/mcp/' "$OLD/server.log"; then false; fi
  control-agent-server stop --run "$OLDRUN"
  kill -TERM -- "-$OLDPID" 2>/dev/null || kill -TERM "$OLDPID"
  trap - EXIT
  ```
  `curl --retry-connrefused` waits for the port (the CLI's `--until-ok` does
  not retry a refused connection). The old server reports `1.22.0`; every
  gated method rejects with
  `AgentServerVersionError` (`AGENT_SERVER_VERSION_TOO_OLD`, required
  `1.23.0` or `1.31.0`, actual `1.22.0`), not an `HttpError`, while an
  ungated call still works. The old server's access log has the
  `/server_info` probes and the count but no gated route.

## Gotchas

- The package is ESM only. Import `clients/typescript/dist/index.js` (most
  classes) and `dist/clients.js` (`ServerClient`, `FileClient`,
  `ConversationEventStream`, `AgentServerClient`); `ConversationEventStream`
  is not exported from the main entry. `dist/` is git-ignored and is not
  rebuilt by anything but `npm run build` in `clients/typescript`; a stale
  build silently verifies old client code, which is why `Preconditions:`
  rebuilds whenever `src/` is newer.
- In Node, only `ConversationEventStream` streams events (it uses the global
  `WebSocket`, Node 22 or later). `WebSocketCallbackClient`,
  `RemoteConversation.startWebSocketClient()` and `BashWebSocketClient` look
  for `window.WebSocket` or `require('ws')` when their module loads; the ESM
  build has no `require`, so they only report errors through `onError`
  (`F34.ws-callback-client`, `F34.bash-websocket`). The unit tests pass
  because vitest provides `require` and a fake `window`.
- Node 22's global `WebSocket` (undici 6) fires `error` but no `close` when
  a handshake is refused, so a `ConversationEventStream` on it stops
  retrying after the first refused reconnect attempt (a server restart
  always produces one) and stays `isReconnecting` forever
  (`F34.event-stream-reconnect-native`). Pass the `ws` package's
  constructor as `createWebSocket` for a Node consumer that must survive a
  restart (`F34.event-stream-reconnect`).
- A real model shown a `UserRejectObservation` often refuses to propose
  the same command again ("a deliberate block"), so a recipe that needs
  both an accept and a reject accepts first and rejects a later, different
  proposal (`F34.conversation-confirm`). After a reject the conversation is
  `idle`, not `finished`.
- A message sent over the events socket always runs the agent (the server
  ignores `run`), unlike `sendMessage`, which posts `run: false`. Do not send
  socket frames to a conversation whose agent cannot reach a model.
- `RemoteState` caches `GET /api/conversations/{id}` for 2 seconds; poll with
  `state.refresh()` when waiting for a status.
- `executeCommand` always reports `timeout_occurred: false`; a command killed
  by its own timeout shows exit `-1` (the server sends no timeout marker), and
  `exit_code` defaults to 0 when the server sends none. The HTTP request
  waits `timeout + 10` seconds.
- `executeCommand` returns only the last `BashOutput` chunk; output beyond
  1 MiB is split into several chunks server-side, and only `bash.searchEvents`
  sees all of them.
- `RemoteWorkspace.fileDownload`, `downloadAsBlob` and `downloadAsText`
  decode the body as text (`F34.workspace-binary-download`); use
  `FileClient.downloadFile` for bytes.
- `GitChange` (main entry) types `status` as lowercase
  `added|modified|deleted|renamed`, but the server sends
  `UPDATED|ADDED|DELETED|MOVED` (`F34.workspace-git-types`); compare against
  the server's values, typed with the generated `GitChangeStatus`.
  `gitChanges` and `gitDiff` wrap every refusal in a plain `Error`, so read
  the status from `isHttpError(e.cause)`, not from `e`.
- The `kind` filter of `RemoteEventsList.search` and `count` needs the
  fully-qualified class path (`F34.events-search-kind`, `F06.search-kind-short`).
- A `RemoteWorkspace` with `conversationId` checks `/server_info` once per
  transport and only then picks the conversation-scoped routes; it refuses a
  path with a query string or `..` and a `cid` parameter client-side.
- `Agent` copies extra options into the request, so `tools` (and any other
  agent field) can be passed directly; without `tools` the server agent has
  none. `RemoteConversation.start()` sends `max_iterations` 500 and
  `stuck_detection: true` unless told otherwise.
- `updateConversation` resolves with `{success: true}`, not the conversation
  (`F34.manager-update-result`); read the conversation back with
  `getConversation`.
- The version gate only reads `version` from `/server_info`; a server
  reporting `unknown` (a build without package metadata) fails every gated
  call. `F34.version-gate-old` needs PyPI (or a cached `uvx` environment) for
  the 1.22.0 release; offline it fails at the `curl` wait for the port.
- The endpoint audit sees only literal `HttpClient` calls in `src/client`,
  `src/conversation` and `src/workspace`; the conversation-scoped aliases
  (reached by rewriting paths at runtime) and calls whose path a helper
  builds (`CanvasExtensionsClient`'s backend routes) show as server-only.
  It writes `.audit/` into its working directory, which is why the recipe
  runs it from a fixture directory with symlinks.
- `npm run check:agent-server-api` regenerates `src/generated/` in place and
  downloads the pinned release's contract; the recipe calls the generator
  with `AGENT_SERVER_GENERATED_OUTPUT` instead, so nothing in the checkout
  changes. The raw `/openapi.json` of a running server is not the public
  contract (it includes `/v1` and internal schemas), so regenerating from it
  shows thousands of unrelated changes.
