# OpenAI-compatible gateway (/v1)

An OpenAI-shaped front door so OpenAI clients (the official SDKs, or anything
else that takes a `base_url`) can talk to the agent server. It is not an LLM
proxy. Every chat or responses call starts a full, persisted OpenHands agent
conversation (or continues one), built from the saved settings with the LLM of
the named profile. The agent runs in the server's workspace with its own
tools, the gateway waits for the run to finish, and it returns the agent's
final answer as an OpenAI object. Model ids are `openhands_<llm-profile-name>`,
one per saved profile. The session key works as `X-Session-API-Key` or as
`Authorization: Bearer`. The `X-OpenHands-ServerConversation-ID` response
header names the conversation behind each answer, and sending it back on a
chat completion continues that conversation.

Source: `openhands-agent-server/openhands/agent_server/openai/`, `openhands-agent-server/openhands/agent_server/api.py`, `openhands-agent-server/openhands/agent_server/dependencies.py`, `openhands-sdk/openhands/sdk/llm/llm_profile_store.py`, `openhands-sdk/openhands/sdk/conversation/response_utils.py`, `examples/02_remote_agent_server/15_openai_compatible_gateway.py`

Needs: `llm`, `tmux`

Routes: `GET /v1/models`, `POST /v1/chat/completions`, `POST /v1/responses`

## Sub-features

