# Event webhooks and product telemetry

What the server pushes out on its own, with no route to call. Every entry in
`config.webhooks` (a `WebhookSpec`) subscribes to every conversation: it
buffers the conversation's events and POSTs them as a JSON array to
`{base_url}/events/<conversation id hex>`, and it POSTs the conversation's
`ConversationInfo` to `{base_url}/conversations` when a conversation is
created, paused, interrupted, updated or deleted. Requests carry the
configured `headers` and the server's own session key, batches flush when
`event_buffer_size` is reached or `flush_delay` seconds after the first queued
event (or when the queue reaches `max_batch_bytes`), failed batches are
retried and requeued, queue limits drop the oldest events, and queued events
are flushed when a conversation closes. Automations and the OpenHands app server consume
these. Separately, product telemetry ships a small allowlisted set of
sanitized diagnostic events (`agent_server.server_started`,
`conversation_created`, `conversation_finished`, ...) to an exporter, but only
when an exporter is configured *and* consent is granted (by settings or the
environment), and never when `DO_NOT_TRACK` is set.

Source: `openhands-agent-server/openhands/agent_server/conversation_service.py`, `openhands-agent-server/openhands/agent_server/pub_sub.py`, `openhands-agent-server/openhands/agent_server/config.py`, `openhands-agent-server/openhands/agent_server/env_parser.py`, `openhands-agent-server/openhands/agent_server/telemetry/`, `openhands-agent-server/openhands/agent_server/init_router.py`, `openhands-agent-server/openhands/agent_server/settings_router.py`, `openhands-agent-server/openhands/agent_server/api.py`

Needs: `llm`

Launch: `--webhook-sink`

Routes: none

## Sub-features

