# Child conversation waiting and typed mailbox

Coordinate existing conversations on one backend without a browser. A bounded
wait observes direct children's settled runs, sends results through the durable
mailbox before cleanup, and releases the parent's state lock. Typed mailbox
input is attributed to its sending agent and deduplicated across retries.

Source: `openhands-agent-server/openhands/agent_server/conversation_service.py`, `openhands-agent-server/openhands/agent_server/conversation_router.py`, `openhands-agent-server/openhands/agent_server/event_service.py`, `openhands-sdk/openhands/sdk/conversation/child_wait.py`, `openhands-sdk/openhands/sdk/event/mailbox.py`, `openhands-tools/openhands/tools/child_conversation/wait.py`

Needs: `git`

Routes: `POST /api/conversations/{conversation_id}/children/wait`,
`POST /api/conversations/{conversation_id}/mailbox`

## Sub-features

- `F35.bounded-wait`: an idle direct child times out normally; invalid children are rejected; timeout deletion removes the child without a browser.
- `F35.mailbox`: typed input persists once; retries reuse its ID, conflicting contents return 409, and proposal messages require evidence.

## How to get to it (agent POV)

- REST: the parent-scoped `children/wait` and sender-scoped `mailbox` endpoints.
- SDK agent tools: registered `WaitForChildrenTool` (`wait_for_children`) and
  `SendMailboxMessageTool` (`send_mailbox_message`). Both use the canonical REST
  endpoints and release the parent state lock during I/O. The mailbox tool wraps
  the request in its `message` argument so the typed mailbox `kind` does not
  collide with the SDK action discriminator.
- TypeScript: `ConversationClient.waitForChildren` and `sendMailboxMessage`,
  including abort signals. Result events arrive through ordinary persisted
  event reads or the existing event WebSocket; no new socket protocol is needed.

## Driving it with control-agent-server

Preconditions:

- This checkout is running, its run is selected, and `doctor` passes. No model
  key is needed: the offline agent below is never run. The fake key and closed
  provider port deliberately prevent accidental model use.
- On Windows the current harness's `launch` uses Unix process groups. Use its
  supported `attach` mode for an independently owned server and stop that server
  separately afterwards. `doctor` and all API probes must still run unmodified.

- **Timeout, cleanup and typed delivery (`F35.bounded-wait`, `F35.mailbox`).**

  ```sh
  W="$AGENT_SERVER_VERIFY_RUN/fixtures/f35-workspace"
  mkdir -p "$W"
  BASE="{\"agent\":{\"llm\":{\"model\":\"openai/offline\",\"api_key\":\"offline\",\"base_url\":\"http://127.0.0.1:9/v1\",\"num_retries\":0},\"tools\":[]},\"workspace\":{\"working_dir\":\"$W\"}}"
  P=$(control-agent-server api POST /api/conversations --json "$BASE" --expect 201 --field id)
  CHILD="${BASE%?},\"parent_conversation_id\":\"$P\"}"
  C=$(control-agent-server api POST /api/conversations --json "$CHILD" --expect 201 --field id)
  control-agent-server api POST "/api/conversations/$P/children/wait" \
    --json "{\"conversation_ids\":[\"$C\"],\"timeout_seconds\":0}" \
    --expect 200 --check timed_out eq true --check interrupted eq false \
    --save F35.bounded-wait/timeout
  MAIL="{\"recipient_id\":\"$C\",\"idempotency_key\":\"f35-message\",\"kind\":\"question\",\"body\":\"Durable agent input\"}"
  control-agent-server api POST "/api/conversations/$P/mailbox" --json "$MAIL" \
    --expect 200 --check duplicate eq false --save F35.mailbox/send
  control-agent-server api POST "/api/conversations/$P/mailbox" --json "$MAIL" \
    --expect 200 --check duplicate eq true --save F35.mailbox/retry
  control-agent-server api POST "/api/conversations/$P/mailbox" \
    --json "{\"recipient_id\":\"$C\",\"idempotency_key\":\"f35-message\",\"kind\":\"question\",\"body\":\"Different input\"}" --expect 409
  control-agent-server api POST "/api/conversations/$P/mailbox" \
    --json "{\"recipient_id\":\"$C\",\"idempotency_key\":\"invalid\",\"kind\":\"proposal\",\"body\":\"Missing evidence\"}" --expect 422
  control-agent-server api POST "/api/conversations/$P/children/wait" \
    --json "{\"conversation_ids\":[\"$P\"],\"timeout_seconds\":0}" --expect 404
  control-agent-server api POST "/api/conversations/$P/children/wait" \
    --json "{\"conversation_ids\":[\"$C\"],\"timeout_seconds\":0,\"on_timeout\":\"delete\"}" \
    --expect 200 --check timed_out eq true --save F35.bounded-wait/delete
  control-agent-server api GET "/api/conversations/$C" --expect 404
  control-agent-server api DELETE "/api/conversations/$P" --expect 200
  ```

## Gotchas

The wait's deadline begins after validation and runtime loading; stop/delete
cleanup can extend request duration. A provisional FINISHED notification does
not settle a live run before its stop hooks finish. Cleanup fences successor
runs, and result delivery precedes deletion. An interrupted pending wait leaves
children running and applies no timeout policy. Started destructive cleanup is
drained. Cross-server orchestration and durable wait recovery are outside this
same-backend primitive.

Mailbox kinds are question, proposal, review-request, blocker and decision.
Proposal, review-request and blocker need evidence. Default steer preserves
active work and goals. Explicit interrupt persists its reason and stops the run
and goal. Idle input is picked up on the next run, including after restart.
Messages are restricted to the existing parent tree, never self or unrelated
roots. Both sender and recipient secret registries redact persisted content.

Deterministic regressions additionally cover real agent tool execution, sync and
async lock release, HTTP disconnect, interruption, stable completion results,
restart recovery and concurrent retries. Live-model agent behavior and packaged
Docker/PyInstaller execution are separate validation lanes.
