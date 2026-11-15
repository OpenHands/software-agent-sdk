# Emoji reactions

A reaction is a small emoji on a message: the agent's way to say "seen, working
on it" before it answers, and the user's way to acknowledge an answer without
writing back. Both are saved as a `ReactionEvent` (`target_event_id`,
`emoji`, `removed`, `source`; a user's reaction also keeps the end of the
message's text in `target_text_ending`, at most 500 characters, with
`target_text_clipped`), and clients fold them into each message's current set.
The model sees the user's reactions on its next turn, as one user line such
as `[The user reacted ❤️ to this message:]` followed by that text ending; its
own reactions stay out of what it sees, since its `react` call is already
there. The agent reacts with the `react` and
`unreact` tools, whose executor only reads the log: the observation carries the
reaction and the agent records the `ReactionEvent` right after it, on its own
thread. The user reacts through `POST /api/conversations/{id}/reactions`,
which does not run the agent. Emoji are a fixed set of shortcodes: `eyes`,
`+1`, `white_check_mark`, `tada`, `thinking_face`, `heart`.

Reactions sit behind the user-facing feature flag `conversation_reactions`
("Conversation reactions", off by default). While it is on, every new
OpenHands-agent conversation gets `react` and `unreact` without naming them,
and the REST route accepts reactions; while it is off, the route answers 409
and new conversations do not get the tools. Flags are listed with
`GET /api/settings/experimental` and switched with
`PATCH /api/settings/experimental/{key}` `{"enabled": bool}`, stored in
`PersistedSettings.experimental_features`, and read when a conversation starts.
A server inside a Docker runtime has no settings of its own: the host passes
the user's switches in `OH_RUNTIME_EXPERIMENTAL_FEATURES` (a JSON object), which
then picks the tools of new conversations, and that server's REST route stops
checking the switch because the host checks it before forwarding.

Source: `openhands-sdk/openhands/sdk/event/reaction.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py` (`react`), `openhands-sdk/openhands/sdk/agent/agent.py` (`follow_up_events`), `openhands-tools/openhands/tools/reaction/`, `openhands-agent-server/openhands/agent_server/event_router.py`, `openhands-agent-server/openhands/agent_server/experimental_features.py`, `openhands-agent-server/openhands/agent_server/experimental_features_router.py`, `openhands-sdk/openhands/sdk/launch/finalize.py` (`experimental_feature_tools`), `openhands-sdk/openhands/sdk/context/view/view.py`, `openhands-agent-server/openhands/agent_server/docker_runtime/routers.py`, `clients/typescript/src/client/conversation-client.ts`, `clients/typescript/src/client/settings-client.ts`

Needs: `llm`

Routes: `POST /api/conversations/{conversation_id}/reactions`

## Sub-features

- `F37.flag`: `GET /api/settings/experimental` lists `conversation_reactions` with `enabled: false` on a fresh server; `PATCH .../conversation_reactions` `{"enabled": true}` returns it on and the list agrees; an unknown key is 404.
- `F37.flag-gates`: with the flag off, `POST .../reactions` is 409 and a new conversation has no `react`/`unreact`; with it on, a new conversation started without naming them has both tools. Turning it off again refuses the route at once (409 `Conversation reactions are turned off`) while that conversation keeps its tools.
- `F37.rest`: `POST .../reactions` with `{emoji, target_event_id}` saves a user `ReactionEvent` on that message and returns it; `removed: true` saves the removal; the conversation stays `idle`.
- `F37.rest-validation`: an emoji outside the set is 422, an unknown target is 400, and leaving out the target with no agent message yet is 400; nothing is saved.
- `F37.tool`: an agent with `react` reacts to the user's latest message when asked to react first, and leaves the reaction on after it answers: the log has the tool's `ObservationEvent` followed by one agent `ReactionEvent` whose target is that message, and no removal.
- `F37.rest-while-confirming`: a reaction posted while a tool call waits for confirmation answers 200 but is held back: it joins the log right after the call's result once the call is accepted, never between the call and its result, and the run finishes.
- `F37.rest-default`: after the agent has answered, a reaction without `target_event_id` goes on its answer: the latest agent message or `finish`.
- `F37.agent-sees-user-reactions`: a user's reaction on the agent's `finish` keeps the answer's text in `target_text_ending`, and the next model request carries it as `[The user reacted ❤️ to this message:]` plus that text, while the agent's own `react` reaction never appears as a reaction line.
- `F37.rest-active-branch`: after navigating back before the agent's answer, the abandoned answer is 400 `No message with id ...`, a reaction without a target is 400 `There is no agent message to react to.`, an event that is not a message is 400 `Event ... is not a message.`, and the user's message on the active branch still takes a reaction.
- `F37.ts-client`: the TypeScript `SettingsClient` lists and switches the flag (an unknown key rejects with a 404 `HttpError`) and `ConversationClient.react` adds and removes a reaction that a GET of the event shows; a reaction with nothing to target rejects with a 400 `HttpError`.
- `F37.runtime-env`: with `OH_RUNTIME_EXPERIMENTAL_FEATURES='{"conversation_reactions": true}'` and the stored flag off, new conversations get both tools and `POST .../reactions` answers 200; with `{}` new conversations get neither.
- `F37.rest-response-parent`: the `ReactionEvent` that `POST .../reactions` returns is the event as stored, with the same `parent_id` (the reacted message when it is the active leaf) that search, get-one and the socket show.

