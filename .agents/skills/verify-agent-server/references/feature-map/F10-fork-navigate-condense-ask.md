# Fork, navigate, condense and ask_agent

How a consumer branches and inspects a conversation's history without
starting over. `fork` deep-copies a conversation (its whole event log, or only
the branch up to one event) into a new, independent, `idle` conversation that
names its source. `navigate` moves the conversation's HEAD (`leaf_event_id`)
to an earlier event, or to the empty tree, in place: nothing is deleted, and
the next appended event starts a sibling branch. `condense` forces one
condensation step now: the agent's summarizing condenser asks its model for a
summary and records a `Condensation` event. `ask_agent` asks the agent's model
a side question about the conversation and returns the answer without
recording it or changing the status.

Source: `openhands-agent-server/openhands/agent_server/conversation_router.py`, `openhands-agent-server/openhands/agent_server/conversation_service.py`, `openhands-agent-server/openhands/agent_server/event_service.py`, `openhands-agent-server/openhands/agent_server/models.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/conversation/state.py`, `openhands-sdk/openhands/sdk/context/condenser/`

Needs: `llm`

Routes: `POST /api/conversations/{conversation_id}/ask_agent`,
`POST /api/conversations/{conversation_id}/condense`,
`POST /api/conversations/{conversation_id}/fork`,
`POST /api/conversations/{conversation_id}/navigate`

## Sub-features

- `F10.fork-whole`: `POST /fork` with no body answers 201 with a new `idle` conversation that names its source in `forked_from_conversation_id`, keeps the source's HEAD and working directory, and holds a copy of every event (same ids) in its own persistence directory; the source is unchanged.
- `F10.fork-named`: a fork request's `id`, `title` and `tags` become the fork's id, title and tags (plus a `title` tag), on the response, on GET and in `meta.json`.
- `F10.fork-tags-consistent`: when a fork request gives no tags, the fork's tags on GET and the tags stored in its `meta.json` are one consistent set (both inherit the source's tags, or neither does).
- `F10.fork-from-event`: `from_event_id` copies only the branch up to and including that event, sets the fork's HEAD and `forked_from_event_id` to it, and leaves later events out.
- `F10.fork-metrics`: by default the fork's `stats` start empty; `reset_metrics: false` copies the source's accumulated cost and token usage.
- `F10.fork-independent`: messages appended to the fork never reach the source and vice versa; the fork's events socket replays the copied history plus its own events.
- `F10.fork-errors`: an unknown source is 404, an unknown `from_event_id` is 404, a taken `id` is 409, a bad tag key or a title over 200 characters is 422, a malformed id is 422 and a missing key is 401; none of them creates a conversation.
- `F10.fork-keeps-credentials`: the fork's `base_state.json` keeps the agent's (encrypted) model keys, like the source's.
- `F10.fork-run-memory`: a fork of a finished DeepSeek conversation runs to `finished` with the source's history in context (it recalls a codename only the source was told).
- `F10.navigate-event`: `POST /navigate` with an earlier `event_id` answers 200 with `leaf_event_id` set to it; GET and `base_state.json` agree and no event is deleted.
- `F10.navigate-branch`: after navigating, the next appended event's `parent_id` is the new HEAD (a sibling of the abandoned branch), pushed live on the events socket; the HEAD move itself is not broadcast.
- `F10.navigate-empty`: `{}` or `{"event_id": null}` selects the empty tree (`leaf_event_id` null, `head_is_empty` true on disk); the next appended event becomes a new root (`parent_id` `__root__`).
- `F10.navigate-errors`: an unknown `event_id` is 404 `Unknown event_id: ...` and an unknown conversation 404, a missing body 422, a missing key 401; none of them moves HEAD.
- `F10.navigate-final-response`: after navigating back before the agent's answer, `agent_final_response` reports the active branch (empty), not the abandoned answer.
- `F10.condense-llm`: on a finished DeepSeek conversation `POST /condense` answers 200 `{"success": true}` after recording one `CondensationRequest` and one `Condensation` with a summary, pushed live on the events socket; the status stays `finished`.
- `F10.condense-no-condenser`: an agent without a request-handling condenser (condenser disabled, or `no_op`) gets an error naming the cause, and no `CondensationRequest` or other event is recorded.
- `F10.condense-status`: that refusal is a client error (4xx), not a 500.
- `F10.condense-llm-failure`: when the condenser's model is unreachable, condense answers 500 with an `error_id`, leaves the `CondensationRequest` pending and records no `Condensation`.
- `F10.condense-errors`: an unknown id is 404 `Conversation not found`, a malformed id 422, a missing key 401 (and records nothing).
- `F10.ask-agent`: `POST /ask_agent` answers a question about the conversation with DeepSeek (200 `{"response": ...}`), records no message and pushes no message on the socket, and leaves the status unchanged.
- `F10.ask-agent-errors`: a missing `question` is 422, a missing key 401, and an unreachable model 500 with an `error_id`.
- `F10.ask-agent-running`: while a run waits on its model, `ask_agent` answers at once (it does not wait for the step), records nothing and leaves the status `running`.
- `F10.ask-agent-unknown`: an unknown conversation id is 404, as the route documents.
- `F10.restart-persist`: after a restart, forks keep their lineage, title, tags, HEAD and events, a fork id that names a persisted but not yet loaded conversation is still 409, and a navigated empty HEAD stays empty (the next message is still a new root).
- `F10.restart-cold-calls`: after a restart, condense and ask_agent work on a conversation whose agent has not been initialized yet, and ask_agent still sees the history (the codename).

## How to get to it (agent POV)

- REST: `POST /api/conversations/{conversation_id}/fork` (optional body
  `ForkConversationRequest`: `id`, `title` (max 200), `tags` (lowercase
  alphanumeric keys), `reset_metrics` (default `true`), `from_event_id`;
  query `include_skills`), 201 `ConversationInfo` of the fork; 404, 409, 422.
- REST: `POST /api/conversations/{conversation_id}/navigate` (required body
  `{"event_id": "<id>" | null}`; `{}` means null; query `include_skills`),
  200 `ConversationInfo` with the new `leaf_event_id`; 404, 422.
- REST: `POST /api/conversations/{conversation_id}/condense` (no body), 200
  `{"success": true}` once the condensation step finished; 404, 500.
