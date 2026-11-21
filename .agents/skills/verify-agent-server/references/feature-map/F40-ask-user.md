# Decision cards (ask_user)

When a choice is the user's to make, the agent can ask one question with two
to four options and a recommendation, which a client shows as a card the user
answers in one tap or in their own words. The question is the agent's
`ask_user` tool call: an `ActionEvent` (`tool_name: "ask_user"`) whose
`action` is an `AskUserAction` (`question`, `options` of `{label,
consequence}`, `recommended` index or null), followed by its
`AskUserObservation` saying the question was shown. The call ends the agent's
turn without `finish`, so the conversation is `finished` while the question
waits. A question is pending until a message of the user's own follows it.
The user answers either by writing in the chat (an ordinary message) or by
picking on the card through `POST /api/conversations/{id}/ask_user/answer`,
which saves an `AskUserAnswerEvent` (what the card shows as answered), then
the user message the agent reads (`[Answer to your question: "..."]`,
`Option N: label`, the user's words), and runs the agent. While a question
waits, nothing answers it for the user: a Stop hook that denies completion
does not run over it, and a `/goal` loop stops on it (`interrupted`) and
refuses to start or resume until the user answers.

Decision cards sit behind the user-facing feature flag `ask_user` ("Decision
cards", off by default). While it is on, every new OpenHands-agent
conversation gets the `ask_user` tool without naming it, and the answer route
accepts answers; while it is off, the route answers 409 and new conversations
do not get the tool. Flags are listed with `GET /api/settings/experimental`
and switched with `PATCH /api/settings/experimental/{key}`
`{"enabled": bool}` (owned by F18), stored in
`PersistedSettings.experimental_features`, and read when a conversation
starts. A server inside a Docker runtime takes the user's switches from
`OH_RUNTIME_EXPERIMENTAL_FEATURES` instead, and its route stops checking the
switch because the host checks it before forwarding.

Source: `openhands-tools/openhands/tools/ask_user/`, `openhands-sdk/openhands/sdk/event/ask_user.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py` (`answer_ask`), `openhands-sdk/openhands/sdk/agent/agent.py` (`ends_turn`), `openhands-sdk/openhands/sdk/conversation/goal/controller.py` (`GoalAsked`), `openhands-agent-server/openhands/agent_server/event_router.py` (`ask_user_router`), `openhands-agent-server/openhands/agent_server/event_service.py` (`answer_ask`, goal loop), `openhands-agent-server/openhands/agent_server/models.py` (`AskUserAnswerRequest`), `openhands-agent-server/openhands/agent_server/experimental_features.py`, `openhands-agent-server/openhands/agent_server/tool_router.py`, `clients/typescript/src/client/conversation-client.ts`, `clients/typescript/src/events/types.ts`

Needs: `llm`

Routes: `POST /api/conversations/{conversation_id}/ask_user/answer`

## Sub-features

- `F40.flag-gates`: `GET /api/settings/experimental` lists `ask_user` ("Decision cards") off on a fresh server; with it off the answer route is 409 `Decision cards are turned off` and a new conversation has no `ask_user` tool; `PATCH .../ask_user` `{"enabled": true}` turns it on and a new conversation started without naming the tool has exactly `ask_user`.
- `F40.question-event`: on DeepSeek flash, an agent asked to let the user pick calls `ask_user`: the log and the socket carry an `ActionEvent` (`tool_name: ask_user`, an `AskUserAction` with the question and two options) and its `AskUserObservation` (`is_error: false`, `Your turn ends here`), and the conversation is `finished` with no `finish` call. Same promise: `S16.ask-user-tool`.
- `F40.answer-errors`: an `ask_id` that is not the pending question, an option past the last one and an answer with neither option nor words are 400; a negative `option_index` or a missing `ask_id` is 422; an unknown conversation is 404 and a missing key 401; switching the flag off refuses a valid answer at once (409) while the conversation keeps its tool; none of these saves anything and the question stays pending. Same promise: `S16.ask-user-answer`.
- `F40.answer`: `POST .../ask_user/answer` `{ask_id, option_index, text}` returns the saved `AskUserAnswerEvent` (`source: user`, `option_label`, the words, `parent_id` the question's observation); the user message `[Answer to your question: "<question>"]` / `Option 2: <label>` / words follows it, the agent runs to `finished` and answers after it, the socket pushes both events, and a second answer to the same question is 400.
- `F40.chat-reply`: a chat message answers a pending question in words: after `POST .../events` with the user's reply the agent runs, and the card can no longer be answered (400), with no `AskUserAnswerEvent`. Same promise: `S16.ask-user-chat-reply`.
- `F40.stop-hook-waits`: with a Stop hook that denies the first completion, a turn that ends on `ask_user` stays `finished` with no hook run and no second model turn, while an ordinary turn on the same hook is denied once and continues. Same promise: `S16.ask-user-holds-completion`.
- `F40.goal-asked`: a `/goal` round that ends on the agent's question records goal status `interrupted` (`verdict: null`, iteration 0) without judging or sending a follow-up; the objective message carries `sender: goal`; `/goal` and `/goal/resume` are 400 `pending_ask` while the question waits and add no goal status; after the user answers on the card, `/goal/resume` runs and the judge records `complete`. Same promise: `S04.goal-asked`.
- `F40.goal-abandoned-branch`: after navigating back to a user message before an unanswered question, a new `/goal` runs its round and is judged (`complete` or `capped`, with a verdict) rather than stopping on the abandoned question. Same promise: `S04.goal-asked-branch`.
- `F40.ts-client`: the TypeScript `ConversationClient.answerAskUser` answers a pending question with `optionIndex`, returning an event that `isAskUserAnswerEvent` accepts, and a second answer rejects with a 400 `HttpError` (`That question is not waiting for an answer.`).
- `F40.runtime-env`: with `OH_RUNTIME_EXPERIMENTAL_FEATURES='{"ask_user": true}'` and the stored flag off, new conversations get `ask_user` and the answer route no longer answers 409 (a stale `ask_id` is 400); with `{}` new conversations do not get the tool.

## How to get to it (agent POV)

- REST: `POST /api/conversations/{conversation_id}/ask_user/answer` with
  `{"ask_id", "option_index"?, "text"?}` (`AskUserAnswerRequest`; `ask_id`
  is the `ask_user` `ActionEvent` id, `option_index` counts from 0, `text`
  is at most 10,000 characters). Returns the `AskUserAnswerEvent` as stored,
  then saves the answer message and runs the agent. 400 when `ask_id` is not
  the pending question or the option does not exist, 409 while the feature is
  off, 404 for an unknown conversation; the route also documents 429 when the
  answer is saved but the server's run capacity is full.
- REST: `GET /api/settings/experimental` and
  `PATCH /api/settings/experimental/{key}` (F18) switch the flag; the
  answering-in-the-chat path is the ordinary `POST .../events` (F06).
- REST (F11): `POST .../goal` and `POST .../goal/resume` answer 400
  `pending_ask` while a question waits.
- Agent tool (registered by the agent-server, not in the default preset and
  not user-selectable): `ask_user` from `openhands.tools.ask_user`, with
  `{"question", "options": [{"label", "consequence"}], "recommended"?}`. The
  `ask_user` feature flag adds it to every new OpenHands-agent conversation.
- Events: the question is an `ActionEvent` with an `AskUserAction` and an
  `ObservationEvent` with an `AskUserObservation`; the tap is an
  `AskUserAnswerEvent` (`ask_id`, `option_index`, `option_label`, `text`,
  `source: user`), never sent to the model; the agent reads the user
  `MessageEvent` after it. All of them come through search, get-one and
  `WS /sockets/events/{conversation_id}`.
- SDK: `LocalConversation.answer_ask(ask_id, option_index, text)` (the SDK
  map's S16); `RemoteConversation` has no answer method, so a remote SDK
  program posts to the route.
- TypeScript client: `ConversationClient.answerAskUser(conversationId, askId,
  {optionIndex?, text?})`; the `AskUserAction`, `AskUserObservation` and
  `AskUserAnswerEvent` types and the `isAskUserAnswerEvent` guard
  (`clients/typescript/src/events/types.ts`). Recipe `F40.ts-client` runs
  them on the built package (`clients/typescript/dist`).
- Configuration: `OH_RUNTIME_EXPERIMENTAL_FEATURES` (`F40.runtime-env`).

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported, and `doctor` is ok. The block below
  activates the DeepSeek profile (`$DEEPSEEK_API_KEY`); every question in this
  family comes from the real model, which calls `ask_user` reliably when the
  prompt asks it to let the user choose, so no `llm-stub` is used.

  ```sh
  control-agent-server llm preset deepseek
  ```

- **The feature flag (`F40.flag-gates`).**
  ```sh
  control-agent-server api GET /api/settings/experimental \
    --check 1.key eq ask_user --check 1.label eq 'Decision cards' --check 1.enabled eq false --save F40.flag-gates/list
  OFF=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api GET "/api/conversations/$OFF" --check agent.tools len-eq 0
  control-agent-server api POST "/api/conversations/$OFF/ask_user/answer" --json '{"ask_id": "qa-f40", "option_index": 0}' \
    --expect 409 --check detail eq 'Decision cards are turned off' --save F40.flag-gates/off
  control-agent-server api PATCH /api/settings/experimental/ask_user --json '{"enabled": true}' --expect 200 --check enabled eq true
  ON=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api GET "/api/conversations/$ON" --check agent.tools len-eq 1 --check agent.tools.0.name eq ask_user \
    --save F40.flag-gates/tools
  ```
  The flag stays on for the rest of this family (until `F40.runtime-env`).
- **Model-backed: the agent asks (`F40.question-event`).**
  Requires: `F40.flag-gates`
  ```sh
  QC=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server ws start "/sockets/events/$QC" --name f40-q --duration 600
  control-agent-server conversation send "$QC" --wait --timeout 300 \
    --text 'QA_F40_ASK I want a demo file. Ask me whether to name it qa-a.txt or qa-b.txt, then stop until I answer.'
  control-agent-server api GET "/api/conversations/$QC/events/search" --query kind=ActionEvent \
    --check items len-eq 1 --check items.0.tool_name eq ask_user --check items.0.action.kind eq AskUserAction \
    --check items.0.action.options len-ge 2 --check items.0.action.question exists --save F40.question-event/action
  ASK=$(control-agent-server api GET "/api/conversations/$QC/events/search" --query kind=ActionEvent --field items.0.id)
  control-agent-server api GET "/api/conversations/$QC/events/search" --query kind=ObservationEvent \
    --check items len-eq 1 --check items.0.action_id eq "$ASK" --check items.0.observation.kind eq AskUserObservation \
    --check items.0.observation.is_error eq false --check items.0.observation.content.0.text contains 'Your turn ends here'
  control-agent-server api GET "/api/conversations/$QC" --check execution_status eq finished
  control-agent-server ws stop f40-q --kinds ActionEvent --contains '"tool_name": "ask_user"' --expect-min 1 --save F40.question-event/frames
  ```
  One `ask_user` call and its observation, no `finish`; the conversation is
  `finished` while the question waits.
- **Refused answers (`F40.answer-errors`).**
  Requires: `F40.question-event`
  ```sh
  control-agent-server api POST "/api/conversations/$QC/ask_user/answer" --json '{"ask_id": "qa-f40-stale", "option_index": 0}' \
    --expect 400 --check detail eq 'That question is not waiting for an answer.' --save F40.answer-errors/stale
  control-agent-server api POST "/api/conversations/$QC/ask_user/answer" --json "{\"ask_id\": \"$ASK\", \"option_index\": 7}" \
    --expect 400 --check detail matches '^The question has [0-9] options; there is no option 7\.$'
  control-agent-server api POST "/api/conversations/$QC/ask_user/answer" --json "{\"ask_id\": \"$ASK\", \"text\": \"   \"}" \
    --expect 400 --check detail contains 'An answer picks an option, gives text, or both.'
  control-agent-server api POST "/api/conversations/$QC/ask_user/answer" --json "{\"ask_id\": \"$ASK\", \"option_index\": -1}" --expect 422
  control-agent-server api POST "/api/conversations/$QC/ask_user/answer" --json '{"option_index": 0}' --expect 422
  control-agent-server api POST /api/conversations/00000000-0000-0000-0000-000000000000/ask_user/answer \
    --json "{\"ask_id\": \"$ASK\", \"option_index\": 0}" --expect 404
  control-agent-server api POST "/api/conversations/$QC/ask_user/answer" --auth none --json "{\"ask_id\": \"$ASK\", \"option_index\": 0}" --expect 401
  control-agent-server api PATCH /api/settings/experimental/ask_user --json '{"enabled": false}' --expect 200
  control-agent-server api POST "/api/conversations/$QC/ask_user/answer" --json "{\"ask_id\": \"$ASK\", \"option_index\": 0}" \
    --expect 409 --check detail eq 'Decision cards are turned off' --save F40.answer-errors/flag-off
  control-agent-server api GET "/api/conversations/$QC" --check agent.tools.0.name eq ask_user
  control-agent-server api PATCH /api/settings/experimental/ask_user --json '{"enabled": true}' --expect 200 --check enabled eq true
  control-agent-server api GET "/api/conversations/$QC/events/search" --query kind=AskUserAnswerEvent --check items len-eq 0
  control-agent-server api GET "/api/conversations/$QC/events/count" --query kind=MessageEvent --check . eq 1
  ```
  Nothing is saved and the question is still the latest thing in the log.
- **Answer on the card (`F40.answer`).**
  Requires: `F40.answer-errors`
  ```sh
  OBS=$(control-agent-server api GET "/api/conversations/$QC/events/search" --query kind=ObservationEvent --field items.0.id)
  control-agent-server ws start "/sockets/events/$QC" --name f40-a --duration 600
  control-agent-server api POST "/api/conversations/$QC/ask_user/answer" \
    --json "{\"ask_id\": \"$ASK\", \"option_index\": 1, \"text\": \"QA_F40_WORDS\"}" --expect 200 \
    --check kind eq AskUserAnswerEvent --check source eq user --check ask_id eq "$ASK" --check option_index eq 1 \
    --check option_label exists --check text eq QA_F40_WORDS --check parent_id eq "$OBS" --save F40.answer/answer
  control-agent-server conversation wait "$QC" --until finished --timeout 300
  control-agent-server api GET "/api/conversations/$QC/events/search" --query kind=MessageEvent --query source=user \
    --query sort_order=TIMESTAMP_DESC --check items.0.llm_message.content.0.text matches '^\[Answer to your question: ".+"\]\nOption 2: .+\nQA_F40_WORDS$' \
    --save F40.answer/message
  control-agent-server conversation events "$QC" --kinds ActionEvent,MessageEvent --show 1 --full --save F40.answer/after
  control-agent-server api GET "/api/conversations/$QC/events/count" --query source=agent --check . ge 3
  control-agent-server ws stop f40-a --kinds AskUserAnswerEvent --expect-min 1 --save F40.answer/frames
  control-agent-server api POST "/api/conversations/$QC/ask_user/answer" --json "{\"ask_id\": \"$ASK\", \"option_index\": 0}" \
    --expect 400 --check detail eq 'That question is not waiting for an answer.' --save F40.answer/second-tap
  ```
  The answer event and the message the agent reads are saved in that order,
  the agent runs again (a second agent event after the answer) and the run
  ends `finished`; the card cannot be answered twice.
- **Answer in the chat (`F40.chat-reply`).**
  Requires: `F40.flag-gates`
  ```sh
  CC=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server conversation send "$CC" --wait --timeout 300 \
    --text 'QA_F40_CHAT I want a demo file. Ask me whether to name it qa-a.txt or qa-b.txt, then stop until I answer.'
  CASK=$(control-agent-server api GET "/api/conversations/$CC/events/search" --query kind=ActionEvent --check items.0.tool_name eq ask_user \
    --field items.0.id)
  control-agent-server conversation send "$CC" --wait --timeout 300 --text 'QA_F40_REPLY Use qa-a.txt.'
  control-agent-server api POST "/api/conversations/$CC/ask_user/answer" --json "{\"ask_id\": \"$CASK\", \"option_index\": 0}" \
    --expect 400 --check detail eq 'That question is not waiting for an answer.' --save F40.chat-reply/closed
  control-agent-server api GET "/api/conversations/$CC/events/search" --query kind=AskUserAnswerEvent --check items len-eq 0
  ```
  The reply in words closed the question, so the card refuses a tap.
- **A Stop hook does not run over a question (`F40.stop-hook-waits`).** Both
  conversations carry a Stop hook that denies completion the first time it
  runs (it leaves a marker file per conversation); the control conversation
  asks for a plain answer.
  Requires: `F40.flag-gates`
  ```sh
  M="$AGENT_SERVER_VERIFY_RUN/qa-f40-stop"
  hook() { printf '{"hook_config": {"stop": [{"hooks": [{"command": "test -f %s || { touch %s; echo QA_F40_KEEP_GOING >&2; exit 2; }"}]}]}}' "$M-$1" "$M-$1"; }
  SC=$(control-agent-server conversation start --no-autotitle --tools none --body-json "$(hook ctl)" --print-id)
  control-agent-server conversation send "$SC" --wait --timeout 300 --text 'QA_F40_CTL Reply with the single word ok.'
  test -f "$M-ctl"
  control-agent-server conversation events "$SC" --kinds HookExecutionEvent --expect-min 2
  SA=$(control-agent-server conversation start --no-autotitle --tools none --body-json "$(hook ask)" --print-id)
  control-agent-server conversation send "$SA" --wait --timeout 300 \
    --text 'QA_F40_STOP I want a demo file. Ask me whether to name it qa-a.txt or qa-b.txt, then stop until I answer.'
  control-agent-server api GET "/api/conversations/$SA" --check hook_config.stop len-eq 1 --check agent.tools.0.name eq ask_user
  control-agent-server api GET "/api/conversations/$SA/events/search" --query kind=ActionEvent \
    --check items len-eq 1 --check items.0.tool_name eq ask_user --save F40.stop-hook-waits/action
  control-agent-server conversation events "$SA" --kinds HookExecutionEvent --expect-count 0
  control-agent-server api GET "/api/conversations/$SA" --check execution_status eq finished
  test ! -e "$M-ask"
  ```
  The control turn is denied once (a marker, two hook runs, a second turn);
  the asking turn ends on the question with no hook run and no marker.
- **A goal stops on the question (`F40.goal-asked`).**
  Requires: `F40.flag-gates`
  ```sh
  G=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server ws start "/sockets/events/$G" --name f40-g --duration 900
  control-agent-server api POST "/api/conversations/$G/goal" \
    --json '{"objective": "QA_F40_GOAL Pick the name of a demo file with me: ask me whether to use qa-a.txt or qa-b.txt, then stop until I answer.", "max_iterations": 2}' \
    --expect 200
  control-agent-server ws read f40-g --contains '"key": "goal", "value": {"active": false' --wait 400
  control-agent-server conversation events "$G" --key goal --contains '"active": false, "status": "interrupted", "iteration": 0' \
    --expect-count 1 --save F40.goal-asked/interrupted
  control-agent-server conversation events "$G" --key goal --contains '"verdict": null' --expect-count 2
  control-agent-server api GET "/api/conversations/$G/events/search" --query kind=MessageEvent \
    --check items len-eq 1 --check items.0.sender eq goal
  GASK=$(control-agent-server api GET "/api/conversations/$G/events/search" --query kind=ActionEvent --check items len-eq 1 \
    --check items.0.tool_name eq ask_user --field items.0.id)
  control-agent-server api POST "/api/conversations/$G/goal/resume" --expect 400 --check detail eq pending_ask --save F40.goal-asked/resume
  control-agent-server api POST "/api/conversations/$G/goal" --json '{"objective": "QA_F40 another"}' --expect 400 --check detail eq pending_ask
  control-agent-server conversation events "$G" --key goal --expect-count 2
  control-agent-server api POST "/api/conversations/$G/ask_user/answer" --json "{\"ask_id\": \"$GASK\", \"option_index\": 0}" --expect 200
  control-agent-server conversation wait "$G" --until finished --timeout 300
  control-agent-server api POST "/api/conversations/$G/goal/resume" --expect 200
  control-agent-server ws read f40-g --contains '"key": "goal", "value": {"active": false, "status": "complete"' --wait 400
  control-agent-server ws stop f40-g
  control-agent-server conversation events "$G" --key goal --contains '"status": "complete"' --expect-count 1 --save F40.goal-asked/complete
  ```
  The round ends on the question; the loop stops `interrupted` without a
  verdict and without a follow-up message. Starting or resuming is refused
  until the user answers; then the resumed loop is judged `complete`.
- **A question left on an abandoned branch (`F40.goal-abandoned-branch`), known bug #6907.**
  The agent answers a first message, then asks a question; the conversation
  navigates back to the first message (no question on the active branch) and
  starts a goal with a new objective.
  Requires: `F40.flag-gates`
  ```sh
  NB=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server conversation send "$NB" --wait --timeout 300 --text 'QA_F40_HI Reply with the single word ok.'
  control-agent-server conversation send "$NB" --wait --timeout 300 \
    --text 'QA_F40_NAV I want a demo file. Ask me whether to name it qa-a.txt or qa-b.txt, then stop until I answer.'
  control-agent-server conversation events "$NB" --kinds ObservationEvent --contains '"kind": "AskUserObservation"' --expect-count 1
  HI=$(control-agent-server api GET "/api/conversations/$NB/events/search" --query kind=MessageEvent --query body=QA_F40_HI --field items.0.id)
  control-agent-server api POST "/api/conversations/$NB/navigate" --json "{\"event_id\": \"$HI\"}" --expect 200
  control-agent-server ws start "/sockets/events/$NB" --name f40-nb --duration 600
  control-agent-server api POST "/api/conversations/$NB/goal" --json '{"objective": "QA_F40_NEW Reply with the single word ok.", "max_iterations": 1}' \
    --expect 200 --save F40.goal-abandoned-branch/start
  control-agent-server ws read f40-nb --contains '"key": "goal", "value": {"active": false' --wait 400
  control-agent-server ws stop f40-nb
  control-agent-server conversation events "$NB" --key goal --show 1 --save F40.goal-abandoned-branch/goal | control-agent-server assert --check events.0.value matches '"status": "(complete|capped)"' # bug: value.events.0.value contains interrupted
  ```
  Expected: the start guard sees no question on the active branch, the round
  runs, and the judge records `complete` or `capped` with a verdict. Instead
  the loop stops `interrupted` with `verdict: null` right after its first
  round: its post-round check reads the whole event log, abandoned branch
  included, and the goal message has `sender: goal`, so the old question
  still looks unanswered
  ([#6907](https://github.com/OpenHands/software-agent-sdk/issues/6907), the
  server side of the same defect in the SDK's `run_goal`).
- **TypeScript client (`F40.ts-client`).** A program answers a fresh
  question with `ConversationClient.answerAskUser`, then tries again.
  Needs: `node`
  Requires: `F40.flag-gates`
  ```sh
  test -f clients/typescript/dist/clients.js || (cd clients/typescript && npm ci && npm run build)
  TC=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server conversation send "$TC" --wait --timeout 300 \
    --text 'QA_F40_TS I want a demo file. Ask me whether to name it qa-a.txt or qa-b.txt, then stop until I answer.'
  TASK=$(control-agent-server api GET "/api/conversations/$TC/events/search" --query kind=ActionEvent --check items.0.tool_name eq ask_user \
    --field items.0.id)
  cat > "$AGENT_SERVER_VERIFY_RUN/qa-f40-ts.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const dist = (file) => pathToFileURL(resolve('clients/typescript/dist', file)).href;
  const { ConversationClient } = await import(dist('clients.js'));
  const { isHttpError, isAskUserAnswerEvent } = await import(dist('index.js'));
  const conversations = new ConversationClient({ host: process.env.AGENT_SERVER_URL, apiKey: process.env.SESSION_API_KEY });
  const [cid, askId] = [process.env.QA_F40_CID, process.env.QA_F40_ASK];

  const answer = await conversations.answerAskUser(cid, askId, { optionIndex: 0 });
  assert.equal(isAskUserAnswerEvent(answer), true);
  assert.equal(answer.ask_id, askId);
  assert.equal(answer.option_index, 0);
  await assert.rejects(conversations.answerAskUser(cid, askId, { text: 'again' }),
    (err) => isHttpError(err) && err.status === 400 && err.detail === 'That question is not waiting for an answer.');
  console.log('QA_F40_TS_OK', answer.id);
  JS
  control-agent-server exec --env QA_F40_CID="$TC" --env QA_F40_ASK="$TASK" --timeout 120 \
    --expect-output QA_F40_TS_OK --save F40.ts-client/program -- node "$AGENT_SERVER_VERIFY_RUN/qa-f40-ts.mjs"
  control-agent-server api GET "/api/conversations/$TC/events/search" --query kind=AskUserAnswerEvent \
    --check items len-eq 1 --check items.0.ask_id eq "$TASK" --save F40.ts-client/stored
  control-agent-server conversation wait "$TC" --until finished --timeout 300
  ```
  The client's answer is stored once; the second answer rejects with 400.
- **Inside a Docker runtime (`F40.runtime-env`).** Translated: the variable a
  Docker-runtime host passes to its container's server is set on this run's
  server directly, with the stored flag off.
  Requires: `F40.flag-gates`
  ```sh
  control-agent-server api PATCH /api/settings/experimental/ask_user --json '{"enabled": false}' --expect 200
  control-agent-server restart --env 'OH_RUNTIME_EXPERIMENTAL_FEATURES={"ask_user": true}'
  control-agent-server api GET /api/settings/experimental --check 1.key eq ask_user --check 1.enabled eq false
  RT=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api GET "/api/conversations/$RT" --check agent.tools len-eq 1 --check agent.tools.0.name eq ask_user \
    --save F40.runtime-env/tools
  control-agent-server api POST "/api/conversations/$RT/ask_user/answer" --json '{"ask_id": "qa-f40-none", "option_index": 0}' \
    --expect 400 --check detail eq 'That question is not waiting for an answer.' --save F40.runtime-env/route
  control-agent-server restart --reset-config --env 'OH_RUNTIME_EXPERIMENTAL_FEATURES={}'
  RT0=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api GET "/api/conversations/$RT0" --check agent.tools len-eq 0
  control-agent-server restart --reset-config
  control-agent-server api POST "/api/conversations/$RT/ask_user/answer" --json '{"ask_id": "qa-f40-none", "option_index": 0}' --expect 409
  ```
  With the variable set, new conversations take their tools from it and the
  route leaves the switch to the host (400 for the stale id, not 409);
  without it, the stored (off) flag applies again.

## Gotchas

- The flag is read when a conversation starts: switching it changes only the
  route for conversations already running, not their tools.
- The question has no event kind of its own: clients find it as an
  `ActionEvent` with `tool_name: ask_user`, and it is pending while no user
  `MessageEvent` without a `sender` came after it on the active branch. The
  `AskUserAnswerEvent` alone does not close it; the message saved right after
  does, so a crash between the two leaves the card answerable again.
- A question does not put the conversation in a waiting state: it is
  `finished`, like any ended turn. Clients that want to show "waiting for you"
  read the last events, not `execution_status`.
- The goal loop's own messages carry `sender: goal` and never answer a
  question; after a goal stops on a question, the user answers and then calls
  `/goal/resume` (the server does not resume by itself).
- `/goal` and `/goal/resume` check only the active branch before starting,
  but the loop's check after each round reads the whole log, so a question
  left on an abandoned branch stops the loop after its first round (#6907,
  `F40.goal-abandoned-branch`).
- The answer route needs the feature on even for a conversation started under
  it; a conversation started before the flag was on has no `ask_user` and so
  never asks.
- Models answer in prose when the prompt does not leave the choice to the
  user; the recipes ask explicitly ("Ask me whether ... then stop until I
  answer") and assert on the tool call, not on the question's wording.
- Agent Canvas has no decision-card UI yet and never calls the answer route (follow-up [OpenHands/OpenHands#19773](https://github.com/OpenHands/OpenHands/issues/19773), open PR [#19789](https://github.com/OpenHands/OpenHands/pull/19789)).
