# Insider Voice: conversation-bound calls and reasoning relay

Voice is a spoken interface to a saved Insider controller conversation. The
OpenHands agent owns tools, skills, approvals, work and durable answers. A
Realtime connection supplies audio; it must delegate each new user request to
that same controller and speak only the result of that request. Ending speech
does not cancel accepted OpenHands work. The optional Codex broker uses a
dedicated signed-in home and a disposable, restricted app-server process for
each call; the default OpenAI broker uses the server's API key.

Source: `openhands-agent-server/openhands/agent_server/voice_router.py`, `openhands-agent-server/openhands/agent_server/codex_voice.py`, `openhands-agent-server/openhands/agent_server/api.py`, `openhands-agent-server/openhands/agent_server/config.py`, `clients/typescript/src/client/voice-client.ts`

Routes: `GET /api/conversations/{conversation_id}/voice`,
`POST /api/conversations/{conversation_id}/voice/realtime`,
`GET /api/conversations/{conversation_id}/voice/realtime/{call_id}`,
`DELETE /api/conversations/{conversation_id}/voice/realtime/{call_id}`

## Sub-features

- `F35.availability`: an idle Insider controller reports provider, delegation owner, model, execution status and run activity without starting a call; without an OpenAI key it reports a specific unavailable reason and `Cache-Control: no-store`.
- `F35.auth`: all four Voice routes require the Agent Server session key, including call polling and deletion; rejected requests leave the controller's events unchanged.
- `F35.controller-only`: an ordinary conversation, an Insider worker and a child conversation cannot acquire Voice; a top-level legacy Insider without `insiderrole` remains eligible.
- `F35.not-found`: an unknown conversation is 404 on availability and creation, malformed conversation IDs are 422, and unknown calls are 404 on polling and deletion.
- `F35.offer-validation`: an SDP offer must be a nonempty string of at most 200000 characters and have no extra fields; a refused offer appends no controller event.
- `F35.missing-key`: a valid-shaped offer without the configured OpenAI key is refused with 409, leaving text conversation history and status unchanged.
- `F35.call-id-validation`: polling and deletion refuse invalid call IDs with 422, rather than forwarding arbitrary paths to a provider.
- `F35.codex-unavailable`: an isolated Codex broker without a usable dedicated sign-in reports `available: false`, a specific reason, `provider: codex` and `delegation: server`; a failed setup leaves no usable call behind.
- `F35.live-call`: an authorized real provider accepts a browser WebRTC offer, returns an SDP answer and audio, and the typed `VoiceClient` carries the same conversation ID through availability, creation, polling where supported, and ending.
- `F35.saved-context`: reopening Voice on an old or condensed controller uses its active saved history, including its condensation summary and final answers; abandoned branches and old instructions cannot become a new request.
- `F35.delegation`: every spoken request, including greetings and recall, produces one durable user message and one actual OpenHands run in the bound controller; verified terminal/file/API work precedes its spoken answer, and the full answer stays saved when speech is shortened.
- `F35.handoff-integrity`: duplicate Codex handoff IDs do not run twice; conflicting, invalid, busy or unaccepted requests are identified as not sent, while accepted failures or uncertain outcomes are not reported as success or retried silently.
- `F35.approval-boundary`: Voice never approves a pending tool and never treats a waiting, paused, error or timed-out controller as completed; the user can inspect and continue the same saved work through normal conversation controls.
- `F35.call-ownership-cleanup`: call IDs cannot be read or ended through another conversation; ending, losing polling or shutting down the server cleans up the call process and media, without splitting or cancelling an accepted append-and-run operation.

## How to get to it (agent POV)

- REST: `GET .../voice` returns `{available, run_active, execution_status,
  model, provider, delegation, reason}`. `POST .../voice/realtime` takes
  `{"sdp":"<WebRTC offer>"}` and returns `{sdp, call_id, model, provider,
  delegation}`. `GET .../voice/realtime/{call_id}` polls **Codex** call state
  (`listening`, `thinking`, `speaking`, `closed`, `error`), bounded transcripts
  and sanitized error codes. `DELETE` ends an owned call. OpenAI's live call
  state is observed on its WebRTC connection, not the Codex polling route.
- TypeScript: `VoiceClient.availability`, `createCall`, `getCallStatus`,
  `endCall` and `close`; the client accepts an `AbortSignal`. `close` releases
  HTTP-client resources; callers must explicitly end a live call. There is no
  Python `RemoteConversation` Voice wrapper.
