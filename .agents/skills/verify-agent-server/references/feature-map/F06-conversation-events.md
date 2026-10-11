# Conversation events and the events WebSocket

Every conversation keeps an append-only event log: the system prompt, each
user and agent message, every tool action and observation, and one state
update per changed field (`execution_status`, `stats`, `last_user_message_id`,
...). Consumers append a user message with `POST .../events` (and optionally
start the agent), read history through search (filters, cursor paging, sort),
count, get-one and batch-get, and follow the conversation live over
`/sockets/events/{conversation_id}`: first-frame auth, a transient
`full_state` snapshot, optional replay of history (`resend_mode=all` or
`since`), then every new event as raw JSON; a message frame sent on the socket
is appended and runs the agent.

Source: `openhands-agent-server/openhands/agent_server/event_router.py`, `openhands-agent-server/openhands/agent_server/event_service.py`, `openhands-agent-server/openhands/agent_server/sockets.py`, `openhands-agent-server/openhands/agent_server/pub_sub.py`, `openhands-agent-server/openhands/agent_server/dependencies.py`, `openhands-sdk/openhands/sdk/conversation/request.py`, `openhands-sdk/openhands/sdk/conversation/event_store.py`, `openhands-sdk/openhands/sdk/event/`

Needs: `llm`

Launch: `--env TZ=UTC`

Routes: `GET /api/conversations/{conversation_id}/events/search`,
`GET /api/conversations/{conversation_id}/events/count`,
`GET /api/conversations/{conversation_id}/events/{event_id}`,
`GET /api/conversations/{conversation_id}/events`,
`POST /api/conversations/{conversation_id}/events`,
`WS /sockets/events/{conversation_id}`

## Sub-features

- `F06.send-no-run`: `POST .../events` with `run: false` persists a user `MessageEvent` (preceded by the `SystemPromptEvent` on the first message), returns `{"success": true}` and leaves the conversation `idle`.
- `F06.search-page`: search returns `{items, next_page_id}` in log order; `limit` plus the inclusive `page_id` cursor pages through it, `TIMESTAMP_DESC` reverses it, and an unknown `page_id` restarts from the first event.
- `F06.search-validation`: search rejects `limit` 0 or 101, an unknown `sort_order` and an unparsable timestamp with 422.
- `F06.search-filters`: `source`, `body` (case-insensitive, message text only) and fully-qualified `kind` filters narrow search results and combine.
- `F06.search-kind-short`: `kind=MessageEvent`, the short name the API docs and the TypeScript client use, filters by the wire `kind`.
- `F06.search-time-window`: `timestamp__gte` is inclusive, `timestamp__lt` exclusive, and a value with a UTC offset is converted to server time.
- `F06.count`: count returns a bare integer equal to the number of search results for the same filters.
- `F06.get-one`: `GET .../events/{event_id}` returns the event that search lists under that id, and the same event is on disk.
- `F06.unknown-event`: an unknown event id returns 404 from get-one and `null` in its slot from batch-get.
- `F06.batch-get`: `GET .../events` with a JSON array of ids as the body returns those events in request order; no body is a 422.
- `F06.not-found-auth`: every event route returns 404 for an unknown conversation, 422 for a malformed conversation id and 401 without a valid session key.
- `F06.send-validation`: a missing body, a message with an unknown role, string content or an unknown content type is rejected with 422 and nothing is appended.
- `F06.send-image`: a message with a text part and an `image` part (a `data:` URL) is persisted with its `image_urls` intact and returned the same by search, get-one and the socket replay; an `image` part without `image_urls` is a 422 and appends nothing.
- `F06.send-non-user-role`: a message with a schema-valid non-user role (`assistant`) is rejected as a client error (4xx), not a 500, and leaves no event behind (not even the system prompt of an empty log).
- `F06.send-run`: `POST .../events` with `run: true` drives a real agent turn to `finished`, persisting the kinds of a normal run (user message, `execution_status` running and finished, `ActionEvent`/`ObservationEvent` pairs, `stats`).
- `F06.ws-live`: a socket opened before the run receives each event live, every live frame is persisted, and a closing `full_state` snapshot follows the run.
- `F06.search-timestamp-order`: after a tool-using run, `sort_order=TIMESTAMP` lists events in timestamp order, so a `timestamp__gte` or `resend_mode=since` cut at a listed event keeps every event listed after it.
- `F06.send-resets-finished`: a message appended to a `finished` conversation sets it back to `idle` with a persisted `execution_status` update.
- `F06.ws-snapshot`: the first frame after authentication is a transient `full_state` `ConversationStateUpdateEvent` that never appears in search; without `resend_mode` nothing else is replayed.
- `F06.ws-handshake`: the socket accepts first-frame, deprecated query and deprecated header keys; a wrong first-frame key, a first frame that is not an auth frame, or no auth frame within 10 s closes with 4001, a wrong deprecated query or header key is refused at the upgrade (HTTP 403), and an unknown conversation closes with 4004.
- `F06.ws-delete-close`: deleting a conversation closes the sockets that follow it.
- `F06.ws-replay-all`: `resend_mode=all` (and the deprecated `resend_all=true`) replays the persisted log in search order after the snapshot.
- `F06.ws-replay-since`: `resend_mode=since` with `after_timestamp` replays exactly the events at or after that timestamp; without `after_timestamp` it replays nothing.
- `F06.ws-bad-frame`: an invalid inbound frame returns a `ServerErrorEvent` (`ValidationError`, `AssertionError`, `JSONDecodeError` for text that is not JSON), appends nothing and leaves the socket open; a redundant auth frame is ignored.
- `F06.ws-send`: a user message frame sent on the socket is persisted and runs the agent to `finished`, streaming the run on the same socket.
- `F06.restart-persist`: event ids, order and count survive a server restart, the socket replays the same log, and new messages append after it.
- `F06.send-run-limit`: with every run slot taken, `POST .../events` with `run: true` returns 429 `Message saved...` and the message is persisted; a socket send gets a `ConversationRunLimitExceeded` `ServerErrorEvent`.
- `F06.send-while-running`: `POST .../events` with `run: true` on a conversation that is already running returns 200 (not the 409 of `POST /run`), persists the message, keeps the conversation `running` and starts no second run.

## How to get to it (agent POV)

- REST: `POST /api/conversations/{conversation_id}/events` with a
  `SendMessageRequest` body `{"role": "user", "content": [{"type": "text",
  "text": "..."}], "run": false}` returns `{"success": true}`; `run` defaults
  to `false`, and a content item is either `{"type": "text", "text": ...}`
  or `{"type": "image", "image_urls": [...]}` (http or `data:` URLs). The Python SDK `RemoteConversation.send_message()` and the
  TypeScript `RemoteConversation.sendMessage()` always send `run: false` and
  then call `POST /run` separately; `ConversationClient.sendEvent()` posts the
  body as given. Recipes drive the route directly (`api POST`), so the body
  and the status are exactly what a client sends and sees; the CLI's
  `conversation send` wraps the same route for other families.
