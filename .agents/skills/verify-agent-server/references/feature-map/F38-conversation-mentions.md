# Conversation mentions

A mention is a markdown link to another conversation,
`[Title](conversation:<id hex>)`: the text is the conversation's title when it
was written, so the message still reads well anywhere, and a UI shows the link
as a way to open that conversation. Mentions are ordinary message text: there
is no new event and no new route.

Mentions sit behind the user-facing feature flag `conversation_mentions`
("Conversation mentions", off by default). While it is on, every new
OpenHands-agent conversation gets two read-only tools without naming them:
`find_conversations` (the user's conversations on this server whose title holds
every query word, newest activity first, each as a mention with its status and
last activity; the conversation itself is left out) and `read_conversation`
(the latest messages and `finish` texts of a conversation named by a mention or
an id, oldest first, without tool calls). Both carry the same system-prompt
guidance: when the agent refers to another conversation it writes its mention.
The tools read this server's own REST API (`GET /api/conversations/search`,
`GET /api/conversations/{id}`, `GET /api/conversations/{id}/events/search`)
and never send or run anything. While the flag is off, new conversations get
neither tool nor the guidance; a conversation started while it was on keeps
them. Flags are switched with `PATCH /api/settings/experimental/{key}` (F18).

Source: `openhands-sdk/openhands/sdk/conversation/types.py` (`conversation_mention`), `openhands-tools/openhands/tools/conversation_mentions/`, `openhands-agent-server/openhands/agent_server/experimental_features.py`, `openhands-agent-server/openhands/agent_server/tool_router.py`

Needs: `llm`

Routes: none of its own (the flag's routes are F18's; the tools read routes mapped in F04 and F06)

## Sub-features

- `F38.flag`: `GET /api/settings/experimental` lists `conversation_mentions` with `enabled: false` on a fresh server; `PATCH .../conversation_mentions` `{"enabled": true}` returns it on.
- `F38.flag-gates`: with the flag off a new conversation has neither `find_conversations` nor `read_conversation`; with it on, a new conversation started without naming them has both. Turning it off again leaves that conversation its tools, and the next new conversation has none.
- `F38.find`: an agent asked which conversation is about a topic calls `find_conversations` and its answer holds that conversation's mention, `(conversation:<hex>)`.
- `F38.read`: an agent given a mention in the user's message and asked what that conversation concluded calls `read_conversation`, whose result holds the other conversation's message text.
- `F38.read-only`: reading a conversation adds no event to it and leaves its status as it was.

## How to get to it (agent POV)

- Agent tools (registered by the agent-server, not part of the default
  preset): `find_conversations` `{"query"?, "limit"?}` and
  `read_conversation` `{"conversation", "limit"?}` from
  `openhands.tools.conversation_mentions`. The `conversation_mentions` flag
  adds them to every new OpenHands-agent conversation; ACP agents do not get
  them.
- REST: `GET /api/settings/experimental` and
  `PATCH /api/settings/experimental/{key}` (owned by F18) switch the flag.
- Configuration: `OH_RUNTIME_EXPERIMENTAL_FEATURES` (F37's `runtime-env`)
  picks the tools of a Docker runtime's conversations the same way; there the
  tools see the conversations of that runtime's server.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported, and `doctor` is ok. The block below
  activates the DeepSeek profile (`$DEEPSEEK_API_KEY`) for the model-backed
  bullets and makes the conversation they look for.

  ```sh
  control-agent-server llm preset deepseek
  TARGET=$(control-agent-server conversation start --no-autotitle --tools none --title 'QA_F38 Fix the Safari export button' --print-id)
  control-agent-server api POST "/api/conversations/$TARGET/events" \
    --json '{"role":"user","content":[{"type":"text","text":"QA_F38_NOTE The fix was to debounce the click handler."}],"run":false}' --expect 200
  TARGET_HEX=$(printf %s "$TARGET" | tr -d -)
  ```

- **The feature flag (`F38.flag`, `F38.flag-gates`).**
  ```sh
  control-agent-server api GET /api/settings/experimental \
    --check 1.key eq conversation_mentions --check 1.enabled eq false --save F38.flag/list
  OFF=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api PATCH /api/settings/experimental/conversation_mentions --json '{"enabled": true}' \
    --expect 200 --check enabled eq true
  ON=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api GET "/api/conversations/$ON" --check agent.tools len-eq 2 \
    --check agent.tools.0.name eq find_conversations --check agent.tools.1.name eq read_conversation --save F38.flag-gates/tools
  control-agent-server api GET "/api/conversations/$OFF" --check agent.tools len-eq 0
  control-agent-server api PATCH /api/settings/experimental/conversation_mentions --json '{"enabled": false}' --expect 200
  AFTER=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api GET "/api/conversations/$AFTER" --check agent.tools len-eq 0 --save F38.flag-gates/off-again
  control-agent-server api GET "/api/conversations/$ON" --check agent.tools len-eq 2
  control-agent-server api PATCH /api/settings/experimental/conversation_mentions --json '{"enabled": true}' --expect 200
  ```
  A conversation started while the flag was on keeps both tools; the flag is
  on again, and stays on for the rest of this family.
- **Model-backed: the agent finds and links a conversation (`F38.find`).**
  Needs: `llm`
  Requires: `F38.flag-gates`
  ```sh
  FC=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server conversation send "$FC" --wait --timeout 300 --text 'Which of my conversations is about the QA_F38 Safari export button? Link it in your answer.'
  control-agent-server conversation events "$FC" --kinds ActionEvent --contains find_conversations --expect-min 1
  control-agent-server conversation events "$FC" --kinds ObservationEvent --contains "conversation:$TARGET_HEX" --expect-min 1 \
    --save F38.find/observation
  control-agent-server conversation events "$FC" --kinds MessageEvent,ActionEvent --contains "(conversation:$TARGET_HEX)" --expect-min 1 \
    --save F38.find/answer
  ```
  The tool's result lists the target as a mention, and the agent's answer
  (a plain message or its `finish`) repeats it as a link.
- **Model-backed: the agent reads a mentioned conversation (`F38.read`, `F38.read-only`).**
  Needs: `llm`
  Requires: `F38.flag-gates`
  ```sh
  BEFORE=$(control-agent-server api GET "/api/conversations/$TARGET/events/count" --field .)
  RC=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server conversation send "$RC" --wait --timeout 300 \
    --text "What did [the export fix](conversation:$TARGET_HEX) conclude? Answer in one sentence."
  control-agent-server conversation events "$RC" --kinds ObservationEvent --contains QA_F38_NOTE --expect-min 1 \
    --save F38.read/observation
  control-agent-server api GET "/api/conversations/$TARGET/events/count" --check . eq "$BEFORE"
  control-agent-server api GET "/api/conversations/$TARGET" --check execution_status eq idle --save F38.read-only/status
  ```
  The agent read the other conversation's note, and that conversation has
  the same number of events and is still idle: reading sends and runs nothing.

## Gotchas

- `find_conversations` matches words in titles only. A conversation started
  with `--no-autotitle` and no `--title` has no title, so it lists as
  `untitled conversation` and never matches a query.
- The tools reach their server through `OH_INTERNAL_SERVER_URL`, which the
  server sets to its own address when it starts (the seam
  `start_child_conversation` uses). A tool call that cannot reach the server
  points at that variable, not at the flag.
- The flag picks the tools when a conversation starts: switching it on does
  not give an open conversation the tools. Start a new one.