- REST: `POST /api/conversations/{conversation_id}/ask_agent` (body
  `{"question": "..."}`), 200 `{"response": "..."}`; 422, 500.
- Second views used here: `GET /api/conversations/{conversation_id}`,
  `.../events/search`, `.../events/count`, `.../events/{event_id}`,
  `.../agent_final_response` (other families), the conversation's
  `meta.json` and `base_state.json` (`state cat --conversation`), the
  requests an `llm-stub` received (`sink read`), and the events socket
  `/sockets/events/{conversation_id}`. `POST .../interrupt` (the run family)
  only ends the stub run in `F10.ask-agent-running`.
- SDK: `RemoteConversation.fork(title=, tags=, reset_metrics=,
  from_event_id=, conversation_id=)` returns a `RemoteConversation` on the
  fork (passing `agent=` raises `NotImplementedError`);
  `RemoteConversation.navigate_to(event_id | None)` posts and then refreshes
  the cached state (HEAD is not broadcast); `RemoteConversation.condense()`;
  `RemoteConversation.ask_agent(question)`
  (`openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py`).
  Example: `examples/02_remote_agent_server/11_conversation_fork.py`.
- TypeScript client: `ConversationClient.forkConversation`,
  `navigateConversation`, `condenseConversation`, `askAgent`
  (`clients/typescript/src/client/conversation-client.ts`) and
  `RemoteConversation.fork`, `navigateTo`, `condense`, `askAgent`
  (`clients/typescript/src/conversation/remote-conversation.ts`).
- Every recipe below drives the REST routes directly; the SDK and TypeScript
  methods are thin wrappers over the same calls.

## Driving it with control-agent-server

Preconditions:

- A run launched with `launch --new` (no special flags), `doctor` ok, and
  `$DEEPSEEK_API_KEY` set; the block below saves the DeepSeek profiles. No
  `tmux` or browser: every agent has `tools: []` (only `finish` and `think`).
- The block below creates, in order: an unknown id `$NOPE`; the
  fully-qualified event kinds that `events/search` and `events/count` filter
  on; `S`, a conversation on an unreachable placeholder model with two user
  messages that never runs (`$E1` = `QA_F10_M1`, `$E2` = `QA_F10_M2`); `A`, a
  DeepSeek flash conversation told a codename and run to `finished`; `NC` and
  `NOOP`, offline agents whose condenser is disabled or `no_op`; `OFF`, an
  offline agent with the default summarizing condenser; and an `llm-stub`
  (`$SIDE_AGENT`) whose first call hangs for 300 s and whose later calls
  answer `QA_F10_SIDE` (a run that waits on its model cannot be held open
  with a real model). Only `A` calls a model here; the `F10.ask-agent-running`
  bullet starts its stub conversation itself, right before it needs it.

```sh
control-agent-server llm preset deepseek
NOPE=$(python3 -c 'import uuid; print(uuid.uuid4())')
MSG=openhands.sdk.event.llm_convertible.message.MessageEvent
CREQ=openhands.sdk.event.condenser.CondensationRequest
COND=openhands.sdk.event.condenser.Condensation
OFFLINE_LLM='{"model": "openai/qa-offline", "api_key": "qa-offline-key", "base_url": "http://127.0.0.1:9/v1", "num_retries": 0}'
S=$(control-agent-server conversation start --placeholder-agent --no-autotitle --no-run --prompt QA_F10_M1 --print-id)
control-agent-server api POST "/api/conversations/$S/events" --json '{"role": "user", "content": [{"type": "text", "text": "QA_F10_M2"}], "run": false}' --expect 200 --quiet
E1=$(control-agent-server api GET "/api/conversations/$S/events/search" --query kind=$MSG --query body=QA_F10_M1 --check items len-eq 1 --field items.0.id)
E2=$(control-agent-server api GET "/api/conversations/$S/events/search" --query kind=$MSG --query body=QA_F10_M2 --check items len-eq 1 --field items.0.id)
A=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'The project codename is PELICAN. Reply with just: OK' --wait --until finished,error --timeout 180 --print-id)
control-agent-server api GET "/api/conversations/$A" --check execution_status eq finished --quiet
NC=$(control-agent-server conversation start --body-json "{\"agent_settings\": {\"agent_kind\": \"openhands\", \"llm\": $OFFLINE_LLM, \"tools\": [], \"condenser\": {\"enabled\": false}}}" --no-autotitle --no-run --prompt QA_F10_NC --print-id)
NOOP=$(control-agent-server conversation start --body-json "{\"agent_settings\": {\"agent_kind\": \"openhands\", \"llm\": $OFFLINE_LLM, \"tools\": [], \"condenser\": {\"condenser_kind\": \"no_op\"}}}" --no-autotitle --no-run --prompt QA_F10_NOOP --print-id)
OFF=$(control-agent-server conversation start --placeholder-agent --no-autotitle --no-run --prompt QA_F10_OFFLINE --print-id)
SIDE=$(control-agent-server fixture llm-stub --name qa-f10-side --step hang:300 --step reply:QA_F10_SIDE --print-path)
SIDE_AGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$SIDE/v1\", \"num_retries\": 0}, \"tools\": []}"
```

- **Fork the whole log (`F10.fork-whole`).** Fork `S` with no body.
  ```sh
  N_S=$(control-agent-server api GET "/api/conversations/$S/events/count" --field .)
  LEAF_S=$(control-agent-server api GET "/api/conversations/$S" --field leaf_event_id)
  WD_S=$(control-agent-server api GET "/api/conversations/$S" --field workspace.working_dir)
  test "$LEAF_S" = "$E2"
  FW=$(control-agent-server api POST "/api/conversations/$S/fork" --expect 201 \
    --check execution_status eq idle --check forked_from_conversation_id eq "$S" \
    --check . contains '"forked_from_event_id": null' --check leaf_event_id eq "$E2" \
    --check workspace.working_dir eq "$WD_S" --check . contains '"title": null' \
    --save F10.fork-whole/fork --field id)
  test "$FW" != "$S"
  control-agent-server api GET "/api/conversations/$FW" --quiet --check forked_from_conversation_id eq "$S" --check execution_status eq idle
  control-agent-server api GET "/api/conversations/$FW/events/count" --check . eq "$N_S" --save F10.fork-whole/count
  control-agent-server api GET "/api/conversations/$FW/events/$E2" --expect 200 --check id eq "$E2" --check parent_id eq "$E1"
  test "$(control-agent-server state ls 'events/event-*.json' --conversation "$FW" | python3 -c 'import json, sys; print(json.load(sys.stdin)["count"])')" = "$N_S"
  control-agent-server state cat meta.json --conversation "$FW" --check forked_from_conversation_id eq "${S//-/}"
  control-agent-server api GET "/api/conversations/$S" --quiet --check . contains '"forked_from_conversation_id": null' --check leaf_event_id eq "$E2"
  control-agent-server api GET "/api/conversations/$S/events/count" --check . eq "$N_S"
  ```
  The fork has a new id, is `idle`, points at `S`, has `S`'s HEAD (`$E2`)
  and working directory, and the same number of events with the same ids,
  stored as `event-*.json` files under its own directory. `S` is untouched.
  `forked_from_event_id` and `title` are present and `null` (the TypeScript
  and SDK models read them as nullable fields): `--check FIELD eq null` would
  also pass for an absent key, so these checks match `"key": null` in the
  serialized body.