- Configuration: `voice_provider` / `OH_VOICE_PROVIDER` selects `openai`
  (default) or `codex`; `codex_voice_home` / `OH_CODEX_VOICE_HOME` selects a
  dedicated home. OpenAI uses `OPENAI_API_KEY` from server environment or the
  secret store. LLM-profile subscription login (F24) does not by itself sign
  in the separate Codex Voice home or fund an OpenAI API call.
- Controller eligibility is `tags.smolpaws == "insider"`, no parent, and
  `tags.insiderrole` absent or `controller`. Creating the controller with the
  intended default tools, installed skills and backend runtime context belongs
  to Canvas/the Insider app. F21 covers profile materialization and launch
  additions, F25 skills, F10 condensation, and F04/F06 saved conversations.
  Verify those prerequisites too: Voice cannot repair a tool-less controller.
- OpenAI exposes `send_to_insider` and `end_voice_call` function calls to the
  browser relay. Codex uses client-managed handoffs delivered by its app-server;
  the Agent Server appends the request to the controller and waits for that
  run's new final answer. The disposable Codex turn is not the reasoning agent.
- Canvas/Insider owns microphone permission, mute, speaker Stop/Resume, cat
  animation, navigation continuity and visible conversation identity. A local
  speaker Stop is not a server cancellation endpoint. Exercise these alongside
  this family using OpenHands' `verify-openhands` skill.

## Driving it with control-agent-server

Preconditions:

- A fresh isolated run has passed `doctor`. The credential-free recipes below
  use its default OpenAI provider and empty secret store; they never run the
  placeholder agents or contact a provider. `jq` is available.
- Live audio recipes need a WebRTC-capable browser and authorized provider
  credentials. This CLI has no SDP/audio producer. The corresponding bullets
  remain explicitly blocked in an unattended API-only replay; drive them with
  the integrated Canvas browser, then record their actual result and artifact
  under the same feature ID. A stubbed provider or intercepted request proves
  only that transport/error path, never successful live speech or delegation.

```sh
CAT=$(control-agent-server conversation start --placeholder-agent --no-run --no-autotitle --tag smolpaws=insider --tag insiderrole=controller --title qa-voice-controller --print-id)
CAT_WORKSPACE=$(control-agent-server api GET "/api/conversations/$CAT" --field workspace.working_dir)
ORDINARY=$(control-agent-server conversation start --placeholder-agent --no-run --no-autotitle --title qa-voice-ordinary --print-id)
WORKER=$(control-agent-server conversation start --placeholder-agent --no-run --no-autotitle --tag smolpaws=insider --tag insiderrole=worker --print-id)
LEGACY=$(control-agent-server conversation start --placeholder-agent --no-run --no-autotitle --tag smolpaws=insider --print-id)
CHILD=$(control-agent-server conversation start --placeholder-agent --no-run --no-autotitle --workspace "$CAT_WORKSPACE" --tag smolpaws=insider --tag insiderrole=controller --body-json "{\"parent_conversation_id\":\"$CAT\"}" --print-id)
UNKNOWN=00000000-0000-4000-8000-000000000035
EVENTS_BEFORE=$(control-agent-server api GET "/api/conversations/$CAT/events/count" --field .)
```

- **Availability without a key (`F35.availability`).** This is also the
  positive authorization and controller-eligibility control.
  ```sh
  control-agent-server api GET "/api/conversations/$CAT/voice" --expect 200 --check available eq false --check provider eq openai --check delegation eq client --check reason eq missing_openai_api_key --check run_active eq false --check execution_status eq idle --check model matches '\S' --check-header cache-control eq no-store --save F35.availability/no-key
  control-agent-server api GET "/api/conversations/$CAT/events/count" --check . eq "$EVENTS_BEFORE"
  ```
- **Authentication on every operation (`F35.auth`).**
  ```sh
  for AUTH in none bad; do
    control-agent-server api GET "/api/conversations/$CAT/voice" --auth "$AUTH" --expect 401 --save F35.auth/availability
    control-agent-server api POST "/api/conversations/$CAT/voice/realtime" --json '{"sdp":"v=0"}' --auth "$AUTH" --expect 401 --save F35.auth/create
    control-agent-server api GET "/api/conversations/$CAT/voice/realtime/qa-missing" --auth "$AUTH" --expect 401 --save F35.auth/poll
    control-agent-server api DELETE "/api/conversations/$CAT/voice/realtime/qa-missing" --auth "$AUTH" --expect 401 --save F35.auth/end
  done
  control-agent-server api GET "/api/conversations/$CAT/events/count" --check . eq "$EVENTS_BEFORE"
  ```
