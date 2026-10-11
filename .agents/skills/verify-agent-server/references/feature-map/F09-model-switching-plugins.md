# Model, profile and ACP switching; runtime plugins

What a consumer changes on a conversation that already exists, without
recreating it: swap the agent's LLM for a caller-supplied config (inline key,
a cipher token from an `encrypted` read, or a saved provider connection),
switch it to a saved LLM profile, change the model an ACP conversation's
subprocess runs, and load a plugin from the conversation's registered
marketplaces into the live agent (its skills, commands and hooks). Every
switch answers `{"success": true}`, is written to the conversation's
`base_state.json`, shows up in `GET /api/conversations/{id}` and as a
`ConversationStateUpdateEvent` on the events socket, and survives a restart;
the next turn runs on the new model and its usage is recorded under the new
LLM's `usage_id`.

Source: `openhands-agent-server/openhands/agent_server/conversation_router.py`, `openhands-agent-server/openhands/agent_server/event_service.py`, `openhands-agent-server/openhands/agent_server/_secrets_exposure.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/llm/llm_registry.py`, `openhands-sdk/openhands/sdk/llm/llm_profile_store.py`, `openhands-sdk/openhands/sdk/marketplace/`, `openhands-sdk/openhands/sdk/plugin/`, `openhands-sdk/openhands/sdk/agent/acp_agent.py`

Needs: `llm`, `tmux`, `node`, `network`

Routes: `POST /api/conversations/{conversation_id}/switch_profile`,
`POST /api/conversations/{conversation_id}/switch_llm`,
`POST /api/conversations/{conversation_id}/load_plugin`,
`POST /api/conversations/{conversation_id}/switch_acp_model`

## Sub-features