- `F32.events-post`: a run's events reach `POST {base_url}/events/<conversation id hex, no dashes>` as JSON arrays, through the agent's reply and the `finished` status update.
- `F32.events-session-key`: every webhook request carries `Content-Type: application/json` and the server's first session key as `X-Session-API-Key`.
- `F32.events-full-state`: each attach of a conversation (its creation, and its first use after a restart) posts a transient `ConversationStateUpdateEvent` with key `full_state`.
- `F32.events-no-deltas`: a streaming run's `StreamingDeltaEvent`s reach the events WebSocket but never a webhook.
- `F32.conversation-created`: creating a conversation POSTs its `ConversationInfo` to `{base_url}/conversations`.
- `F32.conversation-lifecycle`: pausing, interrupting, updating and deleting a conversation each POST its info again (`paused`, `paused`, the new title, `deleting`).
- `F32.custom-headers`: a webhook's `headers` and a `base_url` path prefix apply to both event and conversation posts.
- `F32.buffer-size`: once `event_buffer_size` events are queued they are posted at once, without waiting for `flush_delay`.
- `F32.buffer-stranded`: events that arrive while a batch is in flight are posted as soon as the buffer is full again, not held until `flush_delay` (reproduces a real delay; see Gotchas).
- `F32.flush-delay`: a partial buffer is posted `flush_delay` seconds after its first event; later events do not restart the timer.
- `F32.webhook-spec-docs`: the `WebhookSpec` field descriptions published in the OpenAPI schema (the item type of `POST /api/init`'s `webhooks`) state the real event path and flush timer (reproduces a documentation bug; see Gotchas).
- `F32.flush-on-close`: deleting a conversation posts its queued events before the `DELETE` returns, and a graceful server stop posts every conversation's queue.
- `F32.backpressure`: `max_batch_bytes` caps each request (an event larger than it is posted alone), and `max_queue_size` and `max_queue_bytes` drop the oldest queued events with a warning.
- `F32.retry`: a receiver that answers 500 gets `num_retries + 1` attempts per event batch and per conversation post.
- `F32.requeue`: a batch that exhausted its retries is requeued and sent again on the next flush, not dropped.
- `F32.slow-receiver`: a slow webhook receiver must not keep a conversation that reports `finished` from accepting a new run (reproduces a real 409; see Gotchas).
- `F32.env-config`: `OH_WEBHOOKS_0_*` environment variables configure a webhook and override the config file's first webhook field by field.
- `F32.telemetry-off-by-default`: no exporter, or the `http` exporter without consent, delivers nothing.
- `F32.telemetry-lifecycle`: with the `http` exporter and consent, `server_started` arrives at boot and `server_stopped` at a graceful stop, as `{"schema_version": 1, "events": [...]}` with the configured Bearer token.
- `F32.telemetry-conversation`: a run emits `conversation_created` and `conversation_finished` under the request's `user_id`, with a pseudonymous `conversation_ref` and without the prompt or the conversation id.
- `F32.telemetry-request-failed`: an unhandled 500 emits `request_failed` with the route template (never the concrete path), the exception class and the response's `error_id`, attributed to `X-OpenHands-Telemetry-Distinct-Id`, without the exception message.
- `F32.telemetry-conversation-failed`: a run that ends in `error` emits `conversation_failed` (`terminal_status` `error`) and `conversation_error` with the error code as `error_class`, without the error's detail text.
- `F32.telemetry-usage-buckets`: `conversation_finished` reports token and cost buckets from the run's real usage (reproduces a real gap; see Gotchas).
- `F32.telemetry-consent-settings`: granting or denying consent with `PATCH /api/settings` `misc_settings_diff` starts or stops delivery immediately, and persisted consent enables telemetry after a restart.
- `F32.telemetry-env-override`: `OH_TELEMETRY_CONSENT=denied` with `OH_TELEMETRY_CONSENT_MODE=override` beats a granted setting.
- `F32.telemetry-kill-switch`: `DO_NOT_TRACK=1` beats every consent source.
- `F32.telemetry-salt-and-kind`: without a salt `conversation_ref` is keyed by `OH_SECRET_KEY` (the same ref across restarts); `OH_TELEMETRY_SALT` re-keys it (again the same across restarts), and `OH_TELEMETRY_DEPLOYMENT_KIND` tags every event.
- `F32.telemetry-salt-docs`: the `TelemetrySpec.salt` description published in the OpenAPI schema states the real fallback key (reproduces a documentation bug; see Gotchas).
- `F32.deferred-init`: a dormant pod sends nothing until `POST /api/init` delivers `telemetry` and `webhooks`; then `server_started`, conversation telemetry and webhooks flow.
- `F32.deferred-init-flag`: events from an initialized deferred pod report `deferred_init: true` (reproduces a real mislabel; see Gotchas).

## How to get to it (agent POV)

- Config: `webhooks` in the JSON config file (`OPENHANDS_AGENT_SERVER_CONFIG_PATH`),
  each a `WebhookSpec`: `base_url`, `headers`, `event_buffer_size` (5),
  `flush_delay` (30 s), `num_retries` (3), `retry_delay` (5 s),
  `max_queue_size`, `max_batch_bytes`, `max_queue_bytes`. `launch
  --webhook-sink` starts a recording sink named `webhooks` and adds it with
  `flush_delay` 1; `restart --config-json '{"webhooks": [...]}'` replaces the
  list. Every recipe reads deliveries with `sink read`. The OpenAPI
  document (`GET /openapi.json`) publishes `WebhookSpec` and `TelemetrySpec`
  with their field descriptions, as parts of `POST /api/init`'s body
  (`InitRequest`); `F32.webhook-spec-docs` and `F32.telemetry-salt-docs`
  read them there.
- Environment: `OH_WEBHOOKS='[...]'` or `OH_WEBHOOKS_0_BASE_URL`,
  `OH_WEBHOOKS_0_EVENT_BUFFER_SIZE`, `OH_WEBHOOKS_0_FLUSH_DELAY`,
  `OH_WEBHOOKS_0_HEADERS` (JSON) and so on; they override the file
  (`F32.env-config`).
- REST (side effects, owned by other families): `POST /api/conversations`,
  `POST /api/conversations/{id}/events`, `/run`, `/pause`, `/interrupt`,
  `PATCH` and `DELETE /api/conversations/{id}` produce the deliveries; `PATCH /api/settings`
  with `misc_settings_diff.telemetry.consent` (`granted`, `denied`) is the
  consent switch Agent Canvas flips; `POST /api/init` can deliver `webhooks`
  and `telemetry` to a deferred pod.
- Telemetry config: `OH_TELEMETRY_EXPORTER` (`none`, `http`, `posthog`),
  `OH_TELEMETRY_HTTP_ENDPOINT`, `OH_TELEMETRY_HTTP_TOKEN`,
  `OH_TELEMETRY_FLUSH_DELAY`, `OH_TELEMETRY_EVENT_BUFFER_SIZE`,
  `OH_TELEMETRY_SALT`, `OH_TELEMETRY_DEPLOYMENT_KIND` (or `telemetry` in the
  config file); consent from `misc_settings.telemetry.consent`,
  `OH_TELEMETRY_CONSENT` (`granted`/`denied`) with
  `OH_TELEMETRY_CONSENT_MODE` (`seed`, default, or `override`), and the
  `DO_NOT_TRACK` kill switch. The recipes use `restart --env`.
- SDK / TypeScript client: no client method; `StartConversationRequest.user_id`
  becomes the telemetry `distinct_id`, and `X-OpenHands-Telemetry-Distinct-Id`
  attributes `request_failed` events. Receivers are the automation service
  and the OpenHands app server (`/events/{id}` and `/conversations`).

## Driving it with control-agent-server

Preconditions:

- A fresh run launched with `--webhook-sink` is live and exported: the CLI
  started a recording sink named `webhooks` and wrote it into
  `config.webhooks` with `flush_delay` 1 (default `event_buffer_size` 5,
  `num_retries` 3). `$DEEPSEEK_API_KEY` is set. The block below activates
  deepseek-flash and keeps the sink's URL in `$WH` for the bullets that
  rewrite `webhooks`.
  ```sh
  control-agent-server llm preset deepseek
  WH=$(control-agent-server fixture http-sink --name webhooks --print-path)
  test -n "$WH"
  ```
- Bullets run top to bottom on this one run. Several bullets `restart` it with
  a new `webhooks` list (`restart --config-json` replaces the whole list) or
  new telemetry variables; each says so. Asynchronous deliveries are awaited
  with `sink read --wait N`, which polls until `--expect-min` holds and then
  applies `--expect-max`; an exact count therefore means "no more had
  arrived when the minimum did".

- **A run's events reach the webhook (`F32.events-post`).** A real
  deepseek-flash run with the launch webhook (`flush_delay` 1, buffer 5).
  ```sh
  CID=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Reply with exactly: ok' --wait --print-id)
  HEX=${CID//-/}
  control-agent-server sink read --path "/events/$HEX" --contains '"key": "execution_status", "value": "finished"' --expect-min 1 --wait 15 --save F32.events-post/finished
  MID=$(control-agent-server api GET "/api/conversations/$CID/events/search" --query source=agent --query sort_order=TIMESTAMP_DESC --query limit=1 --field items.0.id)
  test -n "$MID"
  control-agent-server sink read --path "/events/$HEX" --contains "\"id\": \"$MID\"" --expect-min 1 --expect-max 1
  control-agent-server sink read --path "/events/$HEX" --contains '"body": {' --expect-max 0
  control-agent-server sink read --path "/events/$CID" --expect-max 0
  ```
  The deliveries go to `/events/<id without dashes>` (none to the dashed id),
  every body is a JSON array (no object body), one of them carries the
  `execution_status` → `finished` update, and the agent's last stored event
  (its reply, a `MessageEvent` or a `finish` `ActionEvent` depending on the
  model) arrives exactly once, with the id `GET .../events/search` returns.
- **Session key and content type on every request (`F32.events-session-key`).**
  The receiver can call back with the server's own key.
  ```sh
  test -s "$AGENT_SERVER_VERIFY_RUN/private/session_api_key"
  TOTAL=$(control-agent-server sink read | python3 -c 'import json, sys; print(json.load(sys.stdin)["requests"])')
  test "$TOTAL" -ge 2
  control-agent-server sink read --contains "\"X-Session-API-Key\": \"$(cat "$AGENT_SERVER_VERIFY_RUN/private/session_api_key")\"" --expect-min "$TOTAL" --save F32.events-session-key/all-requests
  control-agent-server sink read --contains '"Content-Type": "application/json"' --expect-min "$TOTAL"
  ```
  Every request recorded so far (event batches and conversation posts) has
  `X-Session-API-Key` equal to the run's key and a JSON content type. The
  saved evidence shows the header as `<redacted:session-api-key>`.
- **Conversation created (`F32.conversation-created`).** The run above posted
  its `ConversationInfo` once.
  ```sh
  control-agent-server sink read --path /conversations --contains "\"id\": \"$CID\"" --expect-min 1 --expect-max 1 --wait 15 --save F32.conversation-created/info
  control-agent-server sink read --path /conversations --contains '"body": [' --expect-max 0
  control-agent-server api GET "/api/conversations/$CID" --check execution_status eq finished
  ```
  Exactly one object body for the conversation reached `/conversations`: the
  run's completion is reported only on the events webhook (previous bullet),
  never as a second conversation post.
- **Full-state snapshot on every attach (`F32.events-full-state`).** The
  snapshot is transient: it is sent to subscribers, not stored. After a
  restart, the first route that loads the conversation re-attaches the
  webhook and sends a new one.
  ```sh
  control-agent-server sink read --path "/events/$HEX" --contains '"key": "full_state"' --expect-min 2 --expect-max 2 --wait 15
  control-agent-server sink read --path "/events/$HEX" --contains '"api_key": "**********"' --expect-min 1
  test -n "$DEEPSEEK_API_KEY"
  control-agent-server sink read --contains "$DEEPSEEK_API_KEY" --expect-max 0
  control-agent-server conversation events "$CID" --key full_state --expect-count 0
  control-agent-server restart
  control-agent-server api GET "/api/conversations/$CID/events/search" --query limit=1 --expect 200 --quiet
  control-agent-server sink read --path "/events/$HEX" --contains '"key": "full_state"' --expect-min 3 --expect-max 3 --wait 15 --save F32.events-full-state/after-restart
  ```
  Two snapshots arrived during the run (one when the webhook attached at
  creation, one with the final state); they carry the agent's LLM config
  with `api_key` masked, and the real DeepSeek key appears in no delivery.
  None is in the stored events, and the events search after the restart
  produced a third.
- **Conversation lifecycle posts (`F32.conversation-lifecycle`).** Pause,
  interrupt, rename and delete an idle conversation (no model is needed).
  ```sh
  LID=$(control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_LIFE --no-autotitle --print-id)
  control-agent-server api POST "/api/conversations/$LID/pause" --expect 200
  control-agent-server api GET "/api/conversations/$LID" --check execution_status eq paused
  control-agent-server sink read --path /conversations --contains '"execution_status": "paused"' --expect-min 1 --expect-max 1 --wait 15
  control-agent-server api POST "/api/conversations/$LID/interrupt" --expect 200
  control-agent-server sink read --path /conversations --contains '"execution_status": "paused"' --expect-min 2 --expect-max 2 --wait 15
  control-agent-server api PATCH "/api/conversations/$LID" --json '{"title": "QA_F32_TITLE"}' --expect 200
  control-agent-server api GET "/api/conversations/$LID" --check title eq QA_F32_TITLE
  control-agent-server api DELETE "/api/conversations/$LID" --expect 200
  control-agent-server api GET "/api/conversations/$LID" --expect 404
  control-agent-server sink read --path /conversations --contains "\"id\": \"$LID\"" --expect-min 5 --expect-max 5 --wait 15 --save F32.conversation-lifecycle/posts
  control-agent-server sink read --path /conversations --contains '"execution_status": "paused"' --expect-min 3 --expect-max 3
  control-agent-server sink read --path /conversations --contains '"title": "QA_F32_TITLE"' --expect-min 2 --expect-max 2
  control-agent-server sink read --path /conversations --contains '"execution_status": "deleting"' --expect-min 1 --expect-max 1
  ```
  Five posts for the conversation: created (`idle`), paused, interrupted
  (still `paused`; each awaited before the next action, because the posts
  are unordered background tasks), renamed (still `paused`, new title) and
  deleting (with the title). This is the only paused or deleted conversation
  on this path so far, so the status counts are exact.
- **No streaming deltas on webhooks (`F32.events-no-deltas`).** A streaming
  run, with the events WebSocket as the positive control.
  ```sh
  SID=$(control-agent-server conversation start --tools none --no-autotitle --llm-json '{"stream": true}' --no-run --prompt 'Count from 1 to 20 separated by spaces.' --print-id)
  control-agent-server ws start "/sockets/events/$SID" --name qa-f32-deltas --duration 180
  control-agent-server api POST "/api/conversations/$SID/run" --expect 200
  control-agent-server conversation wait "$SID" --until finished --timeout 120
  control-agent-server ws stop qa-f32-deltas --expect-kind StreamingDeltaEvent --wait 10 --save F32.events-no-deltas/socket
  control-agent-server sink read --path "/events/${SID//-/}" --contains '"key": "execution_status", "value": "finished"' --expect-min 1 --wait 15
  control-agent-server sink read --path "/events/${SID//-/}" --contains StreamingDeltaEvent --expect-max 0 --save F32.events-no-deltas/webhook
  ```
  The socket received `StreamingDeltaEvent` frames; the webhook received the
  run's events through the `finished` update but no delta.
- **Custom headers, path prefix and buffer size (`F32.custom-headers`, `F32.buffer-size`).**
  Restart with one webhook under a path prefix, a custom header,
  `event_buffer_size` 2 and a 600 s `flush_delay`, so only a full buffer can
  trigger a post.
  ```sh
  control-agent-server restart --config-json "{\"webhooks\": [{\"base_url\": \"$WH/qa-f32-b\", \"event_buffer_size\": 2, \"flush_delay\": 600, \"headers\": {\"X-QA-F32\": \"qa-f32-header\"}}]}"
  BID=$(control-agent-server conversation start --placeholder-agent --no-autotitle --print-id)
  control-agent-server sink read --path "/qa-f32-b/events/${BID//-/}" --expect-max 0
  control-agent-server conversation send "$BID" --text QA_F32_BUFFER --no-run
  control-agent-server sink read --path "/qa-f32-b/events/${BID//-/}" --contains '"key": "full_state"' --expect-min 1 --expect-max 1 --wait 10 --save F32.buffer-size/first-batch
  control-agent-server sink read --path "/qa-f32-b/events/${BID//-/}" --contains SystemPromptEvent --expect-min 1 --expect-max 1
  control-agent-server sink read --path "/qa-f32-b/events/" --contains '"X-QA-F32": "qa-f32-header"' --expect-min 1 --save F32.custom-headers/events
  control-agent-server sink read --path /qa-f32-b/conversations --contains "\"id\": \"$BID\"" --expect-min 1
  control-agent-server sink read --path /qa-f32-b/conversations --contains '"X-QA-F32": "qa-f32-header"' --expect-min 1
  ```
  Creating the conversation queued only the snapshot (1 < 2: nothing posted);
  the message's first event filled the buffer and the pair (`full_state`,
  `SystemPromptEvent`) was posted within seconds although the timer is
  600 s. Both the event batch and the conversation post went under
  `/qa-f32-b/` with `X-QA-F32` (plus the session key).
