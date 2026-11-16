# Message edits

The agent can edit one of its earlier messages when it got answered, was
wrong, or became wrong: it strikes the outdated part and adds a short
`[Edit: ...]` note. The event log is append-only, so an edit never changes the
message: the `edit_message` tool records a `MessageEditEvent`
(`target_event_id`, `text`, `source` always `agent`) after the call's own
observation (usually right after; a user reaction held back while tool calls
were open can land between them), and clients show the latest edit's `text`
in place of the message's text, marked as edited. The tool works like
`str_replace` over what the agent sees (`state.view`, not the whole log):
`old_str` must occur exactly once across the agent's messages and `finish`
answers still in its view, each read as it stands after its latest edit (taken
from its own `edit_message` results in the view). A message condensed out of
the view cannot be edited.
The model keeps its message as first written and sees its own call and the
`Edited. The message now reads:` result; the `MessageEditEvent` itself never
reaches it. A user's reaction on an edited message quotes the edited text.

Edits sit behind the user-facing feature flag `message_edits` ("Message
edits", off by default). While it is on, every new OpenHands-agent
conversation gets `edit_message` without naming it; while it is off, new
conversations do not. There is no REST route: only the agent edits.

Source: `openhands-sdk/openhands/sdk/event/message_edit.py`, `openhands-sdk/openhands/sdk/agent/agent.py` (`follow_up_events`), `openhands-tools/openhands/tools/message_edit/`, `openhands-agent-server/openhands/agent_server/experimental_features.py`, `openhands-agent-server/openhands/agent_server/tool_router.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py` (`react`), `clients/typescript/src/events/types.ts`

Needs: `llm`

Routes: none of its own (the flag's routes are F18's).

## Sub-features

- `F39.flag-gates`: `GET /api/settings/experimental` lists `message_edits` with `enabled: false`; a conversation started while it is off has no `edit_message`, and one started after `PATCH .../message_edits` `{"enabled": true}` has it without naming it.
- `F39.tool`: a real model that asked the user a question, then got the answer, edits the question: one agent `MessageEditEvent` targets its earlier message, its `text` strikes the question with `~~` and carries an `[Edit:` note, and the stored message is unchanged.
- `F39.agent-sees`: with an `llm-stub` recorder, the requests after an edit carry the message as first written plus the `edit_message` call and its `Edited.` result, and nothing else for the edit.
- `F39.miss`: an `old_str` the agent never wrote answers an error observation (`old_str is not in any of your messages`) and records no `MessageEditEvent`.
- `F39.reaction-quotes-edit`: with `conversation_reactions` also on, a user's reaction on the edited message keeps the edited text in `target_text_ending`.

## How to get to it (agent POV)

- Agent tool (registered by the agent-server, not part of the default
  preset): `edit_message` from `openhands.tools.message_edit`, with
  `{"old_str", "new_str"}`. The `message_edits` feature flag adds it to every
  new OpenHands-agent conversation; ACP agents do not get it.
- Events: `MessageEditEvent` in search, get-one and the socket like other
  events; the latest one for a `target_event_id` is the message's text.
- REST: `GET /api/settings/experimental` and
  `PATCH /api/settings/experimental/{key}` (owned by F18) switch the flag.
- TypeScript client: the `MessageEditEvent` type and `isMessageEditEvent`
  guard (`clients/typescript/src/events/types.ts`).

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported, and `doctor` is ok. `F39.tool`
  activates the DeepSeek profile (`$DEEPSEEK_API_KEY`).

- **The feature flag (`F39.flag-gates`).**
  ```sh
  control-agent-server api GET /api/settings/experimental \
    --check 3.key eq message_edits --check 3.enabled eq false --save F39.flag-gates/list
  OFF=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api PATCH /api/settings/experimental/message_edits --json '{"enabled": true}' \
    --expect 200 --check enabled eq true
  ON=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api GET "/api/conversations/$ON" --check agent.tools len-eq 1 \
    --check agent.tools.0.name eq edit_message --save F39.flag-gates/tools
  control-agent-server api GET "/api/conversations/$OFF" --check agent.tools len-eq 0
  ```
  The flag stays on for the rest of this family.
- **Model-backed: the agent edits its question (`F39.tool`).**
  Needs: `llm`
  Requires: `F39.flag-gates`
  ```sh
  control-agent-server llm preset deepseek
  EC=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server conversation send "$EC" --wait --timeout 300 --text 'QA_F39_TOOL I need a port for the demo server. Ask me which port to use, in one short question, and wait for my answer.'
  control-agent-server conversation send "$EC" --wait --timeout 300 --text 'Use 8080. Your question is answered now, so edit it the way the edit_message tool describes, then reply ok.'
  control-agent-server api GET "/api/conversations/$EC/events/search" --query kind=MessageEditEvent \
    --check items len-eq 1 --check items.0.source eq agent --check items.0.text contains '~~' \
    --check items.0.text contains '[Edit:' --save F39.tool/edit
  TARGET=$(control-agent-server api GET "/api/conversations/$EC/events/search" --query kind=MessageEditEvent --field items.0.target_event_id)
  control-agent-server api GET "/api/conversations/$EC/events/$TARGET" --check source eq agent \
    --save F39.tool/message-unchanged
  ```
  The target is the agent's question (an agent `MessageEvent`, or its
  `finish` when it asked that way), and its stored text has no `~~`: the edit
  lives only in the new event.
