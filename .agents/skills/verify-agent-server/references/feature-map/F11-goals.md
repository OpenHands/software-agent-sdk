# Goal loop

A `/goal` loop pursues one objective inside an existing conversation: the
server posts the objective as a user message, runs the agent, asks the
agent's own LLM to judge the transcript against the objective, and re-prompts
with the judge's "what is missing" until the judge says complete, the round
cap (`max_iterations`) is reached, or something interrupts it. Everything
happens in the conversation's own history and event stream (no new or forked
conversation). Progress is persisted and pushed as
`ConversationStateUpdateEvent` rows with `key: "goal"` whose `value` is
`{active, status: running|complete|capped|interrupted, iteration,
max_iterations, objective, verdict}`; that is the only place a consumer can
read a goal's state, and it is what `/goal/resume` continues from, also after a
restart. `/goal/stop` cancels only the loop, an ordinary user message
supersedes it, and the loop holds a run slot for its whole lifetime.

Source: `openhands-agent-server/openhands/agent_server/conversation_router.py`, `openhands-agent-server/openhands/agent_server/event_service.py`, `openhands-agent-server/openhands/agent_server/models.py`, `openhands-sdk/openhands/sdk/conversation/goal/`

Needs: `llm`, `tmux`

Launch: `--config-json '{"max_concurrent_runs": 1}'`

Routes: `POST /api/conversations/{conversation_id}/goal`,
`POST /api/conversations/{conversation_id}/goal/stop`,
`POST /api/conversations/{conversation_id}/goal/resume`

## Sub-features

- `F11.goal-validation`: a blank or whitespace objective returns 400 `Goal objective must not be empty.`, `max_iterations` below 1 or a missing objective returns 422, and nothing is recorded.
- `F11.goal-unknown-id`: all three routes return 404 for an unknown conversation id and 422 for a malformed one.
- `F11.goal-auth`: all three routes return 401 without a session key or with a wrong one, while the right key is accepted.
- `F11.goal-stop-noop`: `/goal/stop` with no active loop returns 200 and records nothing.
- `F11.goal-resume-none`: `/goal/resume` on a conversation that never had a goal returns 400 `no_resumable_goal` and records nothing.
- `F11.goal-complete`: on DeepSeek flash a small objective runs to a goal status `complete` (`active: false`, a verdict with `complete: true`), the agent's file is in the workspace and the conversation is `finished`.
- `F11.goal-ws`: the events socket pushes the goal status as `ConversationStateUpdateEvent` frames with `key: "goal"`: `running` at the start, then the terminal status.
- `F11.goal-in-place`: the loop creates no conversation; the objective is a user message and the agent's tool work is in the same conversation's event log.
- `F11.goal-followup`: an incomplete verdict records a `running` status with `iteration: 1` and that verdict, then posts a follow-up user message carrying the judge's `missing` text and runs another round.
- `F11.goal-capped`: when no verdict says complete, the loop stops after `max_iterations` rounds with `status: capped`, `iteration` equal to the cap and the last verdict, after exactly one agent and one judge call per round.
- `F11.goal-resume-finished`: `/goal/resume` after a `complete` or `capped` goal returns 400 `no_resumable_goal`.
- `F11.goal-busy`: while a goal round is in flight, a second `/goal`, `/goal/resume` and `/run` return 409 and add no goal status; an omitted `max_iterations` defaults to 10.
- `F11.goal-stop`: `/goal/stop` on an active loop returns 200 at once and records `interrupted` (`active: false`) with the same objective and iteration, but cancels only the loop: the in-flight round keeps the conversation `running` until it is interrupted.
- `F11.goal-superseded`: a user message posted through `POST /events` while a loop is active cancels the loop (`interrupted`) and is recorded.
- `F11.goal-superseded-socket`: a user message sent over the events socket cancels an active loop the same way.
- `F11.goal-interrupt-run`: interrupting the conversation during a goal round leaves it `paused` and the goal `interrupted`.
- `F11.goal-busy-run`: `/goal` and `/goal/resume` while an ordinary run is active return 409 and add no goal status.
- `F11.goal-resume`: `/goal/resume` after a stop continues from the persisted objective, cap and iteration with a resume prompt (not the objective again) and runs to `complete`.
- `F11.goal-restart-resume`: an interrupted goal's status survives a restart and `/goal/resume` continues it afterwards.
- `F11.goal-run-error`: a round whose model provider is unreachable ends the conversation in `error` (with a `ConversationErrorEvent`) and the goal `interrupted`, which stays resumable.
- `F11.goal-judge-error`: a judge call that fails after a successful round records `interrupted` and leaves the conversation `finished`.
- `F11.goal-capacity`: between rounds (conversation `finished`, judge call in flight) the loop stays busy (409 on its own conversation) and holds its run slot, so with the capacity full `/goal` and `/goal/resume` elsewhere return 429 and record nothing; stopping the loop frees the slot.
- `F11.goal-delete-active`: deleting a conversation whose loop is active answers 200 promptly, the conversation is gone, and the loop's run slot is returned (the next `/goal` elsewhere is admitted, not 429).
- `F11.goal-cap-overrun`: a goal resumed after its last round's judge call failed never reports more rounds than `max_iterations` (no goal status has `iteration` above the cap).
- `F11.goal-restart-active`: a loop that was active when the server restarted is reported `interrupted` (`active: false`) after the restart instead of `running`.

## How to get to it (agent POV)

- REST: `POST /api/conversations/{conversation_id}/goal` with
  `{"objective": "...", "max_iterations": 3}` (`StartGoalRequest`;
  `max_iterations` defaults to 10, minimum 1). Returns 200
  `{"success": true}` as soon as the loop is scheduled; 400
  `Goal objective must not be empty.` (blank objective), 404 unknown id,
  409 `Conversation run or goal loop already running.`, 429
  `Conversation run limit reached. Retry the request later.`, 422 for a bad
  body or id.