- **Only the controller is eligible (`F35.controller-only`).**
  ```sh
  for ID in "$ORDINARY" "$WORKER" "$CHILD"; do
    control-agent-server api GET "/api/conversations/$ID/voice" --expect 409 --check detail contains 'Insider Cat controller' --save F35.controller-only/availability
    control-agent-server api POST "/api/conversations/$ID/voice/realtime" --json '{"sdp":"v=0"}' --expect 409 --check detail contains 'Insider Cat controller' --save F35.controller-only/create
  done
  control-agent-server api GET "/api/conversations/$LEGACY/voice" --expect 200 --check available eq false --save F35.controller-only/legacy
  ```
- **Unknown conversations and calls (`F35.not-found`).**
  ```sh
  control-agent-server api GET "/api/conversations/$UNKNOWN/voice" --expect 404 --save F35.not-found/conversation
  control-agent-server api POST "/api/conversations/$UNKNOWN/voice/realtime" --json '{"sdp":"v=0"}' --expect 404 --save F35.not-found/create
  control-agent-server api GET /api/conversations/not-a-uuid/voice --expect 422 --save F35.not-found/invalid-conversation
  control-agent-server api GET "/api/conversations/$CAT/voice/realtime/qa-missing" --expect 404 --save F35.not-found/poll
  control-agent-server api DELETE "/api/conversations/$CAT/voice/realtime/qa-missing" --expect 404 --save F35.not-found/end
  ```
- **Validate the offer before contacting a provider (`F35.offer-validation`).**
  ```sh
  for BODY in '{}' '{"sdp":""}' '{"sdp":42}' '{"sdp":"v=0","model":"other"}'; do
    control-agent-server api POST "/api/conversations/$CAT/voice/realtime" --json "$BODY" --expect 422 --save F35.offer-validation/rejected
  done
  jq -nc '{sdp: ("x" * 200001)}' | control-agent-server api POST "/api/conversations/$CAT/voice/realtime" --stdin --expect 422 --quiet --save F35.offer-validation/too-long
  control-agent-server api GET "/api/conversations/$CAT/events/count" --check . eq "$EVENTS_BEFORE"
  ```
- **A missing API key preserves text work (`F35.missing-key`).**
  ```sh
  control-agent-server api POST "/api/conversations/$CAT/voice/realtime" --json '{"sdp":"v=0"}' --expect 409 --check detail contains OPENAI_API_KEY --save F35.missing-key/create
  control-agent-server api GET "/api/conversations/$CAT/events/count" --check . eq "$EVENTS_BEFORE"
  control-agent-server api GET "/api/conversations/$CAT" --quiet --check execution_status eq idle --check tags.smolpaws eq insider --save F35.missing-key/controller
  ```
- **Validate call IDs (`F35.call-id-validation`).**
  ```sh
  for METHOD in GET DELETE; do
    control-agent-server api "$METHOD" "/api/conversations/$CAT/voice/realtime/invalid!" --expect 422 --save F35.call-id-validation/invalid
  done
  control-agent-server api GET "/api/conversations/$CAT/events/count" --check . eq "$EVENTS_BEFORE"
  ```
- **An unsigned dedicated Codex home (`F35.codex-unavailable`).** The same
  private run keeps its conversations but changes provider. This does not
  import the user's ordinary Codex home or credentials.
  ```sh
  control-agent-server restart --config-json '{"voice_provider":"codex"}'
  control-agent-server doctor
  control-agent-server api GET "/api/conversations/$CAT/voice" --expect 200 --check available eq false --check provider eq codex --check delegation eq server --check reason matches '^codex_(not_installed|not_signed_in|unavailable)$' --check-header cache-control eq no-store --save F35.codex-unavailable/availability
  control-agent-server api POST "/api/conversations/$CAT/voice/realtime" --json '{"sdp":"v=0"}' --expect 502 --check detail matches '\S' --save F35.codex-unavailable/create
  control-agent-server api GET "/api/conversations/$CAT/events/count" --check . eq "$EVENTS_BEFORE"
  control-agent-server restart --config-json '{"voice_provider":"openai"}'
  control-agent-server doctor
  ```