- **Events stranded behind an in-flight batch (`F32.buffer-stranded`), known bug.**
  Same conversation: the message and its `last_user_message_id` update were
  published while that first batch was being posted, so the buffer (2) is
  full again and they should be posted at once. The stored events come
  first, so the bullet can only fail on the delivery.
  ```sh
  control-agent-server conversation events "$BID" --kinds MessageEvent --contains QA_F32_BUFFER --expect-count 1
  control-agent-server conversation events "$BID" --key last_user_message_id --expect-count 1
  control-agent-server sink read --path "/qa-f32-b/events/${BID//-/}" --expect-min 1 --expect-max 1
  control-agent-server sink read --path "/qa-f32-b/events/${BID//-/}" --contains QA_F32_BUFFER --expect-min 1 --wait 10 --save F32.buffer-stranded/after-wait  # bug
  ```
  The message and its update are stored, and only the first batch was
  posted. Expected: the user message reaches the sink within 10 s. Actual:
  it stays queued until the 600 s timer, the next burst of events, or the
  conversation's close (the restart in `F32.flush-on-close` delivers it).
  See Gotchas.
- **Flush on delete and on shutdown (`F32.flush-on-close`).**
  Restart with `event_buffer_size` 100 and a 600 s timer, so nothing is
  posted unless the conversation closes. The `sleep` gives a wrongly short
  timer the chance to fire (the server's timer is under test). Both restarts
  are shutdowns under test: the first one's graceful stop delivers the
  previous bullet's stranded message (late, not lost).
  ```sh
  control-agent-server restart --config-json "{\"webhooks\": [{\"base_url\": \"$WH/qa-f32-c\", \"event_buffer_size\": 100, \"flush_delay\": 600}]}"
  control-agent-server sink read --path "/qa-f32-b/events/${BID//-/}" --contains QA_F32_BUFFER --expect-min 1 --save F32.flush-on-close/stranded-after-stop
  DID=$(control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_DELETE --no-autotitle --print-id)
  ZID=$(control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_SHUTDOWN --no-autotitle --print-id)
  sleep 2
  control-agent-server sink read --path /qa-f32-c/events/ --expect-max 0
  control-agent-server api DELETE "/api/conversations/$DID" --expect 200
  control-agent-server sink read --path "/qa-f32-c/events/${DID//-/}" --contains QA_F32_DELETE --expect-min 1 --expect-max 1 --save F32.flush-on-close/after-delete
  control-agent-server sink read --path "/qa-f32-c/events/${ZID//-/}" --expect-max 0
  control-agent-server restart
  control-agent-server sink read --path "/qa-f32-c/events/${ZID//-/}" --contains QA_F32_SHUTDOWN --expect-min 1 --save F32.flush-on-close/after-restart
  control-agent-server api GET "/api/conversations/$ZID" --expect 200
  ```
  The first restart delivered `QA_F32_BUFFER` to `/qa-f32-b/`. Nothing was
  posted for either new conversation after 2 s; the `DELETE` returned only
  after the queued events (snapshot, system prompt, message and its
  `last_user_message_id` update) were posted in one batch, and the graceful
  stop of the second `restart` posted the other conversation's queue, which
  still exists afterwards.
- **Flush timer from the first event (`F32.flush-delay`).** Restart with a
  6 s timer and a large buffer. The second event arrives 3 s after the first;
  a timer reset by it would post at about 9 s, a timer from the first event
  at about 6 s.
  ```sh
  control-agent-server restart --config-json "{\"webhooks\": [{\"base_url\": \"$WH/qa-f32-d\", \"event_buffer_size\": 100, \"flush_delay\": 6}]}"
  T0=$SECONDS
  TID=$(control-agent-server conversation start --placeholder-agent --no-autotitle --print-id)
  sleep 3
  control-agent-server conversation send "$TID" --text QA_F32_TIMER --no-run
  control-agent-server sink read --path "/qa-f32-d/events/${TID//-/}" --expect-max 0
  control-agent-server sink read --path "/qa-f32-d/events/${TID//-/}" --expect-min 1 --wait 15
  ELAPSED=$((SECONDS - T0))
  test "$ELAPSED" -ge 5
  test "$ELAPSED" -le 8
  control-agent-server sink read --path "/qa-f32-d/events/${TID//-/}" --expect-min 1 --expect-max 1 --contains QA_F32_TIMER --save F32.flush-delay/one-batch
  control-agent-server sink read --path "/qa-f32-d/events/${TID//-/}" --contains '"key": "full_state"' --expect-min 1 --expect-max 1
  ```
  Nothing was posted after 3 s; one batch with the snapshot and the message
  arrived 5 to 8 s after creation. The field's own description claims the
  opposite (see Gotchas).
- **Webhook field descriptions (`F32.webhook-spec-docs`), known bug.** An
  orchestrator learns `WebhookSpec` from the OpenAPI document, where it is
  the item type of `POST /api/init`'s `webhooks`. Its descriptions should
  match what `F32.events-post` and `F32.flush-delay` proved: events go to
  `{base_url}/events/<conversation id hex>`, and the timer starts at the
  first queued event and is not reset.
  ```sh
  control-agent-server api GET /openapi.json --auth none --quiet --check 'components.schemas.InitRequest.properties.webhooks.anyOf.0.items.$ref' eq '#/components/schemas/WebhookSpec' --check components.schemas.WebhookSpec.properties.base_url.description exists --check components.schemas.WebhookSpec.properties.flush_delay.description exists
  control-agent-server api GET /openapi.json --auth none --quiet --check components.schemas.WebhookSpec.properties.flush_delay.description not-contains 'Timer is reset on each new event'  # bug
  control-agent-server api GET /openapi.json --auth none --quiet --check components.schemas.WebhookSpec.properties.base_url.description not-contains 'Events will be sent to {base_url}/events and'  # bug
  ```
  Expected: descriptions that state the per-conversation path and a timer
  from the first event. Actual: `flush_delay` says "Timer is reset on each
  new event" and `base_url` says "Events will be sent to {base_url}/events
  and conversation info to {base_url}/conversations". The failing check
  prints the published text. See Gotchas.
- **Batch and queue limits (`F32.backpressure`).** Three webhooks on one
  restart, all with a large buffer and a 600 s timer: one with
  `max_batch_bytes` 1 (every event is bigger), one with `max_queue_size` 2,
  and one with `max_queue_bytes` 1500 (between the two small events
  together, about 800 bytes, and the snapshot alone, about 3 KB). The
  delete flushes whatever is still queued.
  ```sh
  control-agent-server restart --config-json "{\"webhooks\": [{\"base_url\": \"$WH/qa-f32-bytes\", \"event_buffer_size\": 100, \"flush_delay\": 600, \"max_batch_bytes\": 1}, {\"base_url\": \"$WH/qa-f32-queue\", \"event_buffer_size\": 100, \"flush_delay\": 600, \"max_queue_size\": 2}, {\"base_url\": \"$WH/qa-f32-qbytes\", \"event_buffer_size\": 100, \"flush_delay\": 600, \"max_queue_bytes\": 1500}]}"
  PID=$(control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_LIMITS --no-autotitle --print-id)
  control-agent-server conversation events "$PID" --expect-count 3
  control-agent-server api DELETE "/api/conversations/$PID" --expect 200
  control-agent-server sink read --path "/qa-f32-bytes/events/${PID//-/}" --expect-min 4 --expect-max 4 --wait 10 --save F32.backpressure/one-event-per-request
  control-agent-server sink read --path "/qa-f32-bytes/events/${PID//-/}" --contains '"key": "full_state"' --expect-min 1 --expect-max 1
  control-agent-server sink read --path "/qa-f32-bytes/events/${PID//-/}" --contains SystemPromptEvent --expect-min 1 --expect-max 1
  control-agent-server sink read --path "/qa-f32-bytes/events/${PID//-/}" --contains QA_F32_LIMITS --expect-min 1 --expect-max 1
  control-agent-server sink read --path "/qa-f32-queue/events/${PID//-/}" --contains QA_F32_LIMITS --expect-min 1 --expect-max 1 --wait 10 --save F32.backpressure/trimmed-queue
  control-agent-server sink read --path "/qa-f32-queue/events/${PID//-/}" --contains '"key": "full_state"' --expect-max 0
  control-agent-server sink read --path "/qa-f32-qbytes/events/${PID//-/}" --contains QA_F32_LIMITS --expect-min 1 --expect-max 1 --wait 10 --save F32.backpressure/byte-trimmed-queue
  control-agent-server sink read --path "/qa-f32-qbytes/events/${PID//-/}" --expect-max 1
  control-agent-server sink read --path "/qa-f32-qbytes/events/${PID//-/}" --contains '"key": "full_state"' --expect-max 0
  control-agent-server sink read --path "/qa-f32-qbytes/events/${PID//-/}" --contains SystemPromptEvent --expect-max 0
  control-agent-server logs --grep 'Webhook queue exceeded' --expect-min 2
  ```
  The three stored events plus the transient snapshot reached the first
  webhook as four requests, one event each (the snapshot alone, the system
  prompt alone, the message alone). The second webhook got one batch: the
  newest events, with the message but without the snapshot, which was the
  oldest and was dropped. The third webhook also got one batch with the
  message, but neither the snapshot nor the system prompt (about 18 KB):
  each was over the byte bound on its own and was dropped as soon as it was
  queued. Each limited webhook logged one `Webhook queue exceeded its
  configured count or byte limit` warning (the first drop; later ones are
  logged every 100). The first webhook, on the same conversation, is the
  positive control that the snapshot and the system prompt were published.
- **Retries against a failing receiver (`F32.retry`).** A sink that answers
  500, `num_retries` 2 and no retry delay; the 600 s timer keeps the event
  batch queued until the delete closes the conversation.
  ```sh
  FAIL=$(control-agent-server fixture http-sink --name qa-f32-fail --status 500 --print-path)
  control-agent-server restart --config-json "{\"webhooks\": [{\"base_url\": \"$FAIL\", \"event_buffer_size\": 100, \"flush_delay\": 600, \"num_retries\": 2, \"retry_delay\": 0}]}"
  RID=$(control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_RETRY --no-autotitle --print-id)
  control-agent-server sink read --name qa-f32-fail --path /events/ --expect-max 0
  control-agent-server api DELETE "/api/conversations/$RID" --expect 200
  control-agent-server sink read --name qa-f32-fail --path "/events/${RID//-/}" --contains QA_F32_RETRY --expect-min 3 --expect-max 3 --save F32.retry/event-attempts
  control-agent-server sink read --name qa-f32-fail --path /conversations --contains "\"id\": \"$RID\"" --expect-min 6 --expect-max 6 --wait 15 --save F32.retry/conversation-attempts
  ```
  The closing flush made exactly 3 attempts (1 + `num_retries`) with the
  same batch, and each conversation post (created, deleting) made 3, for 6.
  The `DELETE` still answered 200.
- **Failed batches are requeued (`F32.requeue`).** Same failing sink, a 1 s
  timer and `num_retries` 1 (2 attempts per flush): a dropped batch would be
  attempted twice, a requeued one again on every flush.
  ```sh
  control-agent-server restart --config-json "{\"webhooks\": [{\"base_url\": \"$FAIL\", \"event_buffer_size\": 100, \"flush_delay\": 1, \"num_retries\": 1, \"retry_delay\": 0}]}"
  QID=$(control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_REQUEUE --no-autotitle --print-id)
  control-agent-server sink read --name qa-f32-fail --path "/events/${QID//-/}" --contains QA_F32_REQUEUE --expect-min 6 --wait 30 --save F32.requeue/attempts
  control-agent-server sink read --name qa-f32-fail --path "/events/${QID//-/}" --contains '"key": "full_state"' --expect-min 6
  control-agent-server api DELETE "/api/conversations/$QID" --expect 200
  ```
  At least three flush cycles re-sent the same queue, oldest event (the
  snapshot) included. The delete ends the loop after one last cycle and
  still answers 200.
- **A slow receiver blocks the next run (`F32.slow-receiver`), known bug.**
  An `llm-stub` fixture stands in for a webhook receiver that takes 15 s to
  answer (its `hang` step; `http-sink` cannot delay). It expects
  chat-completion bodies, so after the hang it drops event-batch connections
  and the server logs a failed attempt; the 15 s, not the failure, is what
  holds the run. With `event_buffer_size` 1 an event of the run is posted
  inline and its publish waits for the receiver. The same stand-in answers
  at once first (`reply:ok`): that run is the positive control, so the 409
  is the receiver's latency and nothing else. The `trap` makes the stand-in
  fast again so later restarts are not slowed.
  ```sh
  SLOW=$(control-agent-server fixture llm-stub --name qa-f32-slow --step reply:ok --print-path)
  trap 'control-agent-server fixture llm-stub --name qa-f32-slow --step reply:ok >/dev/null' EXIT
  control-agent-server restart --config-json "{\"webhooks\": [{\"base_url\": \"$SLOW\", \"event_buffer_size\": 1, \"flush_delay\": 600, \"num_retries\": 0}]}"
  FASTID=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Reply with exactly: ok' --wait --print-id)
  control-agent-server api POST "/api/conversations/$FASTID/run" --expect 200 --save F32.slow-receiver/control-fast-receiver
  control-agent-server fixture llm-stub --name qa-f32-slow --step hang:15
  WCID=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Reply with exactly: ok' --wait --print-id)
  control-agent-server api GET "/api/conversations/$WCID" --check execution_status eq finished
  control-agent-server sink read --name qa-f32-slow --path "/events/${WCID//-/}" --expect-min 1
  control-agent-server api POST "/api/conversations/$WCID/run" --expect 200 --save F32.slow-receiver/run-after-finished  # bug
  ```
  With the fast stand-in, `POST .../run` right after `finished` answers 200
  (the conversation is finished, so that run ends without a model call).
  Expected: the same with the slow stand-in. Actual: 409 `Conversation
  already running` until the receiver answers (about 15 s here; up to 30 s),
  because the run task waits for the pending webhook publish before it
  clears itself. See Gotchas.
- **Webhooks from the environment (`F32.env-config`).** Put the failing sink
  with a 1 s timer in the config file and override only `base_url` and
  `headers` of `webhooks[0]` through the environment.
  ```sh
  control-agent-server restart --config-json "{\"webhooks\": [{\"base_url\": \"$FAIL\", \"flush_delay\": 1}]}" --env "OH_WEBHOOKS_0_BASE_URL=$WH/qa-f32-env" --env 'OH_WEBHOOKS_0_HEADERS={"X-QA-F32-Env": "qa-f32-env"}'
  EID=$(control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_ENV --no-autotitle --print-id)
  control-agent-server sink read --path "/qa-f32-env/events/${EID//-/}" --contains QA_F32_ENV --expect-min 1 --wait 15 --save F32.env-config/events
  control-agent-server sink read --path "/qa-f32-env/events/${EID//-/}" --contains '"X-QA-F32-Env": "qa-f32-env"' --expect-min 1
  control-agent-server sink read --path /qa-f32-env/conversations --contains "\"id\": \"$EID\"" --expect-min 1
  control-agent-server sink read --name qa-f32-fail --path "/events/${EID//-/}" --expect-max 0
  control-agent-server sink read --name qa-f32-fail --path /conversations --contains "\"id\": \"$EID\"" --expect-max 0
  control-agent-server api DELETE "/api/conversations/$EID" --expect 200
  ```
  Events and the conversation post went to the environment's URL with its
  header, after the file's 1 s timer (the file's other fields still apply),
  and nothing for this conversation reached the failing sink named in the
  file.