- REST: `GET .../events/search` (query `page_id`, `limit` 1 to 100,
  `kind`, `source`, `body`, `sort_order` `TIMESTAMP|TIMESTAMP_DESC`,
  `timestamp__gte`, `timestamp__lt`) and `GET .../events/count` (same filters
  without paging). The SDK's `RemoteEventsList` syncs history with search
  (`limit=100`, no filters); the TypeScript client has
  `ConversationClient.searchEvents()`, `getEventCount()` and
  `RemoteEventsList.search()`/`count()`. `conversation events` in the CLI
  pages through search.
- REST: `GET .../events/{event_id}` (TypeScript `ConversationClient.getEvent()`,
  `RemoteEventsList.getEventById()`) and `GET .../events` with a JSON array body
  (batch get; no SDK or TypeScript caller, because browsers cannot send a GET
  body: the TypeScript `getEvents()` fans out to get-one and maps 404 to
  `null`).
- WebSocket: `/sockets/events/{conversation_id}` with query `resend_mode`
  (`all` or `since`), `after_timestamp` (ISO 8601, required for `since`) and
  the deprecated `resend_all=true` and `session_api_key`. The SDK's
  `WebSocketCallbackClient` (inside `RemoteConversation`) and the TypeScript
  `ConversationEventStream` authenticate with the first frame
  `{"type": "auth", "session_api_key": "..."}` and treat the first
  `ConversationStateUpdateEvent` as "ready"; the SDK stops reconnecting on 4001
  and 4004 and reconnects with backoff after any other close. Recipes use
  `ws listen` (one connection, optional `--send` frames) and `ws start`/
  `ws stop` (a background capture around a REST action).
- Config: `max_concurrent_runs` (`OH_MAX_CONCURRENT_RUNS`) bounds runs and so
  the 429 path; the server's local time zone (`TZ`) is the zone of every event
  timestamp and of naive filter values.

## Driving it with control-agent-server

Preconditions:

- A run launched with this family's `Launch:` flags (`--env TZ=UTC`, so
  naive event timestamps are UTC) is live and exported, and `doctor` is ok.
- `$DEEPSEEK_API_KEY` is set; the preset makes `deepseek-flash` the active
  profile, which `conversation start` copies into each conversation.
- `CID` is a conversation with no autotitle and `--tools none` (only the
  built-in `finish` and `think` tools), created without a message, so its
  log is empty and fully deterministic until the `F06.ws-send` bullet runs
  the agent on it.

  ```sh
  control-agent-server llm preset deepseek
  CID=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  QA_UNKNOWN=00000000-0000-4000-8000-0000000000f6
  ```

- **Append a message without running (`F06.send-no-run`).** The log starts
  empty; the first message also creates the system prompt event.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/events/search" --expect 200 \
    --check items len-eq 0 --check next_page_id missing
  control-agent-server api POST "/api/conversations/$CID/events" \
    --json '{"role":"user","content":[{"type":"text","text":"QA_F06_FIRST hello"}],"run":false}' \
    --expect 200 --check success eq true --save F06.send-no-run/post
  control-agent-server api GET "/api/conversations/$CID/events/search" --expect 200 \
    --check items len-eq 3 --check items.0.kind eq SystemPromptEvent --check items.0.source eq agent \
    --check items.1.kind eq MessageEvent --check items.1.source eq user \
    --check items.1.llm_message.content.0.text eq 'QA_F06_FIRST hello' \
    --check items.2.kind eq ConversationStateUpdateEvent --check items.2.key eq last_user_message_id \
    --save F06.send-no-run/search
  control-agent-server api GET "/api/conversations/$CID" --check execution_status eq idle
  control-agent-server api POST "/api/conversations/$CID/events" \
    --json '{"content":[{"type":"text","text":"QA_F06_SECOND again"}]}' --expect 200
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . eq 5
  ```
  The log holds `SystemPromptEvent`, the user `MessageEvent` and a
  `last_user_message_id` state update; a second message (role and `run` left
  to their defaults) adds a `MessageEvent` and another update, and the
  conversation stays `idle`.
- **Paging and sort order (`F06.search-page`).** Five events in pages of two.
  ```sh
  E0=$(control-agent-server api GET "/api/conversations/$CID/events/search" --field items.0.id)
  LAST=$(control-agent-server api GET "/api/conversations/$CID/events/search" --check items len-eq 5 --field items.4.id)
  P2=$(control-agent-server api GET "/api/conversations/$CID/events/search" --query limit=2 \
    --check items len-eq 2 --check items.0.id eq "$E0" --field next_page_id)
  P3=$(control-agent-server api GET "/api/conversations/$CID/events/search" --query limit=2 --query page_id="$P2" \
    --check items len-eq 2 --check items.0.id eq "$P2" --field next_page_id)
  control-agent-server api GET "/api/conversations/$CID/events/search" --query limit=2 --query page_id="$P3" \
    --check items len-eq 1 --check items.0.id eq "$LAST" --check next_page_id missing --save F06.search-page/last-page
  control-agent-server api GET "/api/conversations/$CID/events/search" --query sort_order=TIMESTAMP_DESC --query limit=1 \
    --check items.0.id eq "$LAST" --check next_page_id exists --save F06.search-page/desc
  control-agent-server api GET "/api/conversations/$CID/events/search" --query limit=2 --query page_id=qa-unknown-page \
    --expect 200 --check items.0.id eq "$E0"
  ASC_REVERSED=$(control-agent-server api GET "/api/conversations/$CID/events/search" --field items | jq -c '[.[].id] | reverse')
  DESC_IDS=$(control-agent-server api GET "/api/conversations/$CID/events/search" --query sort_order=TIMESTAMP_DESC \
    --field items | jq -c '[.[].id]')
  test "$ASC_REVERSED" = "$DESC_IDS"
  ```
  `next_page_id` names the first event of the next page (the cursor is
  inclusive), the third page has the last event and no `next_page_id`,
  descending order is exactly the ascending log reversed (starting at the
  newest event), and an unknown cursor silently starts again at the first
  event.
- **Bad search parameters (`F06.search-validation`).**
  ```sh
  control-agent-server api GET "/api/conversations/$CID/events/search" --query limit=0 --expect 422 --check detail.0.loc contains limit
  control-agent-server api GET "/api/conversations/$CID/events/search" --query limit=101 --expect 422 --save F06.search-validation/limit-101
  control-agent-server api GET "/api/conversations/$CID/events/search" --query limit=100 --expect 200
  control-agent-server api GET "/api/conversations/$CID/events/search" --query sort_order=qa-bogus --expect 422 --check detail.0.loc contains sort_order
  control-agent-server api GET "/api/conversations/$CID/events/search" --query timestamp__gte=not-a-date --expect 422 --check detail.0.loc contains timestamp__gte
  ```
  Each bad value is a 422 whose `detail[0].loc` names the parameter.
- **Filters (`F06.search-filters`).**
  ```sh
  control-agent-server api GET "/api/conversations/$CID/events/search" \
    --query kind=openhands.sdk.event.llm_convertible.message.MessageEvent \
    --check items len-eq 2 --check items.0.kind eq MessageEvent --check items.1.kind eq MessageEvent --save F06.search-filters/kind-fqn
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=user \
    --check items len-eq 2 --check items.0.source eq user --check items.1.source eq user
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=environment \
    --check items len-eq 2 --check items.0.kind eq ConversationStateUpdateEvent
  control-agent-server api GET "/api/conversations/$CID/events/search" --query source=qa-nonsense --expect 200 --check items len-eq 0
  control-agent-server api GET "/api/conversations/$CID/events/search" --query body=qa_f06_second \
    --check items len-eq 1 --check items.0.llm_message.content.0.text eq 'QA_F06_SECOND again' --save F06.search-filters/body
  control-agent-server api GET "/api/conversations/$CID/events/search" --query body=QA_F06 --query source=user \
    --query sort_order=TIMESTAMP_DESC --check items len-eq 2 --check items.0.llm_message.content.0.text eq 'QA_F06_SECOND again'
  ```
  `kind` needs the fully-qualified class path, `source` is an exact match
  (an unknown value is just empty), `body` is a case-insensitive substring of
  message text, and filters combine with sort order.
- **Short kind names (`F06.search-kind-short`), known bug.** The query
  parameter's documentation and the TypeScript client say
  `kind=MessageEvent`, the value every event carries in its `kind` field.
  The fully-qualified filter first shows the two messages and that their
  `kind` field is exactly that short name.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/events/search" \
    --query kind=openhands.sdk.event.llm_convertible.message.MessageEvent \
    --check items len-eq 2 --check items.0.kind eq MessageEvent --check items.1.kind eq MessageEvent
  control-agent-server api GET "/api/conversations/$CID/events/search" --query kind=MessageEvent --expect 200 \
    --check items len-eq 2 --save F06.search-kind-short/short-name  # bug
  control-agent-server api GET "/api/conversations/$CID/events/count" --query kind=MessageEvent --check . eq 2  # bug
  ```
  Correct behavior: the two messages. Today both return nothing (`items: []`
  and `0`, with status 200): the service compares `kind` with
  `<module>.<ClassName>` (`event_service.py`, `_event_matches_filters`), so a
  client that follows the docs silently gets an empty history.