- **Named fork (`F10.fork-named`).** Choose the id, title and tags.
  ```sh
  FN=$(python3 -c 'import uuid; print(uuid.uuid4())')
  control-agent-server api POST "/api/conversations/$S/fork" \
    --json "{\"id\": \"$FN\", \"title\": \"QA F10 named fork\", \"tags\": {\"qa\": \"f10\"}}" --expect 201 \
    --check id eq "$FN" --check title eq 'QA F10 named fork' --check tags.qa eq f10 \
    --check tags.title eq 'QA F10 named fork' --check forked_from_conversation_id eq "$S" --save F10.fork-named/fork
  control-agent-server api GET "/api/conversations/$FN" --quiet --check title eq 'QA F10 named fork' --check tags.qa eq f10 --save F10.fork-named/get
  control-agent-server state cat meta.json --conversation "$FN" --check title eq 'QA F10 named fork' --check tags.qa eq f10
  ```
  The fork answers to `$FN`; its `tags` are the requested tags plus
  `title`, and `meta.json` stores the title and the requested tags.
- **Fork from an event (`F10.fork-from-event`).** Fork `S` up to its first
  message.
  ```sh
  FE=$(control-agent-server api POST "/api/conversations/$S/fork" --json "{\"from_event_id\": \"$E1\"}" --expect 201 \
    --check forked_from_event_id eq "$E1" --check leaf_event_id eq "$E1" --save F10.fork-from-event/fork --field id)
  control-agent-server api GET "/api/conversations/$FE/events/count" --query kind=$MSG --check . eq 1 --save F10.fork-from-event/messages
  control-agent-server api GET "/api/conversations/$FE/events/count" --query body=QA_F10_M1 --check . eq 1
  control-agent-server api GET "/api/conversations/$FE/events/count" --query body=QA_F10_M2 --check . eq 0
  control-agent-server state cat base_state.json --conversation "$FE" --max-chars 200 --check leaf_event_id eq "$E1" --check head_is_empty eq false
  control-agent-server api GET "/api/conversations/$S/events/count" --query body=QA_F10_M2 --check . eq 1
  ```
  The fork holds the system prompt and `QA_F10_M1` only, with HEAD on
  `$E1`; `S` still has `QA_F10_M2`.
- **Metrics on a fork (`F10.fork-metrics`).** `A` has a real cost.
  ```sh
  control-agent-server api GET "/api/conversations/$A" --quiet --check stats.usage_to_metrics.default.accumulated_cost gt 0
  FK=$(control-agent-server api POST "/api/conversations/$A/fork" --json '{"reset_metrics": false}' --expect 201 --quiet \
    --check stats.usage_to_metrics.default.accumulated_cost gt 0 --save F10.fork-metrics/kept --field id)
  control-agent-server api GET "/api/conversations/$FK" --quiet --check stats.usage_to_metrics.default.accumulated_cost gt 0
  FR=$(control-agent-server api POST "/api/conversations/$A/fork" --expect 201 --quiet \
    --check stats.usage_to_metrics len-eq 0 --save F10.fork-metrics/reset --field id)
  control-agent-server api GET "/api/conversations/$FR" --quiet --check stats.usage_to_metrics len-eq 0
  ```
  With `reset_metrics: false` the fork reports the source's cost; by default
  `stats.usage_to_metrics` is `{}`.
- **Fork and source diverge (`F10.fork-independent`).** Append to each side
  and replay the fork's socket.
  ```sh
  control-agent-server api POST "/api/conversations/$FW/events" --json '{"role": "user", "content": [{"type": "text", "text": "QA_F10_ONLY_FORK"}], "run": false}' --expect 200 --quiet
  control-agent-server api POST "/api/conversations/$S/events" --json '{"role": "user", "content": [{"type": "text", "text": "QA_F10_ONLY_SOURCE"}], "run": false}' --expect 200 --quiet
  control-agent-server api GET "/api/conversations/$FW/events/count" --query body=QA_F10_ONLY_FORK --check . eq 1 --save F10.fork-independent/fork-has-own
  control-agent-server api GET "/api/conversations/$S/events/count" --query body=QA_F10_ONLY_FORK --check . eq 0
  control-agent-server api GET "/api/conversations/$S/events/count" --query body=QA_F10_ONLY_SOURCE --check . eq 1
  control-agent-server api GET "/api/conversations/$FW/events/count" --query body=QA_F10_ONLY_SOURCE --check . eq 0 --save F10.fork-independent/fork-lacks-source
  control-agent-server ws start "/sockets/events/$FW" --name qa-f10-fork-replay --query resend_mode=all --duration 60
  control-agent-server ws stop qa-f10-fork-replay --wait 15 --kinds MessageEvent --contains QA_F10_ONLY_FORK --expect-min 1 --save F10.fork-independent/replay
  control-agent-server ws read qa-f10-fork-replay --kinds MessageEvent --contains QA_F10_M1 --expect-min 1
  control-agent-server ws read qa-f10-fork-replay --contains QA_F10_ONLY_SOURCE --expect-none
  ```
  Each side sees only its own new message. The fork's socket replays the
  copied `QA_F10_M1` and its own `QA_F10_ONLY_FORK`, never the source's.