- **Telemetry is off by default (`F32.telemetry-off-by-default`).**
  `restart --reset-config` drops the launch webhook and every earlier
  override. First an endpoint and consent without an exporter, then the
  `http` exporter with consent cleared (an empty value is no consent).
  ```sh
  TS=$(control-agent-server fixture http-sink --name qa-f32-telemetry --print-path)
  control-agent-server restart --reset-config --env "OH_TELEMETRY_HTTP_ENDPOINT=$TS/v1/events" --env OH_TELEMETRY_FLUSH_DELAY=1 --env OH_TELEMETRY_CONSENT=granted
  control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_OFF --body-json '{"user_id": "qa-f32-off"}' --no-autotitle
  control-agent-server restart --env OH_TELEMETRY_EXPORTER=http --env OH_TELEMETRY_CONSENT=
  control-agent-server logs --grep 'enabled=False \(default\)' --expect-min 1
  control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_OFF --body-json '{"user_id": "qa-f32-off"}' --no-autotitle
  control-agent-server restart
  control-agent-server sink read --name qa-f32-telemetry --expect-max 0 --save F32.telemetry-off-by-default/nothing
  ```
  Two server lifetimes and two conversations later the endpoint has received
  nothing; the log says the exporter was built but `enabled=False (default)`.
  The next bullet is the positive control on the same endpoint.