- **Real WebRTC and typed client (`F35.live-call`), blocked in API-only replay.**
  Prerequisite: browser SDP/audio, provider credentials and (for Codex) its
  compatible binary and dedicated sign-in. Use the app to create a call from
  `VoiceClient`; record availability, offer/answer metadata without secret SDP,
  received audio counters and sanitized broker status. Poll only Codex calls.
  End through the client, then verify that polling the ended Codex call is 404.
  A returned SDP alone does not prove audible speech.
- **Resume active saved history (`F35.saved-context`), blocked in API-only replay.**
  Prerequisite: the live browser call above. Save a harmless unique fact by
  text, end Voice, condense through F10, reopen the same conversation and ask
  aloud for that fact. Verify a fresh durable request and answer on that same
  ID and a spoken answer consistent with the saved result. Repeat after
  navigating away and reopening. Do not count speech from old context alone.
- **Delegate real work (`F35.delegation`), blocked in API-only replay.**
  Prerequisite: live audio and a normal OpenHands profile with usable terminal,
  file tools and enabled skills. First ask a greeting; then ask for a count of
  this isolated backend's conversations. Inspect saved events for exactly one
  user message per request, actual tool use for the API query, and a new final
  answer before answer audio. Compare the count with authenticated
  `GET /api/conversations/count`; include any conversations the test itself
  created. Continue by text in the regular view and verify the ID is unchanged.
- **Reject duplicates and unaccepted work (`F35.handoff-integrity`), blocked in API-only replay.**
  Prerequisite: a controlled Codex protocol driver in addition to the real
  happy-path call. Replay an identical handoff ID, a conflicting transcript,
  an empty transcript and a second request while the first is running. Compare
  durable event counts and saved answers: no duplicate run, no silent retry,
  no earlier answer offered as the new result. Label protocol-fixture evidence
  separately from real provider speech; do not inject fixtures into a shared
  server. The current API CLI does not inject upstream handoff frames.
- **Keep approvals pending (`F35.approval-boundary`), blocked in API-only replay.**
  Prerequisite: live audio and a controller configured for tool approval.
  Request a harmless workspace command, inspect the pending confirmation in
  the full conversation, and verify no resulting file or completed action
  exists until explicit approval through that conversation. Speech must not
  claim completion. Repeat with a failed or paused run; inspect its saved
  status and error instead of treating an acknowledgement as an answer.
- **Binding and cleanup (`F35.call-ownership-cleanup`), blocked in API-only replay.**
  Prerequisite: two controllers and a live call. Poll and delete its call ID
  under the other controller: both must return 404 while the original call
  still works. Start bounded workspace work, end Voice immediately after its
  request is saved, and verify that the original run can finish. Reopen Voice
  on that controller; no old handoff is replayed. Separately stop polling for
  more than the broker's 90-second idle limit and verify call process cleanup;
  server shutdown must also stop its own relay. Neither action should affect
  ordinary Codex sessions or unrelated conversations.

## Gotchas

- The Codex prototype currently accepts app-server version `0.154.0` exactly.
  A newer installed binary can therefore report `codex_unavailable` even with
  valid authentication. That is an availability finding, not evidence that
  ChatGPT subscription Voice or the user's account is unavailable.
- The broker rejects its ordinary Codex home and any enabled MCP server. Its
  dedicated home and runtime must be provisioned deliberately; never copy
  tokens into map commands, screenshots or public evidence.
- The Agent Server's shared exception handler can replace a 5xx `detail`
  with `Internal Server Error`; assert the 502 status for setup failure and
  use the availability route's reason for the supported diagnostic contract.
- `run_active` tracks the actual run lifetime, not just a transient `finished`
  status event. The relay must wait for completion before speaking success.
- API-only passes do not prove microphone permission, playback, provider
  access, delegation fidelity, navigation continuity or mobile Safari. Keep
  each result scoped to the entry point actually exercised. The browser and
  API evidence can share feature IDs without pretending one proves the other.
- Explicit condensation must not advance the agent or execute pending tools
  (F10). End an active Voice call before condensing, then start a fresh call
  to obtain the new bounded saved-context snapshot.
- Call ownership is process-local and ephemeral; conversation history is
  durable. After a server restart, reestablish Voice on the saved controller,
  rather than trying to resume an old call ID.
