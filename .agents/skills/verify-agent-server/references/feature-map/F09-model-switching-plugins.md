# Model, profile and ACP switching; runtime plugins

What a consumer changes on a conversation that already exists, without
recreating it: swap the agent's LLM for a caller-supplied config (inline key,
a cipher token from an `encrypted` read, or a saved provider connection),
switch it to a saved LLM profile, change the model and the reasoning
effort an ACP conversation's subprocess runs, and load a plugin from the conversation's registered
marketplaces into the live agent (its skills, commands and hooks). A
conversation whose LLM is a model router (`RouterLLM`) also exposes the
router: `GET .../model_router` reports its routes and selection, `POST
.../model_router/routes` adds a saved profile as a route (wrapping a plain
LLM in a router first), and `PATCH .../model_router` pins a route or returns
to automatic routing, all without replacing the router; `switch_llm` and
`switch_profile` still replace the whole LLM, router included. Every
switch answers `{"success": true}`, is written to the conversation's
`base_state.json`, shows up in `GET /api/conversations/{id}` and as a
`ConversationStateUpdateEvent` on the events socket, and survives a restart;
the next turn runs on the new model and its usage is recorded under the new
LLM's `usage_id`.

Source: `openhands-agent-server/openhands/agent_server/conversation_router.py`, `openhands-agent-server/openhands/agent_server/event_service.py`, `openhands-agent-server/openhands/agent_server/_secrets_exposure.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/llm/llm_registry.py`, `openhands-sdk/openhands/sdk/llm/llm_profile_store.py`, `openhands-sdk/openhands/sdk/llm/router/`, `openhands-sdk/openhands/sdk/marketplace/`, `openhands-sdk/openhands/sdk/plugin/`, `openhands-sdk/openhands/sdk/agent/acp_agent.py`

Needs: `llm`, `tmux`, `node`, `network`

Routes: `POST /api/conversations/{conversation_id}/switch_profile`,
`POST /api/conversations/{conversation_id}/switch_llm`,
`POST /api/conversations/{conversation_id}/load_plugin`,
`POST /api/conversations/{conversation_id}/switch_acp_model`,
`POST /api/conversations/{conversation_id}/switch_acp_effort`,
`GET /api/conversations/{conversation_id}/model_router`,
`POST /api/conversations/{conversation_id}/model_router/routes`,
`PATCH /api/conversations/{conversation_id}/model_router`

## Sub-features