- `F09.auth-required`: all four routes answer 401 without a valid session key and change nothing.
- `F09.switch-llm`: `switch_llm` with a fresh `usage_id` answers 200 `{"success": true}`; `GET` and `base_state.json` show the new model and `usage_id`, the condenser LLM that mirrored the old one follows, and the key is never echoed or stored in plaintext.
- `F09.switch-llm-ws`: a switch pushes a `ConversationStateUpdateEvent` with key `agent` and the new model on the events socket, with the key masked.
- `F09.switch-llm-errors`: an unknown conversation is 404, a body without `llm` or without `llm.model` is 422, and a dangling `provider_connection_id` is 422; the installed LLM is unchanged.
- `F09.switch-llm-encrypted-key`: mid-conversation, a switch whose `api_key` is the `gAAAAA` token from an `encrypted` profile read is decrypted before install; the next turn runs on DeepSeek and its usage is recorded under the new `usage_id`.
- `F09.switch-llm-provider-connection`: a switch that references a saved provider connection instead of a key runs the next turn with the connection's key; `GET` shows the `provider_connection_id` and no key.
- `F09.switch-llm-registered-usage-id`: after the agent initialized, a switch whose `usage_id` is already registered (the `default` a saved profile's config carries) either installs the posted LLM or is refused, never a silent 200 no-op.
- `F09.switch-llm-before-first-run`: a switch before the conversation's first run is used by that run, and the run's token usage is recorded under the switched LLM's `usage_id`.
- `F09.switch-llm-mid-run`: a `switch_llm` while a turn is running (the agent is inside a 20 s tool call) answers 200, the socket pushes the new `agent`, and the same run's next LLM call already uses the switched LLM (usage under both `usage_id`s) and the run finishes.
- `F09.switch-llm-mid-run-get`: right after a mid-run switch answers 200, `GET` and `base_state.json` show the switched LLM, as the socket already does.
- `F09.switch-profile`: `switch_profile` to the saved `deepseek-pro` profile answers 200, installs it under `usage_id` `profile:deepseek-pro`, and the next turn runs on `deepseek/deepseek-v4-pro` with its usage recorded under that id.
- `F09.switch-profile-errors`: an unknown profile is 404 `Profile '<name>' not found`, an invalid name and a corrupted profile file are 400, a missing `profile_name` is 422 and an unknown conversation 404; the installed LLM is unchanged.
- `F09.switch-profile-edited`: switching to a profile again after it was edited installs the edited config, not the copy cached at the first switch.
- `F09.acp-model-native`: `switch_acp_model` on a regular (non-ACP) conversation is 400 `switch_acp_model is only supported for ACP conversations.`; a missing `model` is 422 and an unknown conversation 404.
- `F09.acp-model-deferred`: on an ACP conversation that has not run yet, `switch_acp_model` answers 200 without starting the subprocess and persists the model as `agent.acp_model`, `agent_state.acp_current_model_id` and `current_model_id`.
- `F09.acp-model-blank`: a blank or whitespace-only ACP model is refused before the first run, as it is on a live session, and the stored model is kept.
- `F09.switch-llm-acp-refused`: `switch_llm` and `switch_profile` on an ACP conversation are refused, as `switch_acp_model` is on a regular one, and leave the ACP agent's placeholder LLM (`usage_id` `acp-managed`) alone.
- `F09.acp-model-live`: on an ACP conversation with a live session, `switch_acp_model` switches the subprocess's model in place (same ACP session) and `current_model_id`, `acp_model` and `base_state.json` follow; a blank model is 400, a model the provider does not offer is refused and changes nothing, and the next turn runs on the switched model in the same session.
- `F09.acp-model-live-unknown`: a live switch to a model id the provider does not offer (not in `available_models`) is a client error (400, as for a blank model), not a 500.
- `F09.acp-model-live-timeout`: a live switch whose `session/set_model` round-trip outlasts `acp_prompt_timeout` is 504 with `detail` `Internal Server Error` and leaves the model unchanged.
- `F09.ts-client`: the TypeScript client's `ConversationClient.switchLLM`, `switchProfile` and `switchAcpModel` and `ConversationManager.switchProfile` reach the routes: each switch resolves and reads back through `getConversation`, an unknown profile rejects with 404, `switchAcpModel` on a regular conversation with 400, a call without a key with 401, and `switchAcpModel` on an ACP conversation that has not run resolves and stores the model.
- `F09.ts-client-acp-docs`: the TypeScript client's `switchAcpModel` docstrings describe what the server does before an ACP conversation's first run (200, the model is stored for the first session), not a 409.
- `F09.load-plugin`: `load_plugin` with `<plugin>@<marketplace>` from a local marketplace registered on the agent's `agent_context` answers 200 and merges the plugin's skill, its command and its hooks into the live agent, visible in `GET`, `base_state.json` and on the socket.
- `F09.load-plugin-hook-runs`: after a runtime load, the next user turn runs the plugin's `UserPromptSubmit` hook (a `HookExecutionEvent` with its output) and finishes.
- `F09.load-plugin-errors`: no registered marketplaces and no agent context are 400, a registered marketplace whose source is missing is 400, an unknown marketplace or plugin is 404, empty and malformed refs are 400, a missing `plugin_ref` is 422 and an unknown conversation 404; a bare plugin name resolves across the registered marketplaces.
- `F09.load-plugin-twice`: loading the same plugin twice leaves one copy of its hooks, so they still run once per message.
- `F09.load-plugin-sdk`: the Python SDK's `RemoteConversation.load_plugin(ref)`, on a conversation reattached with `RemoteConversation.attach`, raises `HTTPStatusError` 404 for an unknown plugin and then loads the plugin, whose skill and hook `GET` shows.
- `F09.restart-persist`: after a restart the switched LLM, the switched profile (which the next turn runs on), the deferred ACP model and the loaded plugin's skills and hooks are still in place.

## How to get to it (agent POV)

- REST, owned here (bodies are single embedded fields):
  `POST /api/conversations/{id}/switch_llm` `{"llm": {...LLM fields...}}`,
  `POST /api/conversations/{id}/switch_profile` `{"profile_name": "..."}`,
  `POST /api/conversations/{id}/switch_acp_model` `{"model": "..."}`,
  `POST /api/conversations/{id}/load_plugin` `{"plugin_ref": "<plugin>[@<marketplace>]"}`.
  Each answers `{"success": true}`; every recipe below drives them.
- Second views: `GET /api/conversations/{id}` (`agent.llm`, `agent.condenser.llm`,
  `stats.usage_to_metrics.<usage_id>`, `current_model_id`, `agent.acp_model`,
  `agent_state`, `hook_config`, and `agent.agent_context.skills` with
  `include_skills=true`), the conversation's `base_state.json` (`state cat`),
  `WS /sockets/events/{conversation_id}` (`ConversationStateUpdateEvent` keys
  `agent` and `hook_config`), and the `HookExecutionEvent` a plugin hook emits.
- Setup routes owned by other families: `POST /api/profiles/{name}` and
  `GET /api/profiles/{name}` with `X-Expose-Secrets: encrypted` (LLM profiles),
  `POST`/`DELETE /api/llm/provider-connections` (provider connections),
  `POST /api/conversations` and `POST /api/conversations/{id}/events`
  (conversations and runs).
- SDK: `LocalConversation.switch_llm(llm)`, `switch_profile(name)`,
  `switch_acp_model(model)` and `load_plugin(ref)` are what the routes call;
  over HTTP `RemoteConversation.load_plugin(ref)` posts to `load_plugin`
  (`F09.load-plugin-sdk` drives it).
  `RemoteConversation` has no `switch_llm`, `switch_profile` or
  `switch_acp_model` method; Python consumers post to the routes directly.
- TypeScript client: `ConversationClient.switchProfile(id, name)`,
  `switchLLM(id, llm)`, `switchAcpModel(id, model)`;
  `RemoteConversation.switchProfile`, `switchLlm`, `switchAcpModel`;
  `ConversationManager.switchProfile`. There is no TypeScript `loadPlugin`.
  `F09.ts-client` drives the `ConversationClient` methods and
  `ConversationManager.switchProfile` (the `RemoteConversation` methods post
  the same bodies to the same routes).
- Agent Canvas (context only): the per-conversation model picker calls
  `switch_profile` (or `switch_llm` with a profile config that may reference a
  provider connection), and `switch_acp_model` for ACP conversations, picking
  from `available_models`.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported (`launch --new`), `doctor` is ok,
  `$DEEPSEEK_API_KEY` is set, `jq` is on `PATH` and `tmux` is installed (the
  mid-run bullets give the agent the terminal tool). The live ACP bullets
  need `node` (`npx` launches the Claude Code adapter) and outbound HTTPS to
  the npm registry and `api.deepseek.com`; the TypeScript bullet needs
  `node` (and `npm` with the network only if `clients/typescript/dist` is
  missing). The block below saves the
  DeepSeek preset (`deepseek-flash` active, `deepseek-pro`), reads the
  `deepseek-flash` key back as a cipher token (`ENC`), creates the local
  marketplace fixture `qa-f09-mkt` (one plugin with a skill, a command and a
  `UserPromptSubmit` hook that prints `QA_PLUGIN_HOOK`), and starts `CID`, an
  idle conversation from the saved settings that never runs.

  ```sh
  control-agent-server llm preset deepseek
  ENC=$(control-agent-server api GET /api/profiles/deepseek-flash --header 'X-Expose-Secrets: encrypted' --field config.api_key)
  test "${ENC#gAAAAA}" != "$ENC"
  MKT=$(control-agent-server fixture marketplace --name qa-f09-mkt --print-path)
  CID=$(control-agent-server conversation start --tools none --no-autotitle --print-id)
  control-agent-server api GET "/api/conversations/$CID" --check agent.llm.model eq deepseek/deepseek-flash --check agent.llm.usage_id eq default --quiet
  ```

- **Key required (`F09.auth-required`).** Call every route without a key.
  ```sh
  control-agent-server api POST "/api/conversations/$CID/switch_llm" --auth none --json '{"llm": {"model": "openai/qa-f09-noauth", "usage_id": "qa-f09-noauth"}}' --expect 401 --check detail eq Unauthorized --save F09.auth-required/switch-llm
  control-agent-server api POST "/api/conversations/$CID/switch_profile" --auth none --json '{"profile_name": "deepseek-pro"}' --expect 401
  control-agent-server api POST "/api/conversations/$CID/switch_acp_model" --auth bad --json '{"model": "opus"}' --expect 401
  control-agent-server api POST "/api/conversations/$CID/load_plugin" --auth none --json '{"plugin_ref": "qa-f09-mkt-plugin@qa-f09-mkt"}' --expect 401
  control-agent-server api GET "/api/conversations/$CID" --check agent.llm.model eq deepseek/deepseek-flash --check agent.llm.usage_id eq default --quiet
  ```
  Every call is 401 `{"detail": "Unauthorized"}` and the conversation is
  still on `deepseek-flash`.

- **Switch to a caller-supplied LLM (`F09.switch-llm`).** Install an
  unreachable placeholder model with its own `usage_id` on the idle `CID`.
  ```sh
  control-agent-server api POST "/api/conversations/$CID/switch_llm" \
    --json '{"llm": {"model": "openai/qa-f09-switched", "api_key": "sk-qa-f09-switched", "base_url": "http://127.0.0.1:9/v1", "usage_id": "qa-f09-switched"}}' \
    --expect 200 --check success eq true --save F09.switch-llm/switch
  control-agent-server api GET "/api/conversations/$CID" --expect 200 --quiet \
    --check agent.llm.model eq openai/qa-f09-switched --check agent.llm.usage_id eq qa-f09-switched \
    --check agent.llm.base_url eq http://127.0.0.1:9/v1 --check agent.llm.api_key missing \
    --check agent.condenser.llm.model eq openai/qa-f09-switched --check agent.condenser.llm.usage_id eq condenser \
    --check execution_status eq idle --save F09.switch-llm/get
  control-agent-server state cat "server/workspace/conversations/${CID//-/}/base_state.json" --max-chars 200 \
    --check agent.llm.model eq openai/qa-f09-switched --check agent.llm.api_key matches '^gAAAAA' --not-contains sk-qa-f09-switched
  control-agent-server state cat "server/workspace/conversations/${CID//-/}/meta.json" --max-chars 200 --check agent missing
  control-agent-server state grep sk-qa-f09-switched --glob 'server/**/*' --expect-none
  ```
  The switch is 200 `{"success": true}`. `GET` shows the new model,
  `usage_id` and `base_url` with `api_key: null`; the summarizing condenser,
  whose LLM mirrored the agent's, now runs the same model under its own
  `usage_id` `condenser`. `base_state.json` holds the key as a Fernet token,
  `meta.json` has no agent, and the plaintext key is nowhere on disk.
- **Socket view of a switch (`F09.switch-llm-ws`).** Capture the events
  socket while switching again.
  ```sh
  control-agent-server ws start "/sockets/events/$CID" --name qa-f09-switch --duration 120
  control-agent-server api POST "/api/conversations/$CID/switch_llm" \
    --json '{"llm": {"model": "openai/qa-f09-ws", "api_key": "sk-qa-f09-ws", "base_url": "http://127.0.0.1:9/v1", "usage_id": "qa-f09-ws"}}' --expect 200
  control-agent-server ws read qa-f09-switch --kinds ConversationStateUpdateEvent --contains openai/qa-f09-ws --expect-min 1 --wait 20 --save F09.switch-llm-ws/frames
  control-agent-server ws read qa-f09-switch --kinds ConversationStateUpdateEvent \
    --contains '"key": "agent", "value": {"llm": {"model": "openai/qa-f09-ws", "api_key": "**********"' --expect-min 1
  control-agent-server ws read qa-f09-switch --contains 'redacted:encrypted' --expect-none
  control-agent-server ws stop qa-f09-switch --contains sk-qa-f09-ws --expect-none
  control-agent-server api GET "/api/conversations/$CID" --quiet --check agent.llm.model eq openai/qa-f09-ws --check agent.llm.usage_id eq qa-f09-ws
  ```
  A `ConversationStateUpdateEvent` with `key: "agent"` arrives whose value
  carries `openai/qa-f09-ws` with `api_key` `**********` in that same frame
  (the positive check: captures are written through the CLI's redactor, which
  turns the run's keys, `$DEEPSEEK_API_KEY` and every `gAAAAA` token into
  `<redacted:...>`, so a frame that carried the cipher token instead of the
  mask would fail it, and the `redacted:encrypted` read finds no such frame).
  `sk-qa-f09-ws` is not a value the redactor knows, so the last read is a
  real check that no frame contains the plaintext key.
- **Switch errors (`F09.switch-llm-errors`).** Unknown conversation, bad
  bodies and a provider connection that does not exist.
  ```sh
  control-agent-server api POST /api/conversations/00000000-0000-4000-8000-000000000000/switch_llm \
    --json '{"llm": {"model": "openai/qa-f09-x", "usage_id": "qa-f09-x"}}' --expect 404 --save F09.switch-llm-errors/unknown-conversation
  control-agent-server api POST "/api/conversations/$CID/switch_llm" --json '{}' --expect 422 --check detail.0.loc.1 eq llm --save F09.switch-llm-errors/no-llm
  control-agent-server api POST "/api/conversations/$CID/switch_llm" --json '{"llm": {"api_key": "sk-qa-f09", "usage_id": "qa-f09-nomodel"}}' \
    --expect 422 --check detail.0.msg contains 'model must be specified'
  control-agent-server api POST "/api/conversations/$CID/switch_llm" \
    --json '{"llm": {"model": "openai/qa-f09-dangling", "provider_connection_id": "qa-f09-missing", "usage_id": "qa-f09-dangling"}}' \
    --expect 422 --check detail contains "provider connection 'qa-f09-missing', which does not exist" --save F09.switch-llm-errors/dangling-connection
  control-agent-server api GET "/api/conversations/$CID" --quiet --check agent.llm.model eq openai/qa-f09-ws --check agent.llm.usage_id eq qa-f09-ws
  ```
  404 for the unknown id, 422 for a missing `llm` and a missing `model`, and
  422 naming the dangling connection; `CID` is still on `openai/qa-f09-ws`.
- **Mid-conversation switch with a cipher token (`F09.switch-llm-encrypted-key`).**
  Run one turn on `deepseek-flash`, switch to the same model with the `ENC`
  token as key under a new `usage_id`, and run again.
  ```sh
  RID=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Reply with the single word: ok' --wait --until finished --timeout 240 --print-id)
  control-agent-server api GET "/api/conversations/$RID" --quiet --check stats.usage_to_metrics.default.model_name eq deepseek/deepseek-flash
  control-agent-server api POST "/api/conversations/$RID/switch_llm" \
    --json "{\"llm\": {\"model\": \"deepseek/deepseek-flash\", \"api_key\": \"$ENC\", \"usage_id\": \"qa-f09-enc\", \"num_retries\": 0}}" \
    --expect 200 --save F09.switch-llm-encrypted-key/switch
  control-agent-server api GET "/api/conversations/$RID" --quiet --check agent.llm.usage_id eq qa-f09-enc --check agent.llm.api_key missing
  control-agent-server conversation send "$RID" --text 'Reply with the single word: ok' --wait --until finished,error --timeout 240
  control-agent-server api GET "/api/conversations/$RID" --until-ok 60 --quiet --check execution_status eq finished \
    --check stats.usage_to_metrics.qa-f09-enc.model_name eq deepseek/deepseek-flash \
    --check stats.usage_to_metrics.qa-f09-enc.accumulated_token_usage.prompt_tokens gt 0 --save F09.switch-llm-encrypted-key/stats
  ```
  The second turn finishes and its tokens are recorded under `qa-f09-enc`.
  Sent as-is, the token would be rejected by DeepSeek; the route decrypted it
  with the server's cipher before installing the LLM.
- **Switch through a provider connection (`F09.switch-llm-provider-connection`).**
  Save the DeepSeek key as a provider connection and switch `RID` to a
  config that references it instead of carrying a key.
  ```sh
  CONN=$(printf '{"display_name": "QA F09", "provider": "deepseek", "api_key": "%s"}' "$DEEPSEEK_API_KEY" \
    | control-agent-server api POST /api/llm/provider-connections --stdin --expect 201 --field id)
  control-agent-server api POST "/api/conversations/$RID/switch_llm" \
    --json "{\"llm\": {\"model\": \"deepseek/deepseek-flash\", \"provider_connection_id\": \"$CONN\", \"usage_id\": \"qa-f09-conn\", \"num_retries\": 0}}" \
    --expect 200 --save F09.switch-llm-provider-connection/switch
  control-agent-server api GET "/api/conversations/$RID" --quiet --check agent.llm.provider_connection_id eq "$CONN" \
    --check agent.llm.usage_id eq qa-f09-conn --check agent.llm.api_key missing
  control-agent-server conversation send "$RID" --text 'Reply with the single word: ok' --wait --until finished,error --timeout 240
  control-agent-server api GET "/api/conversations/$RID" --until-ok 60 --quiet --check execution_status eq finished \
    --check stats.usage_to_metrics.qa-f09-conn.accumulated_token_usage.prompt_tokens gt 0 --save F09.switch-llm-provider-connection/stats
  CONN1=$(control-agent-server api GET "/api/conversations/$RID" --field stats.usage_to_metrics.qa-f09-conn.accumulated_token_usage.prompt_tokens)
  control-agent-server api DELETE "/api/llm/provider-connections/$CONN" --expect 200
  control-agent-server conversation send "$RID" --text 'Reply with the single word: ok' --wait --until finished,error --timeout 240
  control-agent-server api GET "/api/conversations/$RID" --until-ok 60 --quiet --check execution_status eq finished \
    --check stats.usage_to_metrics.qa-f09-conn.accumulated_token_usage.prompt_tokens gt "$CONN1"
  ```
  The turn finishes on the connection's key (the server does not inherit
  `DEEPSEEK_API_KEY`, so there is no other key it could use). The
  connection is resolved once, at the switch: after it is deleted, the next
  turn still runs on the installed LLM and its token count grows.