- REST: `POST /api/conversations/{conversation_id}/goal/stop` (no body) is
  always 200 `{"success": true}` for a known id, whether or not a loop was
  active; `POST /api/conversations/{conversation_id}/goal/resume` (no body)
  is 200, or 400 `no_resumable_goal` (no goal yet, or the last one ended
  `complete`/`capped`), 409 and 429 like `/goal`.
- Second views (owned by other families, used here as proof):
  `GET /api/conversations/{conversation_id}/events/search` (the goal rows
  are `ConversationStateUpdateEvent` with `key: "goal"`;
  `control-agent-server conversation events <id> --key goal` filters them),
  `GET /api/conversations/{conversation_id}/events/count` with `source` and
  `body` filters (the objective, follow-up and resume messages),
  `GET /api/conversations/{conversation_id}` (`execution_status`),
  `GET /api/conversations/count`, the events socket
  `/sockets/events/{conversation_id}` (`key: "goal"` frames), the conversation
  workspace on disk, and the model requests an `llm-stub` recorded.
- Implicit entry points: an ordinary user message, through
  `POST /api/conversations/{conversation_id}/events` or a message frame on
  the events socket, stops the active loop (`F11.goal-superseded`,
  `F11.goal-superseded-socket`); `/interrupt` ends the current round and the
  loop with it; `DELETE /api/conversations/{conversation_id}` and a graceful
  shutdown close the loop without recording a status
  (`F11.goal-delete-active`, `F11.goal-restart-active`).
- TypeScript client: `ConversationClient.startGoal(conversationId,
  {objective, max_iterations})`, `stopGoal(conversationId)`,
  `resumeGoal(conversationId)`
  (`clients/typescript/src/client/conversation-client.ts`) and
  `RemoteConversation.startGoal(objective, maxIterations?)`, `stopGoal()`,
  `resumeGoal()` (`clients/typescript/src/conversation/remote-conversation.ts`).
- SDK: the Python `RemoteConversation` has no goal method; the SDK's
  `run_goal(conversation, objective, judge_llm, max_iterations=...)`
  (`openhands-sdk/openhands/sdk/conversation/goal/runner.py`) is the
  in-process synchronous driver over the same `GoalController` and judge the
  server uses. Agent Canvas renders the goal chip from the `goal` status
  events.
- Config: `max_concurrent_runs` (`OH_MAX_CONCURRENT_RUNS`, default 10); the
  loop reserves one slot for its whole lifetime. This family launches with 1.
- Recipes below use REST through `api`, the events socket through `ws start`
  (opened before the action) and `ws listen --send`, a real DeepSeek flash
  agent for the happy path (`F11.goal-complete`), and `fixture llm-stub` for
  what a real model cannot do on demand: an exact verdict (always
  incomplete, or complete), a model call that hangs (to hold a round in
  flight), and a judge call that fails or hangs after a good round. A stub's
  one reply serves both as the agent's answer and as the judge's verdict.

## Driving it with control-agent-server

Preconditions:

- A run launched with this family's `Launch:` flags
  (`launch --new --config-json '{"max_concurrent_runs": 1}'`), `doctor` ok,
  `$DEEPSEEK_API_KEY` set (the block below saves the DeepSeek profiles) and
  `tmux` for the real agent's `terminal` tool. With one run slot every
  bullet that starts a round must find the slot free, so the bullets run
  strictly in order and each one that holds the slot frees it (the loop ends,
  or `/interrupt`) before it ends. `F11.goal-capacity` depends on the limit
  of 1.
- The block below creates an unknown id `$NOPE`, six stubs and thirteen
  conversations without an initial message (an initial message starts a run,
  and creating a conversation needs a free slot). Stubs: `qa-f11-no` always
  answers the verdict `{"score": 0.2, "complete": false, "missing":
  "QA_F11_MISSING"}`; `qa-f11-hang` hangs every call for 300 s until
  `F11.goal-resume` rewrites it to a complete verdict; `qa-f11-judge` and
  `qa-f11-judge2` answer once, then fail with 500 (so the round succeeds
  and the judge fails); `qa-f11-slot` answers once, then hangs 60 s (a judge
  call in flight); `qa-f11-park` hangs every call. Conversations: `G` on
  DeepSeek flash with the `terminal` tool; `X` and `W` on `qa-f11-no`; `H`,
  `U`, `V`, `I` on `qa-f11-hang`; `E` on a provider that refuses
  connections (`127.0.0.1:9`); `J` on `qa-f11-judge`; `K` on `qa-f11-slot`;
  `J2` on `qa-f11-judge2`; `D` and `R` on `qa-f11-park`. Every stub agent has
  `tools: []` and `num_retries: 0`.

