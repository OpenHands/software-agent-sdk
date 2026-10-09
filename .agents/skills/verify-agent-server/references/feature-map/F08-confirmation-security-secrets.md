# Confirmation policy, security analyzer and conversation secrets

The runtime safety and credential knobs of a live conversation. An agent's
host decides when actions need a human's approval (`confirmation_policy`:
`NeverConfirm`, `AlwaysConfirm`, `ConfirmRisky` with a risk threshold),
which analyzer rates each action's risk (`security_analyzer`, or none), and
answers a parked action with accept or reject
(`events/respond_to_confirmation`). It also hands the conversation named
secrets at runtime (`secrets`): a terminal command that mentions a secret's
name gets it as an environment variable, its value is masked as
`<secret-hidden>` in observations and in what the model sees, and it is
encrypted at rest. Start-time `confirmation_policy`, `security_analyzer` and
`secrets` fields of `POST /api/conversations` belong to the lifecycle family;
`POST .../run` (how the Python SDK accepts) belongs to the run family.

Source: `openhands-agent-server/openhands/agent_server/conversation_router.py`, `openhands-agent-server/openhands/agent_server/event_router.py`, `openhands-agent-server/openhands/agent_server/event_service.py`, `openhands-agent-server/openhands/agent_server/models.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/conversation/secret_registry.py`, `openhands-sdk/openhands/sdk/conversation/state.py`, `openhands-sdk/openhands/sdk/security/`, `openhands-sdk/openhands/sdk/secret/secrets.py`, `openhands-agent-server/openhands/agent_server/local_secret_resolver.py`, `openhands-sdk/openhands/sdk/agent/agent.py`, `openhands-tools/openhands/tools/terminal/impl.py`

Needs: `llm`, `tmux`

Routes: `POST /api/conversations/{conversation_id}/events/respond_to_confirmation`,
`POST /api/conversations/{conversation_id}/secrets`,
`POST /api/conversations/{conversation_id}/confirmation_policy`,
`POST /api/conversations/{conversation_id}/security_analyzer`

## Sub-features

- `F08.policy-set`: `POST .../confirmation_policy` switches between `AlwaysConfirm`, `ConfirmRisky` (with `threshold` and `confirm_unknown`) and `NeverConfirm`; GET, `base_state.json` and a `confirmation_policy` state update on the events socket reflect each change at once.
- `F08.policy-errors`: an unknown policy kind, a `threshold` of `UNKNOWN` or an unknown level, and a missing or empty `policy` are 422; an unknown conversation is 404; no key is 401; the stored policy is unchanged.
- `F08.always-confirm-wait`: after a runtime switch to `AlwaysConfirm`, the agent's next terminal action parks the conversation in `waiting_for_confirmation` (pushed on the socket) with the `ActionEvent` recorded, no observation, and the command not run.
- `F08.confirm-accept`: `accept: true` runs the pending command (observation and file on disk) and the run continues to `finished`.
- `F08.confirm-reject`: `accept: false` records one `UserRejectObservation` with the given reason (REST and socket) and returns to `idle` without running the command; a second reject is a no-op, and a later accept only resumes the agent.
- `F08.confirm-errors`: a missing or non-boolean `accept` and a malformed id are 422; an unknown conversation is 404; no key is 401.
- `F08.confirm-capacity`: with the server's run capacity full, accept is 429 and leaves the action pending and unrun; reject still works; accept on a running conversation is a 200 no-op.
- `F08.confirm-real`: DeepSeek flash under `AlwaysConfirm` stops before its terminal command and finishes the task once its actions are accepted.
- `F08.analyzer-set-clear`: `POST .../security_analyzer` sets `PatternSecurityAnalyzer` or `LLMSecurityAnalyzer` and clears it with `null`; GET, `base_state.json` and the socket reflect each change.
- `F08.analyzer-errors`: a missing `security_analyzer` field and an unknown kind are 422; an unknown conversation is 404; no key is 401; the stored analyzer is unchanged.
- `F08.confirm-risky`: under `ConfirmRisky` with `PatternSecurityAnalyzer`, a low-risk command runs unasked and `rm -rf` waits for confirmation; once the analyzer is cleared, an unrated command waits too.
- `F08.secrets-update`: `POST .../secrets` accepts plain strings, `{"value": ...}` and `StaticSecret` objects (200); once the state is saved, GET lists the names with masked values and `base_state.json` holds them encrypted, never in plaintext.
- `F08.secrets-visible`: a secret posted at runtime is listed by GET right away, without waiting for another state change.
- `F08.secrets-errors`: a missing or non-object `secrets`, an unknown secret kind and a `LookupSecret` without `url` are 422; an unknown conversation is 404; no key is 401; nothing is stored.
- `F08.secrets-bad-value`: a number, `null` or a boolean as a secret value is refused with 422, not a 500.
- `F08.secrets-terminal`: a terminal command that names a runtime secret receives its value, while the observation and the next request to the model show `<secret-hidden>` and the plaintext is stored nowhere.
- `F08.secrets-override`: a runtime secret replaces a start-time secret of the same name for the next command.
- `F08.secrets-lookup`: a runtime `LookupSecret` is fetched from its URL with its headers only when a command names it; the command gets the fetched text, the observation and the model see `<secret-hidden>`, and GET hides the `Authorization` header that `base_state.json` keeps encrypted.
- `F08.secrets-lookup-self`: a runtime `LookupSecret` whose URL is the server's own `/api/settings/secrets/<name>` (hostless, made absolute from `OH_INTERNAL_SERVER_URL`, or absolute on the run's address) is answered in process from the secrets store with no HTTP request and no session key; a name the store lacks falls through to HTTP (401 without a key) and the command gets no value.
- `F08.secrets-profile-scope`: for a conversation launched from an Agent Profile with `secret_refs`, names outside the list are silently dropped (200).
- `F08.secrets-no-cipher`: on a server with no cipher (no `OH_SECRET_KEY`, no session key) a runtime secret reaches commands while the conversation lives, is persisted only as the redaction placeholder (the server warns at startup), and is empty after a restart although GET still lists the name.
- `F08.secrets-restart`: a runtime secret that reached `base_state.json` is still injected into commands after a server restart.
- `F08.secrets-unsaved-restart`: a runtime secret survives a restart even when nothing else changed in the conversation after it was posted.

## How to get to it (agent POV)