- **Time window (`F06.search-time-window`).** `T1` is the first message's
  timestamp, a naive server-local (here UTC) ISO string.
  ```sh
  T1=$(control-agent-server api GET "/api/conversations/$CID/events/search" --field items.1.timestamp)
  E1=$(control-agent-server api GET "/api/conversations/$CID/events/search" --field items.1.id)
  control-agent-server api GET "/api/conversations/$CID/events/search" --query timestamp__gte="$T1" \
    --check items len-eq 4 --check items.0.id eq "$E1" --save F06.search-time-window/gte
  control-agent-server api GET "/api/conversations/$CID/events/search" --query timestamp__lt="$T1" \
    --check items len-eq 1 --check items.0.kind eq SystemPromptEvent
  T1_PLUS2=$(python3 -c 'import datetime as d, sys; t = d.datetime.fromisoformat(sys.argv[1]).replace(tzinfo=d.timezone.utc); print(t.astimezone(d.timezone(d.timedelta(hours=2))).isoformat())' "$T1")
  control-agent-server api GET "/api/conversations/$CID/events/search" --query timestamp__gte="$T1_PLUS2" \
    --check items len-eq 4 --check items.0.id eq "$E1" --save F06.search-time-window/gte-offset
  control-agent-server api GET "/api/conversations/$CID/events/count" --query timestamp__gte="$T1" --query timestamp__lt="$T1_PLUS2" --check . eq 0
  ```
  `>=` keeps the first message itself, `<` keeps only the system prompt, the
  same instant written as `+02:00` selects the same events, and an empty
  window counts 0.
- **Count (`F06.count`).** Same filters as search, a bare JSON integer.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/events/count" --expect 200 --check . eq 5 --save F06.count/all
  control-agent-server api GET "/api/conversations/$CID/events/search" --check items len-eq 5
  control-agent-server api GET "/api/conversations/$CID/events/count" --query source=user --check . eq 2
  control-agent-server api GET "/api/conversations/$CID/events/count" \
    --query kind=openhands.sdk.event.conversation_state.ConversationStateUpdateEvent --check . eq 2
  control-agent-server api GET "/api/conversations/$CID/events/count" --query body=qa_f06_first --check . eq 1
  control-agent-server api GET "/api/conversations/$CID/events/count" --query timestamp__lt=not-a-date --expect 422
  ```
  Every count equals the matching search length.
- **Get one event (`F06.get-one`).** The second message by id, then the same
  event on disk.
  ```sh
  E2=$(control-agent-server api GET "/api/conversations/$CID/events/search" --query body=QA_F06_SECOND --field items.0.id)
  control-agent-server api GET "/api/conversations/$CID/events/$E2" --expect 200 --check id eq "$E2" \
    --check kind eq MessageEvent --check source eq user --check llm_message.content.0.text eq 'QA_F06_SECOND again' \
    --save F06.get-one/get
  control-agent-server state cat "server/workspace/conversations/${CID//-/}/events/event-00003-$E2.json" \
    --check id eq "$E2" --check kind eq MessageEvent
  ```
  The body matches the search item; unlike search it is serialized without
  dropping `null` fields. On disk it is `event-00003-<id>.json`: the file
  index is the event's position in the log.
- **Unknown event id (`F06.unknown-event`), known bug.** The route documents
  404 and batch-get documents "null for any missing item"; the TypeScript
  `getEvents()` relies on that 404. The known id `E2` is found by both
  routes first.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/events/$E2" --expect 200 --check id eq "$E2"
  control-agent-server api GET "/api/conversations/$CID/events" --json "[\"$E2\"]" --expect 200 \
    --check . len-eq 1 --check 0.id eq "$E2"
  control-agent-server api GET "/api/conversations/$CID/events/00000000-0000-4000-8000-0000000000e6" --expect 404 \
    --save F06.unknown-event/get-one  # bug
  control-agent-server api GET "/api/conversations/$CID/events" --json "[\"$E2\", \"qa-missing-event\"]" --expect 200 \
    --check . len-eq 2 --check 0.id eq "$E2" --check 1 missing --save F06.unknown-event/batch  # bug
  ```
  Correct behavior: 404, and `[<event>, null]`. Today both are 500
  `{"detail": "Internal Server Error", "exception": "'Unknown event_id: ...'"}`:
  `EventLog.get_index` raises `KeyError` before the router's `None` check
  (`event_service.py` `_get_event_sync`), and one missing id fails the whole
  batch.