```sh
control-agent-server llm preset deepseek
NOPE=$(python3 -c 'import uuid; print(uuid.uuid4())')
OFFLINE_AGENT='{"llm": {"model": "openai/qa-offline", "api_key": "qa-offline-key", "base_url": "http://127.0.0.1:9/v1", "num_retries": 0}, "tools": []}'
NO=$(control-agent-server fixture llm-stub --name qa-f11-no --step 'reply:{"score": 0.2, "complete": false, "missing": "QA_F11_MISSING"}' --print-path)
NO_AGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$NO/v1\", \"num_retries\": 0}, \"tools\": []}"
HANG=$(control-agent-server fixture llm-stub --name qa-f11-hang --step hang:300 --print-path)
HANG_AGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$HANG/v1\", \"num_retries\": 0}, \"tools\": []}"
JUDGE=$(control-agent-server fixture llm-stub --name qa-f11-judge --step reply:QA_F11_ROUND_DONE --step status:500 --print-path)
JUDGE_AGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$JUDGE/v1\", \"num_retries\": 0}, \"tools\": []}"
JUDGE2=$(control-agent-server fixture llm-stub --name qa-f11-judge2 --step reply:QA_F11_ROUND_DONE --step status:500 --print-path)
JUDGE2_AGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$JUDGE2/v1\", \"num_retries\": 0}, \"tools\": []}"
SLOT=$(control-agent-server fixture llm-stub --name qa-f11-slot --step reply:QA_F11_ROUND_DONE --step hang:60 --print-path)
SLOT_AGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$SLOT/v1\", \"num_retries\": 0}, \"tools\": []}"
PARK=$(control-agent-server fixture llm-stub --name qa-f11-park --step hang:300 --print-path)
PARK_AGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$PARK/v1\", \"num_retries\": 0}, \"tools\": []}"
G=$(control-agent-server conversation start --tools terminal --no-autotitle --print-id)
X=$(control-agent-server conversation start --body-json "{\"agent\": $NO_AGENT}" --no-autotitle --print-id)
W=$(control-agent-server conversation start --body-json "{\"agent\": $NO_AGENT}" --no-autotitle --print-id)
H=$(control-agent-server conversation start --body-json "{\"agent\": $HANG_AGENT}" --no-autotitle --print-id)
U=$(control-agent-server conversation start --body-json "{\"agent\": $HANG_AGENT}" --no-autotitle --print-id)
V=$(control-agent-server conversation start --body-json "{\"agent\": $HANG_AGENT}" --no-autotitle --print-id)
I=$(control-agent-server conversation start --body-json "{\"agent\": $HANG_AGENT}" --no-autotitle --print-id)
E=$(control-agent-server conversation start --body-json "{\"agent\": $OFFLINE_AGENT}" --no-autotitle --print-id)
J=$(control-agent-server conversation start --body-json "{\"agent\": $JUDGE_AGENT}" --no-autotitle --print-id)
K=$(control-agent-server conversation start --body-json "{\"agent\": $SLOT_AGENT}" --no-autotitle --print-id)
J2=$(control-agent-server conversation start --body-json "{\"agent\": $JUDGE2_AGENT}" --no-autotitle --print-id)
D=$(control-agent-server conversation start --body-json "{\"agent\": $PARK_AGENT}" --no-autotitle --print-id)
R=$(control-agent-server conversation start --body-json "{\"agent\": $PARK_AGENT}" --no-autotitle --print-id)
control-agent-server api GET /api/conversations/count --query status=idle --check . eq 13
```

- **Invalid goal requests (`F11.goal-validation`).** Bad bodies on `X`,
  which has never run.
  ```sh
  N=$(control-agent-server api GET "/api/conversations/$X/events/count" --field .)
  control-agent-server api POST "/api/conversations/$X/goal" --json '{"objective": ""}' --expect 400 --check detail eq 'Goal objective must not be empty.' --save F11.goal-validation/empty
  control-agent-server api POST "/api/conversations/$X/goal" --json '{"objective": "   ", "max_iterations": 2}' --expect 400 --check detail eq 'Goal objective must not be empty.'
  control-agent-server api POST "/api/conversations/$X/goal" --json '{"objective": "QA_F11 x", "max_iterations": 0}' --expect 422 --check detail.0.loc.1 eq max_iterations --save F11.goal-validation/zero-rounds
  control-agent-server api POST "/api/conversations/$X/goal" --json '{"max_iterations": 2}' --expect 422 --check detail.0.loc.1 eq objective
  control-agent-server api POST "/api/conversations/$X/goal" --expect 422
  control-agent-server api GET "/api/conversations/$X/events/count" --check . eq "$N" --save F11.goal-validation/count
  control-agent-server api GET "/api/conversations/$X" --check execution_status eq idle
  ```
  A blank objective is a 400 from the goal controller (the model accepts any
  string), a cap of 0 or a missing objective is a 422 that names the field,
  and the event count and `idle` status are unchanged.
- **Unknown and malformed ids (`F11.goal-unknown-id`).**
  ```sh
  control-agent-server api POST "/api/conversations/$NOPE/goal" --json '{"objective": "QA_F11 x"}' --expect 404 --save F11.goal-unknown-id/goal
  control-agent-server api POST "/api/conversations/$NOPE/goal/stop" --expect 404 --save F11.goal-unknown-id/stop
  control-agent-server api POST "/api/conversations/$NOPE/goal/resume" --expect 404 --save F11.goal-unknown-id/resume
  control-agent-server api POST /api/conversations/not-a-uuid/goal --json '{"objective": "QA_F11 x"}' --expect 422
  control-agent-server api POST /api/conversations/not-a-uuid/goal/stop --expect 422
  control-agent-server api POST /api/conversations/not-a-uuid/goal/resume --expect 422
  ```
  Each unknown id is a 404 and each malformed id a 422.
- **Authentication (`F11.goal-auth`).** The right key gets in first; then no
  key and a wrong key are refused on all three routes.
  ```sh
  control-agent-server api POST "/api/conversations/$X/goal/stop" --expect 200 --check success eq true
  control-agent-server api POST "/api/conversations/$X/goal" --auth none --json '{"objective": "QA_F11 x"}' --expect 401 --save F11.goal-auth/goal-none
  control-agent-server api POST "/api/conversations/$X/goal" --auth bad --json '{"objective": "QA_F11 x"}' --expect 401
  control-agent-server api POST "/api/conversations/$X/goal/stop" --auth none --expect 401
  control-agent-server api POST "/api/conversations/$X/goal/stop" --auth bad --expect 401
  control-agent-server api POST "/api/conversations/$X/goal/resume" --auth none --expect 401
  control-agent-server api POST "/api/conversations/$X/goal/resume" --auth bad --expect 401 --save F11.goal-auth/resume-bad
  control-agent-server conversation events "$X" --key goal --expect-count 0
  ```
  The keyed call is 200, every other call 401, and `X` still has no goal
  status.
- **Stop with nothing to stop (`F11.goal-stop-noop`).**
  ```sh
  N=$(control-agent-server api GET "/api/conversations/$X/events/count" --field .)
  control-agent-server api POST "/api/conversations/$X/goal/stop" --expect 200 --check success eq true --save F11.goal-stop-noop/stop
  control-agent-server api GET "/api/conversations/$X/events/count" --check . eq "$N"
  control-agent-server conversation events "$X" --key goal --expect-count 0 --save F11.goal-stop-noop/goal-events
  ```
  The stop is 200 and records nothing: the answer alone does not say whether
  a loop was stopped.