- **What the model sees (`F39.agent-sees`, `F39.miss`).** The prompt is
  observable only at the provider, so an `llm-stub` records it: the stub asks
  a question, then on the next turn edits a sentence it never wrote (a miss),
  edits its question, and finishes; a last turn records the request after
  the edit.
  Requires: `F39.flag-gates`
  ```sh
  SEE=$(control-agent-server fixture llm-stub --name qa-f39-see \
    --step 'reply:QA_F39_ASK Should I use port 8080?' \
    --step 'tool:edit_message:{"old_str":"never said this","new_str":"x"}' \
    --step 'tool:edit_message:{"old_str":"Should I use port 8080?","new_str":"~~Should I use port 8080?~~ [Edit: Using 8080.]"}' \
    --step 'tool:finish:{"message":"QA_F39_DONE"}' --step 'reply:ok' --step 'reply:ok' --print-path)
  SEE_AGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$SEE/v1\", \"num_retries\": 0}, \"tools\": []}"
  SC=$(control-agent-server conversation start --no-autotitle --body-json "{\"agent\": $SEE_AGENT}" --prompt 'QA_F39_SEE first' \
    --wait --timeout 120 --print-id)
  control-agent-server conversation send "$SC" --text 'QA_F39_SEE use 8080' --wait --timeout 120
  control-agent-server conversation events "$SC" --kinds ObservationEvent --contains 'old_str is not in any of your messages' \
    --expect-min 1 --save F39.miss/observation
  control-agent-server api GET "/api/conversations/$SC/events/search" --query kind=MessageEditEvent \
    --check items len-eq 1 --check items.0.text eq 'QA_F39_ASK ~~Should I use port 8080?~~ [Edit: Using 8080.]' \
    --save F39.agent-sees/edit
  control-agent-server conversation send "$SC" --text 'QA_F39_SEE third' --wait --timeout 120
  control-agent-server sink read --name qa-f39-see --contains 'Edited. The message now reads' --expect-min 2 \
    --save F39.agent-sees/requests
  control-agent-server sink read --name qa-f39-see --contains 'QA_F39_ASK Should I use port 8080?' --expect-min 5
  control-agent-server sink read --name qa-f39-see --contains 'MessageEdit' --expect-max 0
  ```
  The miss is an error result and adds no edit. Every request after the
  question still holds it as first written; the edit shows only as the call
  and its result.
- **A reaction quotes the edited text (`F39.reaction-quotes-edit`).**
  Requires: `F39.agent-sees`
  ```sh
  control-agent-server api PATCH /api/settings/experimental/conversation_reactions --json '{"enabled": true}' --expect 200
  ASK=$(control-agent-server api GET "/api/conversations/$SC/events/search" --query kind=MessageEditEvent --field items.0.target_event_id)
  control-agent-server api POST "/api/conversations/$SC/reactions" --json "{\"emoji\": \"+1\", \"target_event_id\": \"$ASK\"}" \
    --expect 200 --check target_text_ending eq 'QA_F39_ASK ~~Should I use port 8080?~~ [Edit: Using 8080.]' \
    --save F39.reaction-quotes-edit/reaction
  ```
  The reactions route follows its own flag; the edit decides the text.

## Gotchas

- The tool reads messages as they stand after earlier edits, so a second edit
  must quote the edited text, not the original.
- `old_str` that occurs twice (in one message or across messages) is an error;
  the model has to include more context.
- Two `edit_message` calls in one response that edit the same message both
  read it before either is recorded, so the first (in call order) stands and
  the later one answers an error quoting the message as the first left it;
  the agent edits again in its next response. Edits of different messages in
  one response all apply.
- Turning the flag off keeps `edit_message` in conversations started while it
  was on, as with reactions.