- **Batch get (`F06.batch-get`).** A GET with a JSON array body.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/events" --json "[\"$E2\", \"$E1\"]" --expect 200 \
    --check . len-eq 2 --check 0.id eq "$E2" --check 1.id eq "$E1" --save F06.batch-get/two
  control-agent-server api GET "/api/conversations/$CID/events" --expect 422 --check detail.0.loc contains body
  ```
  Events come back in request order, not log order; without a body the
  route answers 422 `body: Field required`.
- **Unknown conversation, bad id, no key (`F06.not-found-auth`).**
  ```sh
  control-agent-server api GET "/api/conversations/$QA_UNKNOWN/events/search" --expect 404 \
    --check detail contains 'Conversation not found' --save F06.not-found-auth/search-404
  control-agent-server api GET "/api/conversations/$QA_UNKNOWN/events/count" --expect 404
  control-agent-server api GET "/api/conversations/$QA_UNKNOWN/events/$E2" --expect 404
  control-agent-server api GET "/api/conversations/$QA_UNKNOWN/events" --json "[\"$E2\"]" --expect 404
  control-agent-server api POST "/api/conversations/$QA_UNKNOWN/events" --json '{"content":[{"type":"text","text":"QA_F06_X"}]}' --expect 404
  control-agent-server api GET /api/conversations/qa-not-a-uuid/events/search --expect 422 --check detail.0.loc contains conversation_id
  control-agent-server api POST /api/conversations/qa-not-a-uuid/events --json '{"content":[{"type":"text","text":"QA_F06_X"}]}' \
    --expect 422 --check detail.0.loc contains conversation_id
  control-agent-server api GET "/api/conversations/$CID/events/search" --auth none --expect 401 --save F06.not-found-auth/no-key
  control-agent-server api GET "/api/conversations/$CID/events/count" --auth bad --expect 401
  control-agent-server api GET "/api/conversations/$CID/events/$E2" --auth none --expect 401
  control-agent-server api GET "/api/conversations/$CID/events" --json "[\"$E2\"]" --auth bad --expect 401
  control-agent-server api POST "/api/conversations/$CID/events" --auth none \
    --json '{"content":[{"type":"text","text":"QA_F06_NOAUTH"}]}' --expect 401
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . eq 5
  ```
  404 `Conversation not found: <id>` on all five routes, 422 for a non-UUID
  id on the read and the write router, 401 without or with a wrong key on
  all five, and the unauthenticated POST stored nothing.
- **Message validation (`F06.send-validation`).**
  ```sh
  control-agent-server api POST "/api/conversations/$CID/events" \
    --json '{"role":"qa-invalid","content":[{"type":"text","text":"QA_F06_BADROLE"}]}' --expect 422 \
    --check detail.0.loc contains role --save F06.send-validation/role
  control-agent-server api POST "/api/conversations/$CID/events" --json '{"content":"QA_F06_PLAIN"}' --expect 422 \
    --check detail.0.loc contains content
  control-agent-server api POST "/api/conversations/$CID/events" \
    --json '{"content":[{"type":"qa-unknown","text":"QA_F06_BADTYPE"}]}' --expect 422
  control-agent-server api POST "/api/conversations/$CID/events" --expect 422 --check detail.0.loc contains body
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . eq 5
  ```
  `content` must be a list of text or image items and the body is
  required; nothing was appended.
- **Image content (`F06.send-image`).** A one-pixel PNG as a `data:` URL,
  on its own conversation `QA_IMG` (never run, so no vision model is
  needed and `CID`'s counts stay as they are).
  ```sh
  QA_IMG=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  QA_PNG='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
  control-agent-server api POST "/api/conversations/$QA_IMG/events" \
    --json "{\"content\":[{\"type\":\"text\",\"text\":\"QA_F06_IMAGE\"},{\"type\":\"image\",\"image_urls\":[\"$QA_PNG\"]}]}" \
    --expect 200 --check success eq true --save F06.send-image/post
  IMG_ID=$(control-agent-server api GET "/api/conversations/$QA_IMG/events/search" --query source=user \
    --check items len-eq 1 --check items.0.llm_message.content.0.text eq QA_F06_IMAGE \
    --check items.0.llm_message.content.1.type eq image --check items.0.llm_message.content.1.image_urls len-eq 1 \
    --check items.0.llm_message.content.1.image_urls.0 eq "$QA_PNG" --save F06.send-image/search --field items.0.id)
  control-agent-server api GET "/api/conversations/$QA_IMG/events/$IMG_ID" --expect 200 \
    --check llm_message.content.1.type eq image --check llm_message.content.1.image_urls.0 eq "$QA_PNG"
  IMG_WS=$(control-agent-server ws listen "/sockets/events/$QA_IMG" --query resend_mode=all --duration 3 \
    --save F06.send-image/replay | jq -r .saved)
  jq -e --arg id "$IMG_ID" --arg url "$QA_PNG" '[.frames[] | select(.direction == "received") | .frame | select(.id == $id) | .llm_message.content[1].image_urls] == [[$url]]' "$IMG_WS"
  control-agent-server api POST "/api/conversations/$QA_IMG/events" --json '{"content":[{"type":"image"}]}' --expect 422 \
    --check detail.2.loc contains ImageContent --check detail.2.loc contains image_urls --check detail.2.type eq missing \
    --save F06.send-image/no-urls
  control-agent-server api GET "/api/conversations/$QA_IMG/events/count" --query source=user --check . eq 1
  ```
  The image part keeps `type: image` and the exact `data:` URL in search,
  get-one and the replayed `MessageEvent` frame (no truncation or masking
  of the base64 payload). Without `image_urls` the body matches neither
  content type: the 422 lists the `TextContent` errors first, then
  `ImageContent.image_urls: Field required`, and no second user message
  was stored.
- **Non-user role (`F06.send-non-user-role`), known bug.** `role` accepts
  `user`, `system`, `assistant` and `tool` in the schema, but a conversation
  only takes user messages. `QA_ROLE` is a second conversation whose log is
  still empty (count 0); `CID` already took the same body with role `user`
  in `F06.send-no-run`.
  ```sh
  QA_ROLE=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api GET "/api/conversations/$QA_ROLE/events/count" --check . eq 0
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . eq 5
  control-agent-server api POST "/api/conversations/$CID/events" \
    --json '{"role":"assistant","content":[{"type":"text","text":"QA_F06_ASSISTANT"}]}' --expect 4xx \
    --save F06.send-non-user-role/assistant  # bug
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . eq 5
  control-agent-server api POST "/api/conversations/$QA_ROLE/events" \
    --json '{"role":"assistant","content":[{"type":"text","text":"QA_F06_ASSISTANT"}]}' --expect 4xx \
    --save F06.send-non-user-role/assistant-empty-log  # bug
  control-agent-server api GET "/api/conversations/$QA_ROLE/events/count" --check . eq 0 \
    --save F06.send-non-user-role/empty-log-count  # bug
  ```
  Correct behavior: a 4xx client error and nothing appended. Today it is 500
  `{"exception": "Only user messages are allowed to be sent to the agent."}`:
  `LocalConversation.send_message` asserts `role == "user"` and the
  `AssertionError` reaches the catch-all handler. On `CID` nothing is
  appended, but the assert runs after the agent is initialized, so on a
  conversation with an empty log the refused message still writes the
  `SystemPromptEvent` (count 1, not 0); while the 500 lasts the replay stops
  at the first marked POST, so a fix that only changes the status still
  fails at the last marked count.
- **First frame is a state snapshot (`F06.ws-snapshot`).**
  ```sh
  control-agent-server ws listen "/sockets/events/$CID" --count 1 --until-kind ConversationStateUpdateEvent \
    --until key=full_state --until value.execution_status=idle --duration 10 --save F06.ws-snapshot/first-frame
  control-agent-server ws listen "/sockets/events/$CID" --duration 3 --save F06.ws-snapshot/no-replay \
    | jq -e '.frames == 1 and .kinds.ConversationStateUpdateEvent == 1'
  control-agent-server api GET "/api/conversations/$CID/events/search" --check items not-contains full_state
  ```
  The first and only frame is `{"kind": "ConversationStateUpdateEvent",
  "key": "full_state", "value": {...whole state, execution_status idle...}}`
  with a fresh id; it is not persisted, and without `resend_mode` no history
  is replayed.
- **Handshake: auth and close codes (`F06.ws-handshake`).** The 10-second
  first-frame timeout is the server's own timer, so one capture waits for it.
  ```sh
  control-agent-server ws listen "/sockets/events/$CID" --auth bad --expect-close 4001 --duration 10 --save F06.ws-handshake/bad-key
  control-agent-server ws listen "/sockets/events/$CID" --auth none --send '{"type":"qa-hello","session_api_key":"qa"}' \
    --expect-close 4001 --duration 10 --save F06.ws-handshake/not-auth-frame
  T0=$SECONDS
  control-agent-server ws listen "/sockets/events/$CID" --auth none --expect-close 4001 --duration 15 --save F06.ws-handshake/no-frame
  test $((SECONDS - T0)) -ge 9
  test $((SECONDS - T0)) -le 14
  control-agent-server ws listen "/sockets/events/$CID" --auth query --until-kind ConversationStateUpdateEvent --until key=full_state --duration 10
  control-agent-server ws listen "/sockets/events/$CID" --auth header --until-kind ConversationStateUpdateEvent --until key=full_state --duration 10
  control-agent-server ws listen "/sockets/events/$CID" --auth bad-query --expect-reject 403 --duration 5 \
    --save F06.ws-handshake/bad-query-key
  control-agent-server ws listen "/sockets/events/$CID" --auth bad-header --expect-reject 403 --duration 5 \
    --save F06.ws-handshake/bad-header-key
  control-agent-server ws listen "/sockets/events/$QA_UNKNOWN" --expect-close 4004 --duration 10 --save F06.ws-handshake/unknown
  ```
  A wrong first-frame key, a first frame of another `type`, and silence all
  close with 4001 `Authentication failed`; silence closes after the server's
  10-second timer (the capture takes 10 to 11 s). The deprecated query and
  header keys are accepted (the server logs a deprecation warning), and a
  wrong query or header key is refused before the upgrade with HTTP 403, so
  that client sees no close code. A valid key on an unknown conversation
  authenticates, then closes with 4004 `Conversation not found`.
- **Deleting a conversation closes its sockets (`F06.ws-delete-close`), known bug.**
  A background capture is subscribed (its `full_state` snapshot arrived and
  it is open) before the DELETE; the DELETE, a 404 on REST and a 4004 for a
  new socket all succeed before the open capture is judged.
  ```sh
  QA_DEL=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server ws start "/sockets/events/$QA_DEL" --name f06-del --duration 60
  control-agent-server ws read f06-del --kinds ConversationStateUpdateEvent --contains '"key": "full_state"' \
    --expect-open --wait 10
  control-agent-server api DELETE "/api/conversations/$QA_DEL" --expect 200 --save F06.ws-delete-close/delete
  control-agent-server api GET "/api/conversations/$QA_DEL" --expect 404
  control-agent-server ws listen "/sockets/events/$QA_DEL" --expect-close 4004 --duration 5
  if control-agent-server ws stop f06-del --expect-open --save F06.ws-delete-close/socket; then false; fi  # bug
  ```
  Correct behavior: the server closes the open socket (with any close code)
  while it deletes the conversation; `EventService.close()` awaits
  `PubSub.close()` before the DELETE answers, so the close has arrived by the
  time the capture is stopped. Today the capture is still open with no close
  frame (`close: null`), although REST answers 404 and a new socket gets
  4004: `PubSub.close()` calls `Subscriber.close()`, which
  `_WebSocketSubscriber` does not implement (`pub_sub.py`, `sockets.py`), so
  an SDK or TypeScript client keeps waiting on a conversation that no longer
  exists.
- **Replay everything (`F06.ws-replay-all`).**
  ```sh
  IDS=$(control-agent-server api GET "/api/conversations/$CID/events/search" --field items | jq -c '[.[].id]')
  ALL=$(control-agent-server ws listen "/sockets/events/$CID" --query resend_mode=all --duration 3 \
    --save F06.ws-replay-all/all | jq -r .saved)
  jq -e --argjson ids "$IDS" '[.frames[] | select(.direction == "received") | .frame] | (.[0].key == "full_state") and ([.[1:][].id] == $ids)' "$ALL"
  LEGACY=$(control-agent-server ws listen "/sockets/events/$CID" --query resend_all=true --duration 3 \
    --save F06.ws-replay-all/resend-all-legacy | jq -r .saved)
  jq -e --argjson ids "$IDS" '[.frames[] | select(.direction == "received") | .frame] | [.[1:][].id] == $ids' "$LEGACY"
  ```
  After the snapshot, the replay is exactly the search ids in the same order,
  for both spellings.
- **Replay since a timestamp (`F06.ws-replay-since`).** `T2` is the second
  message's timestamp; the replay is inclusive.
  ```sh
  T2=$(control-agent-server api GET "/api/conversations/$CID/events/search" --query body=QA_F06_SECOND --field items.0.timestamp)
  SINCE_IDS=$(control-agent-server api GET "/api/conversations/$CID/events/search" --query timestamp__gte="$T2" \
    --check items len-eq 2 --field items | jq -c '[.[].id]')
  SINCE=$(control-agent-server ws listen "/sockets/events/$CID" --query resend_mode=since --query after_timestamp="$T2" \
    --duration 3 --save F06.ws-replay-since/since | jq -r .saved)
  jq -e --argjson ids "$SINCE_IDS" '[.frames[] | select(.direction == "received") | .frame] | (.[0].key == "full_state") and ([.[1:][].id] == $ids)' "$SINCE"
  control-agent-server ws listen "/sockets/events/$CID" --query resend_mode=since --duration 3 \
    --save F06.ws-replay-since/no-timestamp | jq -e '.frames == 1'
  ```
  The replay is the second message and its state update, the same ids as
  search with `timestamp__gte`; `since` without `after_timestamp` replays
  nothing (the server only logs a warning).
- **Bad frames on the socket (`F06.ws-bad-frame`).** A JSON object that is
  not a `Message`, a non-user message, a redundant auth frame, then text
  that is not JSON (sent last, so its reply proves the auth frame before it
  was read and skipped).
  ```sh
  BAD=$(control-agent-server ws listen "/sockets/events/$CID" \
    --send '{"kind":"MessageEvent","text":"QA_F06_BAD"}' \
    --send '{"role":"assistant","content":[{"type":"text","text":"QA_F06_BAD"}]}' \
    --send '{"type":"auth","session_api_key":"qa-ignored"}' \
    --send 'qa-f06-not-json' \
    --duration 4 --expect-open --save F06.ws-bad-frame/frames | jq -r .saved)
  jq -e '[.frames[] | select(.direction == "received") | .frame | select(.kind == "ServerErrorEvent") | .code] == ["ValidationError", "AssertionError", "JSONDecodeError"]' "$BAD"
  control-agent-server api GET "/api/conversations/$CID/events/count" --query body=QA_F06_BAD --check . eq 0
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . eq 5
  ```
  Three `ServerErrorEvent` frames (`code` `ValidationError`, then
  `AssertionError` with `Only user messages are allowed...`, then
  `JSONDecodeError`), none for the auth control message, the socket stays
  open and nothing was appended.
- **Send a message over the socket (`F06.ws-send`).** Every socket message
  runs the agent; this spends one tiny DeepSeek turn.
  ```sh
  WS_RUN=$(control-agent-server ws listen "/sockets/events/$CID" \
    --send '{"role":"user","content":[{"type":"text","text":"QA_F06_WS: reply with the single word ok"}]}' \
    --expect-kind MessageEvent \
    --until-kind ConversationStateUpdateEvent --until value=finished --duration 180 --save F06.ws-send/run | jq -r .saved)
  jq -e '[.frames[] | select(.direction == "received") | .frame] | (map(select(.kind == "MessageEvent" and .source == "user") | .llm_message.content[0].text) | any(startswith("QA_F06_WS"))) and (map(select(.key == "execution_status") | .value) | index("running") != null)' "$WS_RUN"
  jq -e '[.frames[] | select(.direction == "received") | .frame | select(.source == "agent" and (.kind == "MessageEvent" or .kind == "ActionEvent"))] | length >= 1' "$WS_RUN"
  WS_T=$(control-agent-server api GET "/api/conversations/$CID/events/search" --query source=user --query body=QA_F06_WS \
    --check items len-eq 1 --save F06.ws-send/persisted --field items.0.timestamp)
  control-agent-server api GET "/api/conversations/$CID" --check execution_status eq finished
  control-agent-server api GET "/api/conversations/$CID/events/count" --query source=agent --query timestamp__gte="$WS_T" \
    --check . ge 1
  ```
  The same socket streams the user `MessageEvent`, `execution_status`
  `running`, the agent's turn (at least one agent `MessageEvent` or
  `ActionEvent` frame) and `finished`; REST shows the persisted
  message and at least one agent event after it (a `MessageEvent` reply or
  a `finish` `ActionEvent`: the agent has both, so which one is the model's
  choice).
- **Run a turn and watch it live (`F06.send-run`, `F06.ws-live`).** The
  capture starts before the message is sent; the agent only has the file
  editor (no tmux needed).
  ```sh
  LIVE=$(control-agent-server conversation start --no-autotitle --tools file_editor --print-id)
  control-agent-server ws start "/sockets/events/$LIVE" --name f06-live
  control-agent-server api POST "/api/conversations/$LIVE/events" \
    --json '{"content":[{"type":"text","text":"Create qa.txt containing exactly: QA_F06_LIVE"}],"run":true}' \
    --expect 200 --check success eq true --save F06.send-run/post
  control-agent-server conversation wait "$LIVE" --until finished --timeout 240
  control-agent-server ws stop f06-live --wait 30 \
    --kinds ConversationStateUpdateEvent --contains '"key": "full_state"' --expect-min 2 --save F06.ws-live/frames
  control-agent-server ws read f06-live --kinds ActionEvent --expect-min 1
  LIVE_IDS=$(control-agent-server api GET "/api/conversations/$LIVE/events/search" --field items | jq -c '[.[].id]')
  control-agent-server ws read f06-live --kinds MessageEvent --contains QA_F06_LIVE
  control-agent-server ws read f06-live --kinds ConversationStateUpdateEvent --contains '"value": "running"'
  control-agent-server ws read f06-live --kinds ObservationEvent --contains '"tool_name": "file_editor"'
  control-agent-server ws read f06-live --kinds ConversationStateUpdateEvent --contains '"value": "finished"'
  LIVE_FRAMES=$(control-agent-server ws read f06-live --save F06.ws-live/all-frames | jq -r .saved)
  jq -e --argjson ids "$LIVE_IDS" '[.frames[] | select(.direction == "received") | .frame | select(.key != "full_state") | .id] | (length > 0) and (. - $ids == [])' "$LIVE_FRAMES"
  control-agent-server conversation events "$LIVE" --kinds ObservationEvent --contains file_editor --save F06.send-run/observations
  control-agent-server api GET "/api/conversations/$LIVE/events/count" \
    --query kind=openhands.sdk.event.llm_convertible.action.ActionEvent --check . ge 1
  control-agent-server api GET "/api/conversations/$LIVE/events/count" \
    --query kind=openhands.sdk.event.llm_convertible.observation.ObservationEvent --check . ge 1
  control-agent-server conversation events "$LIVE" --key stats --expect-min 1
  LIVE_WS=$(control-agent-server api GET "/api/conversations/$LIVE" --field workspace.working_dir)
  grep -q QA_F06_LIVE "$LIVE_WS/qa.txt"
  ```
  POST returns 200 at once and the run reaches `finished`. The capture is
  stopped only once the second `full_state` snapshot has arrived (the run
  task publishes it after the REST status already says `finished`, so a
  stop right after the wait races it). The socket shows the user message,
  `execution_status` `running`, `ActionEvent`s with their `file_editor`
  `ObservationEvent`s, `finished` and that closing snapshot; every live
  frame id is in the persisted log (read after the capture stops, because
  an event is persisted before it is published), which also has `stats`
  updates, and the agent wrote `qa.txt`.
  A typical log:
  `SystemPromptEvent`, `MessageEvent` (user), state updates
  `last_user_message_id` and `execution_status=running`, then per step
  `ActionEvent`, `ObservationEvent` and `stats`, ending with the `finish`
  action and `execution_status=finished`.
- **Log order is timestamp order (`F06.search-timestamp-order`), known bug.**
  Search walks the log in append order and its docstring relies on that
  being timestamp order (`_search_events_sync`); `after_timestamp` is
  documented for "REST fetches history, the socket continues from that
  point". `LIVE` from the previous bullet has at least one tool step, so
  its first `ObservationEvent` has later events after it.
  ```sh
  OBS_ID=$(control-agent-server api GET "/api/conversations/$LIVE/events/search" \
    --query kind=openhands.sdk.event.llm_convertible.observation.ObservationEvent --check items len-ge 1 --field items.0.id)
  OBS_T=$(control-agent-server api GET "/api/conversations/$LIVE/events/$OBS_ID" --check id eq "$OBS_ID" --field timestamp)
  FROM_OBS=$(control-agent-server api GET "/api/conversations/$LIVE/events/search" --field items \
    | jq -e --arg id "$OBS_ID" '[.[].id] | length - index($id)')
  test "$FROM_OBS" -ge 2
  control-agent-server api GET "/api/conversations/$LIVE/events/count" --query timestamp__gte="$OBS_T" \
    --check . ge "$FROM_OBS" --save F06.search-timestamp-order/count-since-observation  # bug
  control-agent-server ws listen "/sockets/events/$LIVE" --query resend_mode=since --query after_timestamp="$OBS_T" \
    --duration 3 --save F06.search-timestamp-order/replay-since-observation | jq -e --argjson n "$FROM_OBS" '.frames - 1 >= $n'  # bug
  control-agent-server api GET "/api/conversations/$LIVE/events/search" --field items | jq -e '[.[].timestamp] | . == sort'  # bug
  ```
  Correct behavior: a cut at the first `ObservationEvent` keeps it and
  everything listed after it (`FROM_OBS` events), over REST and over the
  socket. Today the count is one short (8 of 9 on a two-edit run): the
  `stats` update of each model call is created, and timestamped, when the
  model answers, but is persisted from an executor that waits for the state
  lock, so it lands after that step's action and observation (and after
  `execution_status=finished` for the last call) with an older timestamp
  (`_setup_stats_streaming`, `_emit_event_from_thread` in
  `event_service.py`). A client that loads history over REST and then
  reconnects with `resend_mode=since` silently misses that update.
- **A message reopens a finished conversation (`F06.send-resets-finished`).**
  ```sh
  control-agent-server api GET "/api/conversations/$LIVE" --check execution_status eq finished
  control-agent-server api POST "/api/conversations/$LIVE/events" \
    --json '{"content":[{"type":"text","text":"QA_F06_FOLLOWUP"}],"run":false}' --expect 200
  control-agent-server api GET "/api/conversations/$LIVE" --check execution_status eq idle --save F06.send-resets-finished/after
  control-agent-server api GET "/api/conversations/$LIVE/events/search" --query sort_order=TIMESTAMP_DESC --query limit=3 \
    --check items.2.key eq execution_status --check items.2.value eq idle \
    --check items.1.kind eq MessageEvent --check items.0.key eq last_user_message_id --save F06.send-resets-finished/tail
  ```
  The status is `idle` again and the log ends with
  `execution_status=idle`, the new message and its `last_user_message_id`
  update; no run started.
- **History survives a restart (`F06.restart-persist`).**
  ```sh
  BEFORE=$(control-agent-server api GET "/api/conversations/$CID/events/search" --field items | jq -c '[.[].id]')
  COUNT=$(control-agent-server api GET "/api/conversations/$CID/events/count" --field .)
  control-agent-server restart
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . eq "$COUNT" --save F06.restart-persist/count
  AFTER=$(control-agent-server api GET "/api/conversations/$CID/events/search" --field items | jq -c '[.[].id]')
  test "$BEFORE" = "$AFTER"
  REPLAY=$(control-agent-server ws listen "/sockets/events/$CID" --query resend_mode=all --duration 3 \
    --save F06.restart-persist/replay | jq -r .saved)
  jq -e --argjson ids "$AFTER" '[.frames[] | select(.direction == "received") | .frame | select(.key != "full_state") | .id] == $ids' "$REPLAY"
  control-agent-server api POST "/api/conversations/$CID/events" \
    --json '{"content":[{"type":"text","text":"QA_F06_AFTER_RESTART"}]}' --expect 200
  control-agent-server api GET "/api/conversations/$CID/events/search" --query sort_order=TIMESTAMP_DESC --query limit=2 \
    --check items.1.llm_message.content.0.text eq QA_F06_AFTER_RESTART
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . gt "$COUNT"
  ```
  The first read after the restart rehydrates the conversation; ids, order
  and count are unchanged, the socket replays the same log, and the new
  message lands after it.
- **Run capacity full (`F06.send-run-limit`).** It restarts the run with one
  run slot and holds that slot with a scripted provider that hangs (a real
  model cannot hang on demand); the next bullet reuses the holder and frees
  it. The waiting conversation is created first, because creating a
  conversation also needs a run slot.
  ```sh
  control-agent-server restart --config-json '{"max_concurrent_runs": 1}'
  QA_STUB=$(control-agent-server fixture llm-stub --name qa-f06-hang --step hang:120 --print-path)
  control-agent-server llm set --profile qa-f06-hang --model openai/qa-stub --base-url "$QA_STUB/v1" --no-api-key --no-validate
  WAITING=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  HOLD=$(control-agent-server conversation start --no-autotitle --tools none --prompt 'QA_F06_HOLD' --print-id)
  control-agent-server conversation wait "$HOLD" --until running --timeout 30
  control-agent-server api POST "/api/conversations/$WAITING/events" \
    --json '{"content":[{"type":"text","text":"QA_F06_429"}],"run":true}' --expect 429 \
    --check detail contains 'Message saved, but the conversation run limit was reached' --save F06.send-run-limit/post-429
  control-agent-server api GET "/api/conversations/$WAITING/events/search" --query source=user --query body=QA_F06_429 \
    --check items len-eq 1 --save F06.send-run-limit/saved
  control-agent-server api GET "/api/conversations/$WAITING" --check execution_status eq idle
  control-agent-server ws listen "/sockets/events/$WAITING" \
    --send '{"role":"user","content":[{"type":"text","text":"QA_F06_LIMIT_WS"}]}' \
    --until-kind ServerErrorEvent --until code=ConversationRunLimitExceeded --duration 15 --save F06.send-run-limit/ws
  control-agent-server api GET "/api/conversations/$WAITING/events/count" --query body=QA_F06_LIMIT_WS --check . eq 1
  ```
  The POST answers 429 with `Message saved, but the conversation run limit
  was reached. Retry POST /api/conversations/<id>/run without resending the
  message.`, the message is in the log and the conversation stays `idle`;
  the socket send gets the same detail as a `ServerErrorEvent` with `code`
  `ConversationRunLimitExceeded` and its message is saved too.
- **A message while the agent runs (`F06.send-while-running`).** Last
  bullet. `HOLD` from the previous bullet is `running`, stuck in its first
  model call on the hanging stub, so the stub's request log counts runs.
  ```sh
  control-agent-server api GET "/api/conversations/$HOLD" --check execution_status eq running
  control-agent-server sink read --name qa-f06-hang --path /v1/chat/completions --expect-min 1 --expect-max 1
  control-agent-server api POST "/api/conversations/$HOLD/events" \
    --json '{"content":[{"type":"text","text":"QA_F06_MIDRUN"}],"run":true}' --expect 200 --check success eq true \
    --save F06.send-while-running/post
  control-agent-server api GET "/api/conversations/$HOLD/events/search" --query source=user --query body=QA_F06_MIDRUN \
    --check items len-eq 1 --save F06.send-while-running/persisted
  control-agent-server api GET "/api/conversations/$HOLD" --check execution_status eq running
  control-agent-server api POST "/api/conversations/$HOLD/run" --expect 409
  control-agent-server api POST "/api/conversations/$HOLD/interrupt" --expect 200
  control-agent-server conversation wait "$HOLD" --until paused --timeout 60
  if control-agent-server sink read --name qa-f06-hang --path /v1/chat/completions --expect-min 2 --wait 10; then false; fi
  control-agent-server api GET "/api/conversations/$HOLD" --check execution_status eq paused
  control-agent-server sink read --name qa-f06-hang --path /v1/chat/completions --expect-max 1 \
    --save F06.send-while-running/model-requests
  control-agent-server llm preset deepseek
  ```
  The POST answers 200 at once although `POST /run` on the same
  conversation is 409 `Conversation already running...`; the message is in
  the log and the conversation stays `running`. After the interrupt the
  stub gets no second model request within a 10-second window (the
  `sink read --wait` would return as soon as one arrived) and the
  conversation is still `paused`, so no second run was started: a running
  loop picks the message up on its next step, and the parked re-run is
  dropped because the interrupt clears it before the run task ends
  (`EventService.interrupt`). The earlier `--expect-min 1 --expect-max 1`
  read is the positive control for the same sink query. Interrupting the
  holder frees the slot and the preset restores `deepseek-flash`.

## Gotchas

- `kind` filters need the fully-qualified class path
  (`openhands.sdk.event.llm_convertible.message.MessageEvent`,
  `...llm_convertible.action.ActionEvent`,
  `...llm_convertible.observation.ObservationEvent`,
  `...llm_convertible.system.SystemPromptEvent`,
  `openhands.sdk.event.conversation_state.ConversationStateUpdateEvent`),
  while events carry the bare name in `kind`; a short name returns an empty
  page, not an error (`F06.search-kind-short`).
- `page_id` is inclusive and an unknown `page_id` silently restarts from the
  first event (the newest for `TIMESTAMP_DESC`), so a stale cursor looks like
  a fresh first page.
- Timestamps are naive server-local ISO strings compared as strings. Recipes
  run the server with `TZ=UTC`; a value with an offset is converted to server
  time first. Search is in log order, which is not strictly timestamp order:
  a `stats` update is appended after an action and its observation but
  carries a timestamp from before the action, so a `timestamp__gte` or
  `resend_mode=since` cut taken at that observation's timestamp skips it
  (`F06.search-timestamp-order`).
- Each conversation's event bus takes at most 50 subscribers, internal ones
  (webhooks, auto-title) included; one more socket closes with 1013
  `Too many connections for this conversation`, which the SDK treats as
  retryable (from source; not driven, since 50 captures cost too much
  memory).
- Search drops `null` fields (`exclude_none`); get-one and batch-get keep
  them. Compare ids, not raw JSON.
- An unknown event id is a 500 today, and one missing id fails a whole batch
  (`F06.unknown-event`); batch-get is a GET with a JSON body, which browsers
  cannot send.
- The `SystemPromptEvent` is written on the first message, not at creation;
  a conversation created without a message has an empty log and count 0.
- `POST .../events` with `run: false` never runs, even on an idle
  conversation; a message to a `finished` or `stuck` conversation sets it to
  `idle`. A normal message also cancels an active `/goal` loop.
- Creating a conversation takes a run slot too, so with
  `max_concurrent_runs` reached `POST /api/conversations` itself answers 429.
  A 429 from `POST .../events` means the message was saved: retry
  `POST /run`, never the message.
- Every frame sent on the socket runs the agent (`run=True` is hard-coded);
  use REST to append without running. Errors come back as `ServerErrorEvent`
  frames whose `code` is the Python exception class name, and the socket
  stays open.
- The snapshot is pushed before any replay and live events are not fenced
  from replayed ones: during a run a reconnect with `resend_mode=all` can
  deliver an event twice or out of order, so dedupe by id. Within a live run
  frames are not strictly in timestamp order either (a
  `last_user_message_id` update can arrive before its message).
- Auth failures look different by path: first-frame auth closes with 4001
  after the upgrade; a wrong deprecated query or header key is refused before
  the upgrade (HTTP 403, no close code), which the SDK does not treat as
  fatal.
- Deleting a conversation does not close its open sockets
  (`F06.ws-delete-close`); they stay open with no further frames, while a
  new connection gets 4004.
- `StreamingDeltaEvent` frames reach this socket only when the agent's LLM
  has `stream: true`; they are never persisted, so search never returns them.
- A per-field `execution_status=finished` frame is a hint, not the end of
  the run: a stop hook can set it back to `running`. The `full_state`
  snapshot published after the run is authoritative (the SDK waits for it);
  recipes that stop at `finished` re-read the status over REST.
- `POST .../events` with `run: true` on a `running` conversation is a 200,
  not the 409 of `POST /run`: the message joins the running loop. A client
  that wants to know whether a new run started must watch the socket.
- The body is required (no body is a 422), but every field has a default:
  `{}` is a valid request and appends a user `MessageEvent` with empty
  `content`.
- The agent gets only the file editor (plus the built-in `finish` and
  `think`) so the family needs no `tmux`. Older runs of this harness put
  the tmux socket under the run directory, where a fresh `map run` made the
  path longer than the Unix socket limit and the terminal tool failed with
  `File name too long`; `launch` now uses a short `/tmp/ohv-*` directory.