- **Resume with no goal (`F11.goal-resume-none`).**
  ```sh
  N=$(control-agent-server api GET "/api/conversations/$X/events/count" --field .)
  control-agent-server api POST "/api/conversations/$X/goal/resume" --expect 400 --check detail eq no_resumable_goal --save F11.goal-resume-none/resume
  control-agent-server api GET "/api/conversations/$X/events/count" --check . eq "$N"
  control-agent-server api GET "/api/conversations/$X" --check execution_status eq idle
  ```
  400 `no_resumable_goal`, no new event, still `idle`.
- **A goal on a real model (`F11.goal-complete`).** DeepSeek flash with the
  `terminal` tool; the socket listens from before the start.
  ```sh
  GW=$(control-agent-server api GET "/api/conversations/$G" --field workspace.working_dir)
  NCONV=$(control-agent-server api GET /api/conversations/count --field .)
  control-agent-server ws start "/sockets/events/$G" --name f11-g --duration 600
  control-agent-server api POST "/api/conversations/$G/goal" --json '{"objective": "Create a file named qa-goal.txt in the current directory containing exactly: hi. Then show its contents with cat.", "max_iterations": 3}' --expect 200 --check success eq true --expect-max-ms 2000 --save F11.goal-complete/start
  control-agent-server ws read f11-g --contains '"key": "goal", "value": {"active": false' --wait 400
  control-agent-server conversation events "$G" --key goal --contains '"active": false, "status": "complete"' --expect-count 1 --save F11.goal-complete/goal-events
  control-agent-server conversation events "$G" --key goal --contains '"complete": true' --expect-count 1
  control-agent-server api GET "/api/conversations/$G" --check execution_status eq finished --save F11.goal-complete/after
  test "$(cat "$GW/qa-goal.txt")" = hi
  ```
  `/goal` answers within 2 s (it only schedules the loop); the last goal
  status is `complete` with a verdict `complete: true` (usually after one
  round), `qa-goal.txt` holds `hi` and the conversation is `finished`.
- **Goal status on the socket (`F11.goal-ws`).**
  ```sh
  control-agent-server ws read f11-g --kinds ConversationStateUpdateEvent --contains '"key": "goal", "value": {"active": true, "status": "running", "iteration": 0'
  control-agent-server ws stop f11-g --kinds ConversationStateUpdateEvent --contains '"key": "goal", "value": {"active": false, "status": "complete"' --expect-min 1 --save F11.goal-ws/frames
  ```
  The socket pushed a `running` frame at iteration 0 and the `complete`
  frame, the same `ConversationStateUpdateEvent` rows the log holds.
- **Same conversation, no new one (`F11.goal-in-place`).**
  ```sh
  control-agent-server api GET /api/conversations/count --check . eq "$NCONV" --save F11.goal-in-place/count
  control-agent-server api GET "/api/conversations/$G/events/count" --query source=user --query body='Create a file named qa-goal.txt' --check . ge 1
  control-agent-server conversation events "$G" --kinds ObservationEvent --contains qa-goal.txt --expect-kind ObservationEvent --save F11.goal-in-place/observations
  ```
  The conversation count is unchanged, the objective is a user message in
  `G`, and `G`'s own log holds the `terminal` observations that touched
  `qa-goal.txt`.