- **Switch to a saved profile (`F09.switch-profile`).** Move `RID` to the
  preset's `deepseek-pro` profile and run a turn on it.
  ```sh
  control-agent-server api POST "/api/conversations/$RID/switch_profile" --json '{"profile_name": "deepseek-pro"}' \
    --expect 200 --check success eq true --save F09.switch-profile/switch
  control-agent-server api GET "/api/conversations/$RID" --quiet --check agent.llm.model eq deepseek/deepseek-v4-pro \
    --check agent.llm.usage_id eq profile:deepseek-pro --check agent.llm.api_key missing --save F09.switch-profile/get
  control-agent-server state cat "server/workspace/conversations/${RID//-/}/base_state.json" --max-chars 200 \
    --check agent.llm.model eq deepseek/deepseek-v4-pro --check agent.llm.usage_id eq profile:deepseek-pro
  control-agent-server conversation send "$RID" --text 'Reply with the single word: ok' --wait --until finished,error --timeout 240
  control-agent-server api GET "/api/conversations/$RID" --until-ok 60 --quiet --check execution_status eq finished \
    --check 'stats.usage_to_metrics.profile:deepseek-pro.model_name' eq deepseek/deepseek-v4-pro \
    --check 'stats.usage_to_metrics.profile:deepseek-pro.accumulated_token_usage.prompt_tokens' gt 0 --save F09.switch-profile/stats
  PRO1=$(control-agent-server api GET "/api/conversations/$RID" --field 'stats.usage_to_metrics.profile:deepseek-pro.accumulated_token_usage.prompt_tokens')
  ```
  The conversation now runs `deepseek/deepseek-v4-pro` under `usage_id`
  `profile:deepseek-pro`, and that usage id has the turn's tokens. `PRO1`
  keeps the count for `F09.restart-persist`.
- **Profile switch errors (`F09.switch-profile-errors`).** Unknown, invalid
  and corrupted profiles, a missing field and an unknown conversation, on
  `CID`.
  ```sh
  control-agent-server api POST "/api/conversations/$CID/switch_profile" --json '{"profile_name": "qa-f09-missing"}' \
    --expect 404 --check detail eq "Profile 'qa-f09-missing' not found" --save F09.switch-profile-errors/unknown-profile
  control-agent-server api POST "/api/conversations/$CID/switch_profile" --json '{"profile_name": "../profiles/deepseek-pro"}' \
    --expect 400 --check detail contains 'Invalid profile name' --save F09.switch-profile-errors/invalid-name
  control-agent-server api POST "/api/conversations/$CID/switch_profile" --json '{"profile_name": ""}' --expect 400 --check detail contains 'Invalid profile name'
  printf '{not json' > "$AGENT_SERVER_VERIFY_RUN/home/.openhands/profiles/qa-f09-corrupt.json"
  control-agent-server api POST "/api/conversations/$CID/switch_profile" --json '{"profile_name": "qa-f09-corrupt"}' \
    --expect 400 --check detail contains 'Failed to load profile `qa-f09-corrupt`' --save F09.switch-profile-errors/corrupted
  rm "$AGENT_SERVER_VERIFY_RUN/home/.openhands/profiles/qa-f09-corrupt.json"
  control-agent-server api POST "/api/conversations/$CID/switch_profile" --json '{}' --expect 422 --check detail.0.loc.1 eq profile_name
  control-agent-server api POST /api/conversations/00000000-0000-4000-8000-000000000000/switch_profile --json '{"profile_name": "deepseek-pro"}' --expect 404
  control-agent-server api GET "/api/conversations/$CID" --quiet --check agent.llm.model eq openai/qa-f09-ws --check agent.llm.usage_id eq qa-f09-ws
  ```
  404 with the profile name, 400 for a path-like or empty name and for a
  profile file that is not JSON (written straight into the run's profile
  directory and removed again), 422 and 404 for the rest; `CID` keeps
  `openai/qa-f09-ws`.