- REST: `POST /api/conversations/{conversation_id}/confirmation_policy`
  (body `SetConfirmationPolicyRequest`: `{"policy": {"kind": "AlwaysConfirm"}}`,
  `{"kind": "NeverConfirm"}` or `{"kind": "ConfirmRisky", "threshold": "LOW|MEDIUM|HIGH", "confirm_unknown": true}`),
  `POST .../security_analyzer` (`{"security_analyzer": {"kind": "PatternSecurityAnalyzer"}}`
  or `null`; other kinds: `LLMSecurityAnalyzer`, `PolicyRailSecurityAnalyzer`,
  `EnsembleSecurityAnalyzer`, `ToolShieldLLMSecurityAnalyzer`, `GraySwanAnalyzer`),
  `POST .../secrets` (`{"secrets": {"NAME": "value" | {"value": "..."} | {"kind": "StaticSecret", "value": "..."} | {"kind": "LookupSecret", "url": "...", "headers": {}}}}`),
  where a `LookupSecret` `url` may be hostless (`/api/settings/secrets/NAME`,
  joined onto `OH_INTERNAL_SERVER_URL`, the server's own address),
  and `POST .../events/respond_to_confirmation` (`{"accept": true}` or
  `{"accept": false, "reason": "..."}`). All four answer `{"success": true}`.
- Second views: `GET /api/conversations/{id}` (`execution_status`,
  `confirmation_policy`, `security_analyzer`, `secret_registry.secret_sources`
  with masked values), the event history (`ActionEvent`, `ObservationEvent`,
  `UserRejectObservation`, `ConversationStateUpdateEvent` keys
  `execution_status`, `confirmation_policy`, `security_analyzer`), and the
  conversation's `base_state.json` on disk.
- WebSocket (owned by the events family): `/sockets/events/{conversation_id}`
  pushes the state updates above and the `UserRejectObservation` live.
- Start-time fields of `POST /api/conversations` (`confirmation_policy`,
  `security_analyzer`, `secrets`) are stored in `meta.json` and re-applied on
  every reload; the runtime routes here change only the live state.
- SDK (`openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py`):
  `RemoteConversation.set_confirmation_policy()`, `set_security_analyzer()`,
  `update_secrets()` (serializes `SecretSource` objects, calls callables), and
  `reject_pending_actions(reason)` (`accept: false`). The SDK accepts by
  calling `run()` (`POST .../run`); `respond_to_confirmation` with
  `accept: true` does the same thing server-side.
- TypeScript client: `ConversationClient.setConfirmationPolicy`,
  `setSecurityAnalyzer`, `updateSecrets`, `respondToConfirmation`
  (`clients/typescript/src/client/conversation-client.ts`) and
  `RemoteConversation.setConfirmationPolicy`, `setSecurityAnalyzer`,
  `updateSecrets` (always sends `StaticSecret` objects) and
  `sendConfirmationResponse(accept, reason)`
  (`clients/typescript/src/conversation/remote-conversation.ts`).
- Agent Canvas (context only): the confirmation-mode toggle and the
  approve/reject buttons on a pending action call these routes.
- Recipes below use REST through `api`, the socket through `ws start`/`ws stop`,
  scripted terminal actions from `fixture llm-stub` (one stub per scenario,
  because a stub's step counter never resets), `fixture http-sink` as the
  URL a `LookupSecret` points at, the settings secrets store
  (`PUT /api/settings/secrets`) as the target of a self-referencing
  `LookupSecret`, two short-lived second servers (one with
  `max_concurrent_runs` 1, one without a cipher), and DeepSeek flash for the
  real-model confirmation path.

## Driving it with control-agent-server

Preconditions:

- A run of this checkout, `doctor` ok, `$DEEPSEEK_API_KEY` set; the block
  below saves the DeepSeek profiles.
- `tmux` is installed: every scripted agent here has the `terminal` tool and
  nothing else (plus the built-in `finish` and `think`).
- The block below creates, in order: the workspace root `$W` with one folder
  per scenario (the `qa-f08-victim` folders are what `rm -rf` must not
  delete), an unknown id `$NOPE`, an offline inline agent `$OFFLINE_AGENT`
  (never run), and one scripted `llm-stub` agent per scenario. Each stub
  answers its conversation's model calls in order and repeats its last step:
  `$ACCEPT_AGENT` (write `qa-f08-accepted.txt`, finish), `$REJECT_AGENT`
  (`rm -rf qa-f08-victim`, finish), `$RISKY_AGENT` (a low-risk `echo`,
  `rm -rf qa-f08-victim`, an `echo`, finish), `$SECRET_AGENT` (write
  `$QA_F08_TOKEN` to a file and echo it, finish, twice), `$ROTATE_AGENT`
  (write `$QA_F08_ROT` to a file, finish), `$LOOKUP_AGENT` (write
  `$QA_F08_LOOKUP` to a file and echo it, finish) and `$SELF_AGENT` (write
  `$QA_F08_HOSTLESS`, `$QA_F08_ABSOLUTE` and `$QA_F08_ABSENT` to three files
  and echo the first, finish). The
  no-cipher bullet makes its own stub on its own server. Last, it creates
  `$KID`, an idle conversation on the offline agent that the knob, error and
  secret-form bullets share.
- Every `known bug` bullet uses only these variables and arranges the rest
  itself (including its own `restart`), so
  `map run --file F08 --only <ID> --fresh` reproduces it alone, as fix
  verification needs.

```sh
control-agent-server llm preset deepseek
W="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f08"
mkdir -p "$W/knobs" "$W/accept" "$W/reject/qa-f08-victim" "$W/risky/qa-f08-victim" "$W/secret" "$W/rotate" \
  "$W/lookup" "$W/self" "$W/scoped" "$W/nocipher" "$W/real"
NOPE=$(python3 -c 'import uuid; print(uuid.uuid4())')
OFFLINE_AGENT='{"llm": {"model": "openai/qa-offline", "api_key": "qa-offline-key", "base_url": "http://127.0.0.1:9/v1", "num_retries": 0}, "tools": []}'
MSG='{"role": "user", "content": [{"type": "text", "text": "Run the command."}]}'
stub_agent() {
  printf '{"llm": {"model": "openai/qa-stub", "api_key": "qa-stub-key", "base_url": "%s/v1", "num_retries": 0}, "tools": [{"name": "terminal"}]}' \
    "$(control-agent-server fixture llm-stub --name "$@" --print-path)"
}
FINISH='tool:finish:{"message":"QA_F08_DONE"}'
ACCEPT_AGENT=$(stub_agent qa-f08-accept --step 'tool:terminal:{"command":"echo qa-accepted > qa-f08-accepted.txt"}' --step "$FINISH")
REJECT_AGENT=$(stub_agent qa-f08-reject --step 'tool:terminal:{"command":"rm -rf qa-f08-victim"}' --step "$FINISH")
RISKY_AGENT=$(stub_agent qa-f08-risky --step 'tool:terminal:{"command":"echo qa-low > qa-f08-low.txt"}' \
  --step 'tool:terminal:{"command":"rm -rf qa-f08-victim"}' --step 'tool:terminal:{"command":"echo qa-unrated > qa-f08-unrated.txt"}' --step "$FINISH")
SECRET_STEP='tool:terminal:{"command":"printf %s \"$QA_F08_TOKEN\" > qa-f08-token.txt; echo \"token=$QA_F08_TOKEN\""}'
SECRET_AGENT=$(stub_agent qa-f08-secret --step "$SECRET_STEP" --step "$FINISH" --step "$SECRET_STEP" --step "$FINISH")
ROTATE_STEP='tool:terminal:{"command":"printf %s \"$QA_F08_ROT\" > qa-f08-rot.txt"}'
ROTATE_AGENT=$(stub_agent qa-f08-rotate --step "$ROTATE_STEP" --step "$FINISH")
LOOKUP_STEP='tool:terminal:{"command":"printf %s \"$QA_F08_LOOKUP\" > qa-f08-lookup.txt; echo \"lookup=$QA_F08_LOOKUP\""}'
LOOKUP_AGENT=$(stub_agent qa-f08-lookup --step "$LOOKUP_STEP" --step "$FINISH")
SELF_STEP='tool:terminal:{"command":"printf %s \"$QA_F08_HOSTLESS\" > qa-f08-hostless.txt; printf %s \"$QA_F08_ABSOLUTE\" > qa-f08-absolute.txt; printf %s \"$QA_F08_ABSENT\" > qa-f08-absent.txt; echo \"self=$QA_F08_HOSTLESS\""}'
SELF_AGENT=$(stub_agent qa-f08-self --step "$SELF_STEP" --step "$FINISH")
REJECT_KIND=openhands.sdk.event.llm_convertible.observation.UserRejectObservation
OBSERVATION_KIND=openhands.sdk.event.llm_convertible.observation.ObservationEvent
KID=$(control-agent-server api POST /api/conversations \
  --json "{\"workspace\": {\"working_dir\": \"$W/knobs\"}, \"agent\": $OFFLINE_AGENT, \"autotitle\": false}" --expect 201 --field id)
```

- **Set the confirmation policy (`F08.policy-set`).** The idle `$KID`
  starts with `NeverConfirm` and no analyzer; a socket capture records each
  change.
  ```sh
  control-agent-server api GET "/api/conversations/$KID" --check confirmation_policy.kind eq NeverConfirm --check security_analyzer eq null
  control-agent-server ws start "/sockets/events/$KID" --name qa-f08-policy --duration 120
  control-agent-server api POST "/api/conversations/$KID/confirmation_policy" --json '{"policy": {"kind": "AlwaysConfirm"}}' \
    --expect 200 --check success eq true --save F08.policy-set/always
  control-agent-server api GET "/api/conversations/$KID" --check confirmation_policy.kind eq AlwaysConfirm
  control-agent-server state cat "server/workspace/conversations/${KID//-/}/base_state.json" --max-chars 200 \
    --check confirmation_policy.kind eq AlwaysConfirm
  control-agent-server api POST "/api/conversations/$KID/confirmation_policy" \
    --json '{"policy": {"kind": "ConfirmRisky", "threshold": "MEDIUM", "confirm_unknown": false}}' --expect 200
  control-agent-server api GET "/api/conversations/$KID" --check confirmation_policy.kind eq ConfirmRisky \
    --check confirmation_policy.threshold eq MEDIUM --check confirmation_policy.confirm_unknown eq false --save F08.policy-set/risky
  control-agent-server api POST "/api/conversations/$KID/confirmation_policy" --json '{"policy": {"kind": "NeverConfirm"}}' --expect 200
  control-agent-server api GET "/api/conversations/$KID" --check confirmation_policy.kind eq NeverConfirm
  control-agent-server ws read qa-f08-policy --kinds ConversationStateUpdateEvent --contains '"threshold": "MEDIUM"' --wait 10
  control-agent-server ws stop qa-f08-policy --kinds ConversationStateUpdateEvent --contains '"key": "confirmation_policy"' \
    --expect-min 3 --save F08.policy-set/socket
  control-agent-server conversation events "$KID" --key confirmation_policy --contains ConfirmRisky
  ```
  Each POST is 200 `{"success": true}`, the next GET and `base_state.json`
  show the new policy (`ConfirmRisky` keeps `threshold` `MEDIUM` and
  `confirm_unknown` `false`), and the socket pushed one `confirmation_policy`
  state update per change (three), the `ConfirmRisky` one with its
  threshold; the event history has them too.
- **Policy errors (`F08.policy-errors`).** Bad bodies, an unknown id, no key.
  ```sh
  control-agent-server api POST "/api/conversations/$KID/confirmation_policy" --json '{"policy": {"kind": "SometimesConfirm"}}' \
    --expect 422 --check detail.0.msg contains 'Unknown kind' --save F08.policy-errors/unknown-kind
  control-agent-server api POST "/api/conversations/$KID/confirmation_policy" --json '{"policy": {"kind": "ConfirmRisky", "threshold": "UNKNOWN"}}' \
    --expect 422 --check detail.0.msg contains 'cannot be UNKNOWN'
  control-agent-server api POST "/api/conversations/$KID/confirmation_policy" --json '{"policy": {"kind": "ConfirmRisky", "threshold": "EXTREME"}}' \
    --expect 422 --check detail.0.loc contains threshold
  control-agent-server api POST "/api/conversations/$KID/confirmation_policy" --json '{}' --expect 422 --check detail.0.loc contains policy
  control-agent-server api POST "/api/conversations/$KID/confirmation_policy" --json '{"policy": {}}' --expect 422
  control-agent-server api POST "/api/conversations/$NOPE/confirmation_policy" --json '{"policy": {"kind": "AlwaysConfirm"}}' --expect 404
  control-agent-server api POST "/api/conversations/$KID/confirmation_policy" --json '{"policy": {"kind": "AlwaysConfirm"}}' --auth none --expect 401
  control-agent-server api GET "/api/conversations/$KID" --check confirmation_policy.kind eq NeverConfirm
  ```
  Every status matches and the policy is still `NeverConfirm`.
- **Wait for confirmation (`F08.always-confirm-wait`).** A runtime switch to
  `AlwaysConfirm`, then a message; the socket is captured from before the
  message.
  ```sh
  AID=$(control-agent-server api POST /api/conversations \
    --json "{\"workspace\": {\"working_dir\": \"$W/accept\"}, \"agent\": $ACCEPT_AGENT, \"autotitle\": false}" --expect 201 --field id)
  control-agent-server api POST "/api/conversations/$AID/confirmation_policy" --json '{"policy": {"kind": "AlwaysConfirm"}}' --expect 200
  control-agent-server ws start "/sockets/events/$AID" --name qa-f08-wait --duration 120
  control-agent-server conversation send "$AID" --text 'Run the command.' --wait --until waiting_for_confirmation --timeout 120
  control-agent-server api GET "/api/conversations/$AID" --check execution_status eq waiting_for_confirmation \
    --check confirmation_policy.kind eq AlwaysConfirm --save F08.always-confirm-wait/waiting
  control-agent-server conversation events "$AID" --kinds ActionEvent --contains qa-f08-accepted.txt
  control-agent-server api GET "/api/conversations/$AID/events/count" --query kind="$OBSERVATION_KIND" --check . eq 0
  test ! -e "$W/accept/qa-f08-accepted.txt"
  control-agent-server ws stop qa-f08-wait --kinds ConversationStateUpdateEvent --contains waiting_for_confirmation --save F08.always-confirm-wait/socket
  ```
  The conversation waits with the terminal `ActionEvent` recorded, no
  observation and no file; the socket pushed `execution_status`
  `waiting_for_confirmation`.
- **Accept (`F08.confirm-accept`).** Approve the pending command.
  ```sh
  control-agent-server api POST "/api/conversations/$AID/events/respond_to_confirmation" --json '{"accept": true}' \
    --expect 200 --check success eq true --save F08.confirm-accept/accept
  control-agent-server conversation wait "$AID" --until finished --timeout 120
  test "$(cat "$W/accept/qa-f08-accepted.txt")" = qa-accepted
  control-agent-server conversation events "$AID" --kinds ObservationEvent --contains qa-f08-accepted.txt
  control-agent-server api GET "/api/conversations/$AID/agent_final_response" --check response eq QA_F08_DONE --save F08.confirm-accept/final
  ```
  The command ran (the file holds `qa-accepted`, the terminal observation is
  recorded) and the agent finished with the stub's `finish` message.
- **Reject (`F08.confirm-reject`).** A conversation created with
  `AlwaysConfirm` in the start body proposes `rm -rf qa-f08-victim`.
  ```sh
  RJ=$(control-agent-server api POST /api/conversations \
    --json "{\"workspace\": {\"working_dir\": \"$W/reject\"}, \"agent\": $REJECT_AGENT, \"autotitle\": false, \"confirmation_policy\": {\"kind\": \"AlwaysConfirm\"}, \"initial_message\": $MSG}" \
    --expect 201 --field id)
  control-agent-server conversation wait "$RJ" --until waiting_for_confirmation --timeout 120
  control-agent-server ws start "/sockets/events/$RJ" --name qa-f08-reject --duration 120
  control-agent-server api POST "/api/conversations/$RJ/events/respond_to_confirmation" \
    --json '{"accept": false, "reason": "QA_F08 rejected"}' --expect 200 --save F08.confirm-reject/reject
  control-agent-server conversation wait "$RJ" --until idle --timeout 60
  test -d "$W/reject/qa-f08-victim"
  control-agent-server api GET "/api/conversations/$RJ/events/search" --query kind="$REJECT_KIND" \
    --check items len-eq 1 --check items.0.rejection_reason eq 'QA_F08 rejected' --check items.0.tool_name eq terminal \
    --save F08.confirm-reject/observation
  control-agent-server ws stop qa-f08-reject --kinds UserRejectObservation --contains 'QA_F08 rejected' --save F08.confirm-reject/socket
  control-agent-server api POST "/api/conversations/$RJ/events/respond_to_confirmation" --json '{"accept": false, "reason": "again"}' --expect 200
  control-agent-server api GET "/api/conversations/$RJ/events/count" --query kind="$REJECT_KIND" --check . eq 1
  control-agent-server api POST "/api/conversations/$RJ/events/respond_to_confirmation" --json '{"accept": true}' --expect 200
  control-agent-server conversation wait "$RJ" --until finished --timeout 120
  control-agent-server api GET "/api/conversations/$RJ/agent_final_response" --check response eq QA_F08_DONE
  test -d "$W/reject/qa-f08-victim"
  ```
  The reject is 200, the conversation goes back to `idle`, one
  `UserRejectObservation` carries the reason (also pushed on the socket) and
  the victim folder survives. A second reject finds nothing pending and adds
  nothing. The later accept has nothing pending either, so it just runs the
  agent, which finishes; the rejected command never runs.
- **Confirmation errors (`F08.confirm-errors`).** Bad bodies, ids and auth.
  ```sh
  control-agent-server api POST "/api/conversations/$RJ/events/respond_to_confirmation" --json '{}' \
    --expect 422 --check detail.0.loc contains accept --save F08.confirm-errors/missing-accept
  control-agent-server api POST "/api/conversations/$RJ/events/respond_to_confirmation" --json '{"accept": "maybe"}' --expect 422
  control-agent-server api POST /api/conversations/not-a-uuid/events/respond_to_confirmation --json '{"accept": false}' --expect 422
  control-agent-server api POST "/api/conversations/$NOPE/events/respond_to_confirmation" --json '{"accept": false}' \
    --expect 404 --check detail contains 'Conversation not found'
  control-agent-server api POST "/api/conversations/$RJ/events/respond_to_confirmation" --json '{"accept": false}' --auth none --expect 401
  ```
  All statuses match.
- **Accept with the run capacity full (`F08.confirm-capacity`).** A second
  server limited to one concurrent run: conversation `X` waits for
  confirmation (a parked conversation holds no run slot), a conversation on
  a hanging stub takes the only slot, then `X` is accepted.
  ```sh
  B=$(control-agent-server launch --new --name f08-cap --print-run --config-json '{"max_concurrent_runs": 1}')
  trap 'control-agent-server stop --run "$B" >/dev/null' EXIT
  CAP_STUB=$(control-agent-server fixture llm-stub --run "$B" --name qa-f08-cap \
    --step 'tool:terminal:{"command":"echo qa-cap > qa-f08-cap.txt"}' --step "$FINISH" --print-path)
  HANG_STUB=$(control-agent-server fixture llm-stub --run "$B" --name qa-f08-hang --step hang:300 --print-path)
  mkdir -p "$W/cap" "$W/hang"
  X=$(control-agent-server api POST /api/conversations --run "$B" \
    --json "{\"workspace\": {\"working_dir\": \"$W/cap\"}, \"agent\": {\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$CAP_STUB/v1\", \"num_retries\": 0}, \"tools\": [{\"name\": \"terminal\"}]}, \"autotitle\": false, \"confirmation_policy\": {\"kind\": \"AlwaysConfirm\"}, \"initial_message\": $MSG}" \
    --expect 201 --field id)
  control-agent-server conversation wait "$X" --run "$B" --until waiting_for_confirmation --timeout 120
  Y=$(control-agent-server api POST /api/conversations --run "$B" \
    --json "{\"workspace\": {\"working_dir\": \"$W/hang\"}, \"agent\": {\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$HANG_STUB/v1\", \"num_retries\": 0}, \"tools\": []}, \"autotitle\": false, \"initial_message\": $MSG}" \
    --expect 201 --field id)
  control-agent-server conversation wait "$Y" --run "$B" --until running --timeout 60
  control-agent-server api POST "/api/conversations/$X/events/respond_to_confirmation" --run "$B" --json '{"accept": true}' \
    --expect 429 --check detail contains 'run limit' --save F08.confirm-capacity/accept-429
  control-agent-server api GET "/api/conversations/$X" --run "$B" --check execution_status eq waiting_for_confirmation
  test ! -e "$W/cap/qa-f08-cap.txt"
  control-agent-server api POST "/api/conversations/$X/events/respond_to_confirmation" --run "$B" --json '{"accept": false}' --expect 200
  control-agent-server conversation wait "$X" --run "$B" --until idle --timeout 60
  control-agent-server api GET "/api/conversations/$X/events/search" --run "$B" --query kind="$REJECT_KIND" \
    --check items len-eq 1 --check items.0.rejection_reason eq 'User rejected the action.'
  control-agent-server api POST "/api/conversations/$Y/events/respond_to_confirmation" --run "$B" --json '{"accept": true}' \
    --expect 200 --save F08.confirm-capacity/accept-running
  control-agent-server api GET "/api/conversations/$Y" --run "$B" --check execution_status eq running
  test ! -e "$W/cap/qa-f08-cap.txt"
  ```
  The accept is 429 `Conversation run limit reached` and `X` keeps waiting
  with its command unrun; the reject needs no slot and succeeds (one
  `UserRejectObservation` with the default reason `User rejected the
  action.`, since the body sent none); an accept
  on the running conversation is a 200 no-op. The trap stops the second
  server.
- **A real model asks first (`F08.confirm-real`).** DeepSeek flash with the
  terminal tool and `AlwaysConfirm`; every action it proposes is accepted
  until it finishes.
  ```sh
  REAL=$(control-agent-server conversation start --tools terminal --no-autotitle --confirmation-policy always --workspace "$W/real" \
    --prompt 'Use the terminal tool to run exactly this one command, then finish: echo qa-f08-real > qa-f08-real.txt' \
    --wait --until waiting_for_confirmation --timeout 300 --save F08.confirm-real/start --print-id)
  test ! -e "$W/real/qa-f08-real.txt"
  control-agent-server conversation events "$REAL" --kinds ActionEvent --contains qa-f08-real
  for _ in 1 2 3 4 5 6 7 8; do
    STATUS=$(control-agent-server api GET "/api/conversations/$REAL" --field execution_status)
    if [ "$STATUS" = finished ]; then break; fi
    if [ "$STATUS" = waiting_for_confirmation ]; then
      control-agent-server api POST "/api/conversations/$REAL/events/respond_to_confirmation" --json '{"accept": true}' --expect 200 --field success
    fi
    control-agent-server conversation wait "$REAL" --until waiting_for_confirmation,finished,error --timeout 300
  done
  control-agent-server api GET "/api/conversations/$REAL" --check execution_status eq finished --save F08.confirm-real/finished
  test "$(cat "$W/real/qa-f08-real.txt")" = qa-f08-real
  ```
  The model's first terminal action waits and has not run; after the accepts
  the file holds `qa-f08-real` and the conversation is `finished`. The loop
  tolerates a model that adds a check command (each is accepted) and a
  status read that is still `waiting_for_confirmation` right after an accept
  (a repeated accept is a no-op).
- **Set and clear the analyzer (`F08.analyzer-set-clear`).** On the idle
  conversation from the first bullet.
  ```sh
  control-agent-server ws start "/sockets/events/$KID" --name qa-f08-analyzer --duration 120
  control-agent-server api POST "/api/conversations/$KID/security_analyzer" --json '{"security_analyzer": {"kind": "PatternSecurityAnalyzer"}}' \
    --expect 200 --check success eq true --save F08.analyzer-set-clear/pattern
  control-agent-server api GET "/api/conversations/$KID" --check security_analyzer.kind eq PatternSecurityAnalyzer \
    --check security_analyzer.high_patterns len-ge 1
  control-agent-server state cat "server/workspace/conversations/${KID//-/}/base_state.json" --max-chars 200 \
    --check security_analyzer.kind eq PatternSecurityAnalyzer
  control-agent-server api POST "/api/conversations/$KID/security_analyzer" --json '{"security_analyzer": {"kind": "LLMSecurityAnalyzer"}}' --expect 200
  control-agent-server api GET "/api/conversations/$KID" --check security_analyzer.kind eq LLMSecurityAnalyzer
  control-agent-server api POST "/api/conversations/$KID/security_analyzer" --json '{"security_analyzer": null}' --expect 200
  control-agent-server api GET "/api/conversations/$KID" --check security_analyzer eq null --raw-out "$W/qa-f08-cleared.json" \
    --save F08.analyzer-set-clear/cleared
  jq -e 'has("security_analyzer") and .security_analyzer == null' "$W/qa-f08-cleared.json"
  control-agent-server ws read qa-f08-analyzer --kinds ConversationStateUpdateEvent --contains LLMSecurityAnalyzer --wait 10
  control-agent-server ws stop qa-f08-analyzer --kinds ConversationStateUpdateEvent --contains '"key": "security_analyzer"' \
    --expect-min 3 --save F08.analyzer-set-clear/socket
  ```
  GET and `base_state.json` follow each change (`PatternSecurityAnalyzer`
  comes back with its default pattern lists), the clear leaves the key
  present with the value `null` (`jq` on the raw body tells that apart from
  a dropped key, which `--check ... eq null` cannot), and the socket pushed
  one `security_analyzer` state update per change (three, the last with
  `value` `null`).
- **Analyzer errors (`F08.analyzer-errors`).** The field is required even to
  clear it.
  ```sh
  control-agent-server api POST "/api/conversations/$KID/security_analyzer" --json '{}' \
    --expect 422 --check detail.0.loc contains security_analyzer --save F08.analyzer-errors/missing
  control-agent-server api POST "/api/conversations/$KID/security_analyzer" --json '{"security_analyzer": {"kind": "NopeAnalyzer"}}' \
    --expect 422 --check detail.0.msg contains 'Unknown kind'
  control-agent-server api POST "/api/conversations/$NOPE/security_analyzer" --json '{"security_analyzer": null}' --expect 404
  control-agent-server api POST "/api/conversations/$KID/security_analyzer" --json '{"security_analyzer": {"kind": "PatternSecurityAnalyzer"}}' \
    --auth none --expect 401
  control-agent-server api GET "/api/conversations/$KID" --check security_analyzer eq null
  ```
  All statuses match and the analyzer is still `null`, although the
  refused unauthenticated request asked for `PatternSecurityAnalyzer`.
- **Risk-based confirmation (`F08.confirm-risky`).** `ConfirmRisky` (default
  threshold `HIGH`, `confirm_unknown` true) with `PatternSecurityAnalyzer`
  set at runtime; the stub proposes `echo`, then `rm -rf qa-f08-victim`, then
  another `echo`.
  ```sh
  RK=$(control-agent-server api POST /api/conversations \
    --json "{\"workspace\": {\"working_dir\": \"$W/risky\"}, \"agent\": $RISKY_AGENT, \"autotitle\": false}" --expect 201 --field id)
  control-agent-server api POST "/api/conversations/$RK/confirmation_policy" --json '{"policy": {"kind": "ConfirmRisky"}}' --expect 200
  control-agent-server api GET "/api/conversations/$RK" --check confirmation_policy.threshold eq HIGH --check confirmation_policy.confirm_unknown eq true
  control-agent-server api POST "/api/conversations/$RK/security_analyzer" --json '{"security_analyzer": {"kind": "PatternSecurityAnalyzer"}}' --expect 200
  control-agent-server conversation send "$RK" --text 'Run the commands.' --wait --until waiting_for_confirmation --timeout 120
  test "$(cat "$W/risky/qa-f08-low.txt")" = qa-low
  test -d "$W/risky/qa-f08-victim"
  control-agent-server conversation events "$RK" --kinds ActionEvent --show 1 --contains 'rm -rf' --save F08.confirm-risky/held
  control-agent-server api GET "/api/conversations/$RK/events/count" --query kind="$OBSERVATION_KIND" --check . eq 1
  control-agent-server api POST "/api/conversations/$RK/events/respond_to_confirmation" --json '{"accept": false, "reason": "QA_F08 high risk"}' --expect 200
  control-agent-server api POST "/api/conversations/$RK/security_analyzer" --json '{"security_analyzer": null}' --expect 200
  control-agent-server api POST "/api/conversations/$RK/events/respond_to_confirmation" --json '{"accept": true}' --expect 200
  control-agent-server conversation wait "$RK" --until waiting_for_confirmation --timeout 120
  test ! -e "$W/risky/qa-f08-unrated.txt"
  control-agent-server api POST "/api/conversations/$RK/events/respond_to_confirmation" --json '{"accept": true}' --expect 200
  control-agent-server conversation wait "$RK" --until finished --timeout 120
  test "$(cat "$W/risky/qa-f08-unrated.txt")" = qa-unrated
  test -d "$W/risky/qa-f08-victim"
  ```
  The low-risk `echo` ran without asking, `rm -rf` (rated `HIGH` by the
  pattern analyzer) waited and was rejected, so the victim folder survives.
  With the analyzer cleared, the next `echo` is unrated (`UNKNOWN`) and
  `confirm_unknown` makes it wait; accepting it lets the agent finish.
- **Update secrets (`F08.secrets-update`).** The three accepted value forms,
  on the idle conversation `$KID`. A tags PATCH forces a state save first
  (see the next bullet for why).
  ```sh
  control-agent-server api POST "/api/conversations/$KID/secrets" \
    --json '{"secrets": {"QA_F08_PLAIN": "qa-f08-plain-value", "QA_F08_DICT": {"value": "qa-f08-dict-value"}, "QA_F08_STATIC": {"kind": "StaticSecret", "value": "qa-f08-static-value", "description": "qa static"}}}' \
    --expect 200 --check success eq true --save F08.secrets-update/post
  control-agent-server api PATCH "/api/conversations/$KID" --json '{"tags": {"qa": "f08-save"}}' --expect 200 --check success eq true
  control-agent-server api GET "/api/conversations/$KID" --expect 200 \
    --check secret_registry.secret_sources.QA_F08_PLAIN.kind eq StaticSecret \
    --check secret_registry.secret_sources.QA_F08_PLAIN.value eq null \
    --check secret_registry.secret_sources.QA_F08_DICT.kind eq StaticSecret \
    --check secret_registry.secret_sources.QA_F08_STATIC.description eq 'qa static' \
    --check secret_registry.secret_sources.QA_F08_STATIC.value eq null --save F08.secrets-update/get
  control-agent-server state cat "server/workspace/conversations/${KID//-/}/base_state.json" --max-chars 200 \
    --check secret_registry.secret_sources.QA_F08_PLAIN.value exists \
    --check secret_registry.secret_sources.QA_F08_PLAIN.value ne '**********' --not-contains qa-f08-plain-value
  control-agent-server state grep qa-f08-dict-value --glob 'server/**/*' --expect-none
  control-agent-server state grep qa-f08-static-value --glob 'server/**/*' --expect-none
  ```
  The POST is 200; after the save GET lists all three names as
  `StaticSecret` with `value` `null` (masked) and the description kept, and
  `base_state.json` holds cipher text: neither the plaintext nor the
  `**********` placeholder a server without a cipher writes
  (`F08.secrets-no-cipher`).
- **Runtime secret visible to GET (`F08.secrets-visible`), known bug.** The
  positive control first: a secret followed by a tags PATCH (which saves the
  state) is listed by GET. Then one more secret is posted and the
  conversation is read right away.
  ```sh
  control-agent-server api POST "/api/conversations/$KID/secrets" --json '{"secrets": {"QA_F08_VISIBLE_SAVED": "qa-f08-visible-saved"}}' --expect 200
  control-agent-server api PATCH "/api/conversations/$KID" --json '{"tags": {"qa": "f08-visible"}}' --expect 200
  control-agent-server api GET "/api/conversations/$KID" --check secret_registry.secret_sources.QA_F08_VISIBLE_SAVED.kind eq StaticSecret
  control-agent-server api POST "/api/conversations/$KID/secrets" --json '{"secrets": {"QA_F08_FRESH": "qa-f08-fresh-value"}}' --expect 200
  control-agent-server api GET "/api/conversations/$KID" --check secret_registry.secret_sources.QA_F08_FRESH.kind eq StaticSecret \
    --save F08.secrets-visible/get  # bug
  ```
  Expected: GET lists `QA_F08_FRESH` as it lists the saved one. Today it does not:
  `LocalConversation.update_secrets` mutates
  `state.secret_registry.secret_sources` in place, the state only autosaves
  on attribute assignment, and GET reads the `base_state.json` snapshot. The
  name appears after the next state change (a PATCH, a message, a policy
  change); until then the live registry has it but no reader can see it, and
  a restart loses it (`F08.secrets-unsaved-restart`).
- **Secret errors (`F08.secrets-errors`).** Bad bodies, an unknown id, no key.
  ```sh
  control-agent-server api POST "/api/conversations/$KID/secrets" --json '{}' --expect 422 --check detail.0.loc contains secrets \
    --save F08.secrets-errors/missing
  control-agent-server api POST "/api/conversations/$KID/secrets" --json '{"secrets": "qa"}' --expect 422 --check detail.0.type eq dict_type
  control-agent-server api POST "/api/conversations/$KID/secrets" --json '{"secrets": {"QA_F08_BAD": {"kind": "NopeSecret", "value": "x"}}}' \
    --expect 422 --check detail.0.msg contains 'Unknown kind'
  control-agent-server api POST "/api/conversations/$KID/secrets" --json '{"secrets": {"QA_F08_LOOKUP": {"kind": "LookupSecret"}}}' \
    --expect 422 --check detail.0.loc contains url
  control-agent-server api POST "/api/conversations/$NOPE/secrets" --json '{"secrets": {"QA_F08_X": "qa-x"}}' --expect 404
  control-agent-server api POST "/api/conversations/$KID/secrets" --json '{"secrets": {"QA_F08_X": "qa-x"}}' --auth none --expect 401
  control-agent-server api PATCH "/api/conversations/$KID" --json '{"tags": {"qa": "f08-errors"}}' --expect 200
  control-agent-server api GET "/api/conversations/$KID" --check secret_registry.secret_sources.QA_F08_BAD missing \
    --check secret_registry.secret_sources.QA_F08_LOOKUP missing --check secret_registry.secret_sources.QA_F08_X missing \
    --check secret_registry.secret_sources.QA_F08_PLAIN exists
  ```
  Every status matches and, after a save, none of the refused names is
  stored while the earlier secrets remain.
- **Non-string secret value (`F08.secrets-bad-value`), known bug.** The same
  body with a string value is accepted (the control); then a number, `null`
  and `true` where a value belongs.
  ```sh
  control-agent-server api POST "/api/conversations/$KID/secrets" --json '{"secrets": {"QA_F08_STRING": "qa-f08-string-value"}}' --expect 200
  control-agent-server api POST "/api/conversations/$KID/secrets" --json '{"secrets": {"QA_F08_NUMBER": 5}}' \
    --expect 422 --save F08.secrets-bad-value/number  # bug
  control-agent-server api POST "/api/conversations/$KID/secrets" --json '{"secrets": {"QA_F08_NULL": null}}' --expect 422  # bug
  control-agent-server api POST "/api/conversations/$KID/secrets" --json '{"secrets": {"QA_F08_BOOL": true}}' --expect 422  # bug
  ```
  Expected: 422 like any other invalid body. Today each is 500
  `"exception": "'int' object has no attribute 'pop'"` (`'NoneType'` for
  `null`, `'bool'` for `true`): `UpdateSecretsRequest.convert_string_secrets` passes non-string,
  non-dict values through, and `DiscriminatedUnionMixin` calls `.pop("kind")`
  on them, an `AttributeError` that escapes request validation. Same root
  cause as `F04.create-secrets-plain`.
- **Secrets in the terminal (`F08.secrets-terminal`).** The stub's command
  writes `$QA_F08_TOKEN` to a file and echoes it.
  ```sh
  SID=$(control-agent-server api POST /api/conversations \
    --json "{\"workspace\": {\"working_dir\": \"$W/secret\"}, \"agent\": $SECRET_AGENT, \"autotitle\": false}" --expect 201 --field id)
  control-agent-server api POST "/api/conversations/$SID/secrets" --json '{"secrets": {"QA_F08_TOKEN": "qa-f08-token-value"}}' \
    --expect 200 --save F08.secrets-terminal/post
  control-agent-server conversation send "$SID" --text 'Run the command.' --wait --until finished --timeout 120
  test "$(cat "$W/secret/qa-f08-token.txt")" = qa-f08-token-value
  control-agent-server conversation events "$SID" --kinds ObservationEvent --contains 'token=<secret-hidden>' \
    --save F08.secrets-terminal/observation
  control-agent-server sink read --name qa-f08-secret --contains 'token=<secret-hidden>' --expect-min 1 --save F08.secrets-terminal/model-request
  control-agent-server sink read --name qa-f08-secret --contains qa-f08-token-value --expect-max 0
  control-agent-server state grep QA_F08_TOKEN --glob 'server/**/*'
  control-agent-server state grep qa-f08-token-value --glob 'server/**/*' --expect-none
  control-agent-server api GET "/api/conversations/$SID" --check secret_registry.secret_sources.QA_F08_TOKEN.kind eq StaticSecret
  ```
  The file holds the real value, so the command got it as an environment
  variable; the observation reads `token=<secret-hidden>`, the stub's
  recorded model requests carry the masked text and never the value, and no
  server file holds the plaintext although the name is stored (the positive
  control for the glob). The run saved the state, so GET lists the name.
- **Override a start-time secret (`F08.secrets-override`).** A conversation
  created with `QA_F08_ROT` = `qa-f08-rot-old`, then a runtime POST of a new
  value.
  ```sh
  ROT=$(control-agent-server api POST /api/conversations \
    --json "{\"workspace\": {\"working_dir\": \"$W/rotate\"}, \"agent\": $ROTATE_AGENT, \"autotitle\": false, \"secrets\": {\"QA_F08_ROT\": {\"kind\": \"StaticSecret\", \"value\": \"qa-f08-rot-old\"}}}" \
    --expect 201 --field id)
  control-agent-server api POST "/api/conversations/$ROT/secrets" --json '{"secrets": {"QA_F08_ROT": "qa-f08-rot-new"}}' \
    --expect 200 --save F08.secrets-override/post
  control-agent-server conversation send "$ROT" --text 'Run the command.' --wait --until finished --timeout 120
  test "$(cat "$W/rotate/qa-f08-rot.txt")" = qa-f08-rot-new
  ```
  The command wrote `qa-f08-rot-new`: the runtime value replaced the
  start-time one in the live registry.
- **Lookup secret (`F08.secrets-lookup`).** A runtime `LookupSecret` whose
  URL is an HTTP sink that answers `qa-f08-lookup-value`, with an
  `Authorization` header (a secret-looking name) and a plain one.
  ```sh
  LOOKUP_URL=$(control-agent-server fixture http-sink --name qa-f08-lookup-src --body qa-f08-lookup-value --print-path)
  LK=$(control-agent-server api POST /api/conversations \
    --json "{\"workspace\": {\"working_dir\": \"$W/lookup\"}, \"agent\": $LOOKUP_AGENT, \"autotitle\": false}" --expect 201 --field id)
  control-agent-server api POST "/api/conversations/$LK/secrets" \
    --json "{\"secrets\": {\"QA_F08_LOOKUP\": {\"kind\": \"LookupSecret\", \"url\": \"$LOOKUP_URL/qa-f08-secret\", \"headers\": {\"Authorization\": \"Bearer qa-f08-lookup-auth\", \"X-QA-F08\": \"qa-f08-header\"}}}}" \
    --expect 200 --check success eq true --save F08.secrets-lookup/post
  control-agent-server sink read --name qa-f08-lookup-src --expect-max 0
  control-agent-server conversation send "$LK" --text 'Run the command.' --wait --until finished --timeout 120
  test "$(cat "$W/lookup/qa-f08-lookup.txt")" = qa-f08-lookup-value
  control-agent-server sink read --name qa-f08-lookup-src --path /qa-f08-secret --contains 'Bearer qa-f08-lookup-auth' \
    --expect-min 1 --save F08.secrets-lookup/fetch
  control-agent-server sink read --name qa-f08-lookup-src --contains qa-f08-header --expect-min 1
  control-agent-server conversation events "$LK" --kinds ObservationEvent --contains 'lookup=<secret-hidden>' --save F08.secrets-lookup/observation
  control-agent-server sink read --name qa-f08-lookup --contains 'lookup=<secret-hidden>' --expect-min 1
  control-agent-server sink read --name qa-f08-lookup --contains qa-f08-lookup-value --expect-max 0
  control-agent-server api GET "/api/conversations/$LK" --check secret_registry.secret_sources.QA_F08_LOOKUP.kind eq LookupSecret \
    --check secret_registry.secret_sources.QA_F08_LOOKUP.url eq "$LOOKUP_URL/qa-f08-secret" \
    --check secret_registry.secret_sources.QA_F08_LOOKUP.headers.X-QA-F08 eq qa-f08-header \
    --check secret_registry.secret_sources.QA_F08_LOOKUP.headers.Authorization missing --save F08.secrets-lookup/get
  control-agent-server state cat "server/workspace/conversations/${LK//-/}/base_state.json" --max-chars 200 \
    --check secret_registry.secret_sources.QA_F08_LOOKUP.headers.Authorization exists --not-contains qa-f08-lookup-auth
  control-agent-server state grep qa-f08-lookup-value --glob 'server/**/*' --expect-none
  ```
  Nothing is fetched when the secret is posted. The command that names
  `QA_F08_LOOKUP` makes the server `GET` the URL with both headers and
  receive the sink's text, which the command writes to the file; the
  observation and the model's next request show `lookup=<secret-hidden>`.
  GET returns the URL and the plain header but leaves the `Authorization`
  header out, `base_state.json` keeps it as cipher text, and the fetched
  value is stored nowhere.
- **Lookup of the server's own secret (`F08.secrets-lookup-self`).** A
  secret stored with `PUT /api/settings/secrets` (the settings family's
  route, used here to arrange), then three runtime `LookupSecret`s that
  carry no headers, so no session key: the hostless URL
  `/api/settings/secrets/QA_F08_STORED`, the same URL made absolute on the
  run's address, and a hostless URL for a name that is not stored.
  ```sh
  RUN_URL=$(control-agent-server config | jq -r .url)
  control-agent-server api PUT /api/settings/secrets --json '{"name": "QA_F08_STORED", "value": "qa-f08-stored-value"}' --expect 200
  SELF=$(control-agent-server api POST /api/conversations \
    --json "{\"workspace\": {\"working_dir\": \"$W/self\"}, \"agent\": $SELF_AGENT, \"autotitle\": false}" --expect 201 --field id)
  control-agent-server api POST "/api/conversations/$SELF/secrets" \
    --json "{\"secrets\": {\"QA_F08_HOSTLESS\": {\"kind\": \"LookupSecret\", \"url\": \"/api/settings/secrets/QA_F08_STORED\"}, \"QA_F08_ABSOLUTE\": {\"kind\": \"LookupSecret\", \"url\": \"$RUN_URL/api/settings/secrets/QA_F08_STORED\"}, \"QA_F08_ABSENT\": {\"kind\": \"LookupSecret\", \"url\": \"/api/settings/secrets/QA_F08_NOT_STORED\"}}}" \
    --expect 200 --check success eq true --save F08.secrets-lookup-self/post
  control-agent-server conversation send "$SELF" --text 'Run the command.' --wait --until finished --timeout 120
  test "$(cat "$W/self/qa-f08-hostless.txt")" = qa-f08-stored-value
  test "$(cat "$W/self/qa-f08-absolute.txt")" = qa-f08-stored-value
  test -e "$W/self/qa-f08-absent.txt"
  test ! -s "$W/self/qa-f08-absent.txt"
  control-agent-server conversation events "$SELF" --kinds ObservationEvent --contains 'self=<secret-hidden>' \
    --save F08.secrets-lookup-self/observation
  control-agent-server api GET "/api/conversations/$SELF" \
    --check secret_registry.secret_sources.QA_F08_HOSTLESS.url eq "$RUN_URL/api/settings/secrets/QA_F08_STORED" \
    --check secret_registry.secret_sources.QA_F08_HOSTLESS.headers len-eq 0 \
    --check secret_registry.secret_sources.QA_F08_ABSENT.url eq "$RUN_URL/api/settings/secrets/QA_F08_NOT_STORED" \
    --save F08.secrets-lookup-self/get
  control-agent-server logs --grep 'secrets/QA_F08_STORED' --expect-none
  control-agent-server logs --grep 'QA_F08_NOT_STORED' --expect-min 1
  control-agent-server api GET /api/settings/secrets/QA_F08_STORED --auth none --expect 401 --save F08.secrets-lookup-self/no-key
  control-agent-server logs --grep 'secrets/QA_F08_STORED' --expect-min 1
  control-agent-server state grep qa-f08-stored-value --glob 'server/**/*' --expect-none
  control-agent-server api DELETE /api/settings/secrets/QA_F08_STORED --expect 200
  ```
  Both stored-name lookups give the command `qa-f08-stored-value` (masked as
  `self=<secret-hidden>` in the observation), and GET shows the hostless URL
  stored absolute on the run's address (`OH_INTERNAL_SERVER_URL`, which the
  server sets from its bound host and port). No HTTP request for
  `QA_F08_STORED` reached the server: the log has no line for that path
  until the unauthenticated GET that follows, which the server refuses with
  401 and logs (the positive control for the log check), so a header-less
  HTTP lookup could never have returned the value; the in-process resolver
  read it from the secrets store. The name that is not stored falls through
  to a real HTTP request, which the server answers 401 (logged for
  `QA_F08_NOT_STORED`), and the command gets an empty value. The
  conversation's state never holds the stored value; the bullet deletes
  the stored secret.
- **Agent Profile secret scope (`F08.secrets-profile-scope`).** An Agent
  Profile that allows only `QA_F08_ALLOWED`.
  ```sh
  control-agent-server api POST /api/agent-profiles/qa-f08-scoped \
    --json '{"llm_profile_ref": "deepseek-flash", "tools": [], "secret_refs": ["QA_F08_ALLOWED"]}' --expect 201
  SCOPED_PROFILE=$(control-agent-server api GET /api/agent-profiles/qa-f08-scoped --field profile.id)
  PSC=$(control-agent-server api POST /api/conversations \
    --json "{\"workspace\": {\"working_dir\": \"$W/scoped\"}, \"agent_profile_id\": \"$SCOPED_PROFILE\", \"autotitle\": false}" \
    --expect 201 --check launched_agent_profile.secret_refs contains QA_F08_ALLOWED --field id)
  control-agent-server api POST "/api/conversations/$PSC/secrets" \
    --json '{"secrets": {"QA_F08_ALLOWED": "qa-f08-allowed-value", "QA_F08_OTHER": "qa-f08-other-value"}}' --expect 200 --check success eq true
  control-agent-server api PATCH "/api/conversations/$PSC" --json '{"tags": {"qa": "f08-scope"}}' --expect 200
  control-agent-server api GET "/api/conversations/$PSC" --check secret_registry.secret_sources.QA_F08_ALLOWED.kind eq StaticSecret \
    --check secret_registry.secret_sources.QA_F08_OTHER missing --save F08.secrets-profile-scope/get
  control-agent-server api DELETE /api/agent-profiles/qa-f08-scoped --expect 200
  ```
  The POST is 200 for both names, but only `QA_F08_ALLOWED` is stored: the
  launched profile's `secret_refs` filter runtime secrets without an error.
- **No cipher (`F08.secrets-no-cipher`).** A second server with neither
  `OH_SECRET_KEY` nor a session key, so it has no cipher. Its stub's key is
  also passed as `OPENAI_API_KEY`: without a cipher the agent's own
  `api_key` is redacted on save too, and the run after the restart would
  otherwise end in `error` (`LLMAuthenticationError`) before any command.
  ```sh
  NC=$(control-agent-server launch --new --no-auth --no-secret-key --name f08-nocipher --print-run \
    --env OPENAI_API_KEY=qa-stub-key)
  trap 'control-agent-server stop --run "$NC" >/dev/null' EXIT
  NC_STEP='tool:terminal:{"command":"printf %s \"$QA_F08_NC\" > qa-f08-nc.txt"}'
  NC_STUB=$(control-agent-server fixture llm-stub --run "$NC" --name qa-f08-nocipher \
    --step "$NC_STEP" --step "$FINISH" --step "$NC_STEP" --step "$FINISH" --print-path)
  N=$(control-agent-server api POST /api/conversations --run "$NC" \
    --json "{\"workspace\": {\"working_dir\": \"$W/nocipher\"}, \"agent\": {\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$NC_STUB/v1\", \"num_retries\": 0}, \"tools\": [{\"name\": \"terminal\"}]}, \"autotitle\": false}" \
    --expect 201 --field id)
  control-agent-server api POST "/api/conversations/$N/secrets" --run "$NC" --json '{"secrets": {"QA_F08_NC": "qa-f08-nc-value"}}' \
    --expect 200 --save F08.secrets-no-cipher/post
  control-agent-server conversation send "$N" --run "$NC" --text 'Run the command.' --wait --until finished --timeout 120
  test "$(cat "$W/nocipher/qa-f08-nc.txt")" = qa-f08-nc-value
  control-agent-server state cat "server/workspace/conversations/${N//-/}/base_state.json" --run "$NC" --max-chars 200 \
    --check secret_registry.secret_sources.QA_F08_NC.value eq '**********' --not-contains qa-f08-nc-value
  control-agent-server state grep QA_F08_NC --run "$NC" --glob 'server/**/*'
  control-agent-server state grep qa-f08-nc-value --run "$NC" --glob 'server/**/*' --expect-none
  control-agent-server logs --run "$NC" --grep 'OH_SECRET_KEY was not defined' --expect-min 1
  rm "$W/nocipher/qa-f08-nc.txt"
  control-agent-server restart --run "$NC"
  control-agent-server conversation send "$N" --run "$NC" --text 'Run it again.' --wait --until finished --timeout 120
  test -e "$W/nocipher/qa-f08-nc.txt"
  test ! -s "$W/nocipher/qa-f08-nc.txt"
  control-agent-server api GET "/api/conversations/$N" --run "$NC" --check secret_registry.secret_sources.QA_F08_NC.kind eq StaticSecret \
    --check secret_registry.secret_sources.QA_F08_NC.value eq null --save F08.secrets-no-cipher/after-restart
  ```
  While the conversation lives, the command gets `qa-f08-nc-value`.
  `base_state.json` holds the redaction placeholder `**********`, never the
  value, and the log carries `OH_SECRET_KEY was not defined. Secrets will
  not be persisted between restarts.` After the restart the same command
  runs with an empty `$QA_F08_NC` (the file exists and is empty), yet GET
  still lists `QA_F08_NC`, masked like any other secret, so a client cannot
  tell that it was lost. This is the documented design (`Config.secret_key`);
  the trap stops the second server.
- **Restart with a saved secret (`F08.secrets-restart`).** `$SID` got
  `QA_F08_TOKEN` at runtime and ran a command (which saved the state) in the
  terminal bullet; restart and run the same command again.
  ```sh
  rm "$W/secret/qa-f08-token.txt"
  control-agent-server restart
  control-agent-server doctor
  control-agent-server conversation send "$SID" --text 'Run it again.' --wait --until finished --timeout 120
  test "$(cat "$W/secret/qa-f08-token.txt")" = qa-f08-token-value
  control-agent-server api GET "/api/conversations/$SID" --check secret_registry.secret_sources.QA_F08_TOKEN.kind eq StaticSecret \
    --save F08.secrets-restart/after-restart
  ```
  After the restart the server is healthy and `QA_F08_TOKEN` (saved
  encrypted with the run's `OH_SECRET_KEY`) is injected again.
- **Unsaved secret restart (`F08.secrets-unsaved-restart`), known bug.** One
  secret is saved by a PATCH (the positive control), a second is posted with
  nothing after it, then the server restarts.
  ```sh
  UNS=$(control-agent-server api POST /api/conversations \
    --json "{\"workspace\": {\"working_dir\": \"$W/knobs\"}, \"agent\": $OFFLINE_AGENT, \"autotitle\": false}" --expect 201 --field id)
  control-agent-server api POST "/api/conversations/$UNS/secrets" --json '{"secrets": {"QA_F08_SAVED": "qa-f08-saved-value"}}' --expect 200
  control-agent-server api PATCH "/api/conversations/$UNS" --json '{"tags": {"qa": "f08-saved"}}' --expect 200
  control-agent-server api POST "/api/conversations/$UNS/secrets" --json '{"secrets": {"QA_F08_UNSAVED": "qa-f08-unsaved-value"}}' --expect 200
  control-agent-server restart
  control-agent-server api PATCH "/api/conversations/$UNS" --json '{"tags": {"qa": "f08-after-restart"}}' --expect 200
  control-agent-server api GET "/api/conversations/$UNS" --check secret_registry.secret_sources.QA_F08_SAVED.kind eq StaticSecret
  control-agent-server api GET "/api/conversations/$UNS" --check secret_registry.secret_sources.QA_F08_UNSAVED.kind eq StaticSecret \
    --save F08.secrets-unsaved-restart/get  # bug
  ```
  Expected: both names after the restart. Today `QA_F08_SAVED` is there and
  `QA_F08_UNSAVED` is gone: the POST never reached `base_state.json` (see
  `F08.secrets-visible`) and runtime secrets are not written to `meta.json`,
  so a restart, an idle eviction (default 20 minutes) or a sandbox pause
  right after `POST .../secrets` silently drops it.

## Gotchas

- `respond_to_confirmation` with `accept: true` is exactly a run request: it
  needs a run slot (429 when full), it is a no-op on a running conversation,
  and with nothing pending it simply resumes the agent. `accept: false` with
  nothing pending is a 200 no-op. After an accept the status read by GET can
  stay `waiting_for_confirmation` for a moment, because the run starts in the
  background.
- A conversation parked in `waiting_for_confirmation` holds no run slot; the
  agent's run ends at the pending action and a new run starts on accept,
  which executes the pending action without calling the model.
- `ActionEvent.security_risk` is the risk the model declared (here always
  `UNKNOWN`, the stub declares none); the analyzer's rating
  (`PatternSecurityAnalyzer` rated `rm -rf` `HIGH`) is not recorded on the
  event, so clients cannot see why an action was held.
- A single `finish` or `think` action never needs confirmation, even under
  `AlwaysConfirm`; with no analyzer every action is rated `UNKNOWN`, so
  `ConfirmRisky` asks for every action unless `confirm_unknown` is false.
- Secret values come back as `null` in GET (masked) and as cipher text in
  `base_state.json`. Without `OH_SECRET_KEY` and without a session key there
  is no cipher: secrets (and the agent's LLM `api_key`) are saved as the
  `**********` placeholder and are gone after a restart, while GET looks the
  same (`F08.secrets-no-cipher`). `launch` sets both keys; only
  `launch --no-auth --no-secret-key` gives a run without a cipher.
- A `LookupSecret` is fetched with `httpx` from inside the server process,
  so its URL must be reachable from the server, not from the client. A
  `LookupSecret` header whose name contains `authorization`, `cookie`,
  `credential`, `key`, `password`, `secret`, `session` or `token`
  (case-insensitive, `is_secret_key`) is left out of GET and encrypted at
  rest; other headers are returned as given.
- A hostless `LookupSecret` URL is made absolute when the request is
  parsed, from `OH_INTERNAL_SERVER_URL` (set by `python -m
  openhands.agent_server` from `--host`/`--port`, a wildcard bind becoming
  `127.0.0.1`; `http://127.0.0.1:8000` when unset), so GET shows the
  absolute URL. A URL on that address under `/api/settings/secrets/<name>`
  is answered in process from the secrets store
  (`local_secret_resolver.py`), never over HTTP, so it needs no session key;
  only a name the store lacks goes out as a real request, which is 401
  without a key.
- A command receives a secret only when its text mentions the secret's name
  (case-insensitive substring match); masking applies to every registered
  secret's last resolved value, wherever it appears in terminal output.
- Runtime secret changes made through `POST .../secrets` live only in
  `base_state.json` and are never written to `meta.json`, so a runtime secret
  survives a reload (restart, idle eviction, sandbox pause) only once a state
  save has written it to `base_state.json`; one that never got there is
  dropped. See `F08.secrets-restart` and `F08.secrets-unsaved-restart`.
- Unknown conversations answer 404 `{"detail": "Not Found"}` on the
  conversation routes and 404 `Conversation not found: <id>` on
  `respond_to_confirmation` (an events route).
- The event `kind` filter of `events/search` and `events/count` needs the
  fully qualified class path (`$REJECT_KIND`, `$OBSERVATION_KIND`); see
  `F06.search-kind-short`.