- **Incomplete verdict re-prompts (`F11.goal-followup`).** `X`'s stub always
  answers an incomplete verdict, so its loop needs every round; cap 2.
  ```sh
  control-agent-server ws start "/sockets/events/$X" --name f11-x --duration 300
  control-agent-server api POST "/api/conversations/$X/goal" --json '{"objective": "QA_F11 audit objective", "max_iterations": 2}' --expect 200 --save F11.goal-followup/start
  control-agent-server ws read f11-x --contains '"key": "goal", "value": {"active": false' --wait 60
  control-agent-server conversation events "$X" --key goal --contains '"active": true, "status": "running", "iteration": 1, "max_iterations": 2, "objective": "QA_F11 audit objective", "verdict": {"score": 0.2, "complete": false, "missing": "QA_F11_MISSING"}' --expect-count 1 --save F11.goal-followup/round-1
  control-agent-server api GET "/api/conversations/$X/events/count" --query source=user --query body='audit iteration 1' --check . eq 1
  control-agent-server api GET "/api/conversations/$X/events/count" --query source=user --query body=QA_F11_MISSING --check . eq 1 --save F11.goal-followup/followup-count
  ```
  After round 1 the loop recorded `running` with `iteration: 1` and the
  judge's verdict, then posted one user message `The goal is NOT yet complete
  (audit iteration 1). Outstanding: QA_F11_MISSING ...` and ran round 2.
- **Round cap (`F11.goal-capped`).** The same loop's end.
  ```sh
  control-agent-server conversation events "$X" --key goal --contains '"active": false, "status": "capped", "iteration": 2, "max_iterations": 2' --expect-count 1 --save F11.goal-capped/goal-events
  control-agent-server conversation events "$X" --key goal --expect-count 3
  control-agent-server ws stop f11-x --contains '"key": "goal", "value": {"active": false, "status": "capped"' --save F11.goal-capped/frames
  control-agent-server sink read --name qa-f11-no --expect-min 4 --expect-max 4 --save F11.goal-capped/model-calls
  control-agent-server sink read --name qa-f11-no --contains 'auditing whether a long-running GOAL' --expect-min 2 --expect-max 2
  control-agent-server api GET "/api/conversations/$X" --check execution_status eq finished
  ```
  Three goal statuses (`running` at 0, `running` at 1, `capped` at 2 with the
  last verdict), the `capped` frame on the socket, and four model calls: two
  agent rounds and two judge calls.
- **Nothing to resume after the end (`F11.goal-resume-finished`).**
  ```sh
  NG=$(control-agent-server conversation events "$G" --key goal | jq -e .matching)
  control-agent-server api POST "/api/conversations/$X/goal/resume" --expect 400 --check detail eq no_resumable_goal --save F11.goal-resume-finished/capped
  control-agent-server api POST "/api/conversations/$G/goal/resume" --expect 400 --check detail eq no_resumable_goal --save F11.goal-resume-finished/complete
  control-agent-server conversation events "$X" --key goal --expect-count 3
  control-agent-server conversation events "$G" --key goal | jq -e ".matching == $NG"
  ```
  Both are 400 `no_resumable_goal` and add no goal status (`G` has one
  `running` status per round plus the `complete` one).
- **Busy while a round is in flight (`F11.goal-busy`).** `H`'s model call
  hangs, which holds the first round open. No `max_iterations` is sent.
  ```sh
  control-agent-server ws start "/sockets/events/$H" --name f11-h --duration 300
  control-agent-server api POST "/api/conversations/$H/goal" --json '{"objective": "QA_F11 busy objective"}' --expect 200 --save F11.goal-busy/start
  control-agent-server conversation wait "$H" --until running --timeout 30
  control-agent-server api POST "/api/conversations/$H/goal" --json '{"objective": "QA_F11 second objective", "max_iterations": 1}' --expect 409 --check detail eq 'Conversation run or goal loop already running.' --save F11.goal-busy/second-goal
  control-agent-server api POST "/api/conversations/$H/goal/resume" --expect 409 --check detail eq 'Conversation run or goal loop already running.' --save F11.goal-busy/resume
  control-agent-server api POST "/api/conversations/$H/run" --expect 409 --check detail eq 'Conversation already running. Wait for completion or pause first.' --save F11.goal-busy/run
  control-agent-server conversation events "$H" --key goal --contains '"active": true, "status": "running", "iteration": 0, "max_iterations": 10, "objective": "QA_F11 busy objective"' --expect-count 1
  control-agent-server conversation events "$H" --key goal --expect-count 1 --save F11.goal-busy/goal-events
  control-agent-server sink read --name qa-f11-hang --expect-min 1 --expect-max 1
  ```
  The second `/goal`, `/goal/resume` and `/run` are 409, the only goal
  status is the first loop's `running` with the default cap of 10, and the
  stub saw exactly one model call (the one still hanging).
- **Stop an active loop (`F11.goal-stop`).**
  ```sh
  control-agent-server api POST "/api/conversations/$H/goal/stop" --expect 200 --check success eq true --expect-max-ms 3000 --save F11.goal-stop/stop
  control-agent-server ws read f11-h --contains '"key": "goal", "value": {"active": false, "status": "interrupted", "iteration": 0, "max_iterations": 10, "objective": "QA_F11 busy objective"' --wait 10
  control-agent-server conversation events "$H" --key goal --contains '"status": "interrupted"' --expect-count 1 --save F11.goal-stop/goal-events
  control-agent-server api GET "/api/conversations/$H" --check execution_status eq running --save F11.goal-stop/still-running
  control-agent-server api POST "/api/conversations/$H/goal/resume" --expect 409
  control-agent-server api POST "/api/conversations/$H/goal/stop" --expect 200
  control-agent-server conversation events "$H" --key goal --expect-count 2
  control-agent-server api POST "/api/conversations/$H/interrupt" --expect 200
  control-agent-server conversation wait "$H" --until paused --timeout 30
  control-agent-server ws stop f11-h --save F11.goal-stop/frames
  ```
  The stop answers at once and records `interrupted` with the same
  objective, cap and iteration (pushed on the socket). The round that was in
  flight keeps running (so `/goal/resume` is 409), a second stop records
  nothing, and `/interrupt` ends the round: `paused`.
- **A user message supersedes the loop (`F11.goal-superseded`).**
  ```sh
  control-agent-server api POST "/api/conversations/$U/goal" --json '{"objective": "QA_F11 supersede objective", "max_iterations": 3}' --expect 200
  control-agent-server conversation wait "$U" --until running --timeout 30
  control-agent-server conversation send "$U" --text 'QA_F11_USER_INTERJECTION' --no-run
  control-agent-server conversation events "$U" --key goal --contains '"active": false, "status": "interrupted", "iteration": 0, "max_iterations": 3, "objective": "QA_F11 supersede objective"' --expect-count 1 --save F11.goal-superseded/goal-events
  control-agent-server api GET "/api/conversations/$U/events/count" --query source=user --query body=QA_F11_USER_INTERJECTION --check . eq 1 --save F11.goal-superseded/message
  control-agent-server api POST "/api/conversations/$U/interrupt" --expect 200
  control-agent-server conversation wait "$U" --until paused --timeout 30
  ```
  `POST /events` returned only after the loop was cancelled: the goal is
  `interrupted` and the message is in the log. The round in flight is then
  interrupted to free the slot.
- **A socket message supersedes the loop (`F11.goal-superseded-socket`).**
  ```sh
  control-agent-server api POST "/api/conversations/$V/goal" --json '{"objective": "QA_F11 socket objective", "max_iterations": 3}' --expect 200
  control-agent-server conversation wait "$V" --until running --timeout 30
  control-agent-server ws listen "/sockets/events/$V" --send '{"role": "user", "content": [{"type": "text", "text": "QA_F11_SOCKET_INTERJECTION"}], "run": true}' --until-kind ConversationStateUpdateEvent --until key=goal --until value.status=interrupted --duration 20 --save F11.goal-superseded-socket/frames
  control-agent-server conversation events "$V" --key goal --contains '"active": false, "status": "interrupted", "iteration": 0, "max_iterations": 3, "objective": "QA_F11 socket objective"' --expect-count 1 --save F11.goal-superseded-socket/goal-events
  control-agent-server api GET "/api/conversations/$V/events/count" --query source=user --query body=QA_F11_SOCKET_INTERJECTION --check . eq 1
  control-agent-server api POST "/api/conversations/$V/interrupt" --expect 200
  control-agent-server conversation wait "$V" --until paused --timeout 30
  ```
  The socket that sent the message receives the `interrupted` goal frame,
  and the log holds the status and the message.
- **Interrupt the round (`F11.goal-interrupt-run`).**
  ```sh
  control-agent-server api POST "/api/conversations/$I/goal" --json '{"objective": "QA_F11 interrupt objective", "max_iterations": 3}' --expect 200
  control-agent-server conversation wait "$I" --until running --timeout 30
  control-agent-server ws start "/sockets/events/$I" --name f11-i --duration 120
  control-agent-server api POST "/api/conversations/$I/interrupt" --expect 200 --save F11.goal-interrupt-run/interrupt
  control-agent-server ws read f11-i --contains '"key": "goal", "value": {"active": false, "status": "interrupted"' --wait 20
  control-agent-server conversation wait "$I" --until paused --timeout 30
  control-agent-server conversation events "$I" --key goal --contains '"status": "interrupted"' --expect-count 1 --save F11.goal-interrupt-run/goal-events
  control-agent-server ws stop f11-i --contains '"key": "execution_status", "value": "paused"' --save F11.goal-interrupt-run/frames
  ```
  The round ends `paused` and the loop, seeing a paused run, records
  `interrupted` instead of judging it.
- **Busy with an ordinary run (`F11.goal-busy-run`).** `I` now has a
  resumable goal; an ordinary run is started on it.
  ```sh
  control-agent-server conversation send "$I" --text 'QA_F11 plain run'
  control-agent-server conversation wait "$I" --until running --timeout 30
  control-agent-server api POST "/api/conversations/$I/goal" --json '{"objective": "QA_F11 refused objective", "max_iterations": 1}' --expect 409 --check detail eq 'Conversation run or goal loop already running.' --save F11.goal-busy-run/goal
  control-agent-server api POST "/api/conversations/$I/goal/resume" --expect 409 --check detail eq 'Conversation run or goal loop already running.' --save F11.goal-busy-run/resume
  control-agent-server conversation events "$I" --key goal --expect-count 2
  control-agent-server api POST "/api/conversations/$I/interrupt" --expect 200
  control-agent-server conversation wait "$I" --until paused --timeout 30
  ```
  Both are 409 and the goal statuses are unchanged (the earlier `running`
  and `interrupted`).
- **Resume a stopped loop (`F11.goal-resume`).** `qa-f11-hang` now answers a
  complete verdict, so the resumed round and its audit finish at once.
  ```sh
  control-agent-server fixture llm-stub --name qa-f11-hang --step 'reply:{"score": 1.0, "complete": true, "missing": ""}'
  control-agent-server ws start "/sockets/events/$H" --name f11-h2 --duration 120
  control-agent-server api POST "/api/conversations/$H/goal/resume" --expect 200 --check success eq true --save F11.goal-resume/resume
  control-agent-server ws read f11-h2 --contains '"key": "goal", "value": {"active": false, "status": "complete", "iteration": 1, "max_iterations": 10, "objective": "QA_F11 busy objective"' --wait 60
  control-agent-server conversation events "$H" --key goal --contains '"active": true, "status": "running", "iteration": 0, "max_iterations": 10, "objective": "QA_F11 busy objective"' --expect-count 2 --save F11.goal-resume/goal-events
  control-agent-server api GET "/api/conversations/$H/events/count" --query source=user --query body='Resuming a goal that was paused or interrupted' --check . eq 1 --save F11.goal-resume/resume-prompt
  control-agent-server api GET "/api/conversations/$H/events/count" --query source=user --query body='QA_F11 busy objective' --check . eq 1
  control-agent-server api GET "/api/conversations/$H" --check execution_status eq finished
  control-agent-server ws stop f11-h2
  ```
  The resumed loop starts a second `running` status from the stored
  objective, cap and iteration, sends the resume prompt instead of the
  objective (still one objective message), and ends `complete` at
  iteration 1 of 10.
- **Resume after a restart (`F11.goal-restart-resume`).** `U`'s goal was
  superseded; its stub now answers a complete verdict.
  ```sh
  control-agent-server restart
  control-agent-server conversation events "$U" --key goal --expect-count 2
  control-agent-server conversation events "$U" --key goal --contains '"active": false, "status": "interrupted"' --expect-count 1 --save F11.goal-restart-resume/after-restart
  control-agent-server ws start "/sockets/events/$U" --name f11-u --duration 120
  control-agent-server api POST "/api/conversations/$U/goal/resume" --expect 200 --save F11.goal-restart-resume/resume
  control-agent-server ws read f11-u --contains '"key": "goal", "value": {"active": false, "status": "complete", "iteration": 1, "max_iterations": 3, "objective": "QA_F11 supersede objective"' --wait 60
  control-agent-server conversation events "$U" --key goal --expect-count 4 --save F11.goal-restart-resume/goal-events
  control-agent-server ws stop f11-u
  ```
  The persisted statuses come back with the conversation, and the resume
  continues the stored objective and cap to `complete`.
- **The round's provider fails (`F11.goal-run-error`).**
  ```sh
  control-agent-server ws start "/sockets/events/$E" --name f11-e --duration 300
  control-agent-server api POST "/api/conversations/$E/goal" --json '{"objective": "QA_F11 offline objective", "max_iterations": 3}' --expect 200 --save F11.goal-run-error/start
  control-agent-server ws read f11-e --contains '"key": "goal", "value": {"active": false, "status": "interrupted", "iteration": 0' --wait 60
  control-agent-server api GET "/api/conversations/$E" --check execution_status eq error --save F11.goal-run-error/after
  control-agent-server conversation events "$E" --kinds ConversationErrorEvent --expect-kind ConversationErrorEvent
  control-agent-server api POST "/api/conversations/$E/goal/resume" --expect 200 --save F11.goal-run-error/resume
  control-agent-server ws read f11-e --contains '"key": "goal", "value": {"active": false, "status": "interrupted"' --expect-min 2 --wait 60
  control-agent-server conversation events "$E" --key goal --expect-count 4 --save F11.goal-run-error/goal-events
  control-agent-server ws stop f11-e
  ```
  The round ends in `error` with a `ConversationErrorEvent`, the goal is
  `interrupted` without a judge call, and it stays resumable: the resume is
  200 and fails the same way (a second `running`/`interrupted` pair).
- **The judge fails (`F11.goal-judge-error`).** `J`'s stub answers the round,
  then returns 500 to the judge.
  ```sh
  control-agent-server ws start "/sockets/events/$J" --name f11-j --duration 120
  control-agent-server api POST "/api/conversations/$J/goal" --json '{"objective": "QA_F11 judge objective", "max_iterations": 3}' --expect 200 --save F11.goal-judge-error/start
  control-agent-server ws read f11-j --contains '"key": "goal", "value": {"active": false, "status": "interrupted"' --wait 60
  control-agent-server conversation events "$J" --key goal --contains '"active": false, "status": "interrupted"' --expect-count 1 --save F11.goal-judge-error/goal-events
  control-agent-server conversation events "$J" --key goal --contains '"verdict": null' --expect-count 2
  control-agent-server api GET "/api/conversations/$J" --check execution_status eq finished
  control-agent-server sink read --name qa-f11-judge --contains 'auditing whether a long-running GOAL' --expect-min 1 --expect-max 1 --save F11.goal-judge-error/model-calls
  control-agent-server ws stop f11-j
  ```
  The round finished, the one judge call failed, and the loop recorded
  `interrupted` with no verdict instead of hanging or reporting `running`.
- **Capacity (`F11.goal-capacity`).** `K`'s round finishes and its judge
  call hangs 60 s: between rounds the conversation is `finished`, but the
  loop keeps the only run slot.
  ```sh
  control-agent-server ws start "/sockets/events/$K" --name f11-k --duration 300
  control-agent-server api POST "/api/conversations/$K/goal" --json '{"objective": "QA_F11 slot objective", "max_iterations": 3}' --expect 200
  control-agent-server conversation wait "$K" --until finished --timeout 60
  control-agent-server api POST "/api/conversations/$K/goal" --json '{"objective": "QA_F11 second objective", "max_iterations": 1}' --expect 409 --check detail eq 'Conversation run or goal loop already running.' --save F11.goal-capacity/own-goal-409
  control-agent-server api POST "/api/conversations/$K/goal/resume" --expect 409 --check detail eq 'Conversation run or goal loop already running.'
  NE=$(control-agent-server api GET "/api/conversations/$E/events/count" --field .)
  control-agent-server api POST "/api/conversations/$W/goal" --json '{"objective": "QA_F11 refused objective", "max_iterations": 1}' --expect 429 --check detail eq 'Conversation run limit reached. Retry the request later.' --save F11.goal-capacity/goal-429
  control-agent-server api POST "/api/conversations/$E/goal/resume" --expect 429 --save F11.goal-capacity/resume-429
  control-agent-server sink read --name qa-f11-slot --contains 'auditing whether a long-running GOAL' --expect-min 1
  control-agent-server conversation events "$W" --key goal --expect-count 0
  control-agent-server api GET "/api/conversations/$E/events/count" --check . eq "$NE"
  control-agent-server api POST "/api/conversations/$K/goal/stop" --expect 200 --expect-max-ms 3000
  control-agent-server ws read f11-k --contains '"key": "goal", "value": {"active": false, "status": "interrupted"' --wait 10
  control-agent-server ws start "/sockets/events/$W" --name f11-w --duration 120
  control-agent-server api POST "/api/conversations/$W/goal" --json '{"objective": "QA_F11 admitted objective", "max_iterations": 1}' --expect 200 --save F11.goal-capacity/goal-after-stop
  control-agent-server ws read f11-w --contains '"key": "goal", "value": {"active": false, "status": "capped", "iteration": 1, "max_iterations": 1' --wait 60
  control-agent-server ws stop f11-k
  control-agent-server ws stop f11-w
  ```
  While the judge call runs, `K` is `finished` but its own `/goal` and
  `/goal/resume` are 409 (the loop is still active), and `/goal` on `W` and
  `/goal/resume` on `E` are 429 and record nothing. The stop returns at once (the judge call is
  abandoned), and the same `/goal` on `W` is then admitted and runs its one
  round to `capped`.
- **Delete a conversation with an active loop (`F11.goal-delete-active`).**
  `D`'s round hangs; deleting `D` closes its loop, and the only run slot
  must come back.
  ```sh
  control-agent-server api POST "/api/conversations/$D/goal" --json '{"objective": "QA_F11 delete objective", "max_iterations": 3}' --expect 200
  control-agent-server conversation wait "$D" --until running --timeout 30
  control-agent-server api DELETE "/api/conversations/$D" --expect 200 --expect-max-ms 5000 --save F11.goal-delete-active/delete
  control-agent-server api GET "/api/conversations/$D" --expect 404
  control-agent-server ws start "/sockets/events/$W" --name f11-w2 --duration 120
  control-agent-server api POST "/api/conversations/$W/goal" --json '{"objective": "QA_F11 after delete objective", "max_iterations": 1}' --expect 200 --save F11.goal-delete-active/goal-after-delete
  control-agent-server ws read f11-w2 --contains '"key": "goal", "value": {"active": false, "status": "capped", "iteration": 1, "max_iterations": 1, "objective": "QA_F11 after delete objective"' --wait 60
  control-agent-server ws stop f11-w2
  ```
  The delete answers 200 within 5 s (about 0.1 s; teardown cancels the loop
  and the hanging round), `D` is 404, and with one run slot the next
  `/goal` on `W` is admitted and runs to `capped`: the deleted loop did not
  keep its slot. Nothing is recorded for `D` (it no longer exists).
- **A resume after a failed last audit overruns the cap (`F11.goal-cap-overrun`), known bug.**
  `J2`'s judge fails on a one-round goal; then its stub answers an
  incomplete verdict and the goal is resumed. `max_iterations` is documented
  as the hard cap on audit rounds and `GoalStatus.iteration` as "audit rounds
  completed so far", so whatever the resume does, no status may report more
  rounds than the cap; the resumed loop should end `capped` at 1 of 1 (or
  cap at once). Today `GoalController.on_run_finished` counts the round
  before the judge call, so the failed audit is recorded as `interrupted`
  at 1 of 1 (a unit test pins the same count for a `/goal/stop` during the
  judge call), and the resume runs another agent round and audit and ends
  `capped` at 2 of 1.
  ```sh
  control-agent-server ws start "/sockets/events/$J2" --name f11-j2 --duration 120
  control-agent-server api POST "/api/conversations/$J2/goal" --json '{"objective": "QA_F11 overrun objective", "max_iterations": 1}' --expect 200
  control-agent-server ws read f11-j2 --contains '"key": "goal", "value": {"active": false, "status": "interrupted"' --wait 60
  control-agent-server fixture llm-stub --name qa-f11-judge2 --step 'reply:{"score": 0.1, "complete": false, "missing": "QA_F11_MISSING"}'
  control-agent-server api POST "/api/conversations/$J2/goal/resume" --expect 200 --save F11.goal-cap-overrun/resume
  control-agent-server ws read f11-j2 --contains '"key": "goal", "value": {"active": false, "status": "capped"' --wait 60
  control-agent-server ws stop f11-j2
  control-agent-server conversation events "$J2" --key goal --contains '"active": false, "status": "interrupted"' --expect-count 1
  control-agent-server conversation events "$J2" --key goal --contains '"active": false, "status": "capped"' --expect-count 1
  control-agent-server conversation events "$J2" --key goal --show 50 --full --save F11.goal-cap-overrun/goal-events | jq -e '[.events[].value | select(.iteration > .max_iterations)] | length == 0'  # bug
  ```
  The arrange steps and positive controls hold (the failed audit recorded
  one `interrupted` status, the resume was admitted and ended with one
  `capped` status); the marked `jq` check fails today: the `capped` status
  has `iteration: 2, max_iterations: 1` (the four statuses are `running`
  0/1, `interrupted` 1/1, `running` 1/1, `capped` 2/1).
- **A loop active at a restart stays "running" (`F11.goal-restart-active`), known bug.**
  A graceful shutdown cancels the loop but skips the `interrupted` status
  (`_closing` is set), so after the restart the last goal status still says
  `active: true, status: running` although no loop runs (the conversation
  comes back `paused`), and `/goal/stop` cannot clear it because there is no
  task to cancel. A goal chip built from the status shows a running goal
  forever. The correct behavior asserted here is that the last status after
  the restart is `active: false`, `interrupted` (still resumable).
  ```sh
  control-agent-server api POST "/api/conversations/$R/goal" --json '{"objective": "QA_F11 restart objective", "max_iterations": 3}' --expect 200
  control-agent-server conversation wait "$R" --until running --timeout 30
  control-agent-server conversation events "$R" --key goal --contains '"active": true, "status": "running", "iteration": 0' --expect-count 1
  control-agent-server restart
  control-agent-server api GET "/api/conversations/$R" --quiet --check execution_status eq paused --save F11.goal-restart-active/after-restart
  control-agent-server conversation events "$R" --key goal --expect-min 1
  control-agent-server conversation events "$R" --key goal --show 1 --full --save F11.goal-restart-active/goal-events | jq -e '.events[-1].value.active == false and .events[-1].value.status == "interrupted"'  # bug
  ```
  Before the restart the loop is active (`running` at iteration 0); after
  it the conversation is `paused` and the goal rows came back, and the
  marked `jq` check fails today: the only goal status is the `running` one
  from before the restart.

## Gotchas

- The goal's state exists only as `ConversationStateUpdateEvent` rows with
  `key: "goal"` (the latest one is current); `ConversationInfo` has no goal
  field. `events/search` and `events/count` filter `kind` by the
  fully-qualified class path (F06), so read them with
  `conversation events <id> --key goal` or the socket.
- `/goal`, `/goal/resume` and `/goal/stop` return before anything happens.
  Wait on the goal status (a `ws start` before the action, then
  `ws read --contains ... --wait`), not on `execution_status`: between rounds
  the conversation is `finished` while the loop is still judging.
- `/goal/stop` is 200 even when nothing was active, and it cancels only the
  loop: a round in flight keeps running (and keeps its run slot) until it
  ends or is interrupted. Use `/interrupt` to stop the agent too.
- Between rounds only `/goal` and `/goal/resume` treat the live loop as busy
  (409); `/run` on the `finished` conversation is accepted (200), makes no
  model call and does not stop the loop.
- Any ordinary user message (REST `POST /events` with `run` true or false,
  or a socket message frame) cancels the active loop first. A socket message
  also asks for a run, which is refused while the old round is in flight.
- The judge is the agent's own LLM (non-streaming), so every round costs one
  extra model call with the whole transcript; a judge answer that is not
  JSON counts as incomplete (score 0), and a judge call that fails ends the
  loop `interrupted`. With a stub the same reply is both the agent's answer
  and the verdict, which is how these recipes script `complete` and
  `capped`.
- A `stuck` round does not end the loop (the judge decides), but `paused`
  and `error` do (`interrupted`). The goal is resumable after `interrupted`
  (and after a stale `running`), never after `complete` or `capped`.
- `/goal/resume` sends a fixed resume prompt, not the objective, and keeps
  the stored iteration count, so a resumed loop has fewer rounds left. The
  round is counted before its judge call, so a failed or stopped audit
  counts too, and resuming from the last round runs one more round past
  the cap (`F11.goal-cap-overrun`).
- The loop reserves a run slot when it starts and keeps it until it ends,
  including judge calls between rounds; with `max_concurrent_runs` reached,
  `/goal` and `/goal/resume` are 429 and record nothing. Creating a
  conversation also needs a free slot, so the recipes create all of theirs
  first.
- Graceful restarts and conversation deletion cancel an active loop without
  recording `interrupted` (`F11.goal-restart-active`); idle eviction never
  happens while a loop is active.
- The blank-objective check lives in the goal controller, not the request
  model, so it is a 400 with `Goal objective must not be empty.` rather than
  a 422.