- `F30.models-list`: `GET /v1/models` lists one `{"id": "openhands_<profile>", "object": "model", "created": 0, "owned_by": "openhands"}` per saved LLM profile, sorted by id, and follows renames and deletes of profiles at once.
- `F30.auth-bearer-or-header`: with session keys configured, all three routes accept the key as `Authorization: Bearer` or as `X-Session-API-Key`, and answer 401 `Unauthorized` to a missing or wrong key before the body is validated, while `/api/*` still refuses Bearer.
- `F30.auth-open-without-keys`: a server with no session keys serves `/v1` without any credential and with any Bearer value.
- `F30.chat-basic`: `POST /v1/chat/completions` on a real model returns a `chat.completion` whose single choice carries the agent's final answer with `finish_reason` `stop`, echoes the model, reports usage, and names a new finished conversation in `X-OpenHands-ServerConversation-ID`.
- `F30.chat-system-suffix`: `system` and `developer` messages become the new conversation's `agent_context.system_message_suffix` (in the system prompt) and never appear as user messages.
- `F30.chat-latest-user-only`: a new conversation receives only the last user message of `messages[]`; earlier user and assistant turns are dropped and stored nowhere.
- `F30.chat-stream`: `stream: true` answers `text/event-stream` with the conversation header and the frames role, whole content, `finish_reason` `stop`, usage (only with `stream_options.include_usage`) and `[DONE]`; a failing streaming request is a plain JSON error, not an SSE frame.
- `F30.chat-reuse-header`: sending `X-OpenHands-ServerConversation-ID` back appends the new user message to that conversation, reruns it (live on its events socket) and returns the same id.
- `F30.chat-reuse-after-restart`: after a server restart the header still continues the persisted conversation.
- `F30.chat-header-new-id`: a well-formed but unknown id in the header creates the conversation with exactly that id; a malformed id is 422 and creates nothing.
- `F30.chat-reuse-keeps-llm`: on a continued conversation the requested model must still exist (404 otherwise) but is not applied: the conversation keeps its own LLM and system suffix, and the other profile's provider is never called.
- `F30.chat-usage-per-request`: `usage` on a continued conversation reports that request's tokens, not the conversation's running total (reproduces a real bug).
- `F30.chat-reuse-concurrent`: two overlapping requests on one conversation each get the answer to their own turn (reproduces a real bug: the first request returns the second request's answer).
- `F30.chat-agent-tools`: the agent runs its own tools in the server workspace (a file appears there), the answer carries no `tool_calls`, and the client's `tools`/`tool_choice` are ignored.
- `F30.chat-input-errors`: bad chat input fails before any conversation exists: 400 without a user message or with a bad content part, 404 for a model without the `openhands_` prefix or an unknown profile, 400 for an invalid profile name, 422 for schema errors.
- `F30.observability-headers`: `X-OpenHands-Observability-*` headers are stored on the new conversation (chat and responses); invalid values are 422 before anything runs.
- `F30.chat-run-error-500`: a run that ends in `error` answers 500 with the rewritten body (`exception` `500: Agent run ended with status: error`), without the conversation header, and leaves the conversation in `error`.
- `F30.chat-confirmation-409`: with confirmation mode saved in the settings, a new conversation that stops `waiting_for_confirmation` answers 409 at once.
- `F30.chat-reuse-confirmation`: a continued conversation that stops `waiting_for_confirmation` must also answer 409 promptly (reproduces a real bug: it waits out 120 s and answers 504).
- `F30.chat-run-limit-429`: when `max_concurrent_runs` is full a new gateway conversation is refused with 429 and nothing is created; the same request succeeds once a slot is free.
- `F30.chat-reuse-run-limit-429`: on a continued conversation a 429 must also leave nothing behind, so a client retry is safe (reproduces a real bug: each refused try appends the user message again).
- `F30.chat-timeout-504`: a run longer than the fixed 120 s answers 504 `Agent run timed out`, and the run keeps going server-side.
- `F30.responses-basic`: `POST /v1/responses` on a real model returns a completed `response` with one `output_text` message equal to the agent's final answer, echoes `instructions` and `metadata`, and puts the instructions in the system suffix.
- `F30.responses-stateless-replay`: every responses call is a new conversation (the conversation header is ignored); replayed items become one user message wrapped in `<message role="...">` markers, and developer items go to the system suffix.
- `F30.responses-errors`: `stream`, `store: true` and `previous_response_id` are 400 before anything else is checked, bad input is 400 or 422, an unknown model 404, and nothing is created.
- `F30.openai-sdk`: the official OpenAI Python SDK lists models, reads the conversation header through `with_raw_response`, continues the conversation with `extra_headers`, parses a stream and creates a response.

## How to get to it (agent POV)

- REST: `GET /v1/models`, `POST /v1/chat/completions` (JSON, or simulated
  SSE with `stream: true`) and `POST /v1/responses`, mounted outside `/api`
  with their own auth dependency (`check_openai_api_key` in
  `openai/router.py`). Request headers: `Authorization: Bearer <key>` or
  `X-Session-API-Key`, `X-OpenHands-ServerConversation-ID` (chat only),
  `X-OpenHands-Observability-Span-Name`, `-Tags`, `-Metadata` and
  `-Parent-Span-Context`. Response header: `X-OpenHands-ServerConversation-ID`
  on success only.
- OpenAI SDKs: `OpenAI(base_url=<server>/v1, api_key=<session key>)`, then
  `models.list()`, `chat.completions.create(...)` (and
  `.with_raw_response.create` to read the conversation header,
  `extra_headers={"X-OpenHands-ServerConversation-ID": ...}` to continue) and
  `responses.create(..., store=False)`.
  `examples/02_remote_agent_server/15_openai_compatible_gateway.py` is the
  maintained example (it starts its own server on port 8770, so recipes use
  an equivalent program through `exec --openai` instead).
- SDK: no `RemoteConversation` method uses `/v1`. The observability headers
  can be built with `openhands.sdk.automation.automation_observability_headers()`
  or `openhands.sdk.observability.laminar.observability_headers_from_env()`.
- TypeScript client: no method; its endpoint audit skips `/v1/` as
  server-only (`clients/typescript/endpoint-audit.config.json`).
- Native second views (owned by other families): `GET /api/profiles`,
  `POST /api/profiles/{name}/rename` and `DELETE /api/profiles/{name}` (the
  model list's source), `PATCH /api/settings` (agent and confirmation
  settings every gateway conversation inherits), `GET /api/conversations/{id}`,
  `/agent_final_response`, `/events/search`, `/sockets/events/{id}`.
- Configuration: session keys (`OH_SESSION_API_KEYS_*`), `OH_PERSISTENCE_DIR`
  (profiles and settings), `OH_WORKSPACE_PATH` (where every gateway agent
  works), `OH_MAX_CONCURRENT_RUNS`. The 120 s timeout and
  the 2 s poll interval are fixed in `openai/service.py`.
- Recipes: `api ... --auth bearer|bearer-bad|none` for auth, `--print-header
  X-OpenHands-ServerConversation-ID` to capture the conversation, `--sse` for
  the stream, `exec --openai` for the OpenAI SDK, `fixture llm-stub` only for
  exact usage numbers, a provider error, a hang and a scripted tool call;
  every happy path runs on DeepSeek flash.

## Driving it with control-agent-server

Preconditions:

- A run of this checkout, `doctor` ok, `$DEEPSEEK_API_KEY` set, `tmux`
  installed (the agent's terminal tool). The block below saves the DeepSeek
  profiles (`openhands_deepseek-flash` and `openhands_deepseek-pro`) and makes
  a scratch folder `$F30`. Every gateway call that runs a model passes
  `--timeout 180` (the CLI default is 60 s; the gateway waits up to 120 s).
- Prompts to the real model always say what to do ("reply with one short word
  and use no tools"): a bare marker makes the agent explore the filesystem to
  find out what it means.
- Error bodies use the unknown profile `openhands_qa-f30-missing` wherever
  the check under test runs before the profile lookup, so a regression shows
  up as a 404 instead of starting an agent.
- Later bullets reuse `CID` (from the chat-basic bullet) and `RID` (from the
  responses-basic bullet); scripted stubs and the profiles that point at them
  are created in the bullet that needs them. Several bullets restart this
  run; the no-key bullet launches and stops its own second run.

```sh
control-agent-server llm preset deepseek
F30="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f30"
mkdir -p "$F30"
control-agent-server api GET /api/settings --check conversation_settings.confirmation_mode eq false \
  --check conversation_settings.security_analyzer eq llm --quiet
```

- **Model list (`F30.models-list`).** List the models, compare with the
  saved profiles, then rename and delete a profile.
  ```sh
  control-agent-server api GET /v1/models --auth bearer --expect 200 --check object eq list --check data len-eq 2 \
    --check data.0.id eq openhands_deepseek-flash --check data.1.id eq openhands_deepseek-pro \
    --check data.0.object eq model --check data.0.owned_by eq openhands --check data.0.created eq 0 \
    --save F30.models-list/list
  control-agent-server api GET /api/profiles --check profiles len-eq 2 --check profiles.0.name eq deepseek-flash \
    --check profiles.1.name eq deepseek-pro --quiet
  control-agent-server llm set --profile qa-f30-old --model openai/qa-unused --base-url http://127.0.0.1:9/v1 \
    --no-api-key --no-validate --no-activate
  control-agent-server api GET /v1/models --auth bearer --check data len-eq 3 --check data.2.id eq openhands_qa-f30-old --quiet
  control-agent-server api POST /api/profiles/qa-f30-old/rename --json '{"new_name": "qa-f30-new"}' --expect 200
  control-agent-server api GET /v1/models --auth bearer --check data contains openhands_qa-f30-new \
    --check data not-contains openhands_qa-f30-old --save F30.models-list/renamed
  control-agent-server api DELETE /api/profiles/qa-f30-new --expect 200
  control-agent-server api GET /v1/models --auth bearer --check data len-eq 2 --check data not-contains openhands_qa-f30-new \
    --save F30.models-list/deleted
  control-agent-server api POST /v1/chat/completions --auth bearer \
    --json '{"model": "openhands_qa-f30-new", "messages": [{"role": "user", "content": "x"}]}' \
    --expect 404 --check detail eq "Profile 'qa-f30-new' not found"
  ```
  The list has exactly the two DeepSeek profiles, sorted, in OpenAI's model
  shape. The new profile appears as a third id at once, the rename replaces
  it, and after the delete the list is back to two and a chat on the deleted
  id is 404.
- **Bearer or session header (`F30.auth-bearer-or-header`).** Call each
  route with no key, a wrong key and the run's key in both shapes.
  ```sh
  control-agent-server api GET /v1/models --auth none --expect 401 --check detail eq Unauthorized --save F30.auth-bearer-or-header/no-key
  control-agent-server api GET /v1/models --auth bearer-bad --expect 401 --check detail eq Unauthorized
  control-agent-server api GET /v1/models --auth bad --expect 401 --check detail eq Unauthorized
  control-agent-server api GET /v1/models --auth bearer --expect 200 --check data len-ge 1 --save F30.auth-bearer-or-header/bearer
  control-agent-server api GET /v1/models --expect 200 --check data len-ge 1 --save F30.auth-bearer-or-header/session-header
  control-agent-server api POST /v1/chat/completions --auth none --json '{}' --expect 401 --check detail eq Unauthorized
  control-agent-server api POST /v1/responses --auth bearer-bad --json '{}' --expect 401 --check detail eq Unauthorized
  control-agent-server api POST /v1/chat/completions --auth bearer --json '{}' --expect 422
  control-agent-server api POST /v1/chat/completions --json '{}' --expect 422
  control-agent-server api POST /v1/responses --auth bearer --json '{}' --expect 422
  control-agent-server api POST /v1/responses --json '{}' --expect 422
  control-agent-server api GET /api/conversations/count --auth bearer --expect 401 --save F30.auth-bearer-or-header/api-refuses-bearer
  control-agent-server api GET /v1/models --auth bearer --header 'Origin: http://localhost:3000' \
    --check-header access-control-allow-origin eq http://localhost:3000 --check-header access-control-expose-headers missing \
    --save F30.auth-bearer-or-header/cors
  ```
  Without a valid key every route is 401 `{"detail": "Unauthorized"}`, even
  for an invalid body; with the key in either shape the same `{}` body gets
  as far as validation (422). The native `/api` routes still refuse Bearer.
  A localhost browser origin is allowed, but the response exposes no headers
  (no `Access-Control-Expose-Headers`), so browser code cannot read
  `X-OpenHands-ServerConversation-ID`.
- **No session keys (`F30.auth-open-without-keys`).** A second server
  launched without keys.
  ```sh
  B=$(control-agent-server launch --new --no-auth --name f30-noauth --print-run)
  trap 'control-agent-server stop --run "$B" >/dev/null || true' EXIT
  control-agent-server api GET /v1/models --run "$B" --auth none --expect 200 --check object eq list --check data len-eq 0 \
    --save F30.auth-open-without-keys/no-credential
  control-agent-server api GET /v1/models --run "$B" --auth none --header 'Authorization: Bearer qa-f30-anything' \
    --header 'X-Session-API-Key: qa-f30-anything' --expect 200 --check object eq list
  control-agent-server api POST /v1/chat/completions --run "$B" --auth none --header 'Authorization: Bearer qa-f30-anything' \
    --json '{"model": "openhands_qa-f30-missing", "messages": [{"role": "user", "content": "x"}]}' \
    --expect 404 --check detail eq "Profile 'qa-f30-missing' not found"
  control-agent-server api POST /v1/responses --run "$B" --auth none \
    --json '{"model": "openhands_qa-f30-missing", "input": "x"}' --expect 404
  control-agent-server stop --run "$B"
  ```
  Every call gets past auth (the chat and responses calls reach the profile
  lookup and 404); the new server has no profiles, so the list is empty. The
  trap stops the second server even when a check fails.
- **Chat completion (`F30.chat-basic`).** One real-model call carrying a
  system and a developer message, an old exchange and a new user turn, plus
  OpenAI fields the gateway ignores. `CID` names its conversation for the
  next bullets.
  ```sh
  CID=$(control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 \
    --json '{"model": "openhands_deepseek-flash", "messages": [{"role": "system", "content": "QA_F30_SYS: answer in one short sentence and never use tools."}, {"role": "developer", "content": "QA_F30_DEV: plain text only."}, {"role": "user", "content": "QA_F30_OLD_TURN: remember the word apple."}, {"role": "assistant", "content": "QA_F30_OLD_ANSWER"}, {"role": "user", "content": "QA_F30_NEW_TURN: say hello."}], "temperature": 0, "user": "qa-f30"}' \
    --check object eq chat.completion --check id matches '^chatcmpl-[0-9a-f]{32}$' --check model eq openhands_deepseek-flash \
    --check choices len-eq 1 --check choices.0.index eq 0 --check choices.0.finish_reason eq stop \
    --check choices.0.message.role eq assistant --check choices.0.message.content len-ge 1 \
    --check usage.prompt_tokens gt 0 --check usage.completion_tokens gt 0 \
    --raw-out "$F30/chat-basic.json" --save F30.chat-basic/completion --print-header X-OpenHands-ServerConversation-ID)
  control-agent-server api GET "/api/conversations/$CID" --check execution_status eq finished --check title missing \
    --check agent.llm.model eq deepseek/deepseek-flash --check workspace.working_dir eq "$AGENT_SERVER_VERIFY_RUN/server/workspace/project" \
    --quiet --save F30.chat-basic/conversation
  control-agent-server api GET "/api/conversations/$CID/agent_final_response" --raw-out "$F30/chat-basic-final.json" --quiet
  python3 - "$F30/chat-basic.json" "$F30/chat-basic-final.json" <<'PY'
  import json, sys
  chat, final = (json.load(open(path)) for path in sys.argv[1:])
  assert chat["choices"][0]["message"]["content"] == final["response"] != "", (chat, final)
  usage = chat["usage"]
  assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"], usage
  PY
  ```
  The status is 200 and the header names a conversation that is `finished`,
  untitled (gateway conversations never autotitle), runs the profile's model
  and works in the server's workspace (`OH_WORKSPACE_PATH`). The answer is
  exactly the conversation's `agent_final_response`; never assert its words.
- **System text goes to the system prompt (`F30.chat-system-suffix`).** Read
  back the chat-basic conversation.
  ```sh
  control-agent-server api GET "/api/conversations/$CID" \
    --check agent.agent_context.system_message_suffix eq "$(printf 'QA_F30_SYS: answer in one short sentence and never use tools.\n\nQA_F30_DEV: plain text only.')" \
    --quiet --save F30.chat-system-suffix/conversation
  control-agent-server api GET "/api/conversations/$CID/events/search" \
    --query kind=openhands.sdk.event.llm_convertible.system.SystemPromptEvent \
    --check items len-eq 1 --check items.0.dynamic_context.text contains QA_F30_SYS \
    --check items.0.dynamic_context.text contains QA_F30_DEV --quiet
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=user --query body=QA_F30_NEW_TURN \
    --check items len-eq 1 --quiet
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=user --query body=QA_F30_SYS \
    --check items len-eq 0 --quiet
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=user --query body=QA_F30_DEV \
    --check items len-eq 0 --quiet
  ```
  The system and developer texts are joined with a blank line into the
  suffix, reach the model in the system prompt event, and are not user
  messages (the same `body` search finds the real user turn, so the empty
  results are not a filter that matches nothing).
- **Only the latest user turn (`F30.chat-latest-user-only`).** Look for the
  dropped turns everywhere the server stores the conversation.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=user \
    --check items len-eq 1 --check items.0.llm_message.content len-eq 1 \
    --check items.0.llm_message.content.0.text eq 'QA_F30_NEW_TURN: say hello.' --save F30.chat-latest-user-only/user-events
  control-agent-server state grep QA_F30_NEW_TURN --glob 'server/workspace/conversations/**/*'
  control-agent-server state grep QA_F30_OLD_TURN --glob 'server/**/*' --expect-none
  control-agent-server state grep QA_F30_OLD_ANSWER --glob 'server/**/*' --expect-none
  ```
  The conversation has one user message, the last one; the earlier user and
  assistant turns are on disk nowhere (the positive grep shows the search
  sees the conversation files). Context carries over only through the
  conversation header.
- **Streaming (`F30.chat-stream`).** The same call with `stream: true`, with
  and without `include_usage`.
  ```sh
  SID=$(control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 --sse \
    --json '{"model": "openhands_deepseek-flash", "messages": [{"role": "user", "content": "QA_F30_STREAM: reply with one short word and use no tools."}], "stream": true, "stream_options": {"include_usage": true}}' \
    --check-header content-type contains text/event-stream \
    --check frames len-eq 5 --check frames.0.object eq chat.completion.chunk --check frames.0.id matches '^chatcmpl-[0-9a-f]{32}$' \
    --check frames.0.model eq openhands_deepseek-flash --check frames.0.choices.0.delta.role eq assistant \
    --check frames.1.choices.0.delta.content len-ge 1 --check frames.2.choices.0.finish_reason eq stop \
    --check frames.3.choices len-eq 0 --check frames.3.usage.total_tokens gt 0 --check frames.4 eq '[DONE]' \
    --raw-out "$F30/stream.txt" --save F30.chat-stream/include-usage --print-header X-OpenHands-ServerConversation-ID)
  control-agent-server api GET "/api/conversations/$SID/agent_final_response" --raw-out "$F30/stream-final.json" --quiet
  python3 - "$F30/stream.txt" "$F30/stream-final.json" <<'PY'
  import json, sys
  frames = [block[6:] for block in open(sys.argv[1]).read().split("\n\n") if block.startswith("data: ")]
  chunks = [json.loads(frame) for frame in frames[:-1]]
  assert len({chunk["id"] for chunk in chunks}) == 1, chunks
  final = json.load(open(sys.argv[2]))["response"]
  assert chunks[1]["choices"][0]["delta"]["content"] == final != "", (chunks, final)
  PY
  control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 --sse \
    --json '{"model": "openhands_deepseek-flash", "messages": [{"role": "user", "content": "QA_F30_STREAM_2: reply with one short word and use no tools."}], "stream": true}' \
    --check-header x-openhands-serverconversation-id matches '^[0-9a-f-]{36}$' \
    --check frames len-eq 4 --check frames.1.choices.0.delta.content len-ge 1 --check frames.2.choices.0.finish_reason eq stop \
    --check frames.2.usage missing --check frames.3 eq '[DONE]' --save F30.chat-stream/no-usage
  control-agent-server api POST /v1/chat/completions --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "messages": [{"role": "user", "content": "x"}], "stream": true}' \
    --expect 404 --check detail eq "Profile 'qa-f30-missing' not found" --check-header content-type contains application/json \
    --check-header x-openhands-serverconversation-id missing --save F30.chat-stream/error-is-json
  ```
  Five frames with `include_usage` (role, the whole answer in one content
  chunk, finish, usage with `choices: []`, `[DONE]`), four without. All
  chunks share one `chatcmpl-` id, the content equals the conversation's final
  response, and the header names the conversation in both cases. A streaming
  request that fails is a plain JSON error with a status code, not an SSE
  error frame.
- **Continue with the header (`F30.chat-reuse-header`).** Send the header
  back while the conversation's events socket is captured.
  ```sh
  control-agent-server ws start "/sockets/events/$CID" --name qa-f30-reuse --duration 300
  R=$(control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 --header "X-OpenHands-ServerConversation-ID: $CID" \
    --json '{"model": "openhands_deepseek-flash", "messages": [{"role": "user", "content": "QA_F30_SECOND: reply with one short word and use no tools."}]}' \
    --check choices.0.message.content len-ge 1 --raw-out "$F30/reuse.json" --save F30.chat-reuse-header/second \
    --print-header X-OpenHands-ServerConversation-ID)
  test "$R" = "$CID"
  control-agent-server ws read qa-f30-reuse --kinds ConversationStateUpdateEvent --contains '"running"' --expect-min 1 --wait 10
  control-agent-server ws stop qa-f30-reuse --kinds MessageEvent --contains QA_F30_SECOND --expect-min 1 \
    --wait 10 --save F30.chat-reuse-header/frames
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=user --check items len-eq 2 \
    --check items.1.llm_message.content.0.text contains QA_F30_SECOND --quiet
  TS2=$(control-agent-server api GET "/api/conversations/$CID/events/search" --query source=user --field items.1.timestamp)
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=agent --query "timestamp__gte=$TS2" \
    --check items len-ge 1 --quiet --save F30.chat-reuse-header/agent-after-turn
  control-agent-server api GET "/api/conversations/$CID" --check execution_status eq finished --quiet
  control-agent-server api GET "/api/conversations/$CID/agent_final_response" --raw-out "$F30/reuse-final.json" --quiet
  python3 - "$F30/reuse.json" "$F30/reuse-final.json" <<'PY'
  import json, sys
  chat, final = (json.load(open(path)) for path in sys.argv[1:])
  assert chat["choices"][0]["message"]["content"] == final["response"] != "", (chat, final)
  PY
  ```
  The response header carries the same id, the socket shows the new user
  `MessageEvent` and an `execution_status` update to `running` live (two
  separate reads: `--expect-kind` only looks inside the `--kinds` filter),
  and the conversation now has both user turns, an agent event after the
  second one (the agent really ran for it) and is `finished` again with the
  returned text as its final response.
- **Continue after a restart (`F30.chat-reuse-after-restart`).** Restart the
  server and continue the same conversation.
  ```sh
  control-agent-server restart
  R=$(control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 --header "X-OpenHands-ServerConversation-ID: $CID" \
    --json '{"model": "openhands_deepseek-flash", "messages": [{"role": "user", "content": "QA_F30_AFTER_RESTART: reply with one short word and use no tools."}]}' \
    --check choices.0.message.content len-ge 1 --save F30.chat-reuse-after-restart/third --print-header X-OpenHands-ServerConversation-ID)
  test "$R" = "$CID"
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=user --check items len-eq 3 \
    --check items.2.llm_message.content.0.text contains QA_F30_AFTER_RESTART --quiet
  TS3=$(control-agent-server api GET "/api/conversations/$CID/events/search" --query source=user --field items.2.timestamp)
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=agent --query "timestamp__gte=$TS3" \
    --check items len-ge 1 --quiet
  ```
  The restarted server loads the conversation from disk, appends the third
  turn to it instead of creating a new one, and runs the agent for it.
- **Caller-chosen id (`F30.chat-header-new-id`).** A malformed id, then an
  unknown well-formed one.
  ```sh
  N0=$(control-agent-server api GET /api/conversations/count --field .)
  control-agent-server api POST /v1/chat/completions --auth bearer --header 'X-OpenHands-ServerConversation-ID: not-a-uuid' \
    --json '{"model": "openhands_deepseek-flash", "messages": [{"role": "user", "content": "QA_F30_BAD_ID: reply with one short word and use no tools."}]}' \
    --expect 422 --check detail.0.type eq uuid_parsing --save F30.chat-header-new-id/malformed
  control-agent-server api GET /api/conversations/count --check . eq "$N0" --quiet
  NEW=$(python3 -c 'import uuid; print(uuid.uuid4())')
  control-agent-server api GET "/api/conversations/$NEW" --expect 404 --quiet
  R=$(control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 --header "X-OpenHands-ServerConversation-ID: $NEW" \
    --json '{"model": "openhands_deepseek-flash", "messages": [{"role": "user", "content": "QA_F30_PICKED: reply with one short word and use no tools."}]}' \
    --check choices.0.message.content len-ge 1 --save F30.chat-header-new-id/picked --print-header X-OpenHands-ServerConversation-ID)
  test "$R" = "$NEW"
  control-agent-server api GET "/api/conversations/$NEW" --check id eq "$NEW" --check execution_status eq finished --quiet
  control-agent-server api GET /api/conversations/count --check . eq $((N0 + 1)) --quiet
  ```
  `not-a-uuid` is 422 (`uuid_parsing`) and creates nothing; an unknown UUID
  becomes the id of the new conversation, so a caller can know the id before
  the answer arrives (and find the conversation after an error, which never
  carries the header).
- **Continuing keeps the conversation's LLM (`F30.chat-reuse-keeps-llm`).**
  Continue `CID` naming another profile (a stub that must never be called) and
  a new system message, then name a model that does not exist.
  ```sh
  UNUSED=$(control-agent-server fixture llm-stub --name qa-f30-unused --step reply:QA_F30_UNUSED_REPLY --print-path)
  control-agent-server llm set --profile qa-f30-unused --model openai/qa-stub --base-url "$UNUSED/v1" \
    --no-api-key --no-validate --no-activate --num-retries 0
  R=$(control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 --header "X-OpenHands-ServerConversation-ID: $CID" \
    --json '{"model": "openhands_qa-f30-unused", "messages": [{"role": "system", "content": "QA_F30_LATE_SYS"}, {"role": "user", "content": "QA_F30_SWITCH: reply with one short word and use no tools."}]}' \
    --check model eq openhands_qa-f30-unused --check choices.0.message.content ne QA_F30_UNUSED_REPLY \
    --save F30.chat-reuse-keeps-llm/other-model --print-header X-OpenHands-ServerConversation-ID)
  test "$R" = "$CID"
  control-agent-server sink read --name qa-f30-unused --expect-max 0
  control-agent-server api GET "/api/conversations/$CID" --check agent.llm.model eq deepseek/deepseek-flash \
    --check agent.agent_context.system_message_suffix not-contains QA_F30_LATE_SYS --quiet
  control-agent-server api POST /v1/chat/completions --auth bearer --header "X-OpenHands-ServerConversation-ID: $CID" \
    --json '{"model": "gpt-4o", "messages": [{"role": "user", "content": "QA_F30_UNKNOWN_MODEL"}]}' \
    --expect 404 --check detail eq "Unknown OpenHands model 'gpt-4o'. Use GET /v1/models."
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=user --query body=QA_F30_UNKNOWN_MODEL \
    --check items len-eq 0 --quiet
  control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 \
    --json '{"model": "openhands_qa-f30-unused", "messages": [{"role": "user", "content": "QA_F30_FRESH_STUB"}]}' \
    --check choices.0.message.content eq QA_F30_UNUSED_REPLY
  control-agent-server sink read --name qa-f30-unused --contains QA_F30_FRESH_STUB --expect-min 1 --expect-max 1
  ```
  The continued turn is answered by the conversation's own DeepSeek LLM: the
  stub got no request and the stored LLM and suffix are unchanged, although
  the response echoes the requested model. An unknown model is still 404 and
  appends nothing. The last call is the positive control: a new conversation
  on the same profile does call the stub.
- **Usage per request (`F30.chat-usage-per-request`), known bug.** A stub
  that reports 10/5 tokens on the first model call and 3/2 on the second; one
  gateway turn is one model call here.
  ```sh
  USAGE=$(control-agent-server fixture llm-stub --name qa-f30-usage --step 'reply:QA_F30_U1@usage=10,5' \
    --step 'reply:QA_F30_U2@usage=3,2' --print-path)
  control-agent-server llm set --profile qa-f30-usage --model openai/qa-stub --base-url "$USAGE/v1" \
    --no-api-key --no-validate --no-activate --num-retries 0
  UCID=$(control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 \
    --json '{"model": "openhands_qa-f30-usage", "messages": [{"role": "user", "content": "QA_F30_USAGE_1"}]}' \
    --check choices.0.message.content eq QA_F30_U1 --check usage.prompt_tokens eq 10 --check usage.completion_tokens eq 5 \
    --check usage.total_tokens eq 15 --print-header X-OpenHands-ServerConversation-ID)
  control-agent-server sink read --name qa-f30-usage --expect-min 1 --expect-max 1
  control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 --header "X-OpenHands-ServerConversation-ID: $UCID" \
    --json '{"model": "openhands_qa-f30-usage", "messages": [{"role": "user", "content": "QA_F30_USAGE_2"}]}' \
    --check choices.0.message.content eq QA_F30_U2 --raw-out "$F30/usage-2.json" --save F30.chat-usage-per-request/second-turn
  control-agent-server sink read --name qa-f30-usage --contains QA_F30_USAGE_2 --expect-min 1 --expect-max 1
  control-agent-server sink read --name qa-f30-usage --expect-min 2 --expect-max 2
  control-agent-server api GET "/api/conversations/$UCID" --check stats.usage_to_metrics.default.token_usages len-eq 2 \
    --check stats.usage_to_metrics.default.token_usages.1.prompt_tokens eq 3 \
    --check stats.usage_to_metrics.default.token_usages.1.completion_tokens eq 2 \
    --check stats.usage_to_metrics.default.accumulated_token_usage.prompt_tokens eq 13 \
    --check stats.usage_to_metrics.default.accumulated_token_usage.completion_tokens eq 7 --quiet
  control-agent-server state cat fixtures/qa-f30/usage-2.json --check usage.prompt_tokens eq 3 \
    --check usage.completion_tokens eq 2 --check usage.total_tokens eq 5  # bug
  ```
  The stub got exactly two model calls, one per request, so the second
  request's own usage is the stub's 3/2/5: the conversation's stats hold one
  `token_usages` entry per model call (10/5, then 3/2) and a running total
  of 13/7 (the positive control: the server has the per-call numbers).
  Expected: the second response reports 3/2/5. Today it reports the
  conversation's accumulated 13/7/20 (`_openai_usage_from_state` reads
  `accumulated_token_usage`), so an OpenAI client that sums `usage` per
  request double-counts every earlier turn. The stream's usage chunk carries
  the same value (it is the response's `usage`).
- **Overlapping turns on one conversation (`F30.chat-reuse-concurrent`), known bug.**
  A stub whose second model call answers `QA_F30_RACE_B` after 8 s. The
  `llm-stub` steps cannot delay a reply, so the bullet rewrites the stub's
  script file (the stub rereads it on every request). Request X continues the
  conversation in the background; request Y follows while X's model call is
  in flight.
  ```sh
  RACE=$(control-agent-server fixture llm-stub --name qa-f30-race --step reply:QA_F30_RACE_A \
    --step reply:QA_F30_RACE_B --step reply:QA_F30_RACE_C --print-path)
  cat > "$AGENT_SERVER_VERIFY_RUN/fixtures/llm-stub/qa-f30-race.json" <<'JSON'
  [{"reply": "QA_F30_RACE_A"}, {"hang": 8, "reply": "QA_F30_RACE_B"}, {"reply": "QA_F30_RACE_C"}]
  JSON
  control-agent-server llm set --profile qa-f30-race --model openai/qa-stub --base-url "$RACE/v1" \
    --no-api-key --no-validate --no-activate --num-retries 0
  QCID=$(control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 \
    --json '{"model": "openhands_qa-f30-race", "messages": [{"role": "user", "content": "QA_F30_RACE_1"}]}' \
    --check choices.0.message.content eq QA_F30_RACE_A --print-header X-OpenHands-ServerConversation-ID)
  control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 --header "X-OpenHands-ServerConversation-ID: $QCID" \
    --json '{"model": "openhands_qa-f30-race", "messages": [{"role": "user", "content": "QA_F30_RACE_X"}]}' \
    --expect 200 --raw-out "$F30/race-x.json" --save F30.chat-reuse-concurrent/first > "$F30/race-x.out" &
  XPID=$!
  control-agent-server sink read --name qa-f30-race --expect-min 2 --wait 20 > /dev/null
  control-agent-server sink read --name qa-f30-race --contains QA_F30_RACE_X --expect-min 1 --expect-max 1
  control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 --header "X-OpenHands-ServerConversation-ID: $QCID" \
    --json '{"model": "openhands_qa-f30-race", "messages": [{"role": "user", "content": "QA_F30_RACE_Y"}]}' \
    --check choices.0.message.content eq QA_F30_RACE_C --save F30.chat-reuse-concurrent/second
  control-agent-server api GET "/api/conversations/$QCID/events/search" --query source=agent \
    --query kind=openhands.sdk.event.llm_convertible.message.MessageEvent --check items len-eq 3 \
    --check items.1.llm_message.content.0.text eq QA_F30_RACE_B --check items.2.llm_message.content.0.text eq QA_F30_RACE_C \
    --save F30.chat-reuse-concurrent/agent-messages
  wait "$XPID" || { cat "$F30/race-x.out"; false; }
  control-agent-server state cat fixtures/qa-f30/race-x.json --check choices.0.message.content eq QA_F30_RACE_B  # bug
  ```
  The conversation answers X with `QA_F30_RACE_B` and finishes, then reruns
  for Y and answers `QA_F30_RACE_C`; Y gets `QA_F30_RACE_C`, and X's request
  succeeds (200). Expected: X gets `QA_F30_RACE_B`, its own turn's answer.
  Today X also gets `QA_F30_RACE_C`: the `finished` state
  between the two runs lasts a few milliseconds, the gateway polls a
  continued conversation every 2 s
  (`_wait_for_reused_conversation_completion`), so X misses it, waits
  through Y's rerun, and returns the final answer of the conversation instead
  of its own turn's. Because the miss depends on a 2 s poll against a window
  of a few milliseconds, roughly one replay in several hundred may see X get
  `QA_F30_RACE_B` and report `xpass`; rerun the bullet before removing the
  marker. A fix that refuses Y with 409 instead of queueing it is also
  correct; update the expectations for Y if that is the chosen fix.
- **The agent runs its own tools (`F30.chat-agent-tools`).** Ask the real
  model to use the terminal, and pass an OpenAI client tool with
  `tool_choice: required`.
  ```sh
  test ! -e "$AGENT_SERVER_VERIFY_RUN/server/workspace/project/qa_f30_tool.txt"
  TID=$(control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 \
    --json '{"model": "openhands_deepseek-flash", "messages": [{"role": "user", "content": "Use the terminal to run exactly: echo QA_F30_TOOL_RAN > qa_f30_tool.txt  Then reply DONE."}], "tools": [{"type": "function", "function": {"name": "qa_f30_client_tool", "description": "QA client tool", "parameters": {"type": "object", "properties": {}}}}], "tool_choice": "required", "parallel_tool_calls": false}' \
    --check choices.0.finish_reason eq stop --check choices.0.message.content exists --check choices.0.message.tool_calls missing \
    --save F30.chat-agent-tools/completion --print-header X-OpenHands-ServerConversation-ID)
  control-agent-server state cat server/workspace/project/qa_f30_tool.txt --contains QA_F30_TOOL_RAN
  control-agent-server api GET "/api/conversations/$TID/events/search" --query kind=openhands.sdk.event.llm_convertible.action.ActionEvent \
    --check items len-ge 1 --quiet
  control-agent-server api GET "/api/conversations/$TID" --check agent.tools contains terminal \
    --check agent.tools not-contains qa_f30_client_tool --check client_tools len-eq 0 --quiet --save F30.chat-agent-tools/conversation
  ```
  The file appears in the server workspace with the marker, the conversation
  has the agent's actions, and the response is a plain assistant message. The
  client's tool is in neither the agent's tools nor `client_tools`.
- **Chat input errors (`F30.chat-input-errors`).** Each bad body, then the
  conversation count.
  ```sh
  N0=$(control-agent-server api GET /api/conversations/count --field .)
  control-agent-server api POST /v1/chat/completions --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "messages": [{"role": "system", "content": "x"}]}' \
    --expect 400 --check detail eq 'At least one user message is required' --save F30.chat-input-errors/no-user
  control-agent-server api POST /v1/chat/completions --auth bearer --json '{"model": "gpt-4o", "messages": []}' \
    --expect 400 --check detail eq 'At least one user message is required'
  control-agent-server api POST /v1/chat/completions --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "messages": [{"role": "user", "content": [{"type": "input_text", "text": "x"}]}]}' \
    --expect 400 --check detail eq 'Unsupported content part type: input_text'
  control-agent-server api POST /v1/chat/completions --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "messages": [{"role": "system", "content": [{"type": "input_text", "text": "x"}]}, {"role": "user", "content": "x"}]}' \
    --expect 400 --check detail eq 'Unsupported content part type: input_text'
  control-agent-server api POST /v1/chat/completions --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "messages": [{"role": "user", "content": [{"type": "image_url"}]}]}' \
    --expect 400 --check detail eq 'image_url content part is missing a url'
  control-agent-server api POST /v1/chat/completions --auth bearer \
    --json '{"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}]}' \
    --expect 404 --check detail eq "Unknown OpenHands model 'gpt-4o'. Use GET /v1/models." --save F30.chat-input-errors/no-prefix
  control-agent-server api POST /v1/chat/completions --auth bearer \
    --json '{"model": "openhands_", "messages": [{"role": "user", "content": "x"}]}' --expect 404
  control-agent-server api POST /v1/chat/completions --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "messages": [{"role": "user", "content": "x"}]}' \
    --expect 404 --check detail eq "Profile 'qa-f30-missing' not found"
  control-agent-server api POST /v1/chat/completions --auth bearer \
    --json '{"model": "openhands_.qa", "messages": [{"role": "user", "content": "x"}]}' \
    --expect 400 --check detail contains 'Invalid profile name'
  control-agent-server api POST /v1/chat/completions --auth bearer \
    --json '{"messages": [{"role": "user", "content": "x"}]}' --expect 422 --check detail.0.loc contains model
  control-agent-server api POST /v1/chat/completions --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "messages": [{"role": "function", "content": "x"}]}' \
    --expect 422 --check detail.0.type eq literal_error
  control-agent-server api POST /v1/chat/completions --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "messages": "x"}' --expect 422
  control-agent-server api GET /api/conversations/count --check . eq "$N0" --quiet
  ```
  The no-user check runs before the model check (`gpt-4o` with no messages is
  400, not 404), content parts are checked in system messages too, a model
  without the prefix or with an unknown profile is 404, a name the profile
  store refuses is 400, and schema errors are 422. The count is unchanged.
- **Observability headers (`F30.observability-headers`).** Valid headers on
  a chat and a responses call, then invalid ones.
  ```sh
  OID=$(control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 \
    --header 'X-OpenHands-Observability-Span-Name: qa_f30.span' --header 'X-OpenHands-Observability-Tags: qa-f30-a, qa-f30-b,' \
    --header 'X-OpenHands-Observability-Metadata: {"qa_key": "qa-f30-value", "qa_n": 7}' \
    --header 'X-OpenHands-Observability-Parent-Span-Context: qa-f30-parent' \
    --json '{"model": "openhands_deepseek-flash", "messages": [{"role": "user", "content": "QA_F30_OBS: reply with one short word and use no tools."}]}' \
    --check choices.0.message.content len-ge 1 --print-header X-OpenHands-ServerConversation-ID)
  control-agent-server state cat meta.json --conversation "$OID" --check observability_span_name eq qa_f30.span \
    --check observability_tags len-eq 2 --check observability_tags.1 eq qa-f30-b --check observability_metadata.qa_key eq qa-f30-value \
    --check observability_metadata.qa_n eq 7 --check observability_parent_span_context eq qa-f30-parent
  ORID=$(control-agent-server api POST /v1/responses --auth bearer --timeout 180 --header 'X-OpenHands-Observability-Span-Name: qa_f30.responses' \
    --json '{"model": "openhands_deepseek-flash", "input": "QA_F30_OBS_R: reply with one short word and use no tools."}' \
    --check status eq completed --print-header X-OpenHands-ServerConversation-ID)
  control-agent-server state cat meta.json --conversation "$ORID" --check observability_span_name eq qa_f30.responses
  N0=$(control-agent-server api GET /api/conversations/count --field .)
  BAD='{"model": "openhands_qa-f30-missing", "messages": [{"role": "user", "content": "x"}]}'
  control-agent-server api POST /v1/chat/completions --auth bearer --header 'X-OpenHands-Observability-Span-Name: bad span' \
    --json "$BAD" --expect 422 --check detail.0.msg contains 'span name' --save F30.observability-headers/bad-span
  control-agent-server api POST /v1/chat/completions --auth bearer --header 'X-OpenHands-Observability-Metadata: not-json' \
    --json "$BAD" --expect 422 --check detail eq 'X-OpenHands-Observability-Metadata must be a JSON object'
  control-agent-server api POST /v1/chat/completions --auth bearer --header 'X-OpenHands-Observability-Metadata: [1]' \
    --json "$BAD" --expect 422 --check detail.0.msg contains 'must be a dictionary'
  control-agent-server api POST /v1/chat/completions --auth bearer --header 'X-OpenHands-Observability-Metadata: {"k": [1, 1.5]}' \
    --json "$BAD" --expect 422 --check detail.0.msg contains homogeneous
  control-agent-server api POST /v1/responses --auth bearer --header 'X-OpenHands-Observability-Span-Name: bad span' \
    --json '{"model": "openhands_qa-f30-missing", "input": "x"}' --expect 422
  control-agent-server api GET /api/conversations/count --check . eq "$N0" --quiet
  ```
  The new conversations' `meta.json` holds the span name, the two non-blank
  tags, the metadata and the parent span context. A span name with a space,
  metadata that is not JSON or not an object, and a mixed numeric list are
  422 before the model is even looked up.
- **Run ends in error (`F30.chat-run-error-500`).** A stub provider that
  answers 401 (not retried), with a caller-chosen id.
  ```sh
  ERR=$(control-agent-server fixture llm-stub --name qa-f30-err --step status:401 --print-path)
  control-agent-server llm set --profile qa-f30-err --model openai/qa-stub --base-url "$ERR/v1" \
    --no-api-key --no-validate --no-activate --num-retries 0
  EID=$(python3 -c 'import uuid; print(uuid.uuid4())')
  control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 --header "X-OpenHands-ServerConversation-ID: $EID" \
    --json '{"model": "openhands_qa-f30-err", "messages": [{"role": "user", "content": "QA_F30_ERR"}]}' \
    --expect 500 --check detail eq 'Internal Server Error' --check exception eq '500: Agent run ended with status: error' \
    --check-header x-openhands-serverconversation-id missing --save F30.chat-run-error-500/error
  control-agent-server sink read --name qa-f30-err --expect-min 1 --expect-max 1
  control-agent-server api GET "/api/conversations/$EID" --check execution_status eq error --quiet
  ```
  The provider was called once, the gateway answers 500 with the reason in
  `exception`, the response has no conversation header, and the conversation
  stays behind in `error`.
- **Confirmation stops a new conversation (`F30.chat-confirmation-409`).**
  Save confirmation mode with no analyzer (always confirm), then call a
  profile whose stub proposes a terminal command.
  ```sh
  CONF=$(control-agent-server fixture llm-stub --name qa-f30-confirm \
    --step 'tool:terminal:{"command":"echo QA_F30_NEEDS_OK > qa_f30_confirm.txt"}' --print-path)
  control-agent-server llm set --profile qa-f30-confirm --model openai/qa-stub --base-url "$CONF/v1" \
    --no-api-key --no-validate --no-activate --num-retries 0
  control-agent-server api PATCH /api/settings --json '{"conversation_settings_diff": {"confirmation_mode": true, "security_analyzer": "none"}}' --expect 200
  CFID=$(python3 -c 'import uuid; print(uuid.uuid4())')
  control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 --header "X-OpenHands-ServerConversation-ID: $CFID" \
    --json '{"model": "openhands_qa-f30-confirm", "messages": [{"role": "user", "content": "QA_F30_CONFIRM"}]}' \
    --expect 409 --check detail eq 'Agent run ended with status: waiting_for_confirmation' --expect-max-ms 30000 \
    --check-header x-openhands-serverconversation-id missing --save F30.chat-confirmation-409/conflict
  control-agent-server api GET "/api/conversations/$CFID" --check execution_status eq waiting_for_confirmation \
    --check confirmation_policy.kind eq AlwaysConfirm --quiet
  test ! -e "$AGENT_SERVER_VERIFY_RUN/server/workspace/project/qa_f30_confirm.txt"
  control-agent-server api PATCH /api/settings --json '{"conversation_settings_diff": {"confirmation_mode": false, "security_analyzer": "llm"}}' --expect 200
  control-agent-server api GET /api/settings --check conversation_settings.confirmation_mode eq false \
    --check conversation_settings.security_analyzer eq llm --quiet
  ```
  The gateway answers 409 within seconds; the conversation inherited
  `AlwaysConfirm` from the saved settings and waits with the command not run.
  The settings are restored at the end.
- **Confirmation on a continued conversation (`F30.chat-reuse-confirmation`), known bug.**
  The same settings (restored by a trap even when the bullet fails); the
  stub answers the first turn with text and the second with a command.
  ```sh
  trap 'control-agent-server api PATCH /api/settings --json "{\"conversation_settings_diff\": {\"confirmation_mode\": false, \"security_analyzer\": \"llm\"}}" >/dev/null || true' EXIT
  RECONF=$(control-agent-server fixture llm-stub --name qa-f30-reconf --step reply:QA_F30_FIRST_OK \
    --step 'tool:terminal:{"command":"echo QA_F30_RECONF > qa_f30_reconf.txt"}' --print-path)
  control-agent-server llm set --profile qa-f30-reconf --model openai/qa-stub --base-url "$RECONF/v1" \
    --no-api-key --no-validate --no-activate --num-retries 0
  control-agent-server api PATCH /api/settings --json '{"conversation_settings_diff": {"confirmation_mode": true, "security_analyzer": "none"}}' --expect 200
  RCID=$(control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 \
    --json '{"model": "openhands_qa-f30-reconf", "messages": [{"role": "user", "content": "QA_F30_RECONF_1"}]}' \
    --check choices.0.message.content eq QA_F30_FIRST_OK --print-header X-OpenHands-ServerConversation-ID)
  control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 --header "X-OpenHands-ServerConversation-ID: $RCID" \
    --json '{"model": "openhands_qa-f30-reconf", "messages": [{"role": "user", "content": "QA_F30_RECONF_2"}]}' \
    --expect 409,504 --check-header x-openhands-serverconversation-id missing \
    --save F30.chat-reuse-confirmation/second-turn > "$F30/reconf-2.json" &
  XPID=$!
  control-agent-server sink read --name qa-f30-reconf --expect-min 2 --wait 30 > /dev/null
  control-agent-server api GET "/api/conversations/$RCID" --until-ok 20 --check execution_status eq waiting_for_confirmation \
    --quiet --save F30.chat-reuse-confirmation/state-while-request-waits
  test ! -e "$AGENT_SERVER_VERIFY_RUN/server/workspace/project/qa_f30_reconf.txt"
  wait "$XPID" || { cat "$F30/reconf-2.json"; false; }
  control-agent-server state cat fixtures/qa-f30/reconf-2.json --check status eq 409 \
    --check response.body.detail eq 'Agent run ended with status: waiting_for_confirmation' --check elapsed_ms lt 30000  # bug
  ```
  The second turn's model call proposes the command and the conversation is
  `waiting_for_confirmation` within seconds (the command not run), while the
  gateway request is still open; the request then ends with 409 or 504 (any
  other answer is not this bug). Expected: it answers 409 within seconds,
  like a new conversation. Today it blocks for the full 120 s and answers 504
  `Agent run timed out` (`exception` `504: Agent run timed out`):
  `_wait_for_reused_conversation_completion` only returns on `is_terminal()`
  (finished, error, stuck), so `paused` and `waiting_for_confirmation` are
  never seen. The bullet takes about two minutes.
- **Run limit (`F30.chat-run-limit-429`).** Restart with one run slot, hold
  it with a conversation whose model call hangs, then call the gateway.
  ```sh
  control-agent-server restart --config-json '{"max_concurrent_runs": 1}'
  HANG=$(control-agent-server fixture llm-stub --name qa-f30-hang --step hang:300 --print-path)
  HOLD=$(control-agent-server conversation start --tools none --no-autotitle --prompt QA_F30_HOLD --print-id \
    --llm-json "{\"model\": \"openai/qa-stub\", \"base_url\": \"$HANG/v1\", \"api_key\": \"qa-f30-stub\", \"num_retries\": 0}")
  control-agent-server conversation wait "$HOLD" --until running --timeout 30
  control-agent-server sink read --name qa-f30-hang --expect-min 1
  N0=$(control-agent-server api GET /api/conversations/count --field .)
  LID=$(python3 -c 'import uuid; print(uuid.uuid4())')
  LIMIT='{"model": "openhands_deepseek-flash", "messages": [{"role": "user", "content": "QA_F30_429: reply with one short word and use no tools."}]}'
  control-agent-server api POST /v1/chat/completions --auth bearer --header "X-OpenHands-ServerConversation-ID: $LID" --json "$LIMIT" \
    --expect 429 --check detail eq 'Conversation run limit reached. Retry the request later.' \
    --check-header x-openhands-serverconversation-id missing --save F30.chat-run-limit-429/refused
  control-agent-server api POST /v1/responses --auth bearer \
    --json '{"model": "openhands_deepseek-flash", "input": "QA_F30_429_R: reply with one short word and use no tools."}' \
    --expect 429 --check detail eq 'Conversation run limit reached. Retry the request later.'
  control-agent-server api GET "/api/conversations/$LID" --expect 404 --quiet
  control-agent-server api GET /api/conversations/count --check . eq "$N0" --quiet
  control-agent-server api DELETE "/api/conversations/$HOLD" --expect 200
  R=$(control-agent-server api POST /v1/chat/completions --auth bearer --timeout 180 --until-ok 30 \
    --header "X-OpenHands-ServerConversation-ID: $LID" --json "$LIMIT" --expect 200 --check choices.0.message.content len-ge 1 \
    --save F30.chat-run-limit-429/after-release --print-header X-OpenHands-ServerConversation-ID)
  test "$R" = "$LID"
  control-agent-server restart --reset-config
  ```
  With the only slot taken both gateway routes answer 429 at once, the
  caller-chosen id does not exist and the count is unchanged. Once the holder
  is deleted the same request succeeds and creates `$LID`. The last line
  restores the default capacity.
- **Run limit on a continued conversation (`F30.chat-reuse-run-limit-429`), known bug.**
  The same full slot, then a turn on the finished `CID` sent twice, as an
  OpenAI SDK does when it retries a 429 (`max_retries` defaults to 2). A trap
  frees the slot and restores the default capacity even when the bullet
  fails.
  ```sh
  trap 'control-agent-server api DELETE "/api/conversations/$HOLD2" >/dev/null || true; control-agent-server restart --reset-config >/dev/null || true' EXIT
  control-agent-server restart --config-json '{"max_concurrent_runs": 1}'
  HANG2=$(control-agent-server fixture llm-stub --name qa-f30-hang2 --step hang:300 --print-path)
  HOLD2=$(control-agent-server conversation start --tools none --no-autotitle --prompt QA_F30_HOLD_2 --print-id \
    --llm-json "{\"model\": \"openai/qa-stub\", \"base_url\": \"$HANG2/v1\", \"api_key\": \"qa-f30-stub\", \"num_retries\": 0}")
  control-agent-server conversation wait "$HOLD2" --until running --timeout 30
  control-agent-server sink read --name qa-f30-hang2 --expect-min 1
  REUSE='{"model": "openhands_deepseek-flash", "messages": [{"role": "user", "content": "QA_F30_429_REUSE: reply with one short word and use no tools."}]}'
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=user --query body=QA_F30_SECOND \
    --check items len-eq 1 --quiet
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=user --query body=QA_F30_429_REUSE \
    --check items len-eq 0 --quiet
  control-agent-server api POST /v1/chat/completions --auth bearer --header "X-OpenHands-ServerConversation-ID: $CID" --json "$REUSE" \
    --expect 429 --check-header x-openhands-serverconversation-id missing --expect-max-ms 10000 --save F30.chat-reuse-run-limit-429/first-try
  control-agent-server api POST /v1/chat/completions --auth bearer --header "X-OpenHands-ServerConversation-ID: $CID" --json "$REUSE" \
    --expect 429 --expect-max-ms 10000 --save F30.chat-reuse-run-limit-429/retry
  control-agent-server api GET "/api/conversations/$HOLD2" --check execution_status eq running --quiet
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=user --query body=QA_F30_429_REUSE \
    --check items len-eq 0 --save F30.chat-reuse-run-limit-429/user-messages  # bug
  ```
  Both tries are refused with 429 at once while the holder still runs, and
  the same `body` search finds an earlier user turn of `CID` (so an empty
  result is not a filter that matches nothing). Expected: like a new
  conversation, a 429 leaves nothing behind, so the client's retry is safe.
  Today the gateway appends the user message before the run is refused
  (`EventService.send_message` saves it, then `run()` raises
  `ConversationRunLimitExceeded`, whose 429 detail says `Message saved, but
  the conversation run limit was reached. Retry POST
  /api/conversations/<id>/run without resending the message.`), so the
  conversation holds one copy per try (2 here), and a retry that finally
  succeeds adds a third. An OpenAI client cannot follow that detail without
  the native API.
- **Gateway timeout (`F30.chat-timeout-504`).** A stub whose model call
  hangs past the gateway's fixed 120 s; the server's own timer is under test,
  so the bullet takes two minutes.
  ```sh
  SLOW=$(control-agent-server fixture llm-stub --name qa-f30-slow --step hang:170 --print-path)
  control-agent-server llm set --profile qa-f30-slow --model openai/qa-stub --base-url "$SLOW/v1" \
    --no-api-key --no-validate --no-activate --num-retries 0 --llm-timeout 300
  TOID=$(python3 -c 'import uuid; print(uuid.uuid4())')
  T0=$SECONDS
  control-agent-server api POST /v1/chat/completions --auth bearer --timeout 200 --header "X-OpenHands-ServerConversation-ID: $TOID" \
    --json '{"model": "openhands_qa-f30-slow", "messages": [{"role": "user", "content": "QA_F30_SLOW"}]}' \
    --expect 504 --check detail eq 'Internal Server Error' --check exception eq '504: Agent run timed out' \
    --check-header x-openhands-serverconversation-id missing --expect-max-ms 140000 --save F30.chat-timeout-504/timeout
  test $((SECONDS - T0)) -ge 115
  control-agent-server api GET "/api/conversations/$TOID" --check execution_status eq running --quiet
  control-agent-server sink read --name qa-f30-slow --expect-min 1 --expect-max 1
  control-agent-server api DELETE "/api/conversations/$TOID" --expect 200
  control-agent-server api GET "/api/conversations/$TOID" --expect 404 --quiet
  ```
  The 504 arrives after 115 to 140 s with the reason in `exception`. The run
  is not cancelled: right after the 504 the conversation is still `running`
  on its single model call, so the bullet deletes it.
- **Responses (`F30.responses-basic`).** One real-model call with
  instructions, metadata and an ignored client tool. `RID` names its
  conversation.
  ```sh
  RID=$(control-agent-server api POST /v1/responses --auth bearer --timeout 180 \
    --json '{"model": "openhands_deepseek-flash", "input": "QA_F30_RESP: reply with one short word and use no tools.", "instructions": "QA_F30_INSTR: answer in plain text.", "metadata": {"qa": "f30"}, "store": false, "tools": [{"type": "function", "name": "qa_f30_client_tool", "parameters": {"type": "object", "properties": {}}}], "temperature": 0}' \
    --check object eq response --check status eq completed --check id matches '^resp_[0-9a-f]{32}$' --check model eq openhands_deepseek-flash \
    --check output len-eq 1 --check output.0.type eq message --check output.0.role eq assistant --check output.0.status eq completed \
    --check output.0.id matches '^msg_[0-9a-f]{32}$' --check output.0.content.0.type eq output_text \
    --check output.0.content.0.text len-ge 1 --check output.0.content.0.annotations len-eq 0 \
    --check instructions eq 'QA_F30_INSTR: answer in plain text.' --check metadata.qa eq f30 --check tools len-eq 0 \
    --check tool_choice eq none --check parallel_tool_calls eq false --check previous_response_id missing \
    --check usage.input_tokens gt 0 --check usage.output_tokens gt 0 --check usage.input_tokens_details.cached_tokens ge 0 \
    --check usage.output_tokens_details.reasoning_tokens ge 0 \
    --raw-out "$F30/response.json" --save F30.responses-basic/response --print-header X-OpenHands-ServerConversation-ID)
  control-agent-server api GET "/api/conversations/$RID" --check execution_status eq finished \
    --check agent.agent_context.system_message_suffix eq 'QA_F30_INSTR: answer in plain text.' --quiet
  control-agent-server api GET "/api/conversations/$RID/events/search" --query source=user --check items len-eq 1 \
    --check items.0.llm_message.content len-eq 1 \
    --check items.0.llm_message.content.0.text eq 'QA_F30_RESP: reply with one short word and use no tools.' --quiet
  control-agent-server api GET "/api/conversations/$RID/agent_final_response" --raw-out "$F30/response-final.json" --quiet
  python3 - "$F30/response.json" "$F30/response-final.json" <<'PY'
  import json, sys
  resp, final = (json.load(open(path)) for path in sys.argv[1:])
  assert resp["output"][0]["content"][0]["text"] == final["response"] != "", (resp, final)
  usage = resp["usage"]
  assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"], usage
  assert 0 < resp["created_at"] <= resp["completed_at"], resp
  PY
  ```
  A completed Response object with one assistant `output_text` equal to the
  conversation's final answer, the instructions and metadata echoed, `tools`
  empty although the request sent one, and the instructions stored as the
  system suffix rather than as a user message.
- **Stateless replay (`F30.responses-stateless-replay`).** Replay a
  developer item, a user turn, the earlier assistant output and a new user
  turn, sending `RID` in the conversation header.
  ```sh
  R2=$(control-agent-server api POST /v1/responses --auth bearer --timeout 180 --header "X-OpenHands-ServerConversation-ID: $RID" \
    --json '{"model": "openhands_deepseek-flash", "input": [{"role": "developer", "content": "QA_F30_R_DEV: answer in plain text and use no tools."}, {"role": "user", "content": "QA_F30_R1: remember the word apple."}, {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "QA_F30_R_PREV", "annotations": []}]}, {"role": "user", "content": [{"type": "input_text", "text": "QA_F30_R2: reply with one short word."}]}]}' \
    --check status eq completed --check output.0.content.0.text len-ge 1 \
    --save F30.responses-stateless-replay/replay --print-header X-OpenHands-ServerConversation-ID)
  test "$R2" != "$RID"
  control-agent-server api GET "/api/conversations/$R2/events/search" --query source=user --check items len-eq 1 \
    --check items.0.llm_message.content len-eq 9 --check items.0.llm_message.content.0.text eq '<message role="user">' \
    --check items.0.llm_message.content.1.text eq 'QA_F30_R1: remember the word apple.' \
    --check items.0.llm_message.content.3.text eq '<message role="assistant">' \
    --check items.0.llm_message.content.4.text eq QA_F30_R_PREV --check items.0.llm_message.content.8.text eq '</message>' \
    --check items.0.llm_message.content not-contains QA_F30_R_DEV --save F30.responses-stateless-replay/user-message
  control-agent-server api GET "/api/conversations/$R2" \
    --check agent.agent_context.system_message_suffix eq 'QA_F30_R_DEV: answer in plain text and use no tools.' --quiet
  control-agent-server api GET "/api/conversations/$RID/events/search" --query source=user --check items len-eq 1 --quiet
  ```
  The call creates a new conversation (the header is ignored, and `RID` still
  has one user message). Its single user message has nine parts: each user
  and assistant item wrapped in `<message role="...">` and `</message>`. The
  developer item is the system suffix instead.
- **Responses errors (`F30.responses-errors`).** Each refused body, then the
  conversation count.
  ```sh
  N0=$(control-agent-server api GET /api/conversations/count --field .)
  control-agent-server api POST /v1/responses --auth bearer --json '{"model": "openhands_qa-f30-missing", "input": "x", "stream": true}' \
    --expect 400 --check detail eq 'Streaming responses are not supported yet' --save F30.responses-errors/stream
  control-agent-server api POST /v1/responses --auth bearer --json '{"model": "gpt-4o", "input": "", "stream": true}' \
    --expect 400 --check detail eq 'Streaming responses are not supported yet'
  control-agent-server api POST /v1/responses --auth bearer --json '{"model": "openhands_qa-f30-missing", "input": "x", "store": true}' \
    --expect 400 --check detail eq 'Persistent response storage (store=True) is not supported yet'
  control-agent-server api POST /v1/responses --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "input": "x", "previous_response_id": "resp_qa"}' \
    --expect 400 --check detail eq 'previous_response_id is not supported; replay input items instead'
  control-agent-server api POST /v1/responses --auth bearer --json '{"model": "openhands_qa-f30-missing", "input": ""}' \
    --expect 400 --check detail eq 'Responses input must not be empty'
  control-agent-server api POST /v1/responses --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "input": [{"role": "assistant", "content": "x"}]}' \
    --expect 400 --check detail eq 'Responses input must include a user message'
  control-agent-server api POST /v1/responses --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "input": [{"role": "user", "content": [{"type": "input_file", "file_id": "qa"}]}]}' \
    --expect 400 --check detail eq 'Unsupported Responses content part type: input_file'
  control-agent-server api POST /v1/responses --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "input": [{"role": "user", "content": [{"type": "input_image"}]}]}' \
    --expect 400 --check detail eq 'input_image content part is missing an image_url'
  control-agent-server api POST /v1/responses --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "input": [{"type": "function_call_output", "call_id": "qa", "output": "x"}]}' --expect 422
  control-agent-server api POST /v1/responses --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "input": [{"role": "tool", "content": "x"}]}' --expect 422
  control-agent-server api POST /v1/responses --auth bearer \
    --json '{"model": "openhands_qa-f30-missing", "input": "x", "metadata": {"n": 1}}' --expect 422 --check detail.0.loc contains metadata
  control-agent-server api POST /v1/responses --auth bearer --json '{"model": "qa-f30", "input": "x"}' \
    --expect 404 --check detail eq "Unknown OpenHands model 'qa-f30'. Use GET /v1/models."
  control-agent-server api POST /v1/responses --auth bearer --json '{"model": "openhands_qa-f30-missing", "input": "x"}' \
    --expect 404 --check detail eq "Profile 'qa-f30-missing' not found"
  control-agent-server api GET /api/conversations/count --check . eq "$N0" --quiet
  ```
  `stream`, `store: true` and `previous_response_id` are refused first (even
  with an unknown model and empty input), then input shape (400), schema
  (422: items without a role, role `tool`, non-string metadata) and the model
  (404). Nothing is created.
- **Official OpenAI SDK (`F30.openai-sdk`).** A program using `openai`
  (installed in the repository's `.venv`) with the base URL and key that
  `exec --openai` exports.
  ```sh
  control-agent-server exec --openai --timeout 600 --expect-output QA_F30_SDK_OK --save F30.openai-sdk/program -- .venv/bin/python - <<'PY'
  import os
  import uuid

  from openai import OpenAI

  client = OpenAI(timeout=180, max_retries=0)  # OPENAI_BASE_URL and OPENAI_API_KEY come from exec --openai
  model = "openhands_deepseek-flash"
  assert model in [m.id for m in client.models.list().data]
  prompt = "reply with one short word and use no tools."
  first = client.chat.completions.with_raw_response.create(
      model=model, messages=[{"role": "user", "content": f"QA_F30_SDK_1: {prompt}"}]
  )
  cid = first.headers.get("X-OpenHands-ServerConversation-ID")
  uuid.UUID(cid)
  completion = first.parse()
  assert completion.choices[0].finish_reason == "stop" and completion.choices[0].message.content
  assert completion.usage.total_tokens > 0
  second = client.chat.completions.with_raw_response.create(
      model=model,
      messages=[{"role": "user", "content": f"QA_F30_SDK_2: {prompt}"}],
      extra_headers={"X-OpenHands-ServerConversation-ID": cid},
  )
  assert second.headers.get("X-OpenHands-ServerConversation-ID") == cid
  assert second.parse().choices[0].message.content
  chunks = list(
      client.chat.completions.create(
          model=model,
          messages=[{"role": "user", "content": f"QA_F30_SDK_3: {prompt}"}],
          stream=True,
          stream_options={"include_usage": True},
      )
  )
  assert "".join(c.choices[0].delta.content or "" for c in chunks if c.choices), chunks
  assert chunks[-1].choices == [] and chunks[-1].usage.total_tokens > 0, chunks[-1]
  response = client.responses.create(model=model, input=f"QA_F30_SDK_4: {prompt}", instructions="Answer in plain text.", store=False)
  assert response.status == "completed" and response.output_text, response
  with open(os.path.join(os.environ["AGENT_SERVER_VERIFY_RUN"], "fixtures/qa-f30/sdk-cid"), "w") as handle:
      handle.write(cid)
  print("QA_F30_SDK_OK", cid)
  PY
  SDK_CID=$(cat "$F30/sdk-cid")
  control-agent-server api GET "/api/conversations/$SDK_CID/events/search" --query source=user --check items len-eq 2 \
    --check items.1.llm_message.content.0.text contains QA_F30_SDK_2 --quiet
  ```
  The SDK sends the key as `Authorization: Bearer`, parses every object
  (including the simulated stream), reads the header through
  `with_raw_response`, and continues the conversation with `extra_headers`:
  the conversation it named has both SDK turns.

## Gotchas

- It is an agent, not a proxy: every call runs the full OpenHands agent from
  the saved settings (default tools including `terminal`, MCP servers,
  condenser, confirmation policy and security analyzer, `max_iterations`) in
  the one shared `workspace_path`, so concurrent gateway calls see each
  other's files. A vague prompt can make the agent run many tools for
  minutes; the client sees nothing until the run ends.
- Model ids are `openhands_` plus an LLM profile name, case-sensitive. The
  active profile is not a default and there is no alias. Profiles and
  settings come from `OH_PERSISTENCE_DIR` (the run's `home/.openhands`), not
  from the conversations directory. If the saved settings select an ACP
  agent, the profile's LLM is ignored and only the system text is applied.
- Chat completions keep only the last user message plus all system and
  developer text; earlier turns in `messages[]` are dropped without an error.
  A client that replays history (the normal OpenAI pattern) gets an agent
  without that context unless it sends `X-OpenHands-ServerConversation-ID`.
  Responses instead folds replayed items into one user message.
- On a continued chat, the request's model, system messages and
  observability headers are validated (an unknown model is still 404) but not
  applied; the response's `model` echoes the request, not the LLM that ran.
  `/v1/responses` never reads the conversation header.
- Do not overlap requests on one conversation. A message sent while the
  conversation runs is queued for a rerun, and the waiting request returns
  whatever the conversation's final answer is when its 2 s poll sees a
  terminal state, which can be the later request's answer (see
  `F30.chat-reuse-concurrent`).
- `usage` is the conversation's accumulated usage (see
  `F30.chat-usage-per-request`); on a fresh conversation that equals the
  request's own usage summed over every model call the agent made.
- Streaming is simulated: the router runs the whole agent first
  (`stream` is forced off for the run), then sends three or four frames and
  `[DONE]` at once. Errors are plain JSON with a status code, never SSE error
  frames. `/v1/responses` refuses `stream: true`.
- The 120 s timeout and the 2 s poll for continued conversations are fixed
  in `openai/service.py`; the CLI's `api` default timeout is 60 s, so
  model-backed calls pass `--timeout 180`. A 504 does not cancel the run.
- Error bodies are FastAPI-shaped (`{"detail": ...}`), not OpenAI's
  `{"error": {...}}`. 5xx bodies are rewritten to `detail` `Internal Server
  Error` with the real reason in `exception` (`"504: Agent run timed out"`),
  so OpenAI SDKs raise a generic `InternalServerError`. No error response
  carries `X-OpenHands-ServerConversation-ID`, even when a conversation was
  created (409, 500, 504); send a UUID of your own in the header to know the
  id in advance, or search conversations by status.
- Validation order (chat): no user message 400, content parts 400 (system
  messages too), model prefix 404, profile name 400 or missing profile 404,
  then the run. Responses: `stream`/`store`/`previous_response_id` 400 first,
  then input checks, then the model. Auth 401 precedes everything.
- Gateway conversations take a run slot like any other; when
  `max_concurrent_runs` (default 10) is full the gateway answers 429 at once
  instead of queueing. For a new conversation a 429 creates nothing, so
  retrying the same request (even with the same caller-chosen id) is safe.
  On a continued conversation the user message is already saved when the
  429 comes back (see `F30.chat-reuse-run-limit-429`), and OpenAI SDKs retry
  429 automatically, so turn off SDK retries (`max_retries=0`) for continued
  conversations or expect duplicate turns.
- Gateway conversations are untitled (`autotitle` off) and never deleted;
  `store: false` is not a retention control. Delete them with
  `DELETE /api/conversations/{id}`.
- Observability tags are split on commas with blanks dropped and accept any
  other text, so tags never cause a 422; metadata that parses but is not an
  object gets a pydantic 422 list, while unparsable JSON gets the
  `must be a JSON object` message.
- The CORS middleware exposes no custom headers, so a cross-origin browser
  client cannot read `X-OpenHands-ServerConversation-ID`.
- `examples/02_remote_agent_server/15_openai_compatible_gateway.py` starts
  its own server on the fixed port 8770 with the caller's `HOME` and writes a
  `gateway_demo` profile there; do not run it beside other runs without an
  isolated `HOME`, `OH_PERSISTENCE_DIR` and working directory.
- Scripted stub profiles always pass `--no-validate` (validation spends a
  stub call and shifts the script) and `--num-retries 0` (a retried 5xx waits
  8 to 64 s per attempt and can push a run past the gateway timeout). Use a
  new stub name per script: the step counter belongs to the stub process.