- **Unknown, taken and invalid input (`F10.fork-errors`).** Count
  conversations first; none of these may create one.
  ```sh
  C0=$(control-agent-server api GET /api/conversations/count --field .)
  LONG=$(python3 -c 'print("x" * 201)')
  control-agent-server api POST "/api/conversations/$NOPE/fork" --expect 404 --check detail eq 'Source conversation not found' --save F10.fork-errors/unknown-source
  control-agent-server api POST "/api/conversations/$S/fork" --json '{"from_event_id": "qa-f10-nope"}' --expect 404 \
    --check detail eq 'Unknown from_event_id: qa-f10-nope' --save F10.fork-errors/unknown-event
  control-agent-server api POST "/api/conversations/$S/fork" --json "{\"id\": \"$S\"}" --expect 409 \
    --check detail eq "Conversation with id $S already exists" --save F10.fork-errors/taken-by-source
  control-agent-server api POST "/api/conversations/$S/fork" --json "{\"id\": \"$FN\"}" --expect 409 --check detail eq "Conversation with id $FN already exists"
  control-agent-server api POST "/api/conversations/$S/fork" --json '{"tags": {"Bad-Key": "x"}}' --expect 422 --check detail.0.loc.1 eq tags --save F10.fork-errors/bad-tag
  control-agent-server api POST "/api/conversations/$S/fork" --json "{\"title\": \"$LONG\"}" --expect 422 --check detail.0.type eq string_too_long
  control-agent-server api POST /api/conversations/not-a-uuid/fork --expect 422 --check detail.0.loc.1 eq conversation_id
  control-agent-server api POST "/api/conversations/$S/fork" --auth none --expect 401
  control-agent-server api GET /api/conversations/count --check . eq "$C0" --save F10.fork-errors/count-after
  test "$(control-agent-server state ls 'server/workspace/conversations/*/meta.json' | python3 -c 'import json, sys; print(json.load(sys.stdin)["count"])')" = "$C0"
  ```
  404 `Source conversation not found`, 404 `Unknown from_event_id:
  qa-f10-nope`, 409 `Conversation with id ... already exists` for the
  source's id and for an existing fork's id, 422 for the tag key, the
  201-character title and the malformed id, 401 without a key (the same
  request with the key succeeded in `F10.fork-whole`). The conversation
  count and the `meta.json` files are unchanged.