- **Switch to an edited profile (`F09.switch-profile-edited`), known bug.**
  Switch an offline conversation to a profile, edit the profile, switch
  again.
  ```sh
  trap 'control-agent-server api DELETE /api/profiles/qa-f09-edit >/dev/null' EXIT
  EID=$(control-agent-server conversation start --placeholder-agent --tools none --no-autotitle --print-id)
  control-agent-server api POST /api/profiles/qa-f09-edit --json '{"llm": {"model": "openai/qa-f09-edit-1", "api_key": "sk-qa-f09-edit", "base_url": "http://127.0.0.1:9/v1"}}' --expect 201
  control-agent-server api POST "/api/conversations/$EID/switch_profile" --json '{"profile_name": "qa-f09-edit"}' --expect 200
  control-agent-server api GET "/api/conversations/$EID" --quiet --check agent.llm.model eq openai/qa-f09-edit-1
  control-agent-server api POST /api/profiles/qa-f09-edit --json '{"llm": {"model": "openai/qa-f09-edit-2", "api_key": "sk-qa-f09-edit", "base_url": "http://127.0.0.1:9/v1"}}' --expect 201
  control-agent-server api GET /api/profiles/qa-f09-edit --quiet --check config.model eq openai/qa-f09-edit-2
  control-agent-server api POST "/api/conversations/$EID/switch_profile" --json '{"profile_name": "qa-f09-edit"}' --expect 200 --save F09.switch-profile-edited/switch-again
  control-agent-server api GET "/api/conversations/$EID" --quiet --check agent.llm.model eq openai/qa-f09-edit-2 --save F09.switch-profile-edited/get  # bug
  ```
  The first switch installs `openai/qa-f09-edit-1` and the profile then
  reads back as `openai/qa-f09-edit-2`. Expected: the second switch installs
  the edited config. Today it answers 200 and reinstalls `qa-f09-edit-1`:
  `LocalConversation.switch_profile` looks up `profile:<name>` in the
  conversation's LLM registry first and only reads the profile file on a
  miss, so a fixed key or model never reaches a conversation that used the
  profile before. The SDK docstring states the cache ("cached in the
  registry under `profile:{profile_name}` after first load"), but the route
  documents nothing and offers no way to refresh, so a REST consumer can
  only apply the edit with `switch_llm` and a fresh `usage_id`. The profile
  is deleted on exit.
- **Switch onto a registered usage id (`F09.switch-llm-registered-usage-id`), known bug.**
  Forward the saved `deepseek-pro` profile's config, read with
  `X-Expose-Secrets: encrypted` (the per-conversation model switch the
  route's docstring describes), to two offline conversations: one fresh, one
  whose agent was initialized by a queued message.
  ```sh
  PRO=$(control-agent-server api GET /api/profiles/deepseek-pro --header 'X-Expose-Secrets: encrypted' --check config.usage_id eq default --field config)
  FID=$(control-agent-server conversation start --placeholder-agent --tools none --no-autotitle --print-id)
  control-agent-server api POST "/api/conversations/$FID/switch_llm" --json "{\"llm\": $PRO}" --expect 200
  control-agent-server api GET "/api/conversations/$FID" --quiet --check agent.llm.model eq deepseek/deepseek-v4-pro
  KID=$(control-agent-server conversation start --placeholder-agent --tools none --no-autotitle --prompt 'Reply with the single word: ok' --no-run --print-id)
  control-agent-server api GET "/api/conversations/$KID" --quiet --check agent.llm.usage_id eq default --check execution_status eq idle
  ST=$(control-agent-server api POST "/api/conversations/$KID/switch_llm" --json "{\"llm\": $PRO}" \
    --expect 200,400,409,422 --save F09.switch-llm-registered-usage-id/switch | jq -r .status)
  test "$ST" != 200 || control-agent-server api GET "/api/conversations/$KID" --quiet --check agent.llm.model eq deepseek/deepseek-v4-pro --save F09.switch-llm-registered-usage-id/get  # bug
  ```
  The profile config carries `usage_id: default`, and the fresh conversation
  switches to `deepseek/deepseek-v4-pro`. Expected: the initialized one
  switches too, or the switch is refused with a 4xx. Today it is 200
  `{"success": true}` and `GET` still shows `openai/qa-placeholder`: once the
  agent initialized, `default` is in the conversation's LLM registry, and
  `switch_llm` reuses the registered LLM for a known `usage_id`
  (first-write-wins) and drops the posted one. `LocalConversation.switch_llm`
  documents that contract, but the route answers `{"success": true}` and its
  docstring offers it for forwarding profile configs, which carry `default`
  (also the `LLM` default when `usage_id` is omitted). Any conversation that
  has received a message silently ignores such a switch; a fresh `usage_id`
  works (as in `F09.switch-llm-encrypted-key`).
- **Switch before the first run (`F09.switch-llm-before-first-run`), known bug.**
  Start from an agent whose model is unreachable, switch to DeepSeek before
  anything ran, then run.
  ```sh
  BID=$(control-agent-server conversation start --placeholder-agent --tools none --no-autotitle --print-id)
  control-agent-server api POST "/api/conversations/$BID/switch_llm" \
    --json "{\"llm\": {\"model\": \"deepseek/deepseek-flash\", \"api_key\": \"$ENC\", \"usage_id\": \"qa-f09-early\", \"num_retries\": 0}}" \
    --expect 200 --save F09.switch-llm-before-first-run/switch
  control-agent-server conversation send "$BID" --text 'Reply with the single word: ok' --wait --until finished,error --timeout 240
  control-agent-server api GET "/api/conversations/$BID" --quiet --check execution_status eq finished --check agent.llm.usage_id eq qa-f09-early
  control-agent-server api GET "/api/conversations/$BID" --until-ok 20 --quiet \
    --check stats.usage_to_metrics.qa-f09-early.accumulated_token_usage.prompt_tokens gt 0 --save F09.switch-llm-before-first-run/stats  # bug
  ```
  The run finishes, which only the switched model can do (the placeholder
  points at a closed port). Expected: its tokens are recorded under
  `qa-f09-early`. Today `stats.usage_to_metrics` has only `condenser`: the
  switch registered the LLM before the registry had its stats subscriber,
  and agent initialization skips LLMs whose `usage_id` is already
  registered, so the switched LLM is never added to `ConversationStats` and
  the conversation's cost and token totals miss every call it makes. The
  same switch after one message (`F09.switch-llm-encrypted-key`) is counted.
- **Switch during a running turn (`F09.switch-llm-mid-run`).** Start a run
  whose first action is a 20 s command, switch once that action exists,
  and let the run finish.
  ```sh
  MID=$(control-agent-server conversation start --tools terminal --no-autotitle --prompt 'Run this exact shell command and nothing else: sleep 20' --print-id)
  control-agent-server ws start "/sockets/events/$MID" --name qa-f09-midrun --duration 300
  control-agent-server ws read qa-f09-midrun --kinds ActionEvent --contains 'sleep 20' --expect-min 1 --wait 120
  control-agent-server api POST "/api/conversations/$MID/switch_llm" \
    --json "{\"llm\": {\"model\": \"deepseek/deepseek-flash\", \"api_key\": \"$ENC\", \"usage_id\": \"qa-f09-midrun\", \"num_retries\": 0}}" \
    --expect 200 --check success eq true --save F09.switch-llm-mid-run/switch
  control-agent-server conversation wait "$MID" --until finished,error --timeout 240
  control-agent-server ws stop qa-f09-midrun --kinds ConversationStateUpdateEvent --contains qa-f09-midrun --expect-min 1 --wait 20 --save F09.switch-llm-mid-run/frames
  control-agent-server api GET "/api/conversations/$MID" --until-ok 60 --quiet --check execution_status eq finished \
    --check agent.llm.usage_id eq qa-f09-midrun \
    --check stats.usage_to_metrics.default.accumulated_token_usage.prompt_tokens gt 0 \
    --check stats.usage_to_metrics.qa-f09-midrun.accumulated_token_usage.prompt_tokens gt 0 --save F09.switch-llm-mid-run/stats
  ```
  The switch is 200 while `sleep 20` runs (today at once, without waiting
  for the tool), the socket carries a state update with `qa-f09-midrun`, and
  the run finishes. Both `usage_id`s have tokens: the call that chose
  `sleep 20` ran on `default`, the call after its observation on
  `qa-f09-midrun`, so the switch applied inside the same run.
- **GET right after a mid-run switch (`F09.switch-llm-mid-run-get`), known bug.**
  The same situation, read back at once.
  ```sh
  GID=$(control-agent-server conversation start --tools terminal --no-autotitle --prompt 'Run this exact shell command and nothing else: sleep 20' --print-id)
  trap 'control-agent-server conversation wait "$GID" --until finished,error --timeout 240 >/dev/null 2>&1 || true; control-agent-server ws stop qa-f09-midget >/dev/null 2>&1 || true' EXIT
  control-agent-server ws start "/sockets/events/$GID" --name qa-f09-midget --duration 150
  control-agent-server ws read qa-f09-midget --kinds ActionEvent --contains 'sleep 20' --expect-min 1 --wait 120
  control-agent-server api POST "/api/conversations/$GID/switch_llm" \
    --json "{\"llm\": {\"model\": \"deepseek/deepseek-flash\", \"api_key\": \"$ENC\", \"usage_id\": \"qa-f09-midget\", \"num_retries\": 0}}" \
    --expect 200 --save F09.switch-llm-mid-run-get/switch
  control-agent-server ws read qa-f09-midget --kinds ConversationStateUpdateEvent --contains '"usage_id": "qa-f09-midget"' --expect-min 1 --wait 10 --save F09.switch-llm-mid-run-get/frames
  control-agent-server api GET "/api/conversations/$GID" --quiet --check execution_status eq running
  control-agent-server api GET "/api/conversations/$GID" --quiet --check agent.llm.usage_id eq qa-f09-midget --save F09.switch-llm-mid-run-get/get  # bug
  control-agent-server state cat "server/workspace/conversations/${GID//-/}/base_state.json" --max-chars 200 --check agent.llm.usage_id eq qa-f09-midget  # bug
  control-agent-server conversation wait "$GID" --until finished,error --timeout 240
  control-agent-server api GET "/api/conversations/$GID" --quiet --check execution_status eq finished --check agent.llm.usage_id eq qa-f09-midget
  ```
  The switch is 200 while `sleep 20` runs, and the socket pushes the
  switched `agent` (`usage_id` `qa-f09-midget`) at once. Expected: `GET`
  shows `qa-f09-midget` from then on. Today the next `GET` (and
  `base_state.json`, which it serves) still report `default` with the run
  `running`, and keep it until the running step ends, about 20 s later,
  although the in-memory agent already switched. (The first `GET` is the
  control that the reads land inside the step; the marked `GET` is the
  assertion that should hold. On a failure the `EXIT` trap waits for the run
  and stops the capture.) The route
  calls `LocalConversation.switch_llm` on the event-loop thread; `arun()`
  holds the reentrant state lock on that same thread during the step, so the
  switch re-enters it and its autosave is deferred to the step's exit (the
  `arun()` comment says unrelated mutators must go through
  `run_in_executor`; `load_plugin` and `switch_acp_model` do). A consumer
  that verifies the switch with `GET` sees the old model for as long as the
  tool runs, and a `restart --hard` in that window loses the acknowledged
  switch (seen live: `GET` shows `default` after the restart).
- **ACP switch on a regular conversation (`F09.acp-model-native`).** `CID`
  runs an OpenHands `Agent`.
  ```sh
  control-agent-server api POST "/api/conversations/$CID/switch_acp_model" --json '{"model": "opus"}' \
    --expect 400 --check detail eq 'switch_acp_model is only supported for ACP conversations.' --save F09.acp-model-native/native
  control-agent-server api POST "/api/conversations/$CID/switch_acp_model" --json '{}' --expect 422 --check detail.0.loc.1 eq model
  control-agent-server api POST /api/conversations/00000000-0000-4000-8000-000000000000/switch_acp_model --json '{"model": "opus"}' --expect 404
  control-agent-server api GET "/api/conversations/$CID" --quiet --check agent.kind eq Agent --check agent.llm.model eq openai/qa-f09-ws --check current_model_id missing
  ```
  400 with the exact message, 422 without `model`, 404 for the unknown id;
  the conversation is unchanged.
- **ACP model before the first run (`F09.acp-model-deferred`).** Create a
  Claude Code ACP conversation with `acp_model: sonnet` and no prompt (no
  subprocess starts until a run), then switch it.
  ```sh
  AID=$(control-agent-server conversation start --no-autotitle --body-json '{"agent_settings": {"agent_kind": "acp", "acp_server": "claude-code", "acp_model": "sonnet"}}' --print-id)
  control-agent-server api GET "/api/conversations/$AID" --quiet --check agent.kind eq ACPAgent --check agent.acp_model eq sonnet \
    --check current_model_id eq sonnet --check execution_status eq idle
  control-agent-server api POST "/api/conversations/$AID/switch_acp_model" --json '{"model": "opus"}' --expect 200 --check success eq true --save F09.acp-model-deferred/switch
  control-agent-server api GET "/api/conversations/$AID" --quiet --check agent.acp_model eq opus --check current_model_id eq opus \
    --check agent_state.acp_current_model_id eq opus --check agent_state.acp_session_id missing --check execution_status eq idle --save F09.acp-model-deferred/get
  control-agent-server state cat "server/workspace/conversations/${AID//-/}/base_state.json" --max-chars 200 \
    --check agent.acp_model eq opus --check agent_state.acp_current_model_id eq opus
  ```
  The switch is 200 although no ACP session exists (`acp_session_id` is
  absent); `acp_model`, `agent_state.acp_current_model_id` and
  `current_model_id` all say `opus`, so the first session would start on it.
- **Blank ACP model (`F09.acp-model-blank`), known bug.** A second ACP
  conversation that has not run.
  ```sh
  AID2=$(control-agent-server conversation start --no-autotitle --body-json '{"agent_settings": {"agent_kind": "acp", "acp_server": "claude-code", "acp_model": "sonnet"}}' --print-id)
  control-agent-server api GET "/api/conversations/$AID2" --quiet --check agent.kind eq ACPAgent --check agent.acp_model eq sonnet --check execution_status eq idle
  control-agent-server api POST "/api/conversations/$AID2/switch_acp_model" --json '{"model": "   "}' --expect 400,422 --save F09.acp-model-blank/blank  # bug
  control-agent-server api GET "/api/conversations/$AID2" --quiet --check agent.acp_model eq sonnet --check current_model_id eq sonnet  # bug
  ```
  Expected: refused, as `ACPAgent.set_acp_model` refuses an empty or
  whitespace-only model on a live session (`model must be a non-empty
  string`, 400). Today the pre-run path skips that check: 200, and
  `agent.acp_model` and `current_model_id` become `"   "`.
- **LLM switches on an ACP conversation (`F09.switch-llm-acp-refused`), known bug.**
  A third ACP conversation that has not run; its agent carries a
  placeholder LLM that only names the model.
  ```sh
  AID3=$(control-agent-server conversation start --no-autotitle --body-json '{"agent_settings": {"agent_kind": "acp", "acp_server": "claude-code", "acp_model": "sonnet"}}' --print-id)
  control-agent-server api GET "/api/conversations/$AID3" --quiet --check agent.llm.usage_id eq acp-managed --check agent.llm.model eq sonnet
  control-agent-server api POST "/api/conversations/$AID3/switch_profile" --json '{"profile_name": "deepseek-pro"}' --expect 400,409,422 --save F09.switch-llm-acp-refused/switch-profile  # bug
  control-agent-server api POST "/api/conversations/$AID3/switch_llm" \
    --json '{"llm": {"model": "openai/qa-f09-acp", "api_key": "sk-qa-f09-acp", "base_url": "http://127.0.0.1:9/v1", "usage_id": "qa-f09-acp"}}' \
    --expect 400,409,422 --save F09.switch-llm-acp-refused/switch-llm  # bug
  control-agent-server api GET "/api/conversations/$AID3" --quiet --check agent.llm.usage_id eq acp-managed --check agent.acp_model eq sonnet  # bug
  ```
  The ACP agent starts with `usage_id` `acp-managed` and the model name
  `sonnet`. Expected: both switches are refused with a 4xx (400 would mirror
  `switch_acp_model` on a regular conversation; the ACP subprocess makes
  its own model calls and `LocalConversation.switch_acp_model` says
  `switch_llm` would not affect it). Today both
  answer 200 and replace the placeholder in `base_state.json` with the
  posted LLM (`GET` still shows `model: sonnet`, because the ACP agent
  overwrites the model name with `acp_model` when it is loaded, but
  `usage_id` becomes `profile:deepseek-pro`, then `qa-f09-acp`). Nothing the
  subprocess runs changes, and the `acp-managed` marker that
  `title_utils` uses to avoid calling the placeholder is gone.
- **Live ACP switch (`F09.acp-model-live`).** Translated environment: the
  ACP provider is the real Claude Code adapter (`claude-code`, which the
  server launches with `npx`, so it needs `node` and the network), and
  DeepSeek's Anthropic-compatible API stands in for an Anthropic account
  (`ANTHROPIC_BASE_URL` `https://api.deepseek.com/anthropic` with the
  DeepSeek key as `ANTHROPIC_API_KEY`, passed as conversation secrets, which
  the adapter honours). `IS_SANDBOX=1` lets Claude Code accept the
  server's `bypassPermissions` session mode when it runs as root (without
  it the run errors with `Mode bypassPermissions is not available in this
  session`). The protocol switch, the session and the persistence are the
  real ones; only the model behind the Anthropic API differs.
  ```sh
  LID=$(control-agent-server conversation start --no-autotitle --body-json '{"agent_settings": {"agent_kind": "acp", "acp_server": "claude-code", "acp_model": "sonnet"}}' \
    --secret ANTHROPIC_API_KEY="$DEEPSEEK_API_KEY" --secret ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic --secret IS_SANDBOX=1 \
    --prompt 'Reply with the single word: ok' --wait --until finished --timeout 300 --print-id)
  SESS=$(control-agent-server api GET "/api/conversations/$LID" --check supports_runtime_model_switch eq true --check current_model_id eq sonnet \
    --check available_models contains '"model_id": "haiku"' --check stats.usage_to_metrics.acp-managed.accumulated_token_usage.completion_tokens gt 0 \
    --field agent_state.acp_session_id)
  test -n "$SESS"
  LC1=$(control-agent-server api GET "/api/conversations/$LID" --field stats.usage_to_metrics.acp-managed.accumulated_token_usage.completion_tokens)
  control-agent-server api POST "/api/conversations/$LID/switch_acp_model" --json '{"model": "haiku"}' --expect 200 --check success eq true --save F09.acp-model-live/switch
  control-agent-server api GET "/api/conversations/$LID" --quiet --check current_model_id eq haiku --check agent.acp_model eq haiku \
    --check agent_state.acp_current_model_id eq haiku --check agent_state.acp_session_id eq "$SESS" --save F09.acp-model-live/get
  control-agent-server state cat "server/workspace/conversations/${LID//-/}/base_state.json" --max-chars 200 --check agent.acp_model eq haiku --check agent_state.acp_current_model_id eq haiku
  control-agent-server api POST "/api/conversations/$LID/switch_acp_model" --json '{"model": "   "}' --expect 400 --check detail eq 'model must be a non-empty string'
  control-agent-server api POST "/api/conversations/$LID/switch_acp_model" --json '{"model": "qa-f09-nope"}' --expect 4xx,5xx --save F09.acp-model-live/unknown-model
  control-agent-server api GET "/api/conversations/$LID" --quiet --check current_model_id eq haiku --check agent.acp_model eq haiku
  control-agent-server conversation send "$LID" --text 'Reply with the single word: ok' --wait --until finished,error --timeout 300
  control-agent-server api GET "/api/conversations/$LID" --until-ok 30 --quiet --check execution_status eq finished --check agent_state.acp_session_id eq "$SESS" \
    --check stats.usage_to_metrics.acp-managed.model_name eq haiku \
    --check stats.usage_to_metrics.acp-managed.accumulated_token_usage.completion_tokens gt "$LC1" --save F09.acp-model-live/second-turn
  ```
  After the first turn the conversation reports
  `supports_runtime_model_switch: true` and the adapter's
  `available_models` (`default`, `opus[1m]`, `sonnet`, `sonnet[1m]`,
  `haiku`), on `sonnet`. The switch to `haiku` is 200 and `current_model_id`,
  `agent.acp_model`, `agent_state.acp_current_model_id` and
  `base_state.json` follow, with the same `acp_session_id` (switched in
  place, not a new session). A blank model is 400 `model must be a
  non-empty string`. An id the adapter does not offer is refused and the
  model stays `haiku` (which status it is refused with is
  `F09.acp-model-live-unknown`). The next turn finishes in the same
  session, and its usage is recorded on `acp-managed` with `model_name`
  `haiku`. Every built-in provider declares
  runtime switching, so the route's other 400 (`does not support runtime
  model switching`) is not reachable with them.
- **Unknown model on a live session (`F09.acp-model-live-unknown`), known bug.**
  `LID` from the bullet above, whose adapter lists its models.
  ```sh
  control-agent-server api GET "/api/conversations/$LID" --quiet --check supports_runtime_model_switch eq true \
    --check available_models contains '"model_id": "sonnet"' --check available_models not-contains qa-f09-nope
  control-agent-server api POST "/api/conversations/$LID/switch_acp_model" --json '{"model": "qa-f09-nope"}' --expect 400,422 --save F09.acp-model-live-unknown/switch  # bug
  ```
  Expected: a client error, as `ACPAgent.set_acp_model` documents ("the ACP
  server rejects the model-switch call (e.g. ... an invalid model id)" is a
  `ValueError`, which the route turns into 400) and as a blank model already
  is. Today it is 500 `Internal Server Error` with `exception` `Internal
  error`: claude-agent-acp answers `set_config_option(model)` with an id it
  does not offer with JSON-RPC -32603, and `set_acp_model` re-raises -32603
  as a server-side failure instead of a `ValueError`, although the session's
  `available_models` already shows the id is not offered. A consumer that
  mistypes a model sees a retriable-looking 500 for a request that can
  never succeed. Nothing changes either way (`F09.acp-model-live` checks
  that the model stays `haiku`).
- **Live switch that times out (`F09.acp-model-live-timeout`).** The same
  translated provider, on a conversation created with
  `acp_prompt_timeout: 0.001`: the server's own timer is what is under test.
  The first turn's prompt times out too (the run ends in `error`), but the
  session it created stays live.
  ```sh
  TOID=$(control-agent-server conversation start --no-autotitle --body-json '{"agent_settings": {"agent_kind": "acp", "acp_server": "claude-code", "acp_model": "sonnet", "acp_prompt_timeout": 0.001}}' \
    --secret ANTHROPIC_API_KEY="$DEEPSEEK_API_KEY" --secret ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic --secret IS_SANDBOX=1 \
    --prompt 'Reply with the single word: ok' --wait --until finished,error --timeout 300 --print-id)
  control-agent-server api GET "/api/conversations/$TOID" --quiet --check agent_state.acp_session_id exists --check current_model_id eq sonnet --check agent.acp_prompt_timeout eq 0.001
  control-agent-server api POST "/api/conversations/$TOID/switch_acp_model" --json '{"model": "haiku"}' --expect 504 --check detail eq 'Internal Server Error' --save F09.acp-model-live-timeout/switch
  control-agent-server api GET "/api/conversations/$TOID" --quiet --check current_model_id eq sonnet --check agent.acp_model eq sonnet --check agent_state.acp_current_model_id eq sonnet
  ```
  The adapter cannot answer the model switch within 1 ms, so the route
  answers 504 with `detail` rewritten to `Internal Server Error` (the
  server's 5xx handler; `exception` is `504: `), and the conversation keeps
  `sonnet` everywhere: a timed-out switch changes nothing locally.
- **TypeScript client (`F09.ts-client`).** `ConversationClient` and
  `ConversationManager` from the built client in `clients/typescript/dist`
  (built here when missing, which needs `npm` and the network), on a fresh
  placeholder conversation `TID` and a fresh ACP conversation `AID4` that
  never runs.
  ```sh
  test -f clients/typescript/dist/clients.js || (cd clients/typescript && npm ci && npm run build)
  TID=$(control-agent-server conversation start --placeholder-agent --tools none --no-autotitle --print-id)
  AID4=$(control-agent-server conversation start --no-autotitle --body-json '{"agent_settings": {"agent_kind": "acp", "acp_server": "claude-code", "acp_model": "sonnet"}}' --print-id)
  F="$AGENT_SERVER_VERIFY_RUN/fixtures"
  cat > "$F/qa-f09-ts.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const dist = (name) => import(pathToFileURL(resolve('clients/typescript/dist', name)).href);
  const { ConversationClient } = await dist('clients.js');
  const { ConversationManager } = await dist('index.js');
  const [tid, acp] = process.argv.slice(2);
  const host = process.env.AGENT_SERVER_URL;
  const apiKey = process.env.SESSION_API_KEY;
  const client = new ConversationClient({ host, apiKey });
  const llm = { model: 'openai/qa-f09-ts', api_key: 'sk-qa-f09-ts', base_url: 'http://127.0.0.1:9/v1', usage_id: 'qa-f09-ts' };
  await client.switchLLM(tid, llm);
  let info = await client.getConversation(tid);
  assert.equal(info.agent.llm.model, 'openai/qa-f09-ts');
  assert.equal(info.agent.llm.usage_id, 'qa-f09-ts');
  await new ConversationManager({ host, apiKey }).switchProfile(tid, 'deepseek-pro');
  info = await client.getConversation(tid);
  assert.equal(info.agent.llm.model, 'deepseek/deepseek-v4-pro');
  assert.equal(info.agent.llm.usage_id, 'profile:deepseek-pro');
  await client.switchProfile(tid, 'deepseek-flash');
  assert.equal((await client.getConversation(tid)).agent.llm.usage_id, 'profile:deepseek-flash');
  await assert.rejects(client.switchProfile(tid, 'qa-f09-missing'), (e) => e.status === 404);
  await assert.rejects(client.switchAcpModel(tid, 'opus'), (e) => e.status === 400);
  await assert.rejects(new ConversationClient({ host }).switchLLM(tid, llm), (e) => e.status === 401);
  assert.equal((await client.getConversation(tid)).agent.llm.usage_id, 'profile:deepseek-flash');
  await client.switchAcpModel(acp, 'opus');
  info = await client.getConversation(acp);
  assert.equal(info.agent.acp_model, 'opus');
  assert.equal(info.current_model_id, 'opus');
  console.log('TS_SWITCH_OK');
  JS
  control-agent-server exec --expect-output TS_SWITCH_OK --save F09.ts-client/program -- node "$F/qa-f09-ts.mjs" "$TID" "$AID4"
  control-agent-server api GET "/api/conversations/$TID" --quiet --check agent.llm.model eq deepseek/deepseek-flash --check agent.llm.usage_id eq profile:deepseek-flash
  control-agent-server state cat "server/workspace/conversations/${AID4//-/}/base_state.json" --max-chars 200 --check agent.acp_model eq opus
  ```
  Every assertion holds: `switchLLM`, `ConversationManager.switchProfile`
  and `switchProfile` each resolve and `getConversation` shows the model and
  `usage_id` they installed; the unknown profile is an `HttpError` 404, the
  ACP switch on a regular conversation 400 and a keyless client 401, none of
  which changed `TID`; `switchAcpModel` on the ACP conversation that has not
  run resolves (no 409) and `acp_model` and `current_model_id` become
  `opus`, also in `base_state.json`.
- **TypeScript docstrings for a pre-run ACP switch (`F09.ts-client-acp-docs`), known bug.**
  Both `switchAcpModel` methods exist; their docstrings should not promise a
  409 the server never sends.
  ```sh
  grep -n 'async switchAcpModel(' clients/typescript/src/client/conversation-client.ts
  grep -n 'async switchAcpModel(' clients/typescript/src/conversation/remote-conversation.ts
  if grep -n 'returns 409 before the first message' clients/typescript/src/client/conversation-client.ts clients/typescript/src/conversation/remote-conversation.ts; then false; fi  # bug
  ```
  Expected: the docstrings of `ConversationClient.switchAcpModel` and
  `RemoteConversation.switchAcpModel` say what `F09.acp-model-deferred` and
  `F09.ts-client` observe: before the first run the server answers 200 and
  stores the model for the first session. Today both say "the server
  returns 409 before the first message", so a TypeScript consumer that
  follows them handles a 409 that never comes (and may tell its user to set
  the model through settings instead).
- **Load a plugin (`F09.load-plugin`).** Start `PID` from the saved settings
  with the `qa-f09-mkt` marketplace registered on its `agent_context` (not
  auto-loaded), then load the plugin while capturing the socket.
  ```sh
  AS=$(control-agent-server api GET /api/settings --header 'X-Expose-Secrets: encrypted' --field agent_settings \
    | jq -c --arg m "$MKT" '.tools = [] | .agent_context.registered_marketplaces = [{"name": "qa-f09-mkt", "source": $m}]')
  PID=$(control-agent-server conversation start --no-autotitle --body-json "{\"agent_settings\": $AS, \"secrets_encrypted\": true}" --print-id)
  control-agent-server api GET "/api/conversations/$PID" --query include_skills=true --quiet \
    --check agent.agent_context.registered_marketplaces.0.name eq qa-f09-mkt --check agent.agent_context.skills len-eq 0 --check hook_config missing
  control-agent-server ws start "/sockets/events/$PID" --name qa-f09-plugin --duration 120
  control-agent-server api POST "/api/conversations/$PID/load_plugin" --json '{"plugin_ref": "qa-f09-mkt-plugin@qa-f09-mkt"}' \
    --expect 200 --check success eq true --save F09.load-plugin/load
  control-agent-server api GET "/api/conversations/$PID" --query include_skills=true --quiet \
    --check agent.agent_context.skills len-eq 2 --check agent.agent_context.skills contains qa-f09-mkt-plugin-skill \
    --check agent.agent_context.skills contains 'qa-f09-mkt-plugin:qa-hello' \
    --check hook_config.user_prompt_submit len-eq 1 --check hook_config.user_prompt_submit.0.hooks.0.command eq 'echo QA_PLUGIN_HOOK' \
    --save F09.load-plugin/get
  control-agent-server state cat "server/workspace/conversations/${PID//-/}/base_state.json" --max-chars 200 \
    --contains qa-f09-mkt-plugin-skill --check hook_config.user_prompt_submit len-eq 1
  control-agent-server ws read qa-f09-plugin --kinds ConversationStateUpdateEvent --contains qa-f09-mkt-plugin-skill --expect-min 1 --wait 20 --save F09.load-plugin/agent-frame
  control-agent-server ws stop qa-f09-plugin --kinds ConversationStateUpdateEvent --contains QA_PLUGIN_HOOK --expect-min 1 --wait 20
  ```
  Before the load the agent has no skills and no hooks. After it, `GET`
  (with `include_skills=true`) lists the plugin's skill and its command (as
  the skill `qa-f09-mkt-plugin:qa-hello`), `hook_config` has the plugin's
  `UserPromptSubmit` hook, `base_state.json` has both, and the socket pushed
  state updates for `agent` and `hook_config`.
- **The loaded hook runs (`F09.load-plugin-hook-runs`).** Run one turn on
  `PID`.
  ```sh
  control-agent-server conversation send "$PID" --text 'Reply with the single word: ok' --wait --until finished,error --timeout 240
  control-agent-server api GET "/api/conversations/$PID" --quiet --check execution_status eq finished
  control-agent-server conversation events "$PID" --kinds HookExecutionEvent --contains '"stdout": "QA_PLUGIN_HOOK' --expect-count 1 --save F09.load-plugin-hook-runs/hook-events
  ```
  One `HookExecutionEvent` (`hook_event_type` `UserPromptSubmit`, `stdout`
  `QA_PLUGIN_HOOK`) precedes the turn, which finishes.
- **Plugin load errors (`F09.load-plugin-errors`).** Conversations without
  marketplaces or without an agent context, then bad refs on `QID`, a fresh
  conversation with the marketplace registered; a bare name is the positive
  control.
  ```sh
  control-agent-server api POST "/api/conversations/$CID/load_plugin" --json '{"plugin_ref": "qa-f09-mkt-plugin"}' \
    --expect 400 --check detail contains 'No marketplaces registered' --save F09.load-plugin-errors/no-marketplaces
  control-agent-server api POST "/api/conversations/$AID/load_plugin" --json '{"plugin_ref": "qa-f09-mkt-plugin"}' \
    --expect 400 --check detail contains 'No agent context available' --save F09.load-plugin-errors/no-agent-context
  GONE=$(printf '%s' "$AS" | jq -c --arg m "$MKT-gone" '.agent_context.registered_marketplaces = [{"name": "qa-f09-gone", "source": $m}]')
  NID=$(control-agent-server conversation start --no-autotitle --body-json "{\"agent_settings\": $GONE, \"secrets_encrypted\": true}" --print-id)
  control-agent-server api POST "/api/conversations/$NID/load_plugin" --json '{"plugin_ref": "qa-f09-mkt-plugin@qa-f09-gone"}' \
    --expect 400 --check detail contains 'Local plugin path does not exist' --save F09.load-plugin-errors/unfetchable-marketplace
  control-agent-server api GET "/api/conversations/$NID" --query include_skills=true --quiet --check agent.agent_context.skills len-eq 0 --check hook_config missing
  QID=$(control-agent-server conversation start --no-autotitle --body-json "{\"agent_settings\": $AS, \"secrets_encrypted\": true}" --print-id)
  control-agent-server api POST "/api/conversations/$QID/load_plugin" --json '{"plugin_ref": "qa-f09-mkt-plugin@qa-f09-other"}' \
    --expect 404 --check detail eq "Marketplace 'qa-f09-other' is not registered" --save F09.load-plugin-errors/unknown-marketplace
  control-agent-server api POST "/api/conversations/$QID/load_plugin" --json '{"plugin_ref": "qa-f09-nope@qa-f09-mkt"}' \
    --expect 404 --check detail eq "Plugin 'qa-f09-nope' not found in marketplace 'qa-f09-mkt'"
  control-agent-server api POST "/api/conversations/$QID/load_plugin" --json '{"plugin_ref": "qa-f09-nope"}' \
    --expect 404 --check detail eq "Plugin 'qa-f09-nope' not found in any registered marketplace"
  control-agent-server api POST "/api/conversations/$QID/load_plugin" --json '{"plugin_ref": ""}' --expect 400 --check detail eq 'Plugin reference must not be empty'
  control-agent-server api POST "/api/conversations/$QID/load_plugin" --json '{"plugin_ref": "qa-f09-mkt-plugin@"}' --expect 400 --check detail contains 'plugin-name@marketplace-name'
  control-agent-server api POST "/api/conversations/$QID/load_plugin" --json '{}' --expect 422 --check detail.0.loc.1 eq plugin_ref
  control-agent-server api POST /api/conversations/00000000-0000-4000-8000-000000000000/load_plugin --json '{"plugin_ref": "qa-f09-mkt-plugin"}' --expect 404
  control-agent-server api GET "/api/conversations/$QID" --query include_skills=true --quiet --check agent.agent_context.skills len-eq 0 --check hook_config missing
  control-agent-server api POST "/api/conversations/$QID/load_plugin" --json '{"plugin_ref": "qa-f09-mkt-plugin"}' --expect 200 --save F09.load-plugin-errors/bare-name
  control-agent-server api GET "/api/conversations/$QID" --query include_skills=true --quiet \
    --check agent.agent_context.skills contains qa-f09-mkt-plugin-skill --check hook_config.user_prompt_submit len-eq 1
  ```
  400 without marketplaces (`CID`) and without an agent context (the ACP
  `AID`), 400 `Local plugin path does not exist: ...` when the only
  registered marketplace points at a missing directory (`NID`, which stays
  without skills or hooks), 404 for an unregistered marketplace and an unknown plugin (with or
  without a marketplace), 400 for an empty or half ref, 422 without the
  field, 404 for the unknown conversation; none of them changed `QID`, and
  the bare name `qa-f09-mkt-plugin` then resolves through the only
  registered marketplace and loads.
- **Load the same plugin twice (`F09.load-plugin-twice`), known bug.** Load
  the plugin into `QID` again, now by its full ref.
  ```sh
  control-agent-server api POST "/api/conversations/$QID/load_plugin" --json '{"plugin_ref": "qa-f09-mkt-plugin@qa-f09-mkt"}' --expect 200 --save F09.load-plugin-twice/second-load
  control-agent-server api GET "/api/conversations/$QID" --query include_skills=true --quiet \
    --check agent.agent_context.skills len-eq 2 --check agent.agent_context.skills contains qa-f09-mkt-plugin-skill --check hook_config.user_prompt_submit len-ge 1
  control-agent-server api GET "/api/conversations/$QID" --quiet --check hook_config.user_prompt_submit len-eq 1 --save F09.load-plugin-twice/get  # bug
  ```
  The second load is 200 and the skills are not duplicated (still two).
  Expected: one copy of the hook as well. Today `hook_config.user_prompt_submit`
  has two identical entries, because `_merge_runtime_plugin_hooks` appends
  with `HookConfig.merge` without de-duplicating, and the hook then runs
  twice per message (seen live: two `HookExecutionEvent`s for one user
  message). A client that retries `load_plugin` doubles every hook's side
  effects.
- **Load a plugin from the Python SDK (`F09.load-plugin-sdk`).** A fresh
  conversation `SID` with the marketplace registered, reattached from an SDK
  program.
  ```sh
  SID=$(control-agent-server conversation start --no-autotitle --body-json "{\"agent_settings\": $AS, \"secrets_encrypted\": true}" --print-id)
  control-agent-server api GET "/api/conversations/$SID" --query include_skills=true --quiet --check agent.agent_context.skills len-eq 0 --check hook_config missing
  mkdir -p "$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f09-sdk"
  control-agent-server exec --env SID="$SID" --env WD="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f09-sdk" --expect-output SDK_LOAD_PLUGIN_OK \
    --save F09.load-plugin-sdk/program -- .venv/bin/python - <<'PY'
  import os
  import uuid

  import httpx

  from openhands.sdk import RemoteConversation, RemoteWorkspace

  ws = RemoteWorkspace(host=os.environ["AGENT_SERVER_URL"], api_key=os.environ["SESSION_API_KEY"], working_dir=os.environ["WD"])
  conv = RemoteConversation.attach(ws, uuid.UUID(os.environ["SID"]), visualizer=None)
  try:
      conv.load_plugin("qa-f09-nope@qa-f09-mkt")
      raise AssertionError("load_plugin accepted an unknown plugin")
  except httpx.HTTPStatusError as e:
      assert e.response.status_code == 404, e
  conv.load_plugin("qa-f09-mkt-plugin@qa-f09-mkt")
  print("SDK_LOAD_PLUGIN_OK")
  conv.close()
  PY
  control-agent-server api GET "/api/conversations/$SID" --query include_skills=true --quiet \
    --check agent.agent_context.skills contains qa-f09-mkt-plugin-skill --check hook_config.user_prompt_submit len-eq 1 --save F09.load-plugin-sdk/get
  ```
  The unknown plugin raises `httpx.HTTPStatusError` with status 404 (the SDK
  logs it and re-raises), the real ref returns `None`, and `GET` then shows
  the plugin's skill and its one `UserPromptSubmit` hook.
- **Restart (`F09.restart-persist`).** Restart the server and read every
  switched conversation back; then run a turn on `RID`.
  ```sh
  control-agent-server restart
  control-agent-server api GET "/api/conversations/$CID" --quiet --check agent.llm.model eq openai/qa-f09-ws --check agent.llm.usage_id eq qa-f09-ws --save F09.restart-persist/cid
  control-agent-server api GET "/api/conversations/$AID" --quiet --check agent.acp_model eq opus --check current_model_id eq opus --save F09.restart-persist/aid
  control-agent-server api GET "/api/conversations/$PID" --query include_skills=true --quiet \
    --check agent.agent_context.skills contains qa-f09-mkt-plugin-skill --check hook_config.user_prompt_submit len-eq 1 --save F09.restart-persist/pid
  control-agent-server api GET "/api/conversations/$RID" --quiet --check agent.llm.model eq deepseek/deepseek-v4-pro --check agent.llm.usage_id eq profile:deepseek-pro
  control-agent-server conversation send "$RID" --text 'Reply with the single word: ok' --wait --until finished,error --timeout 240
  control-agent-server api GET "/api/conversations/$RID" --until-ok 60 --quiet --check execution_status eq finished \
    --check 'stats.usage_to_metrics.profile:deepseek-pro.accumulated_token_usage.prompt_tokens' gt "$PRO1" --save F09.restart-persist/rid-stats
  ```
  All four conversations come back as switched, and the first turn after
  the restart runs on `deepseek-pro` again (its token count grows past
  `PRO1`).

## Gotchas

- Every switch answers `{"success": true}` and nothing else; read the result
  back with `GET /api/conversations/{id}` (which serves the autosaved
  `base_state.json`, so on an idle conversation it shows a switch
  immediately) or the `ConversationStateUpdateEvent` with key `agent`.
  While a step is running, `GET` keeps the old LLM until the step ends
  (`F09.switch-llm-mid-run-get`); the socket and the run itself switch at
  once, so wait for the run (or read the socket) before asserting on `GET`.
- The LLM registry of a conversation is first-write-wins per `usage_id`:
  `switch_llm` with a `usage_id` that is already registered (the default
  `default` once the agent initialized, or any id used before) silently
  keeps the registered LLM, and `switch_profile` reuses the copy cached under
  `profile:<name>` at its first use. Always send a fresh `usage_id` with
  `switch_llm` (`F09.switch-llm-registered-usage-id`,
  `F09.switch-profile-edited`). Switching back to an earlier `usage_id` or
  profile is cheap for the same reason.
- Usage is accounted per `usage_id` in `stats.usage_to_metrics`, but only
  for LLMs registered after the agent initialized
  (`F09.switch-llm-before-first-run`); send one message (or create with an
  initial message) before switching when costs matter.
- The agent is initialized by the first message (`conversation send
  --no-run` is enough) or run, not by the create; ACP agents only at the
  first run, which is when the subprocess starts.
- A summarizing condenser whose LLM equals the agent's is switched along
  with it (same model, `usage_id` `condenser`); a condenser with its own
  config is left alone.
- `switch_llm` decrypts `gAAAAA` tokens in `api_key` and the other secret
  fields with the server's cipher, so a config read with
  `X-Expose-Secrets: encrypted` can be posted back as-is. A
  `provider_connection_id` is resolved once, at the switch, and the resolved
  key is stored (encrypted) with the agent; deleting the connection later
  does not affect the installed LLM, also across a restart.
- Profiles are read from `OH_PERSISTENCE_DIR/profiles` (here
  `home/.openhands/profiles`); profile names follow the LLM profile rules
  (`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`), and a name that breaks them is 400
  here (not 422 as on `/api/profiles/{name}`).
- `load_plugin` only searches the marketplaces registered on the
  conversation agent's `agent_context.registered_marketplaces` (start the
  conversation with them in `agent_settings.agent_context`); the server
  config's `registered_marketplaces` and `/api/plugins` installs are not
  consulted. A registration with `auto_load: true` (as in the
  `registration` that `fixture marketplace` prints) loads the marketplace's
  plugins and standalone skills when the agent initializes (the first
  message), without any `load_plugin`; this family registers without it so
  that the load is what adds the plugin.
- Plugin commands become skills named `<plugin>:<command>`, and
  `GET /api/conversations/{id}` trims `agent.agent_context.skills` to `[]`
  unless `include_skills=true`.
- `switch_acp_model` before the first run only persists the model; the
  provider's capability (`supports_runtime_model_switch`) and
  `available_models` are unknown (`false`, `[]`) until a session started. A
  504 from a live switch has its `detail` rewritten to `Internal Server
  Error`; read `exception`. The TypeScript client's `switchAcpModel`
  docstrings still say the server returns 409 before the first message; the
  server answers 200 and defers (`F09.acp-model-deferred`, `F09.ts-client`,
  `F09.ts-client-acp-docs`).
- A live ACP session needs the provider's CLI and an account for it. For
  `claude-code`, DeepSeek's Anthropic-compatible API works as the account
  (secrets `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic` and
  `ANTHROPIC_API_KEY=$DEEPSEEK_API_KEY`), and as root Claude Code also needs
  `IS_SANDBOX=1` or the session refuses the `bypassPermissions` mode the
  server asks for. Each such turn sends Claude Code's own system prompt
  (about 36k prompt tokens). The ACP agent records its usage under
  `usage_id` `acp-managed`, with `model_name` following live switches.
- Placeholder conversations (`conversation start --placeholder-agent`) point
  at `http://127.0.0.1:9/v1`, so a run that finishes after a switch proves
  the switched model ran.