## How to get to it (agent POV)

- REST: `POST /api/conversations/{conversation_id}/reactions` with
  `{"emoji", "target_event_id"?, "removed"?}` returns the `ReactionEvent`
  as stored (a reaction held back while a tool call waits has no `parent_id`
  until it joins the events). Without `target_event_id` it targets the
  agent's latest message or `finish` action; any message on the active branch,
  the user's own included, can be targeted. It never runs the agent.
- REST: `GET /api/settings/experimental` and
  `PATCH /api/settings/experimental/{key}` (owned by F18) switch the flag.
- Agent tools (registered by the agent-server, not part of the default
  preset): `react` and `unreact` from `openhands.tools.reaction`, with
  `{"emoji", "message_id"?}`; without `message_id` they target the user's
  latest message. The `conversation_reactions` feature flag adds them to
  every new OpenHands-agent conversation; ACP agents do not get them.
- Events: `ReactionEvent` in search, get-one and the socket like other events;
  the latest event for a (message, emoji, source) is the current state.
- SDK: `LocalConversation.react(emoji, target_event_id=None, removed=False)`.
- TypeScript client: `ConversationClient.react(conversationId, emoji,
  {targetEventId?, removed?})`, `SettingsClient.listExperimentalFeatures()`
  and `updateExperimentalFeature(key, enabled)`; the `ReactionEvent` type and
  `isReactionEvent` guard (`clients/typescript/src/events/types.ts`). Recipe
  `F37.ts-client` runs them on the built package (`clients/typescript/dist`).