- **Server lifecycle events (`F32.telemetry-lifecycle`).** Seed consent from
  the environment and add a token.
  ```sh
  control-agent-server restart --env OH_TELEMETRY_CONSENT=granted --env OH_TELEMETRY_HTTP_TOKEN=qa-f32-token
  control-agent-server logs --grep 'enabled=True \(env_seed\)' --expect-min 1
  control-agent-server sink read --name qa-f32-telemetry --contains '{"schema_version": 1, "events": [{"event": "agent_server.server_started", "distinct_id": "anon:' --expect-min 1 --expect-max 1 --wait 15 --save F32.telemetry-lifecycle/started
  control-agent-server sink read --name qa-f32-telemetry --contains '"Authorization": "Bearer qa-f32-token"' --expect-min 1
  control-agent-server restart
  control-agent-server sink read --name qa-f32-telemetry --contains agent_server.server_stopped --expect-min 1 --expect-max 1 --save F32.telemetry-lifecycle/stopped
  control-agent-server sink read --name qa-f32-telemetry --contains agent_server.server_started --expect-min 2 --expect-max 2 --wait 15
  ```
  `server_started` arrived with `schema_version` 1, an anonymous
  `distinct_id` and the Bearer token; the graceful stop delivered
  `server_stopped` before the restart returned, and the new process sent its
  own `server_started`.
- **Conversation telemetry is sanitized (`F32.telemetry-conversation`).** A
  real run with a `user_id`, an automation tag and a marker in the prompt.
  ```sh
  UCID=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Reply with exactly: ok QA_F32_PRIVATE' --body-json '{"user_id": "qa-f32-user"}' --tag automationid=qa-f32 --wait --print-id)
  control-agent-server sink read --name qa-f32-telemetry --contains '"event": "agent_server.conversation_finished", "distinct_id": "qa-f32-user"' --expect-min 1 --expect-max 1 --wait 15 --save F32.telemetry-conversation/finished
  control-agent-server sink read --name qa-f32-telemetry --contains '"event": "agent_server.conversation_created", "distinct_id": "qa-f32-user"' --expect-min 1 --expect-max 1 --save F32.telemetry-conversation/created
  REF=$(python3 -c 'import json, sys; print(next(e["properties"]["conversation_ref"] for line in sys.stdin for e in json.loads(line)["body"]["events"] if e["distinct_id"] == "qa-f32-user"))' < "$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f32-telemetry.jsonl")
  test "${#REF}" -eq 32
  control-agent-server sink read --name qa-f32-telemetry --contains "\"conversation_ref\": \"$REF\", \"llm_model_family\": \"deepseek\", \"agent_kind\"" --expect-min 1 --expect-max 1
  control-agent-server sink read --name qa-f32-telemetry --contains "\"conversation_ref\": \"$REF\", \"terminal_status\": \"finished\"" --expect-min 1 --expect-max 1
  control-agent-server sink read --name qa-f32-telemetry --contains '"llm_model_family": "deepseek"}' --expect-min 1
  control-agent-server sink read --name qa-f32-telemetry --contains '"is_automation": true' --expect-min 1
  control-agent-server sink read --name qa-f32-telemetry --contains QA_F32_PRIVATE --expect-max 0
  control-agent-server sink read --name qa-f32-telemetry --contains "$UCID" --expect-max 0
  control-agent-server sink read --name qa-f32-telemetry --contains "${UCID//-/}" --expect-max 0
  ```
  Both events use `qa-f32-user` as `distinct_id` and carry the same 32-hex
  `conversation_ref` instead of the conversation id: `conversation_created`
  with the model family (and the automation flag), `conversation_finished`
  with `terminal_status` `finished` and the family as its last property.
  Neither the prompt nor the id (dashed or not) appears anywhere in what the
  endpoint received. The checks match each event's own property order, so
  they hold whether the two events arrive in one batch or two.
- **Unhandled errors (`F32.telemetry-request-failed`).** A conversation
  without a condenser makes `POST .../condense` raise an unhandled
  `ValueError` (500; a known bug of the condense family, used here only as
  a reliable unhandled exception). The header plays the browser's analytics
  identity.
  ```sh
  RFC=$(control-agent-server conversation start --body-json '{"agent_settings": {"agent_kind": "openhands", "llm": {"model": "openai/qa-placeholder", "api_key": "qa-placeholder", "base_url": "http://127.0.0.1:9/v1", "num_retries": 0}, "tools": [], "condenser": {"enabled": false}}}' --no-autotitle --no-run --prompt QA_F32_RF --print-id)
  ERR=$(control-agent-server api POST "/api/conversations/$RFC/condense" --header 'X-OpenHands-Telemetry-Distinct-Id: qa-f32-browser' --expect 500 --field error_id)
  test -n "$ERR"
  control-agent-server sink read --name qa-f32-telemetry --contains "\"error_id\": \"$ERR\"" --expect-min 1 --expect-max 1 --wait 15 --save F32.telemetry-request-failed/event
  control-agent-server sink read --name qa-f32-telemetry --contains '"event": "agent_server.request_failed", "distinct_id": "qa-f32-browser"' --expect-min 1 --expect-max 1
  control-agent-server sink read --name qa-f32-telemetry --contains '"route_template": "/api/conversations/{conversation_id}/condense", "method": "POST", "status_code": 500, "error_class": "ValueError"' --expect-min 1
  control-agent-server sink read --name qa-f32-telemetry --contains "$RFC" --expect-max 0
  control-agent-server logs --grep 'ValueError: Cannot condense' --expect-min 1
  control-agent-server sink read --name qa-f32-telemetry --contains 'Cannot condense' --expect-max 0
  ```
  One `request_failed` event carries the same `error_id` as the 500 body,
  the header's identity, the route template, method, status and
  `ValueError`; neither the conversation id nor the exception text (which
  the server log shows) reached the endpoint. If the condense route stops answering 500, find another
  unhandled exception: an `HTTPException` 5xx is not reported.
- **Token and cost buckets from real usage (`F32.telemetry-usage-buckets`), known bug.**
  The run above used tokens; its outcome event should say so.
  ```sh
  control-agent-server api GET "/api/conversations/$UCID" --check stats.usage_to_metrics.default.accumulated_token_usage.prompt_tokens gt 0
  control-agent-server sink read --name qa-f32-telemetry --contains '"total_tokens_bucket": "' --expect-min 1
  control-agent-server sink read --name qa-f32-telemetry --contains '"total_tokens_bucket": "unknown"' --expect-max 0 --save F32.telemetry-usage-buckets/outcome  # bug
  ```
  Expected: a token bucket such as `1000-10000`. Actual: `unknown` (and
  `cost_bucket` `unknown`), although the conversation's stats hold the
  tokens. See Gotchas.