- **Fork tags agree with the stored metadata (`F10.fork-tags-consistent`), known bug.**
  Fork a conversation tagged `qa=src` with a title and no tags, then compare
  the fork's `qa` tag on GET with the one in its `meta.json`.
  ```sh
  TS=$(control-agent-server conversation start --placeholder-agent --no-autotitle --no-run --tag qa=src --prompt QA_F10_TAGS --print-id)
  control-agent-server api GET "/api/conversations/$TS" --quiet --check tags.qa eq src
  control-agent-server state cat meta.json --conversation "$TS" --check tags.qa eq src
  TF=$(control-agent-server api POST "/api/conversations/$TS/fork" --json '{"title": "QA F10 tag fork"}' --expect 201 --quiet \
    --check tags.title eq 'QA F10 tag fork' --save F10.fork-tags-consistent/fork --field id)
  TF_QA=$(control-agent-server api GET "/api/conversations/$TF" --field tags | python3 -c 'import json, sys; v = json.load(sys.stdin).get("qa"); print("null" if v is None else v)')
  control-agent-server state cat meta.json --conversation "$TF" --check title eq 'QA F10 tag fork' --check tags.qa eq "$TF_QA"  # bug
  ```
  The source shows `qa=src` on GET and in `meta.json` (the two views can
  agree). Expected: the fork's GET and `meta.json` agree on `qa`, whichever
  way the server settles it (inherit the source's tags or start without
  them). Today GET (from `base_state.json`) shows only `{"title": "QA F10 tag
  fork"}`, so `TF_QA` is `null`, while `meta.json` says `{"qa": "src"}`:
  `ConversationService.fork_conversation` copies the source's stored metadata
  and overrides `tags` only when the request has some, but
  `LocalConversation.fork` builds the fork's state tags from the request
  alone. Server code that reads `meta.json` tags (telemetry's automation
  flag, the observability span's conversation tags) sees a different set
  than every consumer.
- **The fork keeps the model keys (`F10.fork-keeps-credentials`), known bug.**
  Fork `S`, whose placeholder agent has a model key, and compare the stored
  agents.
  ```sh
  KF=$(control-agent-server api POST "/api/conversations/$S/fork" --expect 201 --field id)
  control-agent-server state cat base_state.json --conversation "$S" --max-chars 200 --check agent.llm.api_key exists --check agent.condenser.llm.api_key exists
  control-agent-server state cat base_state.json --conversation "$KF" --max-chars 200 --check agent.llm.api_key exists --check agent.condenser.llm.api_key exists  # bug
  ```
  Expected: both files hold the keys (encrypted with the server's cipher;
  `state cat` shows `<redacted:encrypted>`). Today the source's do and the
  fork's have no `api_key` at all (the check reports `null`):
  `LocalConversation.fork` builds the fork's
  `LocalConversation` without the source's `cipher`, so its state is
  serialized with secrets dropped, and the server then loads the fork's
  agent from that file (`ConversationService.fork_conversation` starts the
  fork's `EventService` with no agent of its own).
- **A fork runs with the source's memory (`F10.fork-run-memory`), known bug.**
  Fork the finished DeepSeek conversation `A` and ask the fork what only the
  source was told.
  ```sh
  N_A_MSG=$(control-agent-server api GET "/api/conversations/$A/events/count" --query kind=$MSG --field .)
  AF=$(control-agent-server api POST "/api/conversations/$A/fork" --json '{"title": "QA F10 memory fork"}' --expect 201 --field id)
  control-agent-server conversation send "$AF" --text 'What is the project codename I told you? Answer with the codename only.' --wait --until finished,error --timeout 180
  control-agent-server conversation events "$AF" --kinds ConversationErrorEvent --contains LLMAuthenticationError --expect-count 0 --save F10.fork-run-memory/auth-errors  # bug
  control-agent-server api GET "/api/conversations/$AF" --quiet --check execution_status eq finished --save F10.fork-run-memory/status
  control-agent-server api GET "/api/conversations/$AF/agent_final_response" --check response matches '(?i)pelican' --save F10.fork-run-memory/final
  control-agent-server api GET "/api/conversations/$A/events/count" --query kind=$MSG --check . eq "$N_A_MSG"
  ```
  `A` itself ran to `finished` on the same profile (Preconditions), so the
  key works. Expected: the fork's run records no authentication error, runs
  to `finished`, its answer names the codename from the copied history (a
  fact only the source was told, not wording; `agent_final_response` reads a
  reply given as a message or through `finish`), and `A` gains no message
  from the fork's run. Today the fork's run ends in `error` within seconds
  with a `ConversationErrorEvent` `LLMAuthenticationError` ("Your LLM API key
  appears to be invalid or has expired."), because the fork lost the model
  key (`F10.fork-keeps-credentials`); the marked check fails on that event.
  Any other failure (a provider outage, a wrong answer) fails a later,
  unmarked check and reports `fail`, not `xfail`. Any fork of a conversation
  whose model needs a key cannot run.
- **Navigate to an earlier event (`F10.navigate-event`).** Move `S`'s HEAD
  back to its first message.
  ```sh
  N=$(control-agent-server api GET "/api/conversations/$S/events/count" --field .)
  control-agent-server api POST "/api/conversations/$S/navigate" --json "{\"event_id\": \"$E1\"}" --expect 200 \
    --check id eq "$S" --check leaf_event_id eq "$E1" --quiet --save F10.navigate-event/navigate
  control-agent-server api GET "/api/conversations/$S" --quiet --check leaf_event_id eq "$E1" --save F10.navigate-event/get
  control-agent-server state cat base_state.json --conversation "$S" --max-chars 200 --check leaf_event_id eq "$E1" --check head_is_empty eq false
  control-agent-server api GET "/api/conversations/$S/events/count" --check . eq "$N"
  control-agent-server api GET "/api/conversations/$S/events/$E2" --expect 200 --check id eq "$E2"
  ```
  The response, GET and `base_state.json` all carry `leaf_event_id` `$E1`;
  the event count is unchanged and the abandoned `$E2` is still readable.
- **The next event branches (`F10.navigate-branch`).** Watch the socket while
  navigating and appending.
  ```sh
  control-agent-server ws start "/sockets/events/$S" --name qa-f10-branch --duration 120
  control-agent-server ws read qa-f10-branch --wait 15 --kinds ConversationStateUpdateEvent --contains full_state --expect-min 1
  control-agent-server api POST "/api/conversations/$S/navigate" --json "{\"event_id\": \"$E1\"}" --expect 200 --quiet
  control-agent-server api POST "/api/conversations/$S/events" --json '{"role": "user", "content": [{"type": "text", "text": "QA_F10_M3"}], "run": false}' --expect 200 --quiet
  control-agent-server ws stop qa-f10-branch --wait 15 --kinds MessageEvent --contains "\"parent_id\": \"$E1\"" --expect-min 1 --save F10.navigate-branch/frames
  control-agent-server ws read qa-f10-branch --kinds ConversationStateUpdateEvent --contains '"key": "last_user_message_id"' --expect-min 1
  control-agent-server ws read qa-f10-branch --contains '"key": "leaf_event_id"' --expect-none
  E3=$(control-agent-server api GET "/api/conversations/$S/events/search" --query kind=$MSG --query body=QA_F10_M3 \
    --check items.0.parent_id eq "$E1" --save F10.navigate-branch/new-event --field items.0.id)
  control-agent-server api GET "/api/conversations/$S/events/$E2" --check parent_id eq "$E1"
  control-agent-server api GET "/api/conversations/$S" --quiet --check leaf_event_id eq "$E3"
  ```
  `QA_F10_M3` arrives on the socket with `parent_id` `$E1`, the same parent
  as `$E2`: two sibling branches. State changes do arrive as
  `ConversationStateUpdateEvent` frames keyed by field
  (`last_user_message_id` here), but no frame announces the HEAD move, which
  is why the SDK and TypeScript `navigate` methods refresh their state with a
  GET. HEAD now sits on the new message.
- **Navigate to the empty tree (`F10.navigate-empty`).** `{}` and
  `{"event_id": null}` are the same request.
  ```sh
  control-agent-server api POST "/api/conversations/$S/navigate" --json '{}' --expect 200 --quiet --check . contains '"leaf_event_id": null' --save F10.navigate-empty/navigate
  control-agent-server state cat base_state.json --conversation "$S" --max-chars 200 --check leaf_event_id eq null --check head_is_empty eq true
  control-agent-server api POST "/api/conversations/$S/events" --json '{"role": "user", "content": [{"type": "text", "text": "QA_F10_ROOT"}], "run": false}' --expect 200 --quiet
  control-agent-server api GET "/api/conversations/$S/events/search" --query kind=$MSG --query body=QA_F10_ROOT --check items.0.parent_id eq __root__ --save F10.navigate-empty/new-root
  control-agent-server state cat base_state.json --conversation "$S" --max-chars 200 --check head_is_empty eq false
  control-agent-server api POST "/api/conversations/$S/navigate" --json '{"event_id": null}' --expect 200 --quiet --check . contains '"leaf_event_id": null'
  control-agent-server state cat base_state.json --conversation "$S" --max-chars 200 --check head_is_empty eq true
  ```
  HEAD is `null` with `head_is_empty: true` on disk; the next message is a
  new root (`parent_id` `__root__`) and clears the flag; the explicit `null`
  form empties HEAD again (it stays empty for `F10.restart-persist`).
- **Unknown, missing and unauthenticated (`F10.navigate-errors`).** HEAD is
  empty from the previous bullet and must stay so.
  ```sh
  control-agent-server api POST "/api/conversations/$S/navigate" --json '{"event_id": "qa-f10-nope"}' --expect 404 \
    --check detail eq 'Unknown event_id: qa-f10-nope' --save F10.navigate-errors/unknown-event
  control-agent-server api POST "/api/conversations/$NOPE/navigate" --json '{}' --expect 404 --check detail eq 'Conversation not found' --save F10.navigate-errors/unknown-conversation
  control-agent-server api POST "/api/conversations/$S/navigate" --expect 422 --check detail.0.loc.0 eq body --check detail.0.type eq missing
  control-agent-server api POST /api/conversations/not-a-uuid/navigate --json '{}' --expect 422 --check detail.0.loc.1 eq conversation_id
  control-agent-server api POST "/api/conversations/$S/navigate" --json "{\"event_id\": \"$E1\"}" --auth none --expect 401
  control-agent-server api GET "/api/conversations/$S" --quiet --check . contains '"leaf_event_id": null'
  control-agent-server state cat base_state.json --conversation "$S" --max-chars 200 --check head_is_empty eq true
  ```
  404 `Unknown event_id: qa-f10-nope`, 404 `Conversation not found`, 422 for
  a missing body (an empty body is not `{}`) and a malformed id, 401 without
  a key (the same request with the key worked in `F10.navigate-event`). HEAD
  is still empty.
- **Final response after navigating back (`F10.navigate-final-response`), known bug.**
  Use a fork of `A` so `A` keeps its history for the condense bullets.
  ```sh
  AN=$(control-agent-server api POST "/api/conversations/$A/fork" --expect 201 --field id)
  U1=$(control-agent-server api GET "/api/conversations/$AN/events/search" --query kind=$MSG --query source=user --check items len-eq 1 --field items.0.id)
  control-agent-server api GET "/api/conversations/$AN/agent_final_response" --check response matches '\S'
  control-agent-server api POST "/api/conversations/$AN/navigate" --json "{\"event_id\": \"$U1\"}" --expect 200 --quiet --check leaf_event_id eq "$U1"
  control-agent-server api GET "/api/conversations/$AN/agent_final_response" --check response eq '' --save F10.navigate-final-response/after-navigate  # bug
  ```
  Expected: once HEAD is back on the user's message, the active branch has no
  agent answer, so `agent_final_response` is `""` (the agent's context, title
  generation, pending-action and stuck checks all use the active branch).
  Today it still returns the abandoned answer (`OK`): the route scans the
  whole event log (`EventService._get_agent_final_response_sync`). The
  status also stays `finished`, so `POST /run` right after such a navigate
  does nothing; send a new message to regenerate.
- **Condense with DeepSeek (`F10.condense-llm`).** `A` uses the default
  `LLMSummarizingCondenser` on DeepSeek flash.
  ```sh
  control-agent-server api GET "/api/conversations/$A" --quiet --check agent.condenser.kind eq LLMSummarizingCondenser
  control-agent-server ws start "/sockets/events/$A" --name qa-f10-condense --duration 180
  control-agent-server ws read qa-f10-condense --wait 15 --kinds ConversationStateUpdateEvent --contains full_state --expect-min 1
  control-agent-server api POST "/api/conversations/$A/condense" --expect 200 --check success eq true --timeout 180 --save F10.condense-llm/condense
  control-agent-server ws stop qa-f10-condense --wait 30 --kinds Condensation --expect-min 1 --save F10.condense-llm/frames
  control-agent-server ws read qa-f10-condense --kinds CondensationRequest --expect-min 1
  control-agent-server api GET "/api/conversations/$A/events/count" --query kind=$CREQ --check . eq 1
  control-agent-server api GET "/api/conversations/$A/events/count" --query kind=$COND --check . eq 1
  control-agent-server api GET "/api/conversations/$A/events/search" --query kind=$COND \
    --check items.0.summary matches '\S' --check items.0.forgotten_event_ids len-ge 1 --save F10.condense-llm/condensation
  control-agent-server api GET "/api/conversations/$A" --quiet --check execution_status eq finished
  ```
  The call returns after the summary exists: one `CondensationRequest` and
  one `Condensation` (non-empty `summary`, at least one forgotten event id),
  both pushed on the socket; `A` is still `finished`.
- **No request-handling condenser (`F10.condense-no-condenser`).** `NC` has
  its condenser disabled, `NOOP` a `no_op` one.
  ```sh
  control-agent-server api GET "/api/conversations/$NC" --quiet --check agent.condenser eq null
  control-agent-server api GET "/api/conversations/$NOOP" --quiet --check agent.condenser.kind eq NoOpCondenser
  N_NC=$(control-agent-server api GET "/api/conversations/$NC/events/count" --field .)
  N_NOOP=$(control-agent-server api GET "/api/conversations/$NOOP/events/count" --field .)
  control-agent-server api POST "/api/conversations/$NC/condense" --expect 4xx,5xx --check . matches 'No condenser configured' --save F10.condense-no-condenser/disabled
  control-agent-server api POST "/api/conversations/$NOOP/condense" --expect 4xx,5xx --check . matches 'NoOpCondenser does not handle condensation requests' --save F10.condense-no-condenser/no-op
  control-agent-server api GET "/api/conversations/$NC/events/count" --check . eq "$N_NC"
  control-agent-server api GET "/api/conversations/$NOOP/events/count" --check . eq "$N_NOOP"
  control-agent-server api GET "/api/conversations/$NOOP/events/count" --query kind=$CREQ --check . eq 0
  control-agent-server api GET "/api/conversations/$NC" --quiet --check execution_status eq idle
  ```
  Both calls fail, naming the cause (`Cannot condense conversation: No
  condenser configured` / `Condenser NoOpCondenser does not handle
  condensation requests`; the check reads the whole body, so it holds
  whether the cause is in `exception` today or in `detail` once
  `F10.condense-status` is fixed), and neither conversation gains an event.
- **Refusal status (`F10.condense-status`), known bug.** A missing condenser
  is the caller's configuration, not a server fault.
  ```sh
  control-agent-server api GET "/api/conversations/$NC" --quiet --check execution_status eq idle
  control-agent-server api POST "/api/conversations/$NC/condense" --expect 4xx --save F10.condense-status/disabled  # bug
  control-agent-server api POST "/api/conversations/$NOOP/condense" --expect 4xx --save F10.condense-status/no-op  # bug
  ```
  Expected: a 4xx (400, 409 or 422) carrying the cause. Today both answer
  500 `{"detail": "Internal Server Error", "exception": "Cannot condense
  conversation: ...", "error_id": ...}`, and the server logs an unhandled
  traceback: `LocalConversation._condense` raises `ValueError`, which neither
  `EventService.condense` nor the router maps (the route documents only
  404).
- **Unreachable condenser model (`F10.condense-llm-failure`).** `OFF` uses
  the default summarizing condenser on an unreachable placeholder model.
  ```sh
  control-agent-server api GET "/api/conversations/$OFF" --quiet --check agent.condenser.kind eq LLMSummarizingCondenser
  control-agent-server api POST "/api/conversations/$OFF/condense" --expect 500 --check error_id exists --timeout 120 --save F10.condense-llm-failure/condense
  control-agent-server api GET "/api/conversations/$OFF/events/count" --query kind=$CREQ --check . eq 1
  control-agent-server api GET "/api/conversations/$OFF/events/count" --query kind=$COND --check . eq 0
  control-agent-server api GET "/api/conversations/$OFF" --quiet --check execution_status eq idle
  ```
  500 with an `error_id` (the `exception` reads `Unable to compute forgotten
  events`: the summary call and the hard-reset retries all failed). The
  `CondensationRequest` stays in the log unhandled, so the next agent step
  will try to condense first; no `Condensation` exists and the status is
  still `idle`.
- **Unknown, malformed and unauthenticated (`F10.condense-errors`).**
  ```sh
  control-agent-server api POST "/api/conversations/$NOPE/condense" --expect 404 --check detail eq 'Conversation not found' --save F10.condense-errors/unknown
  control-agent-server api POST /api/conversations/not-a-uuid/condense --expect 422 --check detail.0.loc.1 eq conversation_id
  control-agent-server api POST "/api/conversations/$A/condense" --auth none --expect 401
  control-agent-server api GET "/api/conversations/$A/events/count" --query kind=$CREQ --check . eq 1
  ```
  404 `Conversation not found`, 422, 401; `A` still has exactly one
  `CondensationRequest`.
- **Ask a side question (`F10.ask-agent`).** Ask `A` about its own history
  while its socket is open.
  ```sh
  N_MSG=$(control-agent-server api GET "/api/conversations/$A/events/count" --query kind=$MSG --field .)
  N_ALL=$(control-agent-server api GET "/api/conversations/$A/events/count" --field .)
  N_PELICAN=$(control-agent-server api GET "/api/conversations/$A/events/count" --query body=PELICAN --query source=agent --field .)
  control-agent-server ws start "/sockets/events/$A" --name qa-f10-ask --duration 120
  control-agent-server ws read qa-f10-ask --wait 15 --kinds ConversationStateUpdateEvent --contains full_state --expect-min 1
  control-agent-server api POST "/api/conversations/$A/ask_agent" --json '{"question": "What is the project codename? Answer with the codename only."}' \
    --expect 200 --check response matches '(?i)pelican' --timeout 120 --save F10.ask-agent/ask
  control-agent-server api GET "/api/conversations/$A/events/count" --query kind=$MSG --check . eq "$N_MSG" --save F10.ask-agent/messages
  control-agent-server api GET "/api/conversations/$A/events/count" --query body=PELICAN --query source=agent --check . eq "$N_PELICAN"
  control-agent-server api GET "/api/conversations/$A/events/count" --check . eq "$N_ALL"
  control-agent-server api GET "/api/conversations/$A" --quiet --check execution_status eq finished
  control-agent-server ws stop qa-f10-ask --kinds MessageEvent,ActionEvent --expect-none --save F10.ask-agent/frames
  ```
  The answer names the codename, which only the conversation's history holds
  (the check is on that fact, not the wording). Nothing is recorded: the
  message count, the count of agent events that mention the codename (the
  source run's own reply or reasoning may already mention it, so the check
  compares with the count before the call) and the total event count are
  unchanged. Nothing is pushed: the socket was live before the call (its
  `full_state` snapshot had arrived, and the same kind of socket delivered
  `Condensation` frames in `F10.condense-llm`), and `A` is still `finished`.
- **Bad input, no key, unreachable model (`F10.ask-agent-errors`).**
  ```sh
  control-agent-server api POST "/api/conversations/$A/ask_agent" --json '{}' --expect 422 --check detail.0.loc.1 eq question --save F10.ask-agent-errors/missing-question
  control-agent-server api POST "/api/conversations/$A/ask_agent" --json '{"question": "hi"}' --auth none --expect 401
  control-agent-server api POST "/api/conversations/$OFF/ask_agent" --json '{"question": "Reply with one word."}' --expect 500 \
    --check error_id exists --timeout 120 --save F10.ask-agent-errors/offline
  control-agent-server api GET "/api/conversations/$OFF" --quiet --check execution_status eq idle
  ```
  422 naming `question`, 401, and 500 with an `error_id` whose `exception`
  carries the provider's connection error; `OFF` stays `idle`.
- **Side question during a run (`F10.ask-agent-running`).** Start `R` on the
  hanging stub, wait until its step is blocked on the model (the stub has
  received exactly one call), then ask.
  ```sh
  R=$(control-agent-server conversation start --body-json "{\"agent\": $SIDE_AGENT}" --no-autotitle --prompt QA_F10_BUSY --print-id)
  control-agent-server conversation wait "$R" --until running --timeout 30
  control-agent-server sink read --name qa-f10-side --expect-min 1 --expect-max 1 --wait 30
  N_R=$(control-agent-server api GET "/api/conversations/$R/events/count" --field .)
  control-agent-server api POST "/api/conversations/$R/ask_agent" --json '{"question": "QA_F10_SIDE_Q"}' --expect 200 \
    --check response eq QA_F10_SIDE --expect-max-ms 15000 --timeout 60 --save F10.ask-agent-running/ask
  control-agent-server api GET "/api/conversations/$R" --quiet --check execution_status eq running --save F10.ask-agent-running/still-running
  control-agent-server api GET "/api/conversations/$R/events/count" --check . eq "$N_R"
  control-agent-server sink read --name qa-f10-side --contains QA_F10_SIDE_Q --expect-min 1 --expect-max 1
  control-agent-server api POST "/api/conversations/$R/interrupt" --expect 200 --quiet
  control-agent-server conversation wait "$R" --until paused --timeout 30
  control-agent-server api GET "/api/conversations/$R/events/count" --query body=QA_F10_SIDE --check . eq 0
  ```
  The stub's first call (the run's step) is still hanging when the question
  arrives; the second call carries the question and answers `QA_F10_SIDE`
  within a fraction of a second, so `ask_agent` neither takes the state lock
  the step holds nor waits for it. `R` is still `running`, its event count is
  unchanged, and after the interrupt (which frees the run slot) no event
  carries the side answer.
- **Unknown conversation (`F10.ask-agent-unknown`), known bug.** The route
  documents 404, like condense and navigate.
  ```sh
  control-agent-server api GET /openapi.json --auth none --quiet \
    --check 'paths./api/conversations/{conversation_id}/ask_agent.post.responses.404' exists
  control-agent-server api POST "/api/conversations/$NOPE/ask_agent" --json '{"question": "hi"}' --expect 404 --save F10.ask-agent-unknown/unknown  # bug
  ```
  Expected: 404. Today it is 500 `{"detail": "Internal Server Error",
  "exception": "500: Internal Server Error"}`:
  `ConversationService.ask_agent` returns `None` for an unknown id and the
  router turns `None` into a bare 500.
- **State after a restart (`F10.restart-persist`).** Restart, then read the
  forks and `S`'s empty HEAD back.
  ```sh
  control-agent-server restart
  control-agent-server api POST "/api/conversations/$S/fork" --json "{\"id\": \"$FE\"}" --expect 409 \
    --check detail eq "Conversation with id $FE already exists" --save F10.restart-persist/taken-by-unloaded
  control-agent-server api GET "/api/conversations/$FN" --quiet --check forked_from_conversation_id eq "$S" \
    --check title eq 'QA F10 named fork' --check tags.qa eq f10 --check execution_status eq idle --save F10.restart-persist/named-fork
  control-agent-server api GET "/api/conversations/$FE" --quiet --check forked_from_event_id eq "$E1" --check leaf_event_id eq "$E1"
  control-agent-server api GET "/api/conversations/$FW/events/count" --query body=QA_F10_ONLY_FORK --check . eq 1
  control-agent-server api GET "/api/conversations/$S" --quiet --check . contains '"leaf_event_id": null' --save F10.restart-persist/source
  control-agent-server state cat base_state.json --conversation "$S" --max-chars 200 --check head_is_empty eq true
  control-agent-server api POST "/api/conversations/$S/events" --json '{"role": "user", "content": [{"type": "text", "text": "QA_F10_ROOT2"}], "run": false}' --expect 200 --quiet
  control-agent-server api GET "/api/conversations/$S/events/search" --query kind=$MSG --query body=QA_F10_ROOT2 --check items.0.parent_id eq __root__
  control-agent-server doctor
  ```
  Right after the restart nothing has loaded `FE` yet, and a fork that asks
  for `FE`'s id is still 409 (the server checks its catalog of persisted
  conversations, not only the loaded ones), leaving `FE` intact. The forks
  come back with their lineage, title, tags, HEAD and events; `S`'s HEAD is
  still empty, so the first message after the restart is a new root.
- **Condense and ask on a cold conversation (`F10.restart-cold-calls`).**
  After the restart nothing has initialized `A`'s agent yet.
  ```sh
  N_COND=$(control-agent-server api GET "/api/conversations/$A/events/count" --query kind=$COND --field .)
  control-agent-server api POST "/api/conversations/$A/condense" --expect 200 --check success eq true --timeout 180 --save F10.restart-cold-calls/condense
  control-agent-server api GET "/api/conversations/$A/events/count" --query kind=$COND --check . eq $((N_COND + 1))
  control-agent-server api POST "/api/conversations/$A/ask_agent" --json '{"question": "What is the project codename? Answer with the codename only."}' \
    --expect 200 --check response matches '(?i)pelican' --timeout 120 --save F10.restart-cold-calls/ask
  control-agent-server api GET "/api/conversations/$A" --quiet --check execution_status eq finished
  ```
  A second `Condensation` is recorded and ask_agent still names the codename
  from the reloaded history (the condenser keeps the first two events, the
  system prompt and the user's message); `A` stays `finished`.

## Gotchas

- `fork` copies events with their original ids, so the same event id exists
  in the source and every fork; always scope event lookups by conversation.
- A fork shares the source's working directory (`workspace.working_dir`):
  files one side's agent writes are visible to the other. The fork gets its
  own persistence directory, lease and event log only.
- A fork's `tags` (on the response and GET) are the request's tags plus
  `{"title": <title>}` when a title is given, never the source's. Its
  `meta.json`, however, copies the source's stored metadata (secrets,
  plugins, `parent_conversation_id`, client tools) including the source's
  tags when the request has none: a fork of a conversation tagged
  `{"qa": "src"}` shows `{"title": ...}` on GET (also after a restart) while
  its `meta.json` says `{"qa": "src"}` (`F10.fork-tags-consistent`, a known
  bug). The fork's `last_user_message_id` starts `null`.
- The fork's `base_state.json` is written without the server's cipher, so
  the model keys in the fork's agent are dropped from it
  (`F10.fork-keeps-credentials`) and a fork cannot call its model
  (`F10.fork-run-memory`).
- `navigate` with `{}` silently selects the empty tree (the agent loses all
  context on its next run), because `event_id` defaults to `null`; a request
  with no body at all is 422. `head_is_empty` is not part of
  `ConversationInfo`; read it from `base_state.json`.
- HEAD moves are not broadcast on the events socket (`leaf_event_id` and
  `head_is_empty` changes are skipped by the state-change callback); clients
  re-read the conversation after `navigate`.
- `condense` is synchronous and waits for a running step to finish (it takes
  the state lock), so it can take as long as one model call. It needs a model:
  without one that answers, it is a 500 that leaves a pending
  `CondensationRequest` in the log.
- `ask_agent` initializes the agent first, so on a conversation with no
  events yet (created without a message) it appends the `SystemPromptEvent`
  the first run would have written, even when the model call then fails. A
  conversation created with a message already has that event. During a run
  it does not wait for the step (`F10.ask-agent-running`), unlike `condense`,
  `fork` and `navigate`, which take the state lock (from source; not driven
  here).
  On a conversation that has not been reloaded it records no event at all
  (`F10.ask-agent` checks the total count). On one reloaded after a restart,
  each call records and pushes a `stats` `ConversationStateUpdateEvent`
  whose `usage_to_metrics` includes an `ask-agent-llm` entry, while GET's
  `stats` keeps only the stored `default` and `condenser` usage (seen on
  DeepSeek; not asserted). It passes the agent's tools to
  the model and returns the first text block, so on a conversation with no
  messages yet DeepSeek's answer came back as an empty `response` (200).
- `fork` and `navigate` return `ConversationInfo` with
  `agent.agent_context.skills` emptied unless `?include_skills=true` (the
  stored state keeps them); `condense` and `ask_agent` return no
  conversation.