- `F09.auth-required`: all five routes answer 401 without a valid session key and change nothing.
- `F09.switch-llm`: `switch_llm` with a fresh `usage_id` answers 200 `{"success": true}`; `GET` and `base_state.json` show the new model and `usage_id`, the condenser LLM that mirrored the old one follows, and the key is never echoed or stored in plaintext. Same promise: `S03.switch-llm-object`.
- `F09.switch-llm-ws`: a switch pushes a `ConversationStateUpdateEvent` with key `agent` and the new model on the events socket, with the key masked.
- `F09.switch-llm-errors`: an unknown conversation is 404, a body without `llm` or without `llm.model` is 422, and a dangling `provider_connection_id` is 422; the installed LLM is unchanged.
- `F09.switch-llm-encrypted-key`: mid-conversation, a switch whose `api_key` is the `gAAAAA` token from an `encrypted` profile read is decrypted before install; the next turn runs on DeepSeek and its usage is recorded under the new `usage_id`.
- `F09.switch-llm-provider-connection`: a switch that references a saved provider connection instead of a key runs the next turn with the connection's key; `GET` shows the `provider_connection_id` and no key.
- `F09.switch-llm-registered-usage-id`: after the agent initialized, a switch whose `usage_id` is already registered (the `default` a saved profile's config carries) with a different config installs the posted LLM, never a silent 200 no-op. Same promise: `S03.switch-llm-object`.
- `F09.switch-llm-before-first-run`: a switch before the conversation's first run is used by that run, and the run's token usage is recorded under the switched LLM's `usage_id`.
- `F09.switch-llm-mid-run`: a `switch_llm` while a turn is running (the agent is inside a 20 s tool call) answers 200, the socket pushes the new `agent`, and the same run's next LLM call already uses the switched LLM (usage under both `usage_id`s) and the run finishes.
- `F09.switch-llm-mid-run-get`: right after a mid-run switch answers 200, `GET` and `base_state.json` show the switched LLM, as the socket already does.
- `F09.switch-profile`: `switch_profile` to the saved `deepseek-pro` profile answers 200, installs it under `usage_id` `profile:deepseek-pro`, and the next turn runs on `deepseek/deepseek-v4-pro` with its usage recorded under that id. Same promise: `S03.switch-profile`.
- `F09.switch-profile-errors`: an unknown profile is 404 `Profile '<name>' not found`, an invalid name and a corrupted profile file are 400, a missing `profile_name` is 422 and an unknown conversation 404; the installed LLM is unchanged. Same promise: `S03.switch-profile`.
- `F09.switch-profile-edited`: switching to a profile again after it was edited installs the edited config, not the copy cached at the first switch.
- `F09.acp-model-native`: `switch_acp_model` on a regular (non-ACP) conversation is 400 `switch_acp_model is only supported for ACP conversations.`; a missing `model` is 422 and an unknown conversation 404.
- `F09.acp-model-deferred`: on an ACP conversation that has not run yet, `switch_acp_model` answers 200 without starting the subprocess and persists the model as `agent.acp_model`, `agent_state.acp_current_model_id` and `current_model_id`. Same promise: `S21.model-switch`.
- `F09.acp-model-blank`: a blank or whitespace-only ACP model is refused before the first run, as it is on a live session, and the stored model is kept. Same promise: `S21.model-switch`.
- `F09.switch-llm-acp-refused`: `switch_llm` and `switch_profile` on an ACP conversation are refused, as `switch_acp_model` is on a regular one, and leave the ACP agent's placeholder LLM (`usage_id` `acp-managed`) alone.
- `F09.acp-model-live`: on an ACP conversation with a live session, `switch_acp_model` switches the subprocess's model in place (same ACP session) and `current_model_id`, `acp_model` and `base_state.json` follow; a blank model is 400, a model the provider does not offer is refused and changes nothing, and the next turn runs on the switched model in the same session. Same promise: `S21.model-switch`.
- `F09.acp-model-live-unknown`: a live switch to a model id the provider does not offer (not in `available_models`) is a client error (400, as for a blank model), not a 500. Same promise: `S21.model-switch`.
- `F09.acp-model-live-timeout`: a live switch whose `session/set_model` round-trip outlasts `acp_prompt_timeout` is 504 with `detail` `Internal Server Error` and leaves the model unchanged.
- `F09.acp-effort-native`: `switch_acp_effort` on a regular (non-ACP) conversation is 400 `switch_acp_effort is only supported for ACP conversations.`; a missing `effort` is 422 and an unknown conversation 404.
- `F09.acp-effort-deferred`: on an ACP conversation that has not run yet, `switch_acp_effort` answers 200 without starting the subprocess and persists the level as `agent.acp_effort`, `agent_state.acp_current_effort` and `current_effort` (no level list exists before a session, so any non-blank level is stored); a blank level is 400. Same promise: `S21.effort-switch`.
- `F09.acp-model-id-bare`: on a live ACP session `current_model_id` is the bare model id: a legacy `<model>/<effort>` `acp_model` (`sonnet/high`) still starts the session, `current_model_id` is `sonnet`, and an explicit `acp_effort` wins over the suffix in `current_effort`.
- `F09.acp-effort-live`: on a live ACP session, `switch_acp_effort` to a level in `available_efforts` answers 200 and switches the session in place (same `acp_session_id`); `current_effort`, `agent.acp_effort`, `agent_state.acp_current_effort` and `base_state.json` follow, and the next turn finishes in the same session. Same promise: `S21.effort-switch`.
- `F09.acp-effort-unoffered`: on a live session, a level the current model does not offer is 400 naming the offered levels, a blank level is 400, and `current_effort` and `acp_effort` are unchanged. Same promise: `S21.effort-switch`.
- `F09.acp-effort-model-switch`: after `switch_acp_model` to another model (`haiku`) and back (`sonnet`), the pinned `acp_effort` is kept and applied on each model that offers it (`current_effort` follows, `available_efforts` lists that model's levels), in the same session. Same promise: `S21.effort`.
- `F09.acp-effort-live-timeout`: a live `switch_acp_effort` whose round-trip outlasts `acp_prompt_timeout` is 504 with `detail` `Internal Server Error` and leaves the effort unchanged.
- `F09.acp-effort-codex`: on a Codex (`codex`) ACP conversation signed in with a ChatGPT subscription, `available_efforts` lists the levels the current model offers and `switch_acp_effort` switches among them live (blocked: needs a ChatGPT login).
- `F09.ts-client`: the TypeScript client's `ConversationClient.switchLLM`, `switchProfile` and `switchAcpModel` and `ConversationManager.switchProfile` reach the routes: each switch resolves and reads back through `getConversation`, an unknown profile rejects with 404, `switchAcpModel` on a regular conversation with 400, a call without a key with 401, and `switchAcpModel` on an ACP conversation that has not run resolves and stores the model.
- `F09.ts-client-acp-docs`: the TypeScript client's `switchAcpModel` docstrings describe what the server does before an ACP conversation's first run (200, the model is stored for the first session), not a 409.
- `F09.ts-client-acp-effort`: the TypeScript client's `ConversationClient.switchAcpEffort` stores the level on an ACP conversation that has not run (`getConversation` shows `acp_effort` and `current_effort`), and rejects with 400 on a regular conversation and with 401 without a key.
- `F09.load-plugin`: `load_plugin` with `<plugin>@<marketplace>` from a local marketplace registered on the agent's `agent_context` answers 200 and merges the plugin's skill, its command and its hooks into the live agent, visible in `GET` and `base_state.json`; the socket pushes the `agent` and `hook_config` updates, the `agent` one without skill bodies (`skills: []`). Same promise: `S08.load-plugin-runtime`.
- `F09.load-plugin-hook-runs`: after a runtime load, the next user turn runs the plugin's `UserPromptSubmit` hook (a `HookExecutionEvent` with its output) and finishes. Same promise: `S10.plugin-hooks-merge`.
- `F09.load-plugin-errors`: no registered marketplaces and no agent context are 400, a registered marketplace whose source is missing is 400, an unknown marketplace or plugin is 404, empty and malformed refs are 400, a missing `plugin_ref` is 422 and an unknown conversation 404; a bare plugin name resolves across the registered marketplaces. Same promise: `S08.marketplace-registry`, `S08.load-plugin-runtime`.
- `F09.load-plugin-twice`: loading the same plugin twice leaves one copy of its hooks, so they still run once per message. Same promise: `S08.load-plugin-runtime`, `S10.plugin-hooks-reload-once`.
- `F09.load-plugin-sdk`: the Python SDK's `RemoteConversation.load_plugin(ref)`, on a conversation reattached with `RemoteConversation.attach`, raises `HTTPStatusError` 404 for an unknown plugin and then loads the plugin, whose skill and hook `GET` shows. Same promise: `S08.load-plugin-runtime`.
- `F09.restart-persist`: after a restart the switched LLM, the switched profile (which the next turn runs on), the deferred ACP model and the loaded plugin's skills and hooks are still in place.
- `F09.model-router-status`: `GET .../model_router` on a plain-LLM conversation is 200 `{"enabled": false, "selection": null, "last_route_name": null, "routes": []}`; on a conversation created with a `MultimodalRouter` LLM it reports `enabled: true`, `selection.mode` `auto`, no `last_route_name` and each route's `name`, `model` and `label`; an unknown conversation is 404 and no key 401. Same promise: `S03.model-router-selection`.
- `F09.model-router-add-route`: `POST .../model_router/routes` with a saved profile wraps a plain LLM in a `base_router` (the old LLM becomes route `default`, or `base_route_name`), adds the profile as a route named `route_name` (default: the profile name) with `usage_id` `profile:<name>`, extends an existing router keeping its type, stays in `auto` mode and answers the new status; `GET`, `base_state.json` (keys encrypted) and the socket's `agent` update show the routes, and no plaintext key is stored or pushed. Same promise: `S03.model-router-add-route`.
- `F09.model-router-add-route-errors`: an unknown profile is 404 `Profile '<name>' not found`, a route name already in use 400 `Model router route already exists: <name>`, an invalid profile name 400, a missing `profile_name` 422, an unknown conversation 404 and no key 401; the routes are unchanged.
- `F09.model-router-select`: `PATCH .../model_router` with `{"selection": {"mode": "manual", "manual_route_name": ...}}` pins that route and `{"mode": "auto"}` unpins it, keeping the router and its routes; the answer, `GET .../model_router`, `GET` (`agent.llm.selection`), `base_state.json` and the socket's `agent` update all show the selection. Same promise: `S03.model-router-selection`.
- `F09.model-router-select-errors`: an unknown route is 400 naming the available routes, `manual` without a route, `auto` with one, an unknown `mode` and a missing `selection` are 422, a conversation whose LLM is not a router is 400 `Current agent LLM is not a RouterLLM.`, an unknown conversation 404 and no key 401; the selection is unchanged.
- `F09.model-router-switch-replaces`: `switch_profile` and `switch_llm` on a router conversation install the plain LLM in place of the router (routes and pin gone, `model_router` reports `enabled: false`, `PATCH` is then 400), and a later route add wraps the switched LLM in a new router. Same promise: `S03.model-router-switch-replaces`.
- `F09.model-router-acp-refused`: on an ACP conversation `GET .../model_router` reports `enabled: false`, and adding a route or changing the selection is 400 pointing at `switch_acp_model`, leaving the `acp-managed` placeholder LLM alone.
- `F09.model-router-sdk`: the Python SDK's `RemoteConversation.get_model_router()`, `add_model_router_route(profile, route_name=, base_route_name=)`, `update_model_router_selection(selection)`, `switch_routed_llm(route)` and `resume_auto_llm_routing()` reach the routes and return `ModelRouterStatus`; a refused route or profile raises `httpx.HTTPStatusError` 400 or 404.
- `F09.ts-client-model-router`: the TypeScript client's `getModelRouter`, `addModelRouterRoute`, `updateModelRouter`, `switchModelRouter` and `resumeAutoModelRouting` on `ConversationClient` and `ConversationManager` reach the routes and resolve with the status; an unknown route rejects with 400, an unknown profile 404, a duplicate route 400 and a keyless client 401.
- `F09.model-router-run`: after a first turn, a route added from the `deepseek-flash` profile and pinned serves the next turn (its tokens under `profile:deepseek-flash`, `last_route_name` the route), and after a return to `auto` the next turn runs on the first route (`default`) again.
- `F09.model-router-before-first-run`: a route added and pinned before the conversation's first message serves the first run, and that run's tokens are recorded under the route's `usage_id` `profile:deepseek-flash`. Same promise: `S03.model-router-before-first-run`.
- `F09.model-router-restart`: after a restart the routes and the pinned selection are still in place and the selection can be changed again; conversations whose router a switch replaced stay plain.

## How to get to it (agent POV)

- REST, owned here (bodies are single embedded fields):
  `POST /api/conversations/{id}/switch_llm` `{"llm": {...LLM fields...}}`,
  `POST /api/conversations/{id}/switch_profile` `{"profile_name": "..."}`,
  `POST /api/conversations/{id}/switch_acp_model` `{"model": "..."}`,
  `POST /api/conversations/{id}/switch_acp_effort` `{"effort": "..."}`,
  `POST /api/conversations/{id}/load_plugin` `{"plugin_ref": "<plugin>[@<marketplace>]"}`.
  Each answers `{"success": true}`; every recipe below drives them.
  Model router, owned here: `GET /api/conversations/{id}/model_router`,
  `POST /api/conversations/{id}/model_router/routes`
  `{"profile_name": "...", "route_name": "..."?, "base_route_name": "default"?}`,
  `PATCH /api/conversations/{id}/model_router`
  `{"selection": {"mode": "auto"|"manual", "manual_route_name": "..."?}}`.
  Each answers a `ModelRouterStatus`
  (`{"enabled", "selection", "last_route_name", "routes": [{"name", "model", "label"}]}`);
  `/server_info` lists the feature flag `conversation_model_router_routes_v1`.
- Second views: `GET /api/conversations/{id}` (`agent.llm`, `agent.condenser.llm`,
  `stats.usage_to_metrics.<usage_id>`, `current_model_id`, `agent.acp_model`,
  `current_effort`, `available_efforts`, `agent.acp_effort`,
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
  `switch_acp_model(model)`, `switch_acp_effort(effort)` and `load_plugin(ref)`
  are what the routes call (the SDK side of the effort switch is
  `S21.effort-switch` in the SDK map);
  over HTTP `RemoteConversation.load_plugin(ref)` posts to `load_plugin`
  (`F09.load-plugin-sdk` drives it).
  `RemoteConversation` has no `switch_llm`, `switch_profile`,
  `switch_acp_model` or `switch_acp_effort` method; Python consumers post to the routes directly.
  For the model router, `LocalConversation.add_model_router_route`,
  `update_model_router_selection`, `switch_routed_llm` and
  `resume_auto_llm_routing` are what the routes call (SDK side:
  `S03.model-router-*` in the SDK map), and `RemoteConversation` has
  `get_model_router()`, `add_model_router_route(...)`,
  `update_model_router_selection(selection)`, `switch_routed_llm(route)` and
  `resume_auto_llm_routing()`, each returning a `ModelRouterStatus`
  (`F09.model-router-sdk` drives them).
- TypeScript client: `ConversationClient.switchProfile(id, name)`,
  `switchLLM(id, llm)`, `switchAcpModel(id, model)`,
  `switchAcpEffort(id, effort)`;
  `RemoteConversation.switchProfile`, `switchLlm`, `switchAcpModel`,
  `switchAcpEffort`;
  `ConversationManager.switchProfile`;
  model router: `getModelRouter`, `addModelRouterRoute(id, profileName,
  routeName?, baseRouteName = 'default')`, `updateModelRouter(id, selection)`,
  `switchModelRouter(id, routeName)` and `resumeAutoModelRouting(id)` on
  `ConversationClient` and `ConversationManager`, and the same methods
  without the id on `RemoteConversation` (types `ModelRouterSelection`,
  `ModelRouteInfo`, `ModelRouterStatus`; `F09.ts-client-model-router` drives
  the `ConversationClient` and `ConversationManager` ones);
  `ConversationClient.loadPlugin(id, ref)` and
  `PluginsClient.loadPluginForConversation(id, ref)` post
  `{"plugin_ref": ref}` to `load_plugin` (driven by `F34.load-plugin`).
  `F09.ts-client` drives the `ConversationClient` methods and
  `ConversationManager.switchProfile` (the `RemoteConversation` methods post
  the same bodies to the same routes).
- Agent Canvas (context only): the per-conversation model picker calls
  `switch_profile` (or `switch_llm` with a profile config that may reference a
  provider connection), and `switch_acp_model` / `switch_acp_effort` for ACP
  conversations, picking from `available_models` and `available_efforts`.

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
  ENC=$(control-agent-server api GET /api/profiles/deepseek-flash --header 'X-Expose-Secrets: encrypted' --field config.api_key --unsafe-unredacted)
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
  control-agent-server api POST "/api/conversations/$CID/switch_acp_effort" --auth none --json '{"effort": "high"}' --expect 401
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

  Requires: `F09.switch-llm-encrypted-key`
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

  Requires: `F09.switch-llm-encrypted-key`
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
  control-agent-server api GET "/api/conversations/$EID" --quiet --check agent.llm.model eq openai/qa-f09-edit-2 --save F09.switch-profile-edited/get  # bug: status eq 200 && checks.0.actual eq openai/qa-f09-edit-1
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
- **Switch onto a registered usage id (`F09.switch-llm-registered-usage-id`).**
  Forward the saved `deepseek-pro` profile's config, read with
  `X-Expose-Secrets: encrypted` (the per-conversation model switch the
  route's docstring describes), to two offline conversations: one fresh, one
  whose agent was initialized by a queued message.
  ```sh
  PRO=$(control-agent-server api GET /api/profiles/deepseek-pro --header 'X-Expose-Secrets: encrypted' --check config.usage_id eq default --field config --unsafe-unredacted)
  FID=$(control-agent-server conversation start --placeholder-agent --tools none --no-autotitle --print-id)
  control-agent-server api POST "/api/conversations/$FID/switch_llm" --json "{\"llm\": $PRO}" --expect 200
  control-agent-server api GET "/api/conversations/$FID" --quiet --check agent.llm.model eq deepseek/deepseek-v4-pro
  KID=$(control-agent-server conversation start --placeholder-agent --tools none --no-autotitle --prompt 'Reply with the single word: ok' --no-run --print-id)
  control-agent-server api GET "/api/conversations/$KID" --quiet --check agent.llm.usage_id eq default --check execution_status eq idle
  control-agent-server api POST "/api/conversations/$KID/switch_llm" --json "{\"llm\": $PRO}" --expect 200 --save F09.switch-llm-registered-usage-id/switch
  control-agent-server api GET "/api/conversations/$KID" --quiet --check agent.llm.model eq deepseek/deepseek-v4-pro --check agent.llm.usage_id eq default --save F09.switch-llm-registered-usage-id/get
  ```
  The profile config carries `usage_id: default`, and the fresh conversation
  switches to `deepseek/deepseek-v4-pro`. The initialized one switches too,
  although `default` is already in its LLM registry (agent initialization
  registered it): `switch_llm` reuses a registered LLM only when its config
  equals the posted one, and otherwise replaces the entry, keeping the usage
  already recorded under that `usage_id` in `stats.usage_to_metrics`. This is
  what makes forwarding a profile config, which carries `default` (also the
  `LLM` default when `usage_id` is omitted), work on a conversation that has
  received a message.
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
    --check stats.usage_to_metrics.qa-f09-early.accumulated_token_usage.prompt_tokens gt 0 --save F09.switch-llm-before-first-run/stats  # bug: status eq 200 && checks.0.actual eq null
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
  control-agent-server api GET "/api/conversations/$GID" --quiet --check agent.llm.usage_id eq qa-f09-midget --save F09.switch-llm-mid-run-get/get  # bug: status eq 200 && checks.0.actual eq default
  control-agent-server state cat "server/workspace/conversations/${GID//-/}/base_state.json" --max-chars 200 --check agent.llm.usage_id eq qa-f09-midget  # bug: checks.0.actual eq default
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
- **Blank ACP model (`F09.acp-model-blank`).** A second ACP
  conversation that has not run.
  ```sh
  AID2=$(control-agent-server conversation start --no-autotitle --body-json '{"agent_settings": {"agent_kind": "acp", "acp_server": "claude-code", "acp_model": "sonnet"}}' --print-id)
  control-agent-server api GET "/api/conversations/$AID2" --quiet --check agent.kind eq ACPAgent --check agent.acp_model eq sonnet --check execution_status eq idle
  control-agent-server api POST "/api/conversations/$AID2/switch_acp_model" --json '{"model": "   "}' --expect 400,422 --save F09.acp-model-blank/blank
  control-agent-server api GET "/api/conversations/$AID2" --quiet --check agent.acp_model eq sonnet --check current_model_id eq sonnet
  ```
  The switch is refused with 400 (`model must be a non-empty string`), as
  on a live session, and `agent.acp_model` and `current_model_id` stay
  `sonnet`.
- **LLM switches on an ACP conversation (`F09.switch-llm-acp-refused`).**
  A third ACP conversation that has not run; its agent carries a
  placeholder LLM that only names the model.
  ```sh
  AID3=$(control-agent-server conversation start --no-autotitle --body-json '{"agent_settings": {"agent_kind": "acp", "acp_server": "claude-code", "acp_model": "sonnet"}}' --print-id)
  control-agent-server api GET "/api/conversations/$AID3" --quiet --check agent.llm.usage_id eq acp-managed --check agent.llm.model eq sonnet
  control-agent-server api POST "/api/conversations/$AID3/switch_profile" --json '{"profile_name": "deepseek-pro"}' --expect 400 --check detail contains switch_acp_model --save F09.switch-llm-acp-refused/switch-profile
  control-agent-server api POST "/api/conversations/$AID3/switch_llm" \
    --json '{"llm": {"model": "openai/qa-f09-acp", "api_key": "sk-qa-f09-acp", "base_url": "http://127.0.0.1:9/v1", "usage_id": "qa-f09-acp"}}' \
    --expect 400 --check detail contains switch_acp_model --save F09.switch-llm-acp-refused/switch-llm
  control-agent-server api GET "/api/conversations/$AID3" --quiet --check agent.llm.usage_id eq acp-managed --check agent.acp_model eq sonnet
  ```
  The ACP agent starts with `usage_id` `acp-managed` and the model name
  `sonnet`. Both switches are refused with 400 and a `detail` that points
  at `switch_acp_model`, mirroring `switch_acp_model` on a regular
  conversation: the ACP subprocess makes its own model calls, so swapping
  OpenHands' LLM would not change what runs. The placeholder stays in place
  (`usage_id` `acp-managed`, which `title_utils` uses to avoid calling it),
  and `acp_model` stays `sonnet`.
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
  `available_models` (`default`, `opus`, `fable`, `sonnet`, `haiku`), on
  `sonnet`. The switch to `haiku` is 200 and `current_model_id`,
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
- **Unknown model on a live session (`F09.acp-model-live-unknown`).**
  `LID` from the bullet above, whose adapter lists its models.

  Requires: `F09.acp-model-live`
  ```sh
  control-agent-server api GET "/api/conversations/$LID" --quiet --check supports_runtime_model_switch eq true \
    --check available_models contains '"model_id": "sonnet"' --check available_models not-contains qa-f09-nope
  control-agent-server api POST "/api/conversations/$LID/switch_acp_model" --json '{"model": "qa-f09-nope"}' --expect 400 \
    --check detail contains 'Invalid value for config option model: qa-f09-nope' --save F09.acp-model-live-unknown/switch
  control-agent-server api GET "/api/conversations/$LID" --quiet --check current_model_id eq haiku --check agent.acp_model eq haiku
  ```
  A client error, as `ACPAgent.set_acp_model` documents ("the ACP server
  rejects the model-switch call (e.g. ... an invalid model id)" is a
  `ValueError`, which the route turns into 400) and as a blank model is:
  claude-agent-acp answers `set_config_option(model)` with JSON-RPC -32602
  `Invalid params`, and the `detail` carries the adapter's message (`ACP
  server rejected set_config_option(model)(model='qa-f09-nope'): Invalid
  params: Invalid value for config option model: qa-f09-nope`). The model
  stays `haiku`. `set_acp_model` still re-raises an adapter's -32603
  (`Internal error`) as a server-side failure (500), so an adapter that
  answered an unknown id with -32603 would surface as a 500 here.
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
- **ACP effort on a regular conversation (`F09.acp-effort-native`).** `CID`
  runs an OpenHands `Agent`.
  ```sh
  control-agent-server api POST "/api/conversations/$CID/switch_acp_effort" --json '{"effort": "high"}' \
    --expect 400 --check detail eq 'switch_acp_effort is only supported for ACP conversations.' --save F09.acp-effort-native/native
  control-agent-server api POST "/api/conversations/$CID/switch_acp_effort" --json '{}' --expect 422 --check detail.0.loc.1 eq effort
  control-agent-server api POST /api/conversations/00000000-0000-4000-8000-000000000000/switch_acp_effort --json '{"effort": "high"}' --expect 404
  control-agent-server api GET "/api/conversations/$CID" --quiet --check agent.kind eq Agent --check current_effort eq null --check available_efforts len-eq 0
  ```
  400 with the exact message, 422 without `effort`, 404 for the unknown id;
  the conversation reports no effort.
- **ACP effort before the first run (`F09.acp-effort-deferred`).** A Claude
  Code ACP conversation with `acp_model: sonnet`, no `acp_effort` and no
  prompt, so no subprocess starts.
  ```sh
  EAID=$(control-agent-server conversation start --no-autotitle --body-json '{"agent_settings": {"agent_kind": "acp", "acp_server": "claude-code", "acp_model": "sonnet"}}' --print-id)
  control-agent-server api GET "/api/conversations/$EAID" --quiet --check agent.kind eq ACPAgent --check agent.acp_effort eq null \
    --check current_effort eq null --check available_efforts len-eq 0 --check execution_status eq idle
  control-agent-server api POST "/api/conversations/$EAID/switch_acp_effort" --json '{"effort": "high"}' --expect 200 --check success eq true --save F09.acp-effort-deferred/switch
  control-agent-server api GET "/api/conversations/$EAID" --quiet --check agent.acp_effort eq high --check current_effort eq high \
    --check agent_state.acp_current_effort eq high --check agent_state.acp_session_id missing --check current_model_id eq sonnet \
    --check execution_status eq idle --save F09.acp-effort-deferred/get
  control-agent-server state cat "server/workspace/conversations/${EAID//-/}/base_state.json" --max-chars 200 \
    --check agent.acp_effort eq high --check agent_state.acp_current_effort eq high
  control-agent-server api POST "/api/conversations/$EAID/switch_acp_effort" --json '{"effort": "  "}' --expect 400 --check detail eq 'effort must be a non-empty string'
  control-agent-server api GET "/api/conversations/$EAID" --quiet --check agent.acp_effort eq high --check current_effort eq high
  ```
  The switch is 200 although no ACP session exists; `acp_effort`,
  `agent_state.acp_current_effort` and `current_effort` say `high`, so the
  first session starts on it. Before a session there is no level list to
  check against, so only a blank level is refused (400), and it changes
  nothing.
- **Bare model id with a legacy effort suffix (`F09.acp-model-id-bare`).**
  The translated Claude Code provider of `F09.acp-model-live`, on a
  conversation created with the legacy `acp_model: sonnet/high` and an
  explicit `acp_effort: low`; one turn starts the session.
  ```sh
  EID=$(control-agent-server conversation start --no-autotitle --body-json '{"agent_settings": {"agent_kind": "acp", "acp_server": "claude-code", "acp_model": "sonnet/high", "acp_effort": "low"}}' \
    --secret ANTHROPIC_API_KEY="$DEEPSEEK_API_KEY" --secret ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic --secret IS_SANDBOX=1 \
    --prompt 'Reply with the single word: ok' --wait --until finished --timeout 300 --print-id)
  ESESS=$(control-agent-server api GET "/api/conversations/$EID" --check execution_status eq finished --check current_model_id eq sonnet \
    --check agent.acp_model eq sonnet/high --check agent.acp_effort eq low --check current_effort eq low \
    --check available_efforts contains '"value": "high"' --check available_models contains '"model_id": "sonnet"' \
    --save F09.acp-model-id-bare/get --field agent_state.acp_session_id)
  test -n "$ESESS"
  ```
  The session starts on `sonnet` and `current_model_id` is the bare
  `sonnet`, an id that appears in `available_models`; the `/high` suffix is
  still accepted as input, but `acp_effort: low` wins, so `current_effort`
  is `low`. `available_efforts` lists what the adapter offers for `sonnet`
  (`default`, `low`, `medium`, `high`, `xhigh`, `max`).
- **Live effort switch (`F09.acp-effort-live`).** Switch `EID`'s live
  session to `high`, then run a turn.

  Requires: `F09.acp-model-id-bare`
  ```sh
  control-agent-server api POST "/api/conversations/$EID/switch_acp_effort" --json '{"effort": "high"}' --expect 200 --check success eq true --save F09.acp-effort-live/switch
  control-agent-server api GET "/api/conversations/$EID" --quiet --check current_effort eq high --check agent.acp_effort eq high \
    --check agent_state.acp_current_effort eq high --check agent_state.acp_session_id eq "$ESESS" --check current_model_id eq sonnet --save F09.acp-effort-live/get
  control-agent-server state cat "server/workspace/conversations/${EID//-/}/base_state.json" --max-chars 200 --check agent.acp_effort eq high --check agent_state.acp_current_effort eq high
  control-agent-server conversation send "$EID" --text 'Reply with the single word: ok' --wait --until finished,error --timeout 300
  control-agent-server api GET "/api/conversations/$EID" --until-ok 30 --quiet --check execution_status eq finished --check agent_state.acp_session_id eq "$ESESS" \
    --check current_effort eq high --save F09.acp-effort-live/second-turn
  ```
  The switch is 200, `current_effort`, `agent.acp_effort`,
  `agent_state.acp_current_effort` and `base_state.json` say `high`, the
  `acp_session_id` is unchanged (switched in place), and the next turn
  finishes in the same session, still on `high`.
- **Unoffered effort on a live session (`F09.acp-effort-unoffered`).**

  Requires: `F09.acp-effort-live`
  ```sh
  control-agent-server api GET "/api/conversations/$EID" --quiet --check available_efforts not-contains ultra --check current_effort eq high
  control-agent-server api POST "/api/conversations/$EID/switch_acp_effort" --json '{"effort": "ultra"}' --expect 400 \
    --check detail eq "Reasoning effort 'ultra' is not offered for the current model; choose one of: default, low, medium, high, xhigh, max." --save F09.acp-effort-unoffered/ultra
  control-agent-server api POST "/api/conversations/$EID/switch_acp_effort" --json '{"effort": "  "}' --expect 400 --check detail eq 'effort must be a non-empty string'
  control-agent-server api GET "/api/conversations/$EID" --quiet --check current_effort eq high --check agent.acp_effort eq high --check agent_state.acp_current_effort eq high
  ```
  A level the adapter does not offer for `sonnet` is 400 and the detail
  lists the offered levels; a blank level is 400; the effort stays `high`.
- **Effort across a model switch (`F09.acp-effort-model-switch`).** Switch
  `EID` (effort pinned to `high`) to `haiku` and back to `sonnet`.

  Requires: `F09.acp-effort-live`
  ```sh
  control-agent-server api POST "/api/conversations/$EID/switch_acp_model" --json '{"model": "haiku"}' --expect 200
  control-agent-server api GET "/api/conversations/$EID" --quiet --check current_model_id eq haiku --check current_effort eq high \
    --check available_efforts contains '"value": "high"' --check agent.acp_effort eq high --check agent_state.acp_current_effort eq high \
    --check agent_state.acp_session_id eq "$ESESS" --save F09.acp-effort-model-switch/haiku
  control-agent-server api POST "/api/conversations/$EID/switch_acp_model" --json '{"model": "sonnet"}' --expect 200
  control-agent-server api GET "/api/conversations/$EID" --quiet --check current_model_id eq sonnet --check current_effort eq high \
    --check available_efforts contains '"value": "max"' --check agent_state.acp_session_id eq "$ESESS" --save F09.acp-effort-model-switch/back
  ```
  On `haiku` the adapter lists the same levels (`default` to `max`), and
  `current_effort` and the pinned `acp_effort` stay `high`. Back on `sonnet`
  `current_effort` is `high` again, in the same session. The server re-applies
  a pinned effort after a model switch when the new model offers it; on a
  model that offers no levels `current_effort` would read null and
  `switch_acp_effort` 400 `The current model offers no reasoning effort
  setting.`, but every model the pinned claude-agent-acp lists offers
  levels, so that path is not reachable with the built-in providers.
- **Live effort switch that times out (`F09.acp-effort-live-timeout`).**
  `TOID` from `F09.acp-model-live-timeout` (`acp_prompt_timeout: 0.001`,
  live session on `sonnet`, no pinned effort).

  Requires: `F09.acp-model-live-timeout`
  ```sh
  control-agent-server api GET "/api/conversations/$TOID" --quiet --check agent_state.acp_session_id exists --check available_efforts contains '"value": "high"' \
    --check current_effort eq default --check agent.acp_effort eq null
  control-agent-server api POST "/api/conversations/$TOID/switch_acp_effort" --json '{"effort": "high"}' --expect 504 --check detail eq 'Internal Server Error' --save F09.acp-effort-live-timeout/switch
  control-agent-server api GET "/api/conversations/$TOID" --quiet --check current_effort eq default --check agent.acp_effort eq null --check agent_state.acp_current_effort eq default
  ```
  The session runs at the adapter's own `default` effort. The adapter cannot
  answer within 1 ms, so the switch is 504 (`detail` rewritten to `Internal
  Server Error`) and nothing changes locally: `current_effort` stays
  `default` and `acp_effort` unset.
- **Codex effort levels (`F09.acp-effort-codex`), blocked: needs a ChatGPT subscription login for codex-acp (`CODEX_AUTH_JSON`).**
  With the login
  passed as the `CODEX_AUTH_JSON` conversation secret, a `codex` conversation
  runs one turn; `available_efforts` should list the levels the current
  model offers (a model such as `gpt-6.1-sol` also offers `ultra`), and
  `switch_acp_effort` to one of them should switch the live session as in
  `F09.acp-effort-live`. DeepSeek cannot stand in here: codex-acp signs in
  only with an OpenAI key or a ChatGPT login.
  ```sh
  CXID=$(control-agent-server conversation start --no-autotitle --body-json '{"agent_settings": {"agent_kind": "acp", "acp_server": "codex"}}' \
    --secret CODEX_AUTH_JSON="$QA_CODEX_AUTH_JSON" --prompt 'Reply with the single word: ok' --wait --until finished --timeout 300 --print-id)
  control-agent-server api GET "/api/conversations/$CXID" --quiet --check available_efforts contains '"value": "high"'
  control-agent-server api POST "/api/conversations/$CXID/switch_acp_effort" --json '{"effort": "high"}' --expect 200 --save F09.acp-effort-codex/switch
  control-agent-server api GET "/api/conversations/$CXID" --quiet --check current_effort eq high --check agent.acp_effort eq high
  ```
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
- **TypeScript docstrings for a pre-run ACP switch (`F09.ts-client-acp-docs`).**
  Both `switchAcpModel` methods exist; their docstrings should not promise a
  409 the server never sends.
  ```sh
  grep -n 'async switchAcpModel(' clients/typescript/src/client/conversation-client.ts
  grep -n 'async switchAcpModel(' clients/typescript/src/conversation/remote-conversation.ts
  control-agent-server assert --value "$(cat clients/typescript/src/client/conversation-client.ts clients/typescript/src/conversation/remote-conversation.ts | grep -c 'returns 409 before the first message')" --check . eq 0
  control-agent-server assert --value "$(cat clients/typescript/src/client/conversation-client.ts clients/typescript/src/conversation/remote-conversation.ts | grep -c 'the first session. Non-ACP conversations get 400.')" --check . eq 2
  ```
  Expected: the docstrings of `ConversationClient.switchAcpModel` and
  `RemoteConversation.switchAcpModel` say what `F09.acp-model-deferred` and
  `F09.ts-client` observe: before the first run the server answers 200 and
  stores the model for the first session, and a non-ACP conversation gets
  400. Neither promises a 409.
- **TypeScript effort switch (`F09.ts-client-acp-effort`).**
  `ConversationClient.switchAcpEffort` from the built client, on a fresh ACP
  conversation `AID5` that never runs and a fresh placeholder conversation
  `TID2`.
  ```sh
  test -f clients/typescript/dist/clients.js || (cd clients/typescript && npm ci && npm run build)
  TID2=$(control-agent-server conversation start --placeholder-agent --tools none --no-autotitle --print-id)
  AID5=$(control-agent-server conversation start --no-autotitle --body-json '{"agent_settings": {"agent_kind": "acp", "acp_server": "claude-code", "acp_model": "sonnet"}}' --print-id)
  F="$AGENT_SERVER_VERIFY_RUN/fixtures"
  cat > "$F/qa-f09-ts-effort.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const { ConversationClient } = await import(pathToFileURL(resolve('clients/typescript/dist/clients.js')).href);
  const [tid, acp] = process.argv.slice(2);
  const host = process.env.AGENT_SERVER_URL;
  const client = new ConversationClient({ host, apiKey: process.env.SESSION_API_KEY });
  await client.switchAcpEffort(acp, 'high');
  const info = await client.getConversation(acp);
  assert.equal(info.agent.acp_effort, 'high');
  assert.equal(info.current_effort, 'high');
  await assert.rejects(client.switchAcpEffort(tid, 'high'), (e) => e.status === 400);
  await assert.rejects(new ConversationClient({ host }).switchAcpEffort(acp, 'low'), (e) => e.status === 401);
  assert.equal((await client.getConversation(acp)).agent.acp_effort, 'high');
  console.log('TS_EFFORT_OK');
  JS
  control-agent-server exec --expect-output TS_EFFORT_OK --save F09.ts-client-acp-effort/program -- node "$F/qa-f09-ts-effort.mjs" "$TID2" "$AID5"
  control-agent-server state cat "server/workspace/conversations/${AID5//-/}/base_state.json" --max-chars 200 --check agent.acp_effort eq high
  ```
  `switchAcpEffort` on the ACP conversation that has not run resolves and
  `getConversation` shows `acp_effort` and `current_effort` `high` (also in
  `base_state.json`); on the regular conversation it rejects with an
  `HttpError` 400 and without a key with 401, and the stored level is
  unchanged.
- **Load a plugin (`F09.load-plugin`).** Start `PID` from the saved settings
  with the `qa-f09-mkt` marketplace registered on its `agent_context` (not
  auto-loaded), then load the plugin while capturing the socket.
  ```sh
  AS=$(control-agent-server api GET /api/settings --header 'X-Expose-Secrets: encrypted' --field agent_settings --unsafe-unredacted \
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
  control-agent-server ws read qa-f09-plugin --kinds ConversationStateUpdateEvent --contains '"key": "agent"' --expect-min 1 --wait 20 --save F09.load-plugin/agent-frame
  control-agent-server ws read qa-f09-plugin --contains qa-f09-mkt-plugin-skill --expect-none
  control-agent-server ws stop qa-f09-plugin --kinds ConversationStateUpdateEvent --contains QA_PLUGIN_HOOK --expect-min 1 --wait 20
  ```
  Before the load the agent has no skills and no hooks. After it, `GET`
  (with `include_skills=true`) lists the plugin's skill and its command (as
  the skill `qa-f09-mkt-plugin:qa-hello`), `hook_config` has the plugin's
  `UserPromptSubmit` hook, `base_state.json` has both, and the socket pushed
  state updates for `agent` and `hook_config`. The `agent` update (like the
  `full_state` snapshot) carries `agent_context.skills` as `[]`, so no frame
  names the plugin's skill; read skills from `GET` with `include_skills=true`.
- **The loaded hook runs (`F09.load-plugin-hook-runs`).** Run one turn on
  `PID`.

  Requires: `F09.load-plugin`
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

  Requires: `F09.acp-model-deferred`, `F09.load-plugin`
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
- **Load the same plugin twice (`F09.load-plugin-twice`).** Load
  the plugin into `QID` again, now by its full ref.

  Requires: `F09.load-plugin-errors`
  ```sh
  control-agent-server api POST "/api/conversations/$QID/load_plugin" --json '{"plugin_ref": "qa-f09-mkt-plugin@qa-f09-mkt"}' --expect 200 --save F09.load-plugin-twice/second-load
  control-agent-server api GET "/api/conversations/$QID" --query include_skills=true --quiet \
    --check agent.agent_context.skills len-eq 2 --check agent.agent_context.skills contains qa-f09-mkt-plugin-skill --check hook_config.user_prompt_submit len-ge 1
  control-agent-server api GET "/api/conversations/$QID" --quiet --check hook_config.user_prompt_submit len-eq 1 --save F09.load-plugin-twice/get
  ```
  The second load is 200, the skills are not duplicated (still two), and
  `hook_config.user_prompt_submit` keeps one copy of the plugin's hook:
  `_merge_runtime_plugin_hooks` adds only the matchers the live config does
  not already hold, so the hook still runs once per message and a client that
  retries `load_plugin` does not double its side effects.
- **Load a plugin from the Python SDK (`F09.load-plugin-sdk`).** A fresh
  conversation `SID` with the marketplace registered, reattached from an SDK
  program.

  Requires: `F09.load-plugin`
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

  Requires: `F09.switch-llm-ws`, `F09.switch-profile`, `F09.acp-model-deferred`, `F09.load-plugin`
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
- **Model-router status (`F09.model-router-status`).** Save two dummy
  profiles, `qa-f09-route-a` and `qa-f09-route-b` (unreachable endpoints:
  nothing in the router bullets before `F09.model-router-run` calls a model),
  and start `RQ`, an idle conversation whose agent carries a plain
  placeholder LLM, and `RM`, one whose agent LLM is a `MultimodalRouter`
  with routes `primary` and `secondary`.
  ```sh
  control-agent-server api POST /api/profiles/qa-f09-route-a --json '{"llm": {"model": "openai/qa-f09-route-a", "api_key": "sk-qa-f09-route-a", "base_url": "http://127.0.0.1:9/v1"}}' --expect 201
  control-agent-server api POST /api/profiles/qa-f09-route-b --json '{"llm": {"model": "openai/qa-f09-route-b", "api_key": "sk-qa-f09-route-b", "base_url": "http://127.0.0.1:9/v1"}}' --expect 201
  RQ=$(control-agent-server conversation start --no-autotitle --body-json '{"agent": {"kind": "Agent", "tools": [], "llm": {"model": "openai/qa-f09-route-base", "api_key": "sk-qa-f09-route-base", "base_url": "http://127.0.0.1:9/v1"}}}' --print-id)
  RM=$(control-agent-server conversation start --no-autotitle --body-json '{"agent": {"kind": "Agent", "tools": [], "llm": {"router_name": "multimodal_router", "usage_id": "qa-f09-mm", "llms_for_routing": {"primary": {"model": "openai/qa-f09-primary", "api_key": "sk-qa-f09-route-mm", "base_url": "http://127.0.0.1:9/v1", "usage_id": "qa-f09-primary"}, "secondary": {"model": "openai/qa-f09-secondary", "api_key": "sk-qa-f09-route-mm", "base_url": "http://127.0.0.1:9/v1", "usage_id": "qa-f09-secondary"}}}}}' --print-id)
  control-agent-server api GET "/api/conversations/$RQ/model_router" --expect 200 --check enabled eq false --check selection eq null \
    --check last_route_name eq null --check routes len-eq 0 --save F09.model-router-status/plain
  control-agent-server api GET "/api/conversations/$RM/model_router" --expect 200 --check enabled eq true --check selection.mode eq auto \
    --check selection.manual_route_name eq null --check last_route_name eq null --check routes len-eq 2 \
    --check routes.0.name eq primary --check routes.0.model eq openai/qa-f09-primary --check routes.0.label eq primary \
    --check routes.1.name eq secondary --check routes.1.model eq openai/qa-f09-secondary --save F09.model-router-status/router
  control-agent-server api GET "/api/conversations/$RM" --quiet --check agent.llm.router_name eq multimodal_router \
    --check agent.llm.model eq multimodal_router --check agent.llm.usage_id eq qa-f09-mm \
    --check agent.llm.llms_for_routing.primary.api_key missing --check agent.llm.selection.mode eq auto
  control-agent-server api GET /api/conversations/00000000-0000-4000-8000-000000000000/model_router --expect 404
  control-agent-server api GET "/api/conversations/$RM/model_router" --auth none --expect 401 --check detail eq Unauthorized
  ```
  The plain conversation reports `enabled: false` with no selection and no
  routes. The router conversation reports both routes in order, each with
  its model and its name as `label`, `selection.mode` `auto` and no
  `last_route_name` (only a model call sets it). `GET` shows the router
  itself: its `model` is the placeholder `multimodal_router`, its routes'
  keys are masked. An unknown id is 404, no key 401.
- **Add a route from a saved profile (`F09.model-router-add-route`).** Wrap
  `RQ`'s plain LLM, add a second route under its own name, extend `RM`'s
  router, and wrap `RQ2`'s LLM under a chosen base route name.

  Requires: `F09.model-router-status`
  ```sh
  control-agent-server ws start "/sockets/events/$RQ" --name qa-f09-router --duration 120
  control-agent-server api POST "/api/conversations/$RQ/model_router/routes" --json '{"profile_name": "qa-f09-route-a"}' \
    --expect 200 --check enabled eq true --check selection.mode eq auto --check routes len-eq 2 \
    --check routes.0.name eq default --check routes.0.model eq openai/qa-f09-route-base \
    --check routes.1.name eq qa-f09-route-a --check routes.1.model eq openai/qa-f09-route-a --save F09.model-router-add-route/wrap
  control-agent-server api POST "/api/conversations/$RQ/model_router/routes" --json '{"profile_name": "qa-f09-route-b", "route_name": "reviewer"}' \
    --expect 200 --check routes len-eq 3 --check routes.2.name eq reviewer --check routes.2.model eq openai/qa-f09-route-b \
    --check selection.mode eq auto --save F09.model-router-add-route/named
  control-agent-server api GET "/api/conversations/$RQ" --quiet --check agent.llm.router_name eq base_router \
    --check agent.llm.llms_for_routing.default.usage_id eq default \
    --check agent.llm.llms_for_routing.qa-f09-route-a.usage_id eq profile:qa-f09-route-a \
    --check agent.llm.llms_for_routing.reviewer.usage_id eq profile:qa-f09-route-b \
    --check agent.llm.llms_for_routing.reviewer.api_key missing --save F09.model-router-add-route/get
  control-agent-server state cat "server/workspace/conversations/${RQ//-/}/base_state.json" --max-chars 200 \
    --check agent.llm.router_name eq base_router --check agent.llm.llms_for_routing.reviewer.model eq openai/qa-f09-route-b \
    --check agent.llm.llms_for_routing.reviewer.api_key matches '^gAAAAA' --not-contains sk-qa-f09-route
  control-agent-server ws read qa-f09-router --kinds ConversationStateUpdateEvent --contains 'profile:qa-f09-route-b' --expect-min 1 --wait 20 \
    --save F09.model-router-add-route/frames
  control-agent-server ws stop qa-f09-router --contains sk-qa-f09-route --expect-none
  control-agent-server api POST "/api/conversations/$RM/model_router/routes" --json '{"profile_name": "qa-f09-route-b", "route_name": "reviewer", "base_route_name": "unused"}' \
    --expect 200 --check routes len-eq 3 --check routes.0.name eq primary --check routes.2.name eq reviewer --save F09.model-router-add-route/extend
  control-agent-server api GET "/api/conversations/$RM" --quiet --check agent.llm.router_name eq multimodal_router \
    --check agent.llm.llms_for_routing.reviewer.usage_id eq profile:qa-f09-route-b
  RQ2=$(control-agent-server conversation start --no-autotitle --body-json '{"agent": {"kind": "Agent", "tools": [], "llm": {"model": "openai/qa-f09-route-base2", "api_key": "sk-qa-f09-route-base2", "base_url": "http://127.0.0.1:9/v1"}}}' --print-id)
  control-agent-server api POST "/api/conversations/$RQ2/model_router/routes" --json '{"profile_name": "qa-f09-route-a", "base_route_name": "main"}' \
    --expect 200 --check routes.0.name eq main --check routes.0.model eq openai/qa-f09-route-base2 --check routes.1.name eq qa-f09-route-a
  control-agent-server state grep sk-qa-f09-route --glob 'server/**/*' --expect-none
  ```
  The first add turns `RQ`'s LLM into a `base_router` whose route `default`
  is the old LLM (its `usage_id` stays `default`) and whose second route is
  named after the profile, with `usage_id` `profile:qa-f09-route-a`; the
  second add uses the given `route_name`. The router stays in `auto` mode.
  `GET` and `base_state.json` show the routes (keys masked in `GET`, Fernet
  tokens on disk), the socket pushes the new `agent`, and no plaintext key
  is stored or pushed. On `RM` the route joins the existing
  `MultimodalRouter`, which keeps its type, so `base_route_name` is unused;
  on the plain `RQ2` it names the wrapped LLM's route.
- **Route add errors (`F09.model-router-add-route-errors`).** Unknown,
  invalid and missing profiles, route names already taken, an unknown
  conversation and no key, on `RQ`.

  Requires: `F09.model-router-add-route`
  ```sh
  control-agent-server api POST "/api/conversations/$RQ/model_router/routes" --json '{"profile_name": "qa-f09-nope"}' \
    --expect 404 --check detail eq "Profile 'qa-f09-nope' not found" --save F09.model-router-add-route-errors/unknown-profile
  control-agent-server api POST "/api/conversations/$RQ/model_router/routes" --json '{"profile_name": "qa-f09-route-b", "route_name": "default"}' \
    --expect 400 --check detail eq 'Model router route already exists: default' --save F09.model-router-add-route-errors/duplicate
  control-agent-server api POST "/api/conversations/$RQ/model_router/routes" --json '{"profile_name": "qa-f09-route-a"}' \
    --expect 400 --check detail eq 'Model router route already exists: qa-f09-route-a'
  control-agent-server api POST "/api/conversations/$RQ/model_router/routes" --json '{"profile_name": "../qa"}' --expect 400 --check detail contains 'Invalid profile name'
  control-agent-server api POST "/api/conversations/$RQ/model_router/routes" --json '{"route_name": "qa-f09-x"}' --expect 422 --check detail.0.loc.1 eq profile_name
  control-agent-server api POST /api/conversations/00000000-0000-4000-8000-000000000000/model_router/routes --json '{"profile_name": "qa-f09-route-a"}' --expect 404
  control-agent-server api POST "/api/conversations/$RQ/model_router/routes" --auth none --json '{"profile_name": "qa-f09-route-b", "route_name": "qa-f09-noauth"}' --expect 401
  control-agent-server api GET "/api/conversations/$RQ/model_router" --quiet --check routes len-eq 3 --check routes.2.name eq reviewer
  ```
  404 with the profile's name, 400 for a route name already in the table
  (whether it came from the wrap or from a profile), 400 for a name that
  breaks the profile rules, 422 without `profile_name`, 404 for the unknown
  id and 401 without a key; `RQ` still has its three routes.
- **Pin a route and return to auto (`F09.model-router-select`).** On `RQ`,
  then on `RM`.

  Requires: `F09.model-router-add-route`
  ```sh
  control-agent-server ws start "/sockets/events/$RQ" --name qa-f09-router-select --duration 120
  control-agent-server api PATCH "/api/conversations/$RQ/model_router" --json '{"selection": {"mode": "manual", "manual_route_name": "reviewer"}}' \
    --expect 200 --check enabled eq true --check selection.mode eq manual --check selection.manual_route_name eq reviewer \
    --check routes len-eq 3 --save F09.model-router-select/manual
  control-agent-server api GET "/api/conversations/$RQ/model_router" --quiet --check selection.manual_route_name eq reviewer
  control-agent-server api GET "/api/conversations/$RQ" --quiet --check agent.llm.router_name eq base_router \
    --check agent.llm.selection.mode eq manual --check agent.llm.selection.manual_route_name eq reviewer --save F09.model-router-select/get
  control-agent-server state cat "server/workspace/conversations/${RQ//-/}/base_state.json" --max-chars 200 \
    --check agent.llm.selection.manual_route_name eq reviewer
  control-agent-server ws read qa-f09-router-select --kinds ConversationStateUpdateEvent --contains '"manual_route_name": "reviewer"' \
    --expect-min 1 --wait 20 --save F09.model-router-select/frames
  control-agent-server ws stop qa-f09-router-select
  control-agent-server api PATCH "/api/conversations/$RQ/model_router" --json '{"selection": {"mode": "auto"}}' \
    --expect 200 --check selection.mode eq auto --check selection.manual_route_name eq null --check routes len-eq 3 --save F09.model-router-select/auto
  control-agent-server state cat "server/workspace/conversations/${RQ//-/}/base_state.json" --max-chars 200 --check agent.llm.selection.mode eq auto
  control-agent-server api PATCH "/api/conversations/$RM/model_router" --json '{"selection": {"mode": "manual", "manual_route_name": "secondary"}}' \
    --expect 200 --check selection.manual_route_name eq secondary
  control-agent-server api GET "/api/conversations/$RM" --quiet --check agent.llm.router_name eq multimodal_router \
    --check agent.llm.selection.manual_route_name eq secondary
  ```
  The `PATCH` answers the new status; `GET .../model_router`, the router's
  `selection` in `GET`, `base_state.json` and a `ConversationStateUpdateEvent`
  with key `agent` all carry the pin, and `auto` clears it
  (`manual_route_name: null`). The router and its routes are kept: on `RM`
  the pin applies to the `MultimodalRouter`, which stays one.
- **Selection errors (`F09.model-router-select-errors`).** Pin `RQ` to
  `qa-f09-route-a`, then try every refused selection; finally a
  conversation `RP` whose LLM is not a router.

  Requires: `F09.model-router-add-route`
  ```sh
  control-agent-server api PATCH "/api/conversations/$RQ/model_router" --json '{"selection": {"mode": "manual", "manual_route_name": "qa-f09-route-a"}}' --expect 200
  control-agent-server api PATCH "/api/conversations/$RQ/model_router" --json '{"selection": {"mode": "manual", "manual_route_name": "qa-f09-nope"}}' \
    --expect 400 --check detail eq "LLM route 'qa-f09-nope' was not found. Available routes: default, qa-f09-route-a, reviewer" \
    --save F09.model-router-select-errors/unknown-route
  control-agent-server api PATCH "/api/conversations/$RQ/model_router" --json '{"selection": {"mode": "manual"}}' \
    --expect 422 --check detail.0.msg contains 'manual_route_name is required in manual routing mode'
  control-agent-server api PATCH "/api/conversations/$RQ/model_router" --json '{"selection": {"mode": "auto", "manual_route_name": "default"}}' \
    --expect 422 --check detail.0.msg contains 'manual_route_name is only valid in manual routing mode'
  control-agent-server api PATCH "/api/conversations/$RQ/model_router" --json '{"selection": {"mode": "fast"}}' --expect 422 --check detail.0.loc.2 eq mode
  control-agent-server api PATCH "/api/conversations/$RQ/model_router" --json '{}' --expect 422 --check detail.0.loc.1 eq selection
  control-agent-server api PATCH /api/conversations/00000000-0000-4000-8000-000000000000/model_router --json '{"selection": {"mode": "auto"}}' --expect 404
  control-agent-server api PATCH "/api/conversations/$RQ/model_router" --auth none --json '{"selection": {"mode": "auto"}}' --expect 401
  control-agent-server api GET "/api/conversations/$RQ/model_router" --quiet --check selection.mode eq manual --check selection.manual_route_name eq qa-f09-route-a
  RP=$(control-agent-server conversation start --no-autotitle --body-json '{"agent": {"kind": "Agent", "tools": [], "llm": {"model": "openai/qa-f09-route-plain", "api_key": "sk-qa-f09-route-plain", "base_url": "http://127.0.0.1:9/v1"}}}' --print-id)
  control-agent-server api PATCH "/api/conversations/$RP/model_router" --json '{"selection": {"mode": "auto"}}' \
    --expect 400 --check detail eq 'Current agent LLM is not a RouterLLM.' --save F09.model-router-select-errors/not-a-router
  control-agent-server api GET "/api/conversations/$RP/model_router" --quiet --check enabled eq false
  ```
  An unknown route is 400 and lists the routes; the selection shapes the
  model refuses are 422 (a manual pin without a route, an `auto` that names
  one, an unknown `mode`, no `selection`); the unknown id is 404 and no key
  401. `RQ` stays pinned to `qa-f09-route-a`. A `PATCH` never creates a
  router: on `RP` it is 400 and `RP` stays plain (adding a route is what
  wraps an LLM).
- **switch_llm and switch_profile replace the router (`F09.model-router-switch-replaces`).**
  `switch_profile` on `RM` (a pinned `MultimodalRouter` with three routes),
  `switch_llm` on `RQ2` (a wrapped router), then a route add on `RQ2`.

  Requires: `F09.model-router-select`
  ```sh
  control-agent-server api POST "/api/conversations/$RM/switch_profile" --json '{"profile_name": "qa-f09-route-a"}' \
    --expect 200 --check success eq true --save F09.model-router-switch-replaces/switch-profile
  control-agent-server api GET "/api/conversations/$RM/model_router" --check enabled eq false --check routes len-eq 0 --save F09.model-router-switch-replaces/status
  control-agent-server api GET "/api/conversations/$RM" --quiet --check agent.llm.model eq openai/qa-f09-route-a \
    --check agent.llm.usage_id eq profile:qa-f09-route-a --check agent.llm.router_name missing --check agent.llm.llms_for_routing missing
  control-agent-server api PATCH "/api/conversations/$RM/model_router" --json '{"selection": {"mode": "auto"}}' \
    --expect 400 --check detail eq 'Current agent LLM is not a RouterLLM.'
  control-agent-server api POST "/api/conversations/$RQ2/switch_llm" \
    --json '{"llm": {"model": "openai/qa-f09-route-switched", "api_key": "sk-qa-f09-route-switched", "base_url": "http://127.0.0.1:9/v1", "usage_id": "qa-f09-route-switched"}}' \
    --expect 200 --check success eq true
  control-agent-server api GET "/api/conversations/$RQ2/model_router" --quiet --check enabled eq false
  control-agent-server api POST "/api/conversations/$RQ2/model_router/routes" --json '{"profile_name": "qa-f09-route-b"}' \
    --expect 200 --check routes len-eq 2 --check routes.0.name eq default --check routes.0.model eq openai/qa-f09-route-switched \
    --check routes.1.name eq qa-f09-route-b --save F09.model-router-switch-replaces/rewrap
  ```
  Both switches answer `{"success": true}` and install the posted or saved
  LLM as the agent's whole LLM: the router, its routes and its pin are gone
  (`enabled: false`, no `router_name` or `llms_for_routing` in `GET`), and
  a selection change is 400 as on any plain conversation. To change models
  and keep the router, pin a route instead. A route add after the switch
  builds a new `base_router` around the switched LLM (`default`), without
  the routes the old router had (`main`, `qa-f09-route-a`).
- **Model router on an ACP conversation (`F09.model-router-acp-refused`).**
  An ACP conversation `RA` that has not run.

  Requires: `F09.model-router-status`
  ```sh
  RA=$(control-agent-server conversation start --no-autotitle --body-json '{"agent_settings": {"agent_kind": "acp", "acp_server": "claude-code", "acp_model": "sonnet"}}' --print-id)
  control-agent-server api GET "/api/conversations/$RA/model_router" --check enabled eq false --check routes len-eq 0
  control-agent-server api POST "/api/conversations/$RA/model_router/routes" --json '{"profile_name": "qa-f09-route-a"}' \
    --expect 400 --check detail contains switch_acp_model --save F09.model-router-acp-refused/add-route
  control-agent-server api PATCH "/api/conversations/$RA/model_router" --json '{"selection": {"mode": "auto"}}' \
    --expect 400 --check detail contains switch_acp_model --save F09.model-router-acp-refused/select
  control-agent-server api GET "/api/conversations/$RA" --quiet --check agent.kind eq ACPAgent --check agent.llm.usage_id eq acp-managed \
    --check agent.llm.llms_for_routing missing --check agent.acp_model eq sonnet
  ```
  The ACP agent's placeholder LLM is not a router (`enabled: false`); a
  route add and a selection change are both refused with the same 400 as
  `switch_llm` (`switch_llm and switch_profile are not supported for ACP
  conversations; use switch_acp_model instead.`), and the placeholder stays.
- **Model router from the Python SDK (`F09.model-router-sdk`).** A fresh
  plain conversation `RS`, reattached from an SDK program.

  Requires: `F09.model-router-status`
  ```sh
  RS=$(control-agent-server conversation start --no-autotitle --body-json '{"agent": {"kind": "Agent", "tools": [], "llm": {"model": "openai/qa-f09-route-sdk", "api_key": "sk-qa-f09-route-sdk", "base_url": "http://127.0.0.1:9/v1"}}}' --print-id)
  mkdir -p "$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f09-router-sdk"
  control-agent-server exec --env RS="$RS" --env WD="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f09-router-sdk" --expect-output SDK_MODEL_ROUTER_OK \
    --save F09.model-router-sdk/program -- .venv/bin/python - <<'PY'
  import os
  import uuid

  import httpx

  from openhands.sdk import RemoteConversation, RemoteWorkspace
  from openhands.sdk.llm import ModelRouterSelection, ModelRouterStatus

  ws = RemoteWorkspace(host=os.environ["AGENT_SERVER_URL"], api_key=os.environ["SESSION_API_KEY"], working_dir=os.environ["WD"])
  conv = RemoteConversation.attach(ws, uuid.UUID(os.environ["RS"]), visualizer=None)
  status = conv.get_model_router()
  assert isinstance(status, ModelRouterStatus) and status.enabled is False and status.routes == [], status
  status = conv.add_model_router_route("qa-f09-route-a", base_route_name="base")
  assert [r.name for r in status.routes] == ["base", "qa-f09-route-a"], status
  status = conv.add_model_router_route("qa-f09-route-b", route_name="reviewer")
  assert [r.model for r in status.routes] == ["openai/qa-f09-route-sdk", "openai/qa-f09-route-a", "openai/qa-f09-route-b"], status
  status = conv.switch_routed_llm("reviewer")
  assert status.selection == ModelRouterSelection(mode="manual", manual_route_name="reviewer"), status
  status = conv.resume_auto_llm_routing()
  assert status.selection.mode == "auto" and status.selection.manual_route_name is None, status
  status = conv.update_model_router_selection(ModelRouterSelection(mode="manual", manual_route_name="qa-f09-route-a"))
  assert status.selection.manual_route_name == "qa-f09-route-a", status
  for call, code in ((lambda: conv.switch_routed_llm("qa-f09-nope"), 400), (lambda: conv.add_model_router_route("qa-f09-nope"), 404)):
      try:
          call()
          raise AssertionError("a refused call succeeded")
      except httpx.HTTPStatusError as e:
          assert e.response.status_code == code, e
  print("SDK_MODEL_ROUTER_OK")
  conv.close()
  PY
  control-agent-server api GET "/api/conversations/$RS/model_router" --quiet --check selection.manual_route_name eq qa-f09-route-a \
    --check routes len-eq 3 --check routes.0.name eq base --save F09.model-router-sdk/get
  ```
  Every method returns a `ModelRouterStatus` matching what the server
  stored: the wrap under `base_route_name` `base`, the named route, the pin,
  the return to `auto` and the explicit selection. The unknown route and
  the unknown profile raise `httpx.HTTPStatusError` 400 and 404 (the SDK
  logs and re-raises), and the server keeps the `qa-f09-route-a` pin.
- **TypeScript model-router methods (`F09.ts-client-model-router`).**
  `ConversationClient` and `ConversationManager` from the built client
  (rebuilt when `clients/typescript/src` is newer than `dist`), on a fresh
  plain conversation `RTS`.

  Requires: `F09.model-router-status`
  ```sh
  test -d clients/typescript/node_modules/typescript || (cd clients/typescript && npm ci --silent)
  if [ ! -f clients/typescript/dist/index.js ] || \
    [ -n "$(find clients/typescript/src clients/typescript/package.json -newer clients/typescript/dist/index.js -print -quit)" ]; then
    (cd clients/typescript && npm run build --silent)
  fi
  RTS=$(control-agent-server conversation start --no-autotitle --body-json '{"agent": {"kind": "Agent", "tools": [], "llm": {"model": "openai/qa-f09-route-ts", "api_key": "sk-qa-f09-route-ts", "base_url": "http://127.0.0.1:9/v1"}}}' --print-id)
  F="$AGENT_SERVER_VERIFY_RUN/fixtures"
  mkdir -p "$F"
  cat > "$F/qa-f09-ts-router.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const dist = (name) => import(pathToFileURL(resolve('clients/typescript/dist', name)).href);
  const { ConversationClient } = await dist('clients.js');
  const { ConversationManager } = await dist('index.js');
  const [cid] = process.argv.slice(2);
  const host = process.env.AGENT_SERVER_URL;
  const apiKey = process.env.SESSION_API_KEY;
  const client = new ConversationClient({ host, apiKey });
  const manager = new ConversationManager({ host, apiKey });
  let status = await client.getModelRouter(cid);
  assert.equal(status.enabled, false);
  assert.deepEqual(status.routes, []);
  status = await client.addModelRouterRoute(cid, 'qa-f09-route-a');
  assert.equal(status.enabled, true);
  assert.deepEqual(status.routes.map((r) => r.name), ['default', 'qa-f09-route-a']);
  status = await manager.addModelRouterRoute(cid, 'qa-f09-route-b', 'reviewer');
  assert.deepEqual(status.routes.map((r) => r.name), ['default', 'qa-f09-route-a', 'reviewer']);
  status = await client.switchModelRouter(cid, 'reviewer');
  assert.deepEqual(status.selection, { mode: 'manual', manual_route_name: 'reviewer' });
  status = await manager.resumeAutoModelRouting(cid);
  assert.equal(status.selection.mode, 'auto');
  status = await manager.switchModelRouter(cid, 'qa-f09-route-a');
  assert.equal(status.selection.manual_route_name, 'qa-f09-route-a');
  status = await client.resumeAutoModelRouting(cid);
  assert.equal(status.selection.mode, 'auto');
  await client.updateModelRouter(cid, { mode: 'manual', manual_route_name: 'reviewer' });
  assert.equal((await manager.getModelRouter(cid)).selection.manual_route_name, 'reviewer');
  await assert.rejects(client.switchModelRouter(cid, 'qa-f09-nope'), (e) => e.status === 400);
  await assert.rejects(client.addModelRouterRoute(cid, 'qa-f09-nope'), (e) => e.status === 404);
  await assert.rejects(client.addModelRouterRoute(cid, 'qa-f09-route-a'), (e) => e.status === 400);
  await assert.rejects(new ConversationClient({ host }).getModelRouter(cid), (e) => e.status === 401);
  assert.equal((await client.getModelRouter(cid)).routes.length, 3);
  console.log('TS_MODEL_ROUTER_OK');
  JS
  control-agent-server exec --expect-output TS_MODEL_ROUTER_OK --save F09.ts-client-model-router/program -- node "$F/qa-f09-ts-router.mjs" "$RTS"
  control-agent-server api GET "/api/conversations/$RTS" --quiet --check agent.llm.router_name eq base_router \
    --check agent.llm.selection.manual_route_name eq reviewer --check agent.llm.llms_for_routing.reviewer.model eq openai/qa-f09-route-b
  ```
  Every call resolves with the server's `ModelRouterStatus`: the wrap
  (`default` plus the profile's route), the named route from
  `ConversationManager`, pins and returns to `auto` through both classes,
  and `updateModelRouter` with an explicit selection; the unknown route
  rejects with an `HttpError` 400, the unknown profile 404, the duplicate
  route 400 and a keyless client 401, and `GET` shows the router pinned to
  `reviewer`. (`RemoteConversation`'s methods of the same names post the
  same bodies to the same routes.)
- **Turns on a pinned route (`F09.model-router-run`).** A conversation `RT`
  from the saved settings runs one turn on `deepseek-flash`; then the
  `deepseek-flash` profile is added as route `flash`, pinned, and a turn
  runs; then routing returns to `auto` and a turn runs. The block re-saves
  the DeepSeek preset (it is idempotent) so the bullet runs on its own.

  Needs: `llm`
  ```sh
  control-agent-server llm preset deepseek
  RT=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Reply with the single word: ok' --wait --until finished --timeout 240 --print-id)
  DEF1=$(control-agent-server api GET "/api/conversations/$RT" --field stats.usage_to_metrics.default.accumulated_token_usage.prompt_tokens)
  test "$DEF1" -gt 0
  control-agent-server api POST "/api/conversations/$RT/model_router/routes" --json '{"profile_name": "deepseek-flash", "route_name": "flash"}' \
    --expect 200 --check routes.0.name eq default --check routes.1.name eq flash
  control-agent-server api PATCH "/api/conversations/$RT/model_router" --json '{"selection": {"mode": "manual", "manual_route_name": "flash"}}' \
    --expect 200 --check last_route_name eq null
  control-agent-server conversation send "$RT" --text 'Reply with the single word: ok' --wait --until finished,error --timeout 240
  control-agent-server api GET "/api/conversations/$RT" --until-ok 60 --quiet --check execution_status eq finished \
    --check 'stats.usage_to_metrics.profile:deepseek-flash.accumulated_token_usage.prompt_tokens' gt 0 \
    --check stats.usage_to_metrics.default.accumulated_token_usage.prompt_tokens eq "$DEF1" --save F09.model-router-run/manual-stats
  control-agent-server api GET "/api/conversations/$RT/model_router" --check last_route_name eq flash --check selection.manual_route_name eq flash \
    --save F09.model-router-run/manual-status
  FL1=$(control-agent-server api GET "/api/conversations/$RT" --field 'stats.usage_to_metrics.profile:deepseek-flash.accumulated_token_usage.prompt_tokens')
  control-agent-server api PATCH "/api/conversations/$RT/model_router" --json '{"selection": {"mode": "auto"}}' \
    --expect 200 --check selection.mode eq auto --check last_route_name eq flash
  control-agent-server conversation send "$RT" --text 'Reply with the single word: ok' --wait --until finished,error --timeout 240
  control-agent-server api GET "/api/conversations/$RT" --until-ok 60 --quiet --check execution_status eq finished \
    --check stats.usage_to_metrics.default.accumulated_token_usage.prompt_tokens gt "$DEF1" \
    --check 'stats.usage_to_metrics.profile:deepseek-flash.accumulated_token_usage.prompt_tokens' eq "$FL1" --save F09.model-router-run/auto-stats
  control-agent-server api GET "/api/conversations/$RT/model_router" --check last_route_name eq default --check selection.mode eq auto
  ```
  The pinned turn finishes on `flash`: its tokens are recorded under
  `profile:deepseek-flash` while `default` does not grow, and
  `last_route_name` becomes `flash` (only a call sets it; a `PATCH` keeps
  the last one). After the return to `auto`, the base router routes to its
  first route: the next turn's tokens go to `default`, `flash` does not
  grow, and `last_route_name` is `default`.
- **A route pinned before the first run (`F09.model-router-before-first-run`), known bug #5639.**
  Start from a placeholder agent whose model is unreachable, add the
  `deepseek-flash` profile as route `flash` and pin it before anything
  ran, then run.

  Requires: `F09.model-router-run`
  Needs: `llm`
  ```sh
  RB=$(control-agent-server conversation start --placeholder-agent --tools none --no-autotitle --print-id)
  control-agent-server api POST "/api/conversations/$RB/model_router/routes" --json '{"profile_name": "deepseek-flash", "route_name": "flash"}' --expect 200
  control-agent-server api PATCH "/api/conversations/$RB/model_router" --json '{"selection": {"mode": "manual", "manual_route_name": "flash"}}' --expect 200
  control-agent-server conversation send "$RB" --text 'Reply with the single word: ok' --wait --until finished,error --timeout 240
  control-agent-server api GET "/api/conversations/$RB" --quiet --check execution_status eq finished
  control-agent-server api GET "/api/conversations/$RB/model_router" --quiet --check last_route_name eq flash
  control-agent-server api GET "/api/conversations/$RB" --until-ok 20 --quiet \
    --check 'stats.usage_to_metrics.profile:deepseek-flash.accumulated_token_usage.prompt_tokens' gt 0 --save F09.model-router-before-first-run/stats  # bug: status eq 200 && checks.0.actual eq null
  ```
  The run finishes and `last_route_name` is `flash`, which only the pinned
  route can do (the placeholder points at a closed port). Expected: its
  tokens are recorded under `profile:deepseek-flash`, as they are when the
  route is added after the first message (`F09.model-router-run`). Today
  `stats.usage_to_metrics` is `{}`: the route add and the pin register the
  LLM before the stats subscribe on the first message
  ([#5639](https://github.com/OpenHands/software-agent-sdk/issues/5639)).
- **Restart (`F09.model-router-restart`).** Restart the server and read the
  router conversations back.

  Requires: `F09.model-router-select-errors`, `F09.model-router-switch-replaces`
  ```sh
  control-agent-server restart
  control-agent-server api GET "/api/conversations/$RQ/model_router" --check enabled eq true --check selection.mode eq manual \
    --check selection.manual_route_name eq qa-f09-route-a --check routes len-eq 3 --check routes.2.name eq reviewer --save F09.model-router-restart/rq
  control-agent-server api GET "/api/conversations/$RQ" --quiet --check agent.llm.router_name eq base_router \
    --check agent.llm.llms_for_routing.reviewer.usage_id eq profile:qa-f09-route-b
  control-agent-server api PATCH "/api/conversations/$RQ/model_router" --json '{"selection": {"mode": "manual", "manual_route_name": "reviewer"}}' \
    --expect 200 --check selection.manual_route_name eq reviewer
  control-agent-server api GET "/api/conversations/$RM/model_router" --quiet --check enabled eq false
  control-agent-server api GET "/api/conversations/$RQ2/model_router" --quiet --check routes len-eq 2 --check routes.1.name eq qa-f09-route-b
  ```
  `RQ` comes back as a `base_router` with its three routes and the
  `qa-f09-route-a` pin, and accepts a new pin; `RM`, whose router
  `switch_profile` replaced, is still plain, and `RQ2` still has the router
  built after its `switch_llm`.

## Gotchas

- Agent Canvas does not switch or show the ACP effort yet; its side is [OpenHands/OpenHands#19436](https://github.com/OpenHands/OpenHands/pull/19436), and it also needs an Agent Server release after 1.61.0.
- Every switch answers `{"success": true}` and nothing else; read the result
  back with `GET /api/conversations/{id}` (which serves the autosaved
  `base_state.json`, so on an idle conversation it shows a switch
  immediately) or the `ConversationStateUpdateEvent` with key `agent`.
  While a step is running, `GET` keeps the old LLM until the step ends
  (`F09.switch-llm-mid-run-get`); the socket and the run itself switch at
  once, so wait for the run (or read the socket) before asserting on `GET`.
- `switch_llm` with a `usage_id` that is already registered (the default
  `default` once the agent initialized, or any id used before) installs the
  posted config, replacing the registered entry when the config differs and
  keeping that id's usage in `stats.usage_to_metrics`
  (`F09.switch-llm-registered-usage-id`); an identical config reuses the
  registered LLM, so switching back is cheap. `switch_profile` reuses the
  copy cached under `profile:<name>` at its first use
  (`F09.switch-profile-edited`).
- Usage is accounted per `usage_id` in `stats.usage_to_metrics`, but only
  for LLMs registered after the agent initialized
  (`F09.switch-llm-before-first-run`); send one message (or create with an
  initial message) before switching when costs matter.
- The model-router routes act on the conversation's agent LLM only when it
  is a `RouterLLM`: `GET .../model_router` answers `enabled: false` for any
  other LLM, `PATCH` is 400 there, and only `POST .../model_router/routes`
  turns a plain LLM into a router. The router's own `model` is a placeholder
  (`base_router`, `multimodal_router`), and its usage is never recorded under
  the router: each route keeps its LLM's `usage_id` (`default` for a wrapped
  agent LLM, `profile:<name>` for an added profile). A route is a copy of the
  profile at the time it was added (later profile edits do not reach it),
  and adding the same profile twice under two route names reuses the
  conversation's cached `profile:<name>` LLM. An empty `route_name` falls back
  to the profile name. A route added or a pin set before the first message
  is not counted in `stats` (`F09.model-router-before-first-run`), like any
  LLM registered then.
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
  unless `include_skills=true`. Socket and webhook
  `ConversationStateUpdateEvent`s (`full_state` and `agent`) always carry
  `agent_context.skills` as `[]`; the persisted state keeps the skills.
- `switch_acp_model` before the first run only persists the model; the
  provider's capability (`supports_runtime_model_switch`) and
  `available_models` are unknown (`false`, `[]`) until a session started. A
  504 from a live switch has its `detail` rewritten to `Internal Server
  Error`; read `exception`. The TypeScript client's `switchAcpModel`
  docstrings document this 200-and-defer behaviour (`F09.acp-model-deferred`,
  `F09.ts-client`, `F09.ts-client-acp-docs`).
- `switch_acp_effort` before the first run stores any non-blank level, since
  no level list exists yet; the first session applies it only if the model
  offers it (otherwise the server logs a warning and the session keeps the
  adapter's default), and `current_effort` then reports what the session
  runs. On a live session the level must be in `available_efforts`, which
  are per model (with claude-agent-acp every listed model offers `default`
  to `max`); `acp_effort` stays pinned across a model switch and is
  re-applied when the new model offers it. With nothing pinned the session runs
  at the adapter's own `default` level (`F09.acp-effort-deferred`,
  `F09.acp-effort-model-switch`, `F09.acp-effort-live-timeout`).
- `current_model_id` is the bare model id (`sonnet`), also for a legacy
  `acp_model` such as `sonnet/high`; a cold read before the first session
  falls back to the stored `acp_model`, suffix included
  (`F09.acp-model-id-bare`).
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