- **Failed runs (`F32.telemetry-conversation-failed`).** The same
  unreachable model, this time with a run, so the conversation ends in
  `error` without a model call. The stored `ConversationErrorEvent` is the
  second view: its `code` must arrive and its `detail` must not.
  ```sh
  FAILID=$(control-agent-server conversation start --body-json '{"user_id": "qa-f32-failed", "agent_settings": {"agent_kind": "openhands", "llm": {"model": "openai/qa-placeholder", "api_key": "qa-placeholder", "base_url": "http://127.0.0.1:9/v1", "num_retries": 0}, "tools": [], "condenser": {"enabled": false}}}' --no-autotitle --prompt QA_F32_FAILED_RUN --wait --until error --timeout 120 --print-id)
  CODE=$(control-agent-server api GET "/api/conversations/$FAILID/events/search" --query kind=openhands.sdk.event.conversation_error.ConversationErrorEvent --field items.0.code)
  DETAIL=$(control-agent-server api GET "/api/conversations/$FAILID/events/search" --query kind=openhands.sdk.event.conversation_error.ConversationErrorEvent --field items.0.detail)
  test -n "$CODE"
  test -n "$DETAIL"
  control-agent-server sink read --name qa-f32-telemetry --contains '"event": "agent_server.conversation_failed", "distinct_id": "qa-f32-failed"' --expect-min 1 --expect-max 1 --wait 15 --save F32.telemetry-conversation-failed/failed
  control-agent-server sink read --name qa-f32-telemetry --contains '"terminal_status": "error"' --expect-min 1 --expect-max 1
  control-agent-server sink read --name qa-f32-telemetry --contains '"event": "agent_server.conversation_error", "distinct_id": "qa-f32-failed"' --expect-min 1 --expect-max 1 --wait 15 --save F32.telemetry-conversation-failed/error
  control-agent-server sink read --name qa-f32-telemetry --contains "\"error_class\": \"$CODE\"" --expect-min 1
  control-agent-server sink read --name qa-f32-telemetry --contains "$DETAIL" --expect-max 0
  control-agent-server sink read --name qa-f32-telemetry --contains QA_F32_FAILED_RUN --expect-max 0
  control-agent-server sink read --name qa-f32-telemetry --contains "${FAILID//-/}" --expect-max 0
  ```
  `conversation_failed` reports `terminal_status` `error`, and a
  `conversation_error` carries the stored event's `code` (a class name such
  as `LLMServiceUnavailableError`) as `error_class`; the stored `detail`
  (the provider's error text), the prompt and the conversation id never
  reach the endpoint.
- **Consent from settings (`F32.telemetry-consent-settings`).** Clear the
  environment's consent, then switch it with `PATCH /api/settings` as Agent
  Canvas does. The re-grant conversation is the positive control for the
  denied one: the sink's queue is first in, first out.
  ```sh
  control-agent-server restart --env OH_TELEMETRY_CONSENT=
  control-agent-server api PATCH /api/settings --json '{"misc_settings_diff": {"telemetry": {"consent": "granted"}}}' --expect 200 --check misc_settings.telemetry.consent eq granted --save F32.telemetry-consent-settings/grant
  control-agent-server api GET /api/settings --check misc_settings.telemetry.consent eq granted
  control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_GRANT --body-json '{"user_id": "qa-f32-granted"}' --no-autotitle
  control-agent-server sink read --name qa-f32-telemetry --contains '"distinct_id": "qa-f32-granted"' --expect-min 1 --wait 15
  control-agent-server api PATCH /api/settings --json '{"misc_settings_diff": {"telemetry": {"consent": "denied"}}}' --expect 200 --check misc_settings.telemetry.consent eq denied
  control-agent-server logs --grep 'Telemetry disabled \(settings\)' --expect-min 1
  control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_DENY --body-json '{"user_id": "qa-f32-denied"}' --no-autotitle
  control-agent-server api PATCH /api/settings --json '{"misc_settings_diff": {"telemetry": {"consent": "granted"}}}' --expect 200
  control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_REGRANT --body-json '{"user_id": "qa-f32-regranted"}' --no-autotitle
  control-agent-server sink read --name qa-f32-telemetry --contains '"distinct_id": "qa-f32-regranted"' --expect-min 1 --wait 15
  control-agent-server sink read --name qa-f32-telemetry --contains '"distinct_id": "qa-f32-denied"' --expect-max 0 --save F32.telemetry-consent-settings/denied
  control-agent-server restart
  control-agent-server logs --grep 'enabled=True \(settings\)' --expect-min 1
  control-agent-server sink read --name qa-f32-telemetry --contains agent_server.server_started --expect-min 3 --expect-max 3 --wait 15
  ```
  Granting took effect for the next conversation without a restart, denying
  stopped delivery at once (the denied conversation never arrived although
  a later one did), and after a restart the persisted `granted` alone
  enabled telemetry (`enabled=True (settings)`) and a third `server_started`.
  Consent granted at runtime does not emit `server_started` for the running
  process.
- **Environment override beats settings (`F32.telemetry-env-override`).**
  Settings still say `granted`.
  ```sh
  control-agent-server restart --env OH_TELEMETRY_CONSENT=denied --env OH_TELEMETRY_CONSENT_MODE=override
  control-agent-server logs --grep 'enabled=False \(env_override\)' --expect-min 1
  control-agent-server api GET /api/settings --check misc_settings.telemetry.consent eq granted
  control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_OVERRIDE --body-json '{"user_id": "qa-f32-override"}' --no-autotitle
  control-agent-server restart --env OH_TELEMETRY_CONSENT_MODE=seed
  control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_SEED --body-json '{"user_id": "qa-f32-seed"}' --no-autotitle
  control-agent-server sink read --name qa-f32-telemetry --contains '"distinct_id": "qa-f32-seed"' --expect-min 1 --wait 15
  control-agent-server sink read --name qa-f32-telemetry --contains '"distinct_id": "qa-f32-override"' --expect-max 0 --save F32.telemetry-env-override/nothing
  ```
  With `override`, `denied` won over the stored `granted` for a whole server
  lifetime (its shutdown flush included); as a mere `seed` the same value
  lost to the setting and the next conversation was delivered.
- **Kill switch (`F32.telemetry-kill-switch`).** `DO_NOT_TRACK` against an
  environment override that grants.
  ```sh
  control-agent-server restart --env DO_NOT_TRACK=1 --env OH_TELEMETRY_CONSENT=granted --env OH_TELEMETRY_CONSENT_MODE=override
  control-agent-server logs --grep 'enabled=False \(kill_switch\)' --expect-min 1
  control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_DNT --body-json '{"user_id": "qa-f32-dnt"}' --no-autotitle
  control-agent-server restart --env DO_NOT_TRACK=
  control-agent-server logs --grep 'enabled=True \(env_override\)' --expect-min 1
  control-agent-server conversation start --placeholder-agent --no-run --prompt QA_F32_AFTER_DNT --body-json '{"user_id": "qa-f32-after-dnt"}' --no-autotitle
  control-agent-server sink read --name qa-f32-telemetry --contains '"distinct_id": "qa-f32-after-dnt"' --expect-min 1 --wait 15
  control-agent-server sink read --name qa-f32-telemetry --contains '"distinct_id": "qa-f32-dnt"' --expect-max 0 --save F32.telemetry-kill-switch/nothing
  control-agent-server api PATCH /api/settings --json '{"misc_settings_diff": {"telemetry": {"consent": null}}}' --expect 200 --check misc_settings.telemetry.consent missing
  ```
  `DO_NOT_TRACK=1` won over `granted` + `override` (`kill_switch`); with it
  emptied the same override delivered the next conversation. The last call
  removes the stored consent again.
- **Pseudonym key and deployment kind (`F32.telemetry-salt-and-kind`).**
  The failed conversation of `F32.telemetry-conversation-failed` (the
  unreachable model: a run errors at once, without a model call) is run
  again in three server lifetimes; each run emits a `conversation_error`
  with its `conversation_ref`. The refs are recomputed here from the
  conversation id the way the server documents pseudonyms (`blake2s` of the
  id's 16 bytes, 16-byte digest, keyed by the salt, or by a 32-byte
  `blake2s` of a key longer than 32 bytes). The run's secret key is read
  from its private file inside the one-liner and never printed.
  ```sh
  FREF=$(python3 -c 'import hashlib, os, sys, uuid; k = open(os.environ["AGENT_SERVER_VERIFY_RUN"] + "/private/secret_key").read().strip().encode(); k = k if len(k) <= 32 else hashlib.blake2s(k, digest_size=32).digest(); print(hashlib.blake2s(uuid.UUID(sys.argv[1]).bytes, key=k, digest_size=16).hexdigest())' "$FAILID")
  SREF=$(python3 -c 'import hashlib, sys, uuid; print(hashlib.blake2s(uuid.UUID(sys.argv[1]).bytes, key=b"qa-f32-salt", digest_size=16).hexdigest())' "$FAILID")
  test "${#FREF}" -eq 32
  test "$FREF" != "$SREF"
  control-agent-server sink read --name qa-f32-telemetry --contains "\"conversation_ref\": \"$FREF\", \"terminal_status\": \"error\"" --expect-min 1 --expect-max 1
  control-agent-server api POST "/api/conversations/$FAILID/run" --expect 200 --quiet
  control-agent-server sink read --name qa-f32-telemetry --contains "\"deployment_kind\": \"local\", \"source\": \"openhands-agent-server\", \"conversation_ref\": \"$FREF\", \"error_class\"" --expect-min 2 --expect-max 2 --wait 30 --save F32.telemetry-salt-and-kind/secret-key-ref
  control-agent-server restart --env OH_TELEMETRY_SALT=qa-f32-salt --env OH_TELEMETRY_DEPLOYMENT_KIND=remote
  NL=$(control-agent-server sink read --name qa-f32-telemetry --contains '"deployment_kind": "local"' | python3 -c 'import json, sys; print(json.load(sys.stdin)["requests"])')
  test "$NL" -ge 1
  control-agent-server sink read --name qa-f32-telemetry --contains '"deployment_kind": "remote", "source": "openhands-agent-server"}}' --expect-min 1 --expect-max 1 --wait 15 --save F32.telemetry-salt-and-kind/started-remote
  control-agent-server api POST "/api/conversations/$FAILID/run" --expect 200 --quiet
  control-agent-server sink read --name qa-f32-telemetry --contains "\"deployment_kind\": \"remote\", \"source\": \"openhands-agent-server\", \"conversation_ref\": \"$SREF\", \"error_class\"" --expect-min 1 --expect-max 1 --wait 30 --save F32.telemetry-salt-and-kind/salted-ref
  control-agent-server restart
  control-agent-server api POST "/api/conversations/$FAILID/run" --expect 200 --quiet
  control-agent-server sink read --name qa-f32-telemetry --contains "\"deployment_kind\": \"remote\", \"source\": \"openhands-agent-server\", \"conversation_ref\": \"$SREF\", \"error_class\"" --expect-min 2 --expect-max 2 --wait 30 --save F32.telemetry-salt-and-kind/salted-ref-after-restart
  control-agent-server sink read --name qa-f32-telemetry --contains '"deployment_kind": "remote", "source": "openhands-agent-server"}}' --expect-min 3 --expect-max 3 --wait 15
  control-agent-server sink read --name qa-f32-telemetry --contains '"deployment_kind": "local"' --expect-max "$NL"
  control-agent-server sink read --name qa-f32-telemetry --contains "\"conversation_ref\": \"$FREF\", \"error_class\"" --expect-max 2
  ```
  Without a salt, the ref of the earlier failed run is the one keyed by the
  run's `OH_SECRET_KEY`, and a run several restarts later reports the same
  ref, tagged `deployment_kind` `local`. With `OH_TELEMETRY_SALT` the same
  conversation gets the ref keyed by the salt, before and after a further
  restart; the secret-key ref never appears again. From the salted restart
  on, every event is `remote` (both `server_started` events and the
  `server_stopped` between them, and the conversation errors) and no new
  `local` one arrived. The variables stay set for the rest of the run.
- **Salt description (`F32.telemetry-salt-docs`), known bug.** The
  `TelemetrySpec` schema (the type of `POST /api/init`'s `telemetry`)
  should describe the fallback the previous bullet proved: without a salt
  the server keys pseudonyms with its secret key.
  ```sh
  control-agent-server api GET /openapi.json --auth none --quiet --check 'components.schemas.InitRequest.properties.telemetry.anyOf.0.$ref' eq '#/components/schemas/TelemetrySpec' --check components.schemas.TelemetrySpec.properties.salt.description exists
  control-agent-server api GET /openapi.json --auth none --quiet --check components.schemas.TelemetrySpec.properties.salt.description matches '(?i)secret'  # bug
  ```
  Expected: the description names the secret-key fallback. Actual: "Falls
  back to a per-process random salt, which keeps pseudonyms stable within a
  run but unlinkable across runs", while `build_telemetry_sink` falls back
  to `OH_SECRET_KEY` first (a random salt only when there is no secret key
  either), so refs are linkable across restarts. If the code changes to
  match the description instead, the secret-key ref check of
  `F32.telemetry-salt-and-kind` fails and both bullets need updating. See
  Gotchas.
- **Deferred pod gets telemetry and webhooks from `/api/init` (`F32.deferred-init`).**
  A second, dormant server boots with telemetry already configured (to
  `/boot` on a sink of this run) and receives a different endpoint
  (`/init`) and a webhook in its init body.
  ```sh
  DTS=$(control-agent-server fixture http-sink --name qa-f32-deferred --print-path)
  DEFERRED=$(control-agent-server launch --new --deferred-init --name f32-deferred --env OH_TELEMETRY_EXPORTER=http --env "OH_TELEMETRY_HTTP_ENDPOINT=$DTS/boot" --env OH_TELEMETRY_CONSENT=granted --env OH_TELEMETRY_FLUSH_DELAY=1 --print-run)
  trap 'control-agent-server stop --run "$DEFERRED" >/dev/null' EXIT
  control-agent-server api GET /api/init --run "$DEFERRED" --auth none --check state eq dormant
  control-agent-server api POST /api/init --run "$DEFERRED" --auth init --json "{\"telemetry\": {\"exporter\": \"http\", \"http_endpoint\": \"$DTS/init\", \"flush_delay\": 1}, \"webhooks\": [{\"base_url\": \"$WH/qa-f32-dinit\", \"flush_delay\": 1}]}" --expect 200 --check state eq ready --save F32.deferred-init/init
  control-agent-server sink read --name qa-f32-deferred --path /init --contains agent_server.server_started --expect-min 1 --expect-max 1 --wait 15 --save F32.deferred-init/started
  control-agent-server sink read --name qa-f32-deferred --path /boot --expect-max 0
  XCID=$(control-agent-server conversation start --run "$DEFERRED" --placeholder-agent --no-run --prompt QA_F32_DEFERRED --body-json '{"user_id": "qa-f32-deferred"}' --no-autotitle --print-id)
  control-agent-server sink read --path "/qa-f32-dinit/events/${XCID//-/}" --contains QA_F32_DEFERRED --expect-min 1 --wait 15 --save F32.deferred-init/webhook
  control-agent-server sink read --path /qa-f32-dinit/conversations --contains "\"id\": \"$XCID\"" --expect-min 1
  control-agent-server sink read --name qa-f32-deferred --path /init --contains '"distinct_id": "qa-f32-deferred"' --expect-min 1 --wait 15
  control-agent-server stop --run "$DEFERRED"
  ```
  The dormant pod sent nothing to its boot endpoint (not even at init, when
  the boot sink was drained and replaced); after `POST /api/init` it sent
  exactly one `server_started` and the conversation's telemetry to the
  delivered endpoint, and its conversation's events and info to the
  delivered webhook. The second server is stopped (the `trap` also stops it
  if a command fails).
- **Deferred pods are labelled as such (`F32.deferred-init-flag`), known bug.**
  The events the initialized pod sent above should say it booted deferred.
  ```sh
  control-agent-server sink read --name qa-f32-deferred --path /init --contains agent_server.server_started --expect-min 1
  control-agent-server sink read --name qa-f32-deferred --path /init --contains '"deferred_init": ' --expect-min 1
  control-agent-server sink read --name qa-f32-deferred --path /init --contains '"deferred_init": true' --expect-min 1 --save F32.deferred-init-flag/events  # bug
  ```
  Expected: `deferred_init: true` in the runtime properties. Actual: `false`
  on every event, because `/api/init` builds the telemetry sink from the
  merged config, which clears `deferred_init`. See Gotchas.

## Gotchas

- **Field descriptions (`F32.webhook-spec-docs`).** Event batches go to
  `{base_url}/events/{conversation_id.hex}` (no dashes) and conversation
  info to `{base_url}/conversations`. The `WebhookSpec` field descriptions,
  which the OpenAPI document publishes for `POST /api/init`'s `webhooks`,
  are wrong twice: `base_url` says events go to `{base_url}/events`, and
  `flush_delay` says "Timer is reset on each new event", while
  `_start_flush_timer` only starts a timer when none is running (and
  `test_flush_delay_not_reset_on_new_event` asserts that);
  `F32.events-post` and `F32.flush-delay` prove the code. Documentation
  bug, trivial severity.
- The server forwards its own first session key to every webhook as
  `X-Session-API-Key` (event batches and conversation posts alike), so a
  receiver can call the server back. Point webhooks only at trusted
  receivers. `sink read` output and saved evidence redact it; the raw
  recording `<run>/fixtures/<sink>.jsonl` keeps it (`stop --purge-private`
  removes fixtures).
- Bodies are full `model_dump(mode="json")` events, null fields included,
  plus a transient `full_state` snapshot on every attach: at creation, at
  the end of each run, and whenever an idle or restarted conversation is
  loaded again (the events search loads it; `GET /api/conversations/{id}`
  answers from the catalog and does not). Snapshots include the agent's
  config with secrets masked (`**********`). Receivers should de-duplicate
  by event id and must not rely on delivery order: in every batch recorded
  while mapping this family, a `last_user_message_id` update was posted
  before the `MessageEvent` it names, although the event store (and
  `timestamp`) has the message first.
- **Stranded events (`F32.buffer-stranded`).** When the buffer fills, the
  batch is posted inline while `_post_lock` is held; an event published
  during that POST only starts the flush timer (`if
  self._post_lock.locked(): self._start_flush_timer(); return`), and
  `_post_events` only drains what was queued when it started. So events
  that arrive during an in-flight POST wait for `flush_delay` (30 s by
  default), the next burst, or the conversation's close, although the
  buffer is full. They are late, not lost. It happens on every
  conversation's first message: the `SystemPromptEvent` fills a buffer of
  2 and the message and its `last_user_message_id` update are published
  during that POST. The unit test
  `test_concurrent_event_delivery_has_one_request_in_flight` calls
  `close()` before counting, which hides it. Low severity (latency).
- **Slow receiver (`F32.slow-receiver`).** With a full buffer the POST runs
  inside the fan-out of that event, so its publish future stays pending
  while the receiver is slow (httpx timeout 30 s per attempt, plus
  `num_retries` × `retry_delay`). The run's `finally` waits up to 30 s for
  pending publishes (`wait_for_pending(30.0)` in `event_service.py`) before
  clearing its task, so `POST .../run` answers 409 `Conversation already
  running` for that long while `GET` already reports `finished`. A message
  sent with `run: true` in that window is re-armed instead. Medium-low
  severity: automations that chain runs see spurious 409s. With
  `event_buffer_size` 1 a slow receiver also costs the attach-time
  snapshot: its inline POST runs inside the 0.5 s initial-push timeout
  (`INITIAL_STATE_PUSH_TIMEOUT_SECONDS`), is cancelled (the log says
  `Initial state push ... timed out after 0.5s`) and is not retried.
- Conversation posts are fire-and-forget tasks (unordered, never awaited,
  no retained reference), so recipes poll for them. They are sent on
  create (a new id only), pause, interrupt, `PATCH` and `DELETE` (status
  forced to `deleting`); a run's completion is not posted there, only as
  `execution_status` events. The creation post reports the status at the
  time `POST /api/conversations` returns (`idle` even when an
  `initial_message` started a run).
- Limits: `max_batch_bytes` is a flush trigger as well as a cap. A queue
  whose serialized size reaches it is posted at once, like a full buffer,
  so `max_batch_bytes` 1 posts every event on arrival (subject to the
  stranding above). `max_queue_size` and `max_queue_bytes` drop the oldest
  events first, the new event included: an event larger than
  `max_queue_bytes` on its own is dropped as soon as it is queued and never
  delivered. The warning is logged once per webhook and conversation and
  then every 100 drops. Sizes measured while mapping, compact JSON: a
  placeholder conversation's `full_state` about 3 KB, its
  `SystemPromptEvent` about 18 KB, a short `MessageEvent` about 0.5 KB, a
  `last_user_message_id` update about 0.3 KB.
- Retries: `num_retries + 1` attempts, `retry_delay` seconds apart. A failed
  event batch is requeued (past `max_queue_size` or `max_queue_bytes` the
  oldest events are dropped with a warning) and re-sent on every timer
  flush, forever, while the conversation lives; a failed conversation post
  is logged and dropped. Telemetry does the opposite and drops failed
  batches. Delivery is at least once (a batch whose POST failed after the
  receiver read it is sent again), and the flush at delete or shutdown is a
  single cycle: if it fails, the queued events are gone.
- `OH_WEBHOOKS_0_*` variables merge field by field into the file's
  `webhooks[0]`; `restart --env` keeps them for later restarts until
  `--reset-config`, which also drops the launch's `--webhook-sink` entry.
- Telemetry is separate from LLM completion logs and from Laminar/OTel
  tracing. Defaults: exporter `none`, `event_buffer_size` 20, `flush_delay`
  30 s (the recipes set `OH_TELEMETRY_FLUSH_DELAY=1`). Consent precedence:
  `DO_NOT_TRACK` → `OH_TELEMETRY_CONSENT` with mode `override` →
  `misc_settings.telemetry.consent` → the legacy
  `misc_settings.app_preferences.user_consents_to_analytics` →
  `OH_TELEMETRY_CONSENT` as a seed → unset (off). The log line `Telemetry
  initialised: exporter=... enabled=... (<reason>)` is wrapped by the log
  formatter, so grep the `enabled=... (<reason>)` part.
- **Pseudonym key (`F32.telemetry-salt-and-kind`, `F32.telemetry-salt-docs`).**
  `conversation_ref` is `blake2s(conversation_id.bytes, key, digest_size=16)`
  where the key is `telemetry.salt` (`OH_TELEMETRY_SALT`), else
  `OH_SECRET_KEY` (a key over 32 bytes is first reduced with a 32-byte
  `blake2s`), else a random per-process value. A server with a session key
  and no explicit secret key uses the first session key as its secret key,
  so the random fallback only happens with neither. The refs are therefore
  stable across restarts and change with `restart --rotate-secret-key`. The
  `salt` description (also in the OpenAPI schema) promises the random
  fallback and "unlinkable across runs". Documentation bug, trivial
  severity; operators who rely on the description get linkable refs.
- A conversation reports one terminal outcome per subscriber: after a
  restart, a conversation whose stored status is already terminal is
  seeded as reported, so rerunning it emits `conversation_error` events but
  no second `conversation_failed` or `conversation_finished`.
- `server_started` is emitted once per process, at boot (or after a
  successful `/api/init`), and only if telemetry is enabled then; consent
  granted later does not emit it, and `server_stopped` only follows an
  emitted start. The anonymous `distinct_id` (`anon:<hex>`) is per process;
  a request's `user_id` passes through verbatim.
- **Usage buckets (`F32.telemetry-usage-buckets`).** The subscriber emits
  the outcome on the first terminal status it sees. A live run publishes
  the `execution_status` update (a bare string) before its final
  `full_state` snapshot, so `_capture_usage` never sees `stats` and
  `total_tokens_bucket` and `cost_bucket` are always `unknown`.
  `test_outcome_reports_real_bucketed_usage` only feeds `full_state`
  snapshots, so it passes. Low severity (analytics quality).
- **`deferred_init` label (`F32.deferred-init-flag`).**
  `_build_initialized_config` sets `deferred_init=False` and `/api/init`
  builds the telemetry sink, runtime properties included, from that merged
  config, and a dormant pod emits nothing in practice (its boot sink exists
  but `server_started` is withheld and its `/api/*` routes answer 503). So
  the property is `false` on every event a deferred pod sends; the README
  lists it among the runtime properties without defining it, so "booted
  deferred" is the reading this bullet assumes. Low severity (analytics
  quality).
- `request_failed` is emitted only for unhandled exceptions; a route that
  raises `HTTPException(500)` (for example the workspaces registry on an
  unreadable file) is not reported. `F32.telemetry-request-failed` borrows
  the condense route's unhandled `ValueError`, which is itself a known bug
  of the condense family.
- Not driven here: the `posthog` exporter (needs the `[posthog]` extra and a
  project key; without them it degrades to no-op with a log line).
- `launch` adds loopback hosts to `NO_PROXY`; httpx honors `HTTP(S)_PROXY`,
  so a server started another way may never reach a loopback sink.
- `sink read --contains` matches the JSON text of the whole recorded request
  (method, path, headers, body), with `", "` and `": "` separators, so
  two-field matches rely on key order (`"key": "execution_status", "value":
  "finished"`). The exact counts in this family assume a fresh run replayed
  in order; earlier deliveries on the same sink path change them. `--wait`
  only waits for `--expect-min`.