- Configuration: `OH_RUNTIME_EXPERIMENTAL_FEATURES` (JSON object of switches,
  set by a Docker-runtime host for its container's server) replaces the stored
  switches for new conversations and turns off the route's own check
  (`F37.runtime-env`).

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

- **The feature flag (`F37.flag`, `F37.flag-gates`).**
  ```sh
  control-agent-server api GET /api/settings/experimental \
    --check 0.key eq conversation_reactions --check 0.enabled eq false --save F37.flag/list
  OFF=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api POST "/api/conversations/$OFF/reactions" --json '{"emoji": "eyes"}' --expect 409
  control-agent-server api PATCH /api/settings/experimental/conversation_reactions --json '{"enabled": true}' \
    --expect 200 --check enabled eq true
  control-agent-server api PATCH /api/settings/experimental/time_travel --json '{"enabled": true}' --expect 404
  ON=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api GET "/api/conversations/$ON" --check agent.tools len-eq 2 --check agent.tools.0.name eq react \
    --check agent.tools.1.name eq unreact --save F37.flag-gates/tools
  control-agent-server api GET "/api/conversations/$OFF" --check agent.tools len-eq 0
  control-agent-server api PATCH /api/settings/experimental/conversation_reactions --json '{"enabled": false}' --expect 200
  control-agent-server api POST "/api/conversations/$ON/reactions" --json '{"emoji": "eyes"}' --expect 409 \
    --check detail eq 'Conversation reactions are turned off' --save F37.flag-gates/off-again
  control-agent-server api GET "/api/conversations/$ON" --check agent.tools len-eq 2
  control-agent-server api PATCH /api/settings/experimental/conversation_reactions --json '{"enabled": true}' --expect 200 \
    --check enabled eq true
  ```
  Switching the flag off again refuses the route at once, while the
  conversation started under it keeps `react` and `unreact`. The flag is on
  again, and stays on for the rest of this family.
- **React over REST (`F37.rest`).**
  Requires: `F37.flag`
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
  Requires: `F37.flag-gates` (it turns the reactions flag on; without it the
  agent has no react tool)
  ```sh
  AC=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server conversation send "$AC" --wait --timeout 300 --text 'QA_F37_TOOL Please react to this message with the eyes emoji first, then reply with the single word ok.'
  AM=$(control-agent-server api GET "/api/conversations/$AC/events/search" --query body=QA_F37_TOOL --field items.0.id)
  control-agent-server api GET "/api/conversations/$AC/events/search" --query kind=ReactionEvent \
    --check items len-eq 1 --check items.0.source eq agent --check items.0.emoji eq eyes \
    --check items.0.target_event_id eq "$AM" --check items.0.removed eq false --save F37.tool/reactions
  control-agent-server conversation events "$AC" --kinds ObservationEvent --contains 'Reacted :eyes:' --expect-min 1
  ```
  The agent reacted to the user's message before answering and left the
  reaction on: a reaction stays until it no longer fits, so an answer does not
  take it off.
- **Default target is the agent's answer (`F37.rest-default`).**
  Requires: `F37.tool`
  ```sh
  LAST_ACTION=$(control-agent-server api GET "/api/conversations/$AC/events/search" --query source=agent --query sort_order=TIMESTAMP_DESC \
    --query kind=ActionEvent --field items.0.id)
  # An agent that answered with `finish` has no MessageEvent: --field then
  # prints an error, so fall back to a value that matches nothing.
  LAST_MESSAGE=$(control-agent-server api GET "/api/conversations/$AC/events/search" --query source=agent --query sort_order=TIMESTAMP_DESC \
    --query kind=MessageEvent --field items.0.id 2>/dev/null) || LAST_MESSAGE=none
  control-agent-server api POST "/api/conversations/$AC/reactions" --json '{"emoji": "+1"}' --expect 200 \
    --check source eq user --check target_event_id matches "^($LAST_ACTION|$LAST_MESSAGE)\$" --save F37.rest-default/add
  ```
  With no target the user's reaction goes on the agent's answer: its
  `finish` action when it finished with the tool (then the latest agent
  action), or its latest `MessageEvent` when it answered in plain text.
- **Reacting while a tool call waits (`F37.rest-while-confirming`).** A
  conversation that confirms every action answers once, then stops at its
  next tool call; the user reacts to the first answer while it waits.
  Requires: `F37.flag-gates`
  ```sh
  WC=$(control-agent-server conversation start --confirmation-policy always --prompt 'Run: echo first' --wait --until waiting_for_confirmation --print-id)
  control-agent-server api POST "/api/conversations/$WC/events/respond_to_confirmation" --json '{"accept": true}' --expect 200
  control-agent-server conversation wait "$WC" --until finished
  control-agent-server conversation send "$WC" --text 'Now run: echo second' --wait --until waiting_for_confirmation
  control-agent-server api POST "/api/conversations/$WC/reactions" --json '{"emoji": "heart"}' --expect 200 \
    --check source eq user --save F37.rest-while-confirming/add
  control-agent-server api GET "/api/conversations/$WC/events/search" --query kind=ReactionEvent --check items len-eq 0
  control-agent-server api POST "/api/conversations/$WC/events/respond_to_confirmation" --json '{"accept": true}' --expect 200
  control-agent-server conversation wait "$WC" --until finished
  control-agent-server api GET "/api/conversations/$WC/events/search" --query kind=ReactionEvent \
    --check items len-eq 1 --check items.0.emoji eq heart --save F37.rest-while-confirming/saved
  control-agent-server api GET "/api/conversations/$WC/events/search" --query kind=Condensation --check items len-eq 0
  control-agent-server api GET "/api/conversations/$WC/events/search" --query kind=ObservationEvent --query limit=100 \
    --check items len-eq 2 --save F37.rest-while-confirming/observations
  ```
  The reaction is not in the log while the call waits; it is saved right
  after the call's result, and the run finishes with exactly the two commands
  the user asked for and no condensation. A reaction between a call and
  its result would make the model's API refuse the history. Held reactions
  live in memory: a server restart while the call waits drops them.
- **What the model sees (`F37.agent-sees-user-reactions`).** The prompt
  the server sends is observable only at the provider, so this bullet records
  it with an `llm-stub` (a recorder here, not a stand-in for a happy path): the
  stub reacts with `eyes`, then answers with `finish`; the user puts `heart`
  on that answer; the next request carries the user's reaction line and no
  line for the agent's own reaction.
  Requires: `F37.flag`
  ```sh
  SEE=$(control-agent-server fixture llm-stub --name qa-f37-see --step 'tool:react:{"emoji":"eyes"}' \
    --step 'tool:finish:{"message":"QA_F37_ANSWER"}' --print-path)
  SEE_AGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$SEE/v1\", \"num_retries\": 0}, \"tools\": []}"
  SC=$(control-agent-server conversation start --no-autotitle --body-json "{\"agent\": $SEE_AGENT}" --prompt 'QA_F37_SEE first' \
    --wait --timeout 120 --print-id)
  control-agent-server api GET "/api/conversations/$SC/events/search" --query kind=ReactionEvent \
    --check items len-eq 1 --check items.0.source eq agent --check items.0.emoji eq eyes
  control-agent-server api POST "/api/conversations/$SC/reactions" --json '{"emoji": "heart"}' --expect 200 \
    --check source eq user --check target_text_ending eq QA_F37_ANSWER --check target_text_clipped eq false \
    --save F37.agent-sees-user-reactions/add
  control-agent-server sink read --name qa-f37-see --expect-min 2 --expect-max 2
  control-agent-server conversation send "$SC" --text 'QA_F37_SEE second' --wait --timeout 120
  control-agent-server sink read --name qa-f37-see --contains 'Reacted :eyes:' --expect-min 2
  control-agent-server sink read --name qa-f37-see --contains 'The user reacted' --expect-min 1 --expect-max 1 \
    --save F37.agent-sees-user-reactions/next-request
  control-agent-server sink read --name qa-f37-see --contains 'The user reacted \u2764\ufe0f to this message:]\nQA_F37_ANSWER' \
    --expect-min 1 --expect-max 1
  control-agent-server sink read --name qa-f37-see --contains 'You reacted' --expect-max 0
  ```
  Only the third request (after the user's reaction) holds the reaction line,
  with the answer's text after it (the sink keeps the request as JSON, so the
  glyph and the line break read as `\u2764\ufe0f` and `\n`). The agent's `react` call and its
  `Reacted :eyes:.` result are in every later request, but its
  `ReactionEvent` never becomes a line of its own.
- **Targets come from the active branch (`F37.rest-active-branch`).** Move
  the conversation back to the user's first message, before the answer.
  Requires: `F37.agent-sees-user-reactions`
  ```sh
  U1=$(control-agent-server api GET "/api/conversations/$SC/events/search" --query kind=MessageEvent --query body=QA_F37_SEE \
    --query sort_order=TIMESTAMP --field items.0.id)
  FIN=$(control-agent-server api GET "/api/conversations/$SC/events/search" --query kind=ActionEvent --query sort_order=TIMESTAMP \
    --query source=agent --field items.1.id)
  OBS=$(control-agent-server api GET "/api/conversations/$SC/events/search" --query kind=ObservationEvent --field items.0.id)
  control-agent-server api POST "/api/conversations/$SC/reactions" --json "{\"emoji\": \"eyes\", \"target_event_id\": \"$OBS\"}" \
    --expect 400 --check detail eq "Event $OBS is not a message." --save F37.rest-active-branch/not-a-message
  control-agent-server api POST "/api/conversations/$SC/navigate" --json "{\"event_id\": \"$U1\"}" --expect 200
  control-agent-server api POST "/api/conversations/$SC/reactions" --json "{\"emoji\": \"eyes\", \"target_event_id\": \"$FIN\"}" \
    --expect 400 --check detail eq "No message with id $FIN." --save F37.rest-active-branch/abandoned
  control-agent-server api POST "/api/conversations/$SC/reactions" --json '{"emoji": "eyes"}' --expect 400 \
    --check detail eq 'There is no agent message to react to.'
  control-agent-server api POST "/api/conversations/$SC/reactions" --json "{\"emoji\": \"tada\", \"target_event_id\": \"$U1\"}" \
    --expect 200 --check target_event_id eq "$U1" --check target_text_ending eq 'QA_F37_SEE first'
  ```
  The agent's `finish` is still stored but no longer on the active branch, so
  it cannot be reacted to; the user's own message can.
- **TypeScript client (`F37.ts-client`).** A program drives
  `SettingsClient` and `ConversationClient` against the run.
  Needs: `node`
  Requires: `F37.flag`
  ```sh
  test -f clients/typescript/dist/clients.js || (cd clients/typescript && npm ci && npm run build)
  TSC=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  cat > "$AGENT_SERVER_VERIFY_RUN/qa-f37-ts.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const dist = (file) => pathToFileURL(resolve('clients/typescript/dist', file)).href;
  const { SettingsClient, ConversationClient } = await import(dist('clients.js'));
  const { isHttpError } = await import(dist('index.js'));
  const opts = { host: process.env.AGENT_SERVER_URL, apiKey: process.env.SESSION_API_KEY };
  const settings = new SettingsClient(opts);
  const conversations = new ConversationClient(opts);
  const [cid, target, empty] = [process.env.QA_F37_CID, process.env.QA_F37_TARGET, process.env.QA_F37_EMPTY];

  const flags = await settings.listExperimentalFeatures();
  assert.deepEqual(flags.map((f) => [f.key, f.enabled]), [['conversation_reactions', true]]);
  assert.equal((await settings.updateExperimentalFeature('conversation_reactions', true)).enabled, true);
  await assert.rejects(settings.updateExperimentalFeature('qa_f37_unknown', true),
    (err) => isHttpError(err) && err.status === 404);

  const added = await conversations.react(cid, 'tada', { targetEventId: target });
  assert.equal(added.kind, 'ReactionEvent');
  assert.equal(added.source, 'user');
  assert.equal(added.removed, false);
  const removed = await conversations.react(cid, 'tada', { targetEventId: target, removed: true });
  assert.equal(removed.removed, true);
  await assert.rejects(conversations.react(empty, 'eyes'),
    (err) => isHttpError(err) && err.status === 400 && err.detail === 'There is no agent message to react to.');
  console.log('QA_F37_TS_OK', added.id, removed.id);
  JS
  OUT=$(control-agent-server exec --env QA_F37_CID="$RC" --env QA_F37_TARGET="$RM" --env QA_F37_EMPTY="$TSC" --timeout 120 \
    --expect-output QA_F37_TS_OK --save F37.ts-client/program -- node "$AGENT_SERVER_VERIFY_RUN/qa-f37-ts.mjs")
  TS_ADDED=$(printf '%s' "$OUT" | grep -o 'QA_F37_TS_OK [0-9a-f-]* [0-9a-f-]*' | cut -d' ' -f2)
  test -n "$TS_ADDED"
  control-agent-server api GET "/api/conversations/$RC/events/$TS_ADDED" --expect 200 --check kind eq ReactionEvent \
    --check emoji eq tada --check target_event_id eq "$RM" --save F37.ts-client/added
  ```
  The flag reads on, the unknown key rejects with 404, and the reaction the
  client added is in the conversation's log.
- **Inside a Docker runtime (`F37.runtime-env`).** Translated: the env
  variable a Docker-runtime host passes to its container's server is set on
  this run's server directly, with the stored flag off.
  Requires: `F37.flag`
  ```sh
  control-agent-server api PATCH /api/settings/experimental/conversation_reactions --json '{"enabled": false}' --expect 200
  control-agent-server restart --env 'OH_RUNTIME_EXPERIMENTAL_FEATURES={"conversation_reactions": true}'
  control-agent-server api GET /api/settings/experimental --check 0.enabled eq false
  RT=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api GET "/api/conversations/$RT" --check agent.tools len-eq 2 --save F37.runtime-env/tools
  control-agent-server api POST "/api/conversations/$RC/reactions" --json "{\"emoji\": \"+1\", \"target_event_id\": \"$RM\"}" \
    --expect 200 --check emoji eq '+1' --save F37.runtime-env/react
  control-agent-server restart --reset-config --env 'OH_RUNTIME_EXPERIMENTAL_FEATURES={}'
  RT0=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api GET "/api/conversations/$RT0" --check agent.tools len-eq 0
  control-agent-server restart --reset-config
  control-agent-server api POST "/api/conversations/$RC/reactions" --json "{\"emoji\": \"+1\", \"target_event_id\": \"$RM\"}" --expect 409
  ```
  With the variable set, the server takes its switches from it and leaves
  the route's check to the host; without it, the stored (off) flag applies
  again.

- **The response is the stored event (`F37.rest-response-parent`).**
  A reaction on the latest message is stamped with that message as its
  parent; the POST answer carries the same `parent_id` as the stored copy.
  Requires: `F37.flag`
  ```sh
  control-agent-server api PATCH /api/settings/experimental/conversation_reactions --json '{"enabled": true}' --expect 200
  PC=$(control-agent-server conversation start --no-autotitle --tools none --print-id)
  control-agent-server api POST "/api/conversations/$PC/events" \
    --json '{"role": "user", "content": [{"type": "text", "text": "QA_F37_PARENT"}], "run": false}' --expect 200
  PM=$(control-agent-server api GET "/api/conversations/$PC/events/search" --query body=QA_F37_PARENT --field items.0.id)
  PR=$(control-agent-server api POST "/api/conversations/$PC/reactions" --json "{\"emoji\": \"heart\", \"target_event_id\": \"$PM\"}" \
    --expect 200 --field id)
  control-agent-server api GET "/api/conversations/$PC/events/$PR" --check parent_id eq "$PM" --save F37.rest-response-parent/stored
  control-agent-server api POST "/api/conversations/$PC/reactions" --json "{\"emoji\": \"+1\", \"target_event_id\": \"$PM\"}" \
    --expect 200 --check parent_id eq "$PR" --save F37.rest-response-parent/post
  ```
  The first reaction is read back to show the stored `parent_id`; the second
  is stamped with the first as its parent (the active leaf), and its POST
  answer says so: `LocalConversation.react` returns the event as the log
  stored it.

## Gotchas

- The flag is read when a conversation starts: turning it on or off does not
  change the tools of running conversations, only the REST route. Child
  conversations reuse their parent's agent, and so its tools.

- Reactions do not wake the agent. A user's `+1` on a question is not an
  answer yet.
- The agent's `react` call is an ordinary tool call, so the `ActionEvent` and
  its short `ObservationEvent` stay in the log next to the `ReactionEvent`;
  clients that show reactions as chips can hide that pair.
- A user's reaction is saved under the conversation's state lock. The
  server's run loop lets go of it during LLM calls, so during a run
  `POST .../reactions` waits only while a tool or an event write holds it.
- Targets come from the active branch: after navigating back, messages on the
  abandoned branch cannot be reacted to.
- A client older than this event kind cannot parse a conversation's events
  once it holds a reaction, as with any new event kind.
- Agent Canvas does not call the reaction or experimental-feature routes yet
  and pins a release without them; its Features page and reaction chips are
  [OpenHands/OpenHands#19505](https://github.com/OpenHands/OpenHands/issues/19505)
  (PR [#19503](https://github.com/OpenHands/OpenHands/pull/19503)).
