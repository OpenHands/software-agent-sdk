# Emoji reactions

A reaction is a small emoji on a message: the agent's way to say "seen, working
on it" before it answers, and the person's way to acknowledge an answer without
writing back. Both are saved as a non-LLM `ReactionEvent` (`target_event_id`,
`emoji`, `removed`, `source`), so clients fold them into each message's current
set and the model never sees them. The agent reacts with the `react` and
`unreact` tools, whose executor only reads the log: the observation carries the
reaction and the agent records the `ReactionEvent` right after it, on its own
thread. The person reacts through `POST /api/conversations/{id}/reactions`,
which does not run the agent. Emoji are a fixed set of shortcodes: `eyes`,
`+1`, `white_check_mark`, `tada`, `thinking_face`, `heart`.

Source: `openhands-sdk/openhands/sdk/event/reaction.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py` (`react`), `openhands-sdk/openhands/sdk/agent/agent.py` (`follow_up_events`), `openhands-tools/openhands/tools/reaction/`, `openhands-agent-server/openhands/agent_server/event_router.py`

Needs: `llm`

Routes: `POST /api/conversations/{conversation_id}/reactions`

## Sub-features

- `F37.rest`: `POST .../reactions` with `{emoji, target_event_id}` saves a user `ReactionEvent` on that message and returns it; `removed: true` saves the removal; the conversation stays `idle`.
- `F37.rest-validation`: an emoji outside the set is 422, an unknown target is 400, and leaving out the target with no agent message yet is 400; nothing is saved.
- `F37.tool`: an agent with `react` reacts to the person's latest message: the log has the tool's `ObservationEvent` followed by an agent `ReactionEvent` whose target is that message.
- `F37.rest-default`: after the agent has answered, a reaction without `target_event_id` goes on its answer: the latest agent message or `finish`.

## How to get to it (agent POV)

- REST: `POST /api/conversations/{conversation_id}/reactions` with
  `{"emoji", "target_event_id"?, "removed"?}` returns the saved
  `ReactionEvent`. Without `target_event_id` it targets the agent's latest
  message or `finish` action. It never runs the agent.
- Agent tools (registered by the agent-server, not part of the default
  preset): `react` and `unreact` from `openhands.tools.reaction`, with
  `{"emoji", "message_id"?}`; without `message_id` they target the person's
  latest message. Name them in `agent_settings.tools`
  (`conversation start --tools react,unreact`).
- Events: `ReactionEvent` in search, get-one and the socket like other events;
  the latest event for a (message, emoji, source) is the current state.
- SDK: `LocalConversation.react(emoji, target_event_id=None, removed=False)`.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported, and `doctor` is ok. The block below
  activates the DeepSeek profile (`$DEEPSEEK_API_KEY`) for `F37.tool`.

  ```sh
  control-agent-server llm preset deepseek
  RC=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api POST "/api/conversations/$RC/events" \
    --json '{"role":"user","content":[{"type":"text","text":"QA_F37 hello"}],"run":false}' --expect 200
  RM=$(control-agent-server api GET "/api/conversations/$RC/events/search" --query body=QA_F37 --field items.0.id)
  ```

- **React over REST (`F37.rest`).**
  ```sh
  control-agent-server api POST "/api/conversations/$RC/reactions" --json "{\"emoji\": \"heart\", \"target_event_id\": \"$RM\"}" \
    --expect 200 --check kind eq ReactionEvent --check source eq user --check emoji eq heart \
    --check target_event_id eq "$RM" --check removed eq false --save F37.rest/add
  control-agent-server api POST "/api/conversations/$RC/reactions" --json "{\"emoji\": \"heart\", \"target_event_id\": \"$RM\", \"removed\": true}" \
    --expect 200 --check removed eq true
  control-agent-server api GET "/api/conversations/$RC/events/search" --query kind=ReactionEvent --check items len-eq 2
  control-agent-server api GET "/api/conversations/$RC" --check execution_status eq idle
  ```
  Both events are in the log and nothing ran.
- **Bad reactions (`F37.rest-validation`).**
  Requires: `F37.rest`
  ```sh
  control-agent-server api POST "/api/conversations/$RC/reactions" --json "{\"emoji\": \"rocket\", \"target_event_id\": \"$RM\"}" --expect 422
  control-agent-server api POST "/api/conversations/$RC/reactions" --json '{"emoji": "eyes", "target_event_id": "missing"}' --expect 400
  control-agent-server api POST "/api/conversations/$RC/reactions" --json '{"emoji": "eyes"}' --expect 400 \
    --check detail eq 'There is no agent message to react to.' --save F37.rest-validation/no-agent-message
  control-agent-server api GET "/api/conversations/$RC/events/search" --query kind=ReactionEvent --check items len-eq 2
  ```
  Nothing new is saved.
- **Model-backed: the agent reacts (`F37.tool`).**
  Needs: `llm`
  ```sh
  AC=$(control-agent-server conversation start --no-autotitle --tools react,unreact --print-id)
  control-agent-server conversation send "$AC" --wait --timeout 300 --text 'QA_F37_TOOL First call the react tool with the emoji eyes. Then reply with the single word ok and finish.'
  AM=$(control-agent-server api GET "/api/conversations/$AC/events/search" --query body=QA_F37_TOOL --field items.0.id)
  control-agent-server api GET "/api/conversations/$AC/events/search" --query kind=ReactionEvent \
    --check items.0.source eq agent --check items.0.emoji eq eyes --check items.0.target_event_id eq "$AM" \
    --save F37.tool/reactions
  control-agent-server conversation events "$AC" --kinds ObservationEvent --contains 'Reacted :eyes:' --expect-min 1
  ```
  The agent reacted to the person's message before answering.
- **Default target is the agent's answer (`F37.rest-default`).**
  Requires: `F37.tool`
  ```sh
  ANSWER=$(control-agent-server api GET "/api/conversations/$AC/events/search" --query source=agent --query sort_order=TIMESTAMP_DESC \
    --query kind=ActionEvent --field items.0.id)
  control-agent-server api POST "/api/conversations/$AC/reactions" --json '{"emoji": "+1"}' --expect 200 \
    --check source eq user --check target_event_id eq "$ANSWER" --save F37.rest-default/add
  ```
  With no target the person's reaction goes on the agent's answer, here its
  `finish` (the agent's last action).

## Gotchas

- Reactions do not wake the agent. A person's `+1` on a question is not an
  answer yet.
- The agent's `react` call is an ordinary tool call, so the `ActionEvent` and
  its short `ObservationEvent` stay in the log next to the `ReactionEvent`;
  clients that show reactions as chips can hide that pair.
