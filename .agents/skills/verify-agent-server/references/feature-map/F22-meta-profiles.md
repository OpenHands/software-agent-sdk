# Meta-profiles (model router)

A meta-profile is a named, secret-free model-routing configuration: a
`classifier_model` plus either an ordered list of `classes`
(`{description, model}`, the classifier answers a class number) or a direct
`prompt_template` containing `{{ instance_text }}` (and optionally
`{{ model_table }}`), where the classifier answers `{"model": "<name>"}`.
Every model reference is the name of a saved LLM profile. Consumers list,
read, save (create or overwrite), delete and activate meta-profiles; each is
one JSON file under `OH_PERSISTENCE_DIR/meta-profiles`, at most 50 of them.
Activation records `active_meta_profile` in the server's settings and wires
`agent_settings` (`active_meta_profile`, `enable_classify_and_switch_llm_tool:
true` and an inline copy in `meta_profile`), so every conversation started
from those settings gets the `route_task_to_model` tool
(`ClassifyAndSwitchLLMTool`). When the agent calls it, the classifier profile
picks a class and the conversation switches to that class's LLM profile for
its next steps. Deleting the active meta-profile turns routing off again.

Source: `openhands-agent-server/openhands/agent_server/meta_profiles_router.py`, `openhands-sdk/openhands/sdk/llm/meta_profile_store.py`, `openhands-sdk/openhands/sdk/tool/builtins/classify_and_switch_llm.py`, `openhands-agent-server/openhands/agent_server/persistence/models.py`, `clients/typescript/src/client/meta-profiles-client.ts`

Needs: `llm`, `node`

Routes: `GET /api/meta-profiles`, `GET /api/meta-profiles/{name}`,
`POST /api/meta-profiles/{name}`, `DELETE /api/meta-profiles/{name}`,
`POST /api/meta-profiles/{name}/activate`

## Sub-features

- `F22.auth-required`: every meta-profile route answers 401 without a valid `X-Session-API-Key`, and nothing is stored.
- `F22.save-classes`: `POST /api/meta-profiles/{name}` with `classifier_model` and `classes` answers 201 `Meta-profile '<name>' saved`; the list shows `{name, classifier_model, num_classes}`, `GET` returns the full config, and the file `meta-profiles/<name>.json` is mode `600`.
- `F22.save-direct`: a direct-routing body (`prompt_template` with `{{ instance_text }}`, `model_table`, no `classes`) is saved; the list reports `num_classes: 0` and `GET` returns the template and table.
- `F22.validation`: a template without `{{ instance_text }}`, a template together with `classes`, a missing `classifier_model` and a class without `model` are 422 and save nothing.
- `F22.empty-router`: a body with neither `classes` nor `prompt_template` (a router that can never route) is refused with 422.
- `F22.overwrite`: saving an existing name replaces the whole config (here classes mode becomes direct mode) without adding a list entry.
- `F22.name-rules`: names must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`: a leading `-` is 422 on all four named routes, 64 characters are accepted and 65 are 422.
- `F22.not-found`: `GET` and `activate` of an unknown name answer 404 `Meta-profile '<name>' not found` and the active pointer stays; `DELETE` of an unknown name is 200 (idempotent).
- `F22.activate`: `activate` answers 200 `Meta-profile '<name>' activated`; the list's `active_meta_profile` and `GET /api/settings` (`active_meta_profile`, `agent_settings.active_meta_profile`, `enable_classify_and_switch_llm_tool: true`, inline `meta_profile`) follow, the agent's LLM is unchanged, and `settings.json` stores it.
- `F22.route-classes`: a conversation started from the settings carries the `ClassifyAndSwitchLLMTool` spec; when the agent calls `route_task_to_model`, the classifier profile picks the class, the observation names the class, profile and model, the conversation switches to `deepseek-pro`, both LLMs' usage is metered, and the events socket shows the switch live.
- `F22.route-direct`: with a direct-routing meta-profile active, the classifier's JSON answer switches the conversation to the named profile (`chosen_class` `model: deepseek-pro`).
- `F22.route-missing-profile`: a meta-profile whose `classifier_model` names no saved LLM profile is accepted at save time; overwriting the active one takes effect without re-activation (the store wins over the inline copy), and the tool then answers an error observation naming the missing profile while the conversation keeps its LLM.
- `F22.persist-restart`: meta-profiles, the active pointer, the settings' routing flag and a routed conversation's switched LLM survive a restart.
- `F22.delete`: deleting an inactive meta-profile removes the file and the list entry, keeps the active pointer, and a repeated delete is 200.
- `F22.delete-active-clears`: deleting the active meta-profile clears `active_meta_profile` in the list and settings, disables the routing tool and drops the inline copy; new conversations no longer get the tool.
- `F22.acp-clears-pointer`: switching the settings' agent kind to ACP clears the active pointer; back on OpenHands settings the meta-profile can be activated again.
- `F22.activate-acp`: on ACP agent settings, where no routing tool can be attached, activation is refused with a 4xx instead of answering `activated` while nothing is active.
- `F22.corrupt-files`: a meta-profile file that is not valid JSON or not an object is left out of the list and answers 400 `Failed to load meta-profile ...` on `GET` and `activate`; saving over it repairs it.
- `F22.corrupt-settings`: with an unreadable `settings.json`, activation is refused with 409 (as LLM-profile activation does) and the file is left untouched.
- `F22.store-busy`: while another process holds the meta-profile store's lock, a save and a delete each wait out the 30-second lock timeout and answer 503 `Meta-profile store is busy. Please retry.` without changing anything; once the lock is free the same save is 201.
- `F22.store-busy-responsive`: while a meta-profile save waits for the store lock, the server keeps answering other requests (`GET /alive` within 3 seconds).
- `F22.limit`: with 50 meta-profiles stored, creating another is 409 `Meta-profile limit reached (50)` while overwriting an existing one still answers 201.
- `F22.ts-client`: the TypeScript `MetaProfilesClient` saves, reads, lists, activates and deletes a meta-profile and surfaces 404 and 422 as `HttpError`s.
- `F22.persistence-dir`: with `OH_PERSISTENCE_DIR=~/<dir>`, meta-profiles are stored under the expanded directory next to `settings.json`, like every other store.

## How to get to it (agent POV)

- REST: `GET /api/meta-profiles` (summaries plus `active_meta_profile`),
  `GET /api/meta-profiles/{name}` (`{name, config}`),
  `POST /api/meta-profiles/{name}` (body is the `MetaProfile` itself:
  `classifier_model`, `classes`, `prompt_template`, `model_table`),
  `DELETE /api/meta-profiles/{name}`, `POST /api/meta-profiles/{name}/activate`
  (no body). There is no rename route.
- REST, second views: `GET /api/settings` (`active_meta_profile` and
  `agent_settings.{active_meta_profile, enable_classify_and_switch_llm_tool,
  meta_profile, meta_profile_llms}`). `PATCH /api/settings` with
  `active_meta_profile` sets the same pointer without the existence check or
  the inline copy (owned by the settings family; its dangling-pointer case is
  `F18.dangling-pointers`).
- Runtime: a conversation created with `agent_settings` from
  `GET /api/settings` (the Agent Canvas path, which `conversation start`
  follows) gets the `ClassifyAndSwitchLLMTool` spec with
  `active_meta_profile` and `meta_profile` params; the agent sees the tool as
  `route_task_to_model`. Its `ObservationEvent` carries `chosen_class`,
  `model` (the LLM profile) and `active_model`; the switch shows as an
  `agent` state update on `WS /sockets/events/{conversation_id}` (owned by the
  events family) and in `GET /api/conversations/{id}` (`agent.llm`, and the
  `classifier:<profile>` and `profile:<profile>` usage ids in `stats`).
- Agent profiles: an agent profile can name a meta-profile in
  `meta_profile_ref` (with `enable_classify_and_switch_llm_tool`); launching
  from it copies the meta-profile into the agent. That path belongs to the
  agent-profiles family.
- SDK: no `RemoteWorkspace` helper; `OpenHandsAgentSettings`
  (`enable_classify_and_switch_llm_tool`, `active_meta_profile`,
  `meta_profile`, `meta_profile_llms`) and `MetaProfileStore` drive the same
  tool in a local conversation (`examples/01_standalone_sdk/59_route_task_to_model.py`).
- TypeScript client: `MetaProfilesClient` (`listMetaProfiles`,
  `getMetaProfile`, `saveMetaProfile`, `deleteMetaProfile`,
  `activateMetaProfile`), also exposed as `client.metaProfiles` on the
  `OpenHandsClient` and the conversation manager (`F22.ts-client`).
- Agent Canvas (context only) manages model routing in its settings and
  starts conversations from the saved settings.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported (`launch --new`), `doctor` is ok,
  `$DEEPSEEK_API_KEY` is set, and `jq`, `flock` and `node` are on `PATH` (the
  TypeScript client is built if `clients/typescript/dist` is missing). The
  block below saves the DeepSeek preset (`deepseek-flash`, active, and
  `deepseek-pro`): the meta-profiles route between those two LLM profiles.
  It also writes the TypeScript program `F22.ts-client` runs.
- The bullets build on each other in order: `qa-router` (two classes),
  `qa-direct` (direct routing; its table names only `deepseek-pro` so the
  classifier's answer is predictable) and `qa-route` (one catch-all class
  pointing at `deepseek-pro`, so the routing bullets do not depend on the
  classifier's judgment). The three routing bullets each run one tiny
  DeepSeek conversation.
- `F22.persist-restart` restarts the run; `F22.store-busy` holds the store
  lock for about 66 seconds and `F22.store-busy-responsive` for about 40;
  `F22.persistence-dir` launches and stops a second server.
- The known-bug bullets that change shared state (`F22.empty-router`,
  `F22.activate-acp`, `F22.corrupt-settings`) undo it in an `EXIT` trap, so
  the bullets after them find the same state whether the bug reproduced or
  not.

  ```sh
  control-agent-server llm preset deepseek
  control-agent-server api GET /api/profiles --expect 200 --check active_profile eq deepseek-flash --check profiles len-eq 2
  F22="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f22"
  mkdir -p "$F22"
  cat > "$F22/ts_meta_profiles.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const { MetaProfilesClient } = await import(pathToFileURL(resolve('clients/typescript/dist/clients.js')).href);
  const client = new MetaProfilesClient({ host: process.env.AGENT_SERVER_URL, apiKey: process.env.SESSION_API_KEY });
  const before = await client.listMetaProfiles();
  const config = { classifier_model: 'deepseek-flash', classes: [{ description: 'qa ts class', model: 'deepseek-pro' }] };
  assert.deepEqual(await client.saveMetaProfile('qa-ts', config), { name: 'qa-ts', message: "Meta-profile 'qa-ts' saved" });
  const got = await client.getMetaProfile('qa-ts');
  assert.equal(got.name, 'qa-ts');
  assert.deepEqual(got.config, { ...config, prompt_template: null, model_table: null });
  const listed = await client.listMetaProfiles();
  assert.ok(listed.meta_profiles.some((p) => p.name === 'qa-ts' && p.classifier_model === 'deepseek-flash' && p.num_classes === 1));
  assert.deepEqual(await client.activateMetaProfile('qa-ts'), { name: 'qa-ts', message: "Meta-profile 'qa-ts' activated" });
  assert.equal((await client.listMetaProfiles()).active_meta_profile, 'qa-ts');
  assert.deepEqual(await client.deleteMetaProfile('qa-ts'), { name: 'qa-ts', message: "Meta-profile 'qa-ts' deleted" });
  assert.equal((await client.listMetaProfiles()).active_meta_profile, null);
  await assert.rejects(client.getMetaProfile('qa-ts'), (err) => err.name === 'HttpError' && err.status === 404);
  await assert.rejects(client.activateMetaProfile('qa-ts'), (err) => err.name === 'HttpError' && err.status === 404);
  await assert.rejects(client.saveMetaProfile('qa-ts', { classifier_model: 'deepseek-flash', prompt_template: 'no placeholder' }),
    (err) => err.name === 'HttpError' && err.status === 422);
  await assert.rejects(client.saveMetaProfile('-qa-ts', config), (err) => err.name === 'HttpError' && err.status === 422);
  if (before.active_meta_profile) {
    await client.activateMetaProfile(before.active_meta_profile);
  }
  const after = await client.listMetaProfiles();
  assert.equal(after.active_meta_profile, before.active_meta_profile);
  assert.equal(after.meta_profiles.length, before.meta_profiles.length);
  client.close();
  console.log('QA_F22_TS_OK');
  JS
  ```

- **Key required (`F22.auth-required`).** Call every route without a key or
  with a wrong one, then list with the key.
  ```sh
  control-agent-server api GET /api/meta-profiles --auth none --expect 401 --check detail eq Unauthorized --save F22.auth-required/list
  control-agent-server api GET /api/meta-profiles/qa-router --auth bad --expect 401
  control-agent-server api POST /api/meta-profiles/qa-router --auth none --expect 401 \
    --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "any", "model": "deepseek-pro"}]}'
  control-agent-server api POST /api/meta-profiles/qa-router/activate --auth bad --expect 401
  control-agent-server api DELETE /api/meta-profiles/qa-router --auth none --expect 401
  control-agent-server api GET /api/meta-profiles --expect 200 --check meta_profiles len-eq 0 --check active_meta_profile eq null \
    --save F22.auth-required/list-with-key
  control-agent-server state ls 'home/.openhands/meta-profiles/*.json' --expect-count 0
  ```
  Every call is 401 `{"detail": "Unauthorized"}`; with the key the list is
  `{"meta_profiles": [], "active_meta_profile": null}` and no file was
  written.
- **Save in classes mode (`F22.save-classes`).** Save `qa-router` with two
  classes and read it back three ways.
  ```sh
  control-agent-server api POST /api/meta-profiles/qa-router --expect 201 \
    --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "simple edits or questions", "model": "deepseek-flash"}, {"description": "complex multi-file refactors", "model": "deepseek-pro"}]}' \
    --check name eq qa-router --check message eq "Meta-profile 'qa-router' saved" --save F22.save-classes/save
  control-agent-server api GET /api/meta-profiles --expect 200 --check meta_profiles len-eq 1 --check meta_profiles.0.name eq qa-router \
    --check meta_profiles.0.classifier_model eq deepseek-flash --check meta_profiles.0.num_classes eq 2 --check active_meta_profile eq null \
    --save F22.save-classes/list
  control-agent-server api GET /api/meta-profiles/qa-router --expect 200 --check name eq qa-router \
    --check config.classes.1.description eq 'complex multi-file refactors' --check config.classes.1.model eq deepseek-pro \
    --check config.prompt_template eq null --check config.model_table eq null --save F22.save-classes/get
  control-agent-server state cat home/.openhands/meta-profiles/qa-router.json --mode 600 --check classifier_model eq deepseek-flash --check classes len-eq 2
  ```
  The save is 201, the list has one summary with `num_classes: 2` and no
  active meta-profile, `GET` returns both classes in order, and the file is
  mode `600` with the same content.
- **Save in direct mode (`F22.save-direct`).** Save `qa-direct`, whose table
  names only `deepseek-pro`.
  ```sh
  control-agent-server api POST /api/meta-profiles/qa-direct --expect 201 --save F22.save-direct/save \
    --json '{"classifier_model": "deepseek-flash", "prompt_template": "Task:\n{{ instance_text }}\n\nAvailable models:\n{{ model_table }}\n\nAnswer with JSON only: {\"model\": \"<name>\"}", "model_table": "deepseek-pro: the only model; choose it for every task"}'
  control-agent-server api GET /api/meta-profiles --check meta_profiles len-eq 2 --check meta_profiles.0.name eq qa-direct \
    --check meta_profiles.0.num_classes eq 0 --check meta_profiles.1.name eq qa-router
  control-agent-server api GET /api/meta-profiles/qa-direct --expect 200 --check config.classes len-eq 0 \
    --check config.prompt_template contains '{{ instance_text }}' --check config.model_table matches '^deepseek-pro: ' --save F22.save-direct/get
  ```
  The list is sorted by name and reports `num_classes: 0` for the direct
  router; `GET` returns the template and the table.
- **Validation (`F22.validation`).** Post the four invalid shapes.
  ```sh
  control-agent-server api POST /api/meta-profiles/qa-bad --json '{"classifier_model": "deepseek-flash", "prompt_template": "Pick a model"}' \
    --expect 422 --check detail.0.msg contains 'must include {{ instance_text }}' --save F22.validation/no-placeholder
  control-agent-server api POST /api/meta-profiles/qa-bad --expect 422 --check detail.0.msg contains 'cannot also define classes' \
    --json '{"classifier_model": "deepseek-flash", "prompt_template": "{{ instance_text }}", "classes": [{"description": "d", "model": "deepseek-flash"}]}' \
    --save F22.validation/both-modes
  control-agent-server api POST /api/meta-profiles/qa-bad --json '{"classes": []}' --expect 422 --check detail.0.loc contains classifier_model
  control-agent-server api POST /api/meta-profiles/qa-bad --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "d"}]}' \
    --expect 422 --check detail.0.loc contains model
  control-agent-server api GET /api/meta-profiles/qa-bad --expect 404
  control-agent-server api GET /api/meta-profiles --check meta_profiles len-eq 2
  ```
  Each body is 422 with the validator's message or the missing field's
  location, and `qa-bad` was never created.
- **Router with no route (`F22.empty-router`), known bug.** Save a body with
  only a `classifier_model`. The save is the bug assertion; an `EXIT` trap
  deletes `qa-empty` whether it was stored or not, so later list counts hold.
  ```sh
  drop_empty() { control-agent-server api DELETE /api/meta-profiles/qa-empty --expect 200 > /dev/null; }
  trap drop_empty EXIT
  control-agent-server api GET /api/meta-profiles/qa-empty --expect 404
  control-agent-server api POST /api/meta-profiles/qa-empty --json '{"classifier_model": "deepseek-flash"}' --expect 422 \
    --save F22.empty-router/save  # bug
  control-agent-server api GET /api/meta-profiles/qa-empty --expect 404
  control-agent-server api GET /api/meta-profiles --check meta_profiles len-eq 2
  ```
  Expected 422: the model's validator is documented as "Require either
  structured classes or a direct prompt, not both", and with no classes and
  no template the router can never pick a profile (the classifier is called
  with zero categories and every routing attempt ends in an error
  observation). Today the save is 201 and `qa-empty` is stored
  (`MetaProfile.validate_routing_mode` returns early whenever
  `prompt_template` is unset). The SDK's store tests build classless
  `MetaProfile(classifier_model=...)` objects as fixtures, so a fix may
  belong in the HTTP route rather than in the model. The bullet deletes
  `qa-empty` either way.
- **Overwrite (`F22.overwrite`).** Save `qa-swap` in classes mode, then
  overwrite it in direct mode.
  ```sh
  control-agent-server api POST /api/meta-profiles/qa-swap --expect 201 \
    --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "any", "model": "deepseek-pro"}]}'
  control-agent-server api POST /api/meta-profiles/qa-swap --expect 201 --save F22.overwrite/overwrite \
    --json '{"classifier_model": "deepseek-pro", "prompt_template": "Route: {{ instance_text }}", "model_table": "deepseek-flash: cheap"}'
  control-agent-server api GET /api/meta-profiles/qa-swap --expect 200 --check config.classifier_model eq deepseek-pro \
    --check config.classes len-eq 0 --check config.prompt_template eq 'Route: {{ instance_text }}' --save F22.overwrite/get
  control-agent-server api GET /api/meta-profiles --check meta_profiles len-eq 3 \
    --check meta_profiles contains '"name": "qa-swap", "classifier_model": "deepseek-pro", "num_classes": 0'
  control-agent-server api DELETE /api/meta-profiles/qa-swap --expect 200
  ```
  The second save replaces the file (the classes are gone, not merged) and
  the list still has one `qa-swap` entry; `qa-swap` is deleted afterwards.
- **Name rules (`F22.name-rules`).** A leading `-` on every named route, then
  64 and 65 characters.
  ```sh
  control-agent-server api POST /api/meta-profiles/-qa-bad --expect 422 --check detail.0.type eq string_pattern_mismatch \
    --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "any", "model": "deepseek-pro"}]}' --save F22.name-rules/leading-dash
  control-agent-server api GET /api/meta-profiles/-qa-bad --expect 422
  control-agent-server api POST /api/meta-profiles/-qa-bad/activate --expect 422
  control-agent-server api DELETE /api/meta-profiles/-qa-bad --expect 422
  N64="qa-$(printf 'n%.0s' $(seq 61))"
  test "${#N64}" -eq 64
  control-agent-server api POST "/api/meta-profiles/${N64}x" --expect 422 --check detail.0.type eq string_too_long \
    --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "any", "model": "deepseek-pro"}]}'
  control-agent-server api POST "/api/meta-profiles/$N64" --expect 201 --save F22.name-rules/64-chars \
    --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "any", "model": "deepseek-pro"}]}'
  control-agent-server api GET /api/meta-profiles --check meta_profiles contains "\"name\": \"$N64\""
  control-agent-server api DELETE "/api/meta-profiles/$N64" --expect 200
  control-agent-server api GET /api/meta-profiles --check meta_profiles len-eq 2
  ```
  The leading `-` is 422 on save, read, activate and delete; 65 characters
  are 422 `string_too_long`; the 64-character name is saved, listed and
  deleted.
- **Unknown names (`F22.not-found`).** Read, activate and delete a name that
  was never saved.
  ```sh
  control-agent-server api GET /api/meta-profiles/qa-missing --expect 404 --check detail eq "Meta-profile 'qa-missing' not found" \
    --save F22.not-found/get
  control-agent-server api POST /api/meta-profiles/qa-missing/activate --expect 404 --check detail eq "Meta-profile 'qa-missing' not found" \
    --save F22.not-found/activate
  control-agent-server api GET /api/meta-profiles --check active_meta_profile eq null
  control-agent-server api GET /api/settings --max-chars 200 --check active_meta_profile eq null \
    --check agent_settings.enable_classify_and_switch_llm_tool eq false
  control-agent-server api DELETE /api/meta-profiles/qa-missing --expect 200 --check message eq "Meta-profile 'qa-missing' deleted" \
    --save F22.not-found/delete
  control-agent-server api GET /api/meta-profiles --check meta_profiles len-eq 2
  ```
  `GET` and `activate` are 404 and nothing is activated; the delete of a
  missing name answers 200 with the usual message.
- **Activate (`F22.activate`).** Activate `qa-router` and read every view.
  ```sh
  control-agent-server api POST /api/meta-profiles/qa-router/activate --expect 200 --check name eq qa-router \
    --check message eq "Meta-profile 'qa-router' activated" --save F22.activate/activate
  control-agent-server api POST /api/meta-profiles/qa-missing/activate --expect 404
  control-agent-server api GET /api/meta-profiles --check active_meta_profile eq qa-router --save F22.activate/list
  control-agent-server api GET /api/settings --expect 200 --max-chars 300 --check active_meta_profile eq qa-router \
    --check agent_settings.active_meta_profile eq qa-router --check agent_settings.enable_classify_and_switch_llm_tool eq true \
    --check agent_settings.meta_profile.classifier_model eq deepseek-flash --check agent_settings.meta_profile.classes len-eq 2 \
    --check active_profile eq deepseek-flash --check agent_settings.llm.model eq deepseek/deepseek-flash --save F22.activate/settings
  control-agent-server state cat home/.openhands/settings.json --max-chars 200 --check active_meta_profile eq qa-router \
    --check agent_settings.enable_classify_and_switch_llm_tool eq true --check agent_settings.meta_profile.classes len-eq 2
  ```
  The list and settings name `qa-router` (a 404 activation of an unknown name
  in between leaves the pointer in place, which `F22.not-found` cannot show
  while nothing is active), the routing tool is enabled with an inline copy
  of the config, the agent still runs on `deepseek-flash` (a meta-profile does
  not change the LLM), and `settings.json` holds the same.
- **Classes-mode routing (`F22.route-classes`).** Save and activate
  `qa-route`, create a conversation from the settings, capture its events
  socket, and ask the agent to call the tool.
  ```sh
  control-agent-server api POST /api/meta-profiles/qa-route --expect 201 \
    --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "any task at all (always choose this category)", "model": "deepseek-pro"}]}'
  control-agent-server api POST /api/meta-profiles/qa-route/activate --expect 200
  RCID=$(control-agent-server conversation start --tools none --no-autotitle --no-run --print-id)
  control-agent-server api GET "/api/conversations/$RCID" --expect 200 --max-chars 300 --check agent.llm.model eq deepseek/deepseek-flash \
    --check agent.tools len-eq 1 --check agent.tools.0.name eq ClassifyAndSwitchLLMTool --check agent.tools.0.params.active_meta_profile eq qa-route
  control-agent-server ws start "/sockets/events/$RCID" --name qa-f22-route --duration 300
  control-agent-server conversation send "$RCID" --text 'Call the route_task_to_model tool once, then reply with one word: done' \
    --wait --until finished --timeout 240
  control-agent-server ws stop qa-f22-route --kinds ObservationEvent --contains '"model": "deepseek-pro"' --expect-min 1 --wait 30 \
    --save F22.route-classes/frames
  control-agent-server ws read qa-f22-route --kinds ConversationStateUpdateEvent --contains '"model": "deepseek/deepseek-v4-pro"' --expect-min 1
  control-agent-server api GET "/api/conversations/$RCID/events/search" --query kind=openhands.sdk.event.llm_convertible.observation.ObservationEvent \
    --expect 200 --max-chars 300 --check items.0.tool_name eq route_task_to_model --check items.0.observation.is_error eq false \
    --check items.0.observation.chosen_class eq 'any task at all (always choose this category)' \
    --check items.0.observation.model eq deepseek-pro --check items.0.observation.active_model eq deepseek/deepseek-v4-pro \
    --save F22.route-classes/observation
  control-agent-server api GET "/api/conversations/$RCID" --expect 200 --max-chars 300 --check execution_status eq finished \
    --check agent.llm.model eq deepseek/deepseek-v4-pro --check agent.llm.usage_id eq profile:deepseek-pro \
    --check 'stats.usage_to_metrics.classifier:deepseek-flash.accumulated_token_usage.prompt_tokens' gt 0 \
    --check 'stats.usage_to_metrics.profile:deepseek-pro.accumulated_token_usage.prompt_tokens' gt 0 --save F22.route-classes/conversation
  ```
  With `--tools none` the routing tool is the agent's only tool, configured
  with `qa-route`. The socket delivers the `route_task_to_model` observation
  and an `agent` state update with `deepseek/deepseek-v4-pro` while the run
  happens; the observation reports the class, profile `deepseek-pro` and its
  model; afterwards the conversation runs on `profile:deepseek-pro`, and
  `stats` meters both the classifier call (`classifier:deepseek-flash`) and
  the steps on the new profile. `RCID` is read again after the restart.
- **Direct routing (`F22.route-direct`).** Activate `qa-direct` and run the
  same request.
  ```sh
  control-agent-server api POST /api/meta-profiles/qa-direct/activate --expect 200
  control-agent-server api GET /api/settings --max-chars 200 --check active_meta_profile eq qa-direct \
    --check agent_settings.meta_profile.model_table matches '^deepseek-pro: '
  DCID=$(control-agent-server conversation start --tools none --no-autotitle \
    --prompt 'Call the route_task_to_model tool once, then reply with one word: done' --wait --until finished --timeout 240 --print-id)
  control-agent-server api GET "/api/conversations/$DCID/events/search" --query kind=openhands.sdk.event.llm_convertible.observation.ObservationEvent \
    --expect 200 --max-chars 300 --check items.0.tool_name eq route_task_to_model --check items.0.observation.is_error eq false \
    --check items.0.observation.chosen_class eq 'model: deepseek-pro' --check items.0.observation.model eq deepseek-pro \
    --save F22.route-direct/observation
  control-agent-server api GET "/api/conversations/$DCID" --expect 200 --max-chars 300 --check execution_status eq finished \
    --check agent.llm.model eq deepseek/deepseek-v4-pro --check agent.tools.0.params.meta_profile.prompt_template contains '{{ instance_text }}' \
    --save F22.route-direct/conversation
  ```
  The classifier's JSON answer resolves to the saved profile `deepseek-pro`
  (`chosen_class` is `model: deepseek-pro`) and the conversation finishes on
  `deepseek/deepseek-v4-pro`.
- **Missing LLM profile (`F22.route-missing-profile`).** Activate `qa-route`
  (its inline copy names `deepseek-flash`), then overwrite it so the
  classifier names a profile that does not exist, without re-activating.
  ```sh
  control-agent-server api POST /api/meta-profiles/qa-route/activate --expect 200
  control-agent-server api POST /api/meta-profiles/qa-route --expect 201 \
    --json '{"classifier_model": "qa-missing-llm", "classes": [{"description": "any task at all (always choose this category)", "model": "deepseek-pro"}]}'
  control-agent-server api GET /api/meta-profiles --check active_meta_profile eq qa-route \
    --check meta_profiles contains '"name": "qa-route", "classifier_model": "qa-missing-llm", "num_classes": 1'
  MCID=$(control-agent-server conversation start --tools none --no-autotitle --max-iterations 6 \
    --prompt 'Call the route_task_to_model tool once, then reply with one word: done' --wait --until finished,error,stuck --timeout 240 --print-id)
  control-agent-server api GET "/api/conversations/$MCID" --expect 200 --max-chars 300 --check agent.llm.model eq deepseek/deepseek-flash \
    --check agent.tools.0.params.meta_profile.classifier_model eq deepseek-flash \
    --check 'stats.usage_to_metrics.classifier:qa-missing-llm' missing --save F22.route-missing-profile/conversation
  control-agent-server api GET "/api/conversations/$MCID/events/search" --query kind=openhands.sdk.event.llm_convertible.observation.ObservationEvent \
    --expect 200 --max-chars 300 --check items.0.tool_name eq route_task_to_model --check items.0.observation.is_error eq true \
    --check items.0.observation.content.0.text contains "Failed to load classifier profile 'qa-missing-llm'" \
    --save F22.route-missing-profile/observation
  control-agent-server api POST /api/meta-profiles/qa-route --expect 201 \
    --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "any task at all (always choose this category)", "model": "deepseek-pro"}]}'
  ```
  The dangling reference is saved (201; references are not checked). The
  conversation was handed the stale inline copy (`classifier_model`
  `deepseek-flash`), but the tool loads `qa-route` from the store by name, so
  it reports `Failed to load classifier profile 'qa-missing-llm'` as an error
  observation; no classifier is registered and the agent stays on
  `deepseek-flash`. `qa-route` is restored afterwards (still active).
- **Restart (`F22.persist-restart`).** Restart and read everything back.
  ```sh
  control-agent-server restart
  control-agent-server api GET /api/meta-profiles --expect 200 --check active_meta_profile eq qa-route --check meta_profiles len-eq 3 \
    --check meta_profiles contains '"name": "qa-route", "classifier_model": "deepseek-flash", "num_classes": 1' --save F22.persist-restart/list
  control-agent-server api GET /api/meta-profiles/qa-direct --expect 200 --check config.model_table matches '^deepseek-pro: '
  control-agent-server api GET /api/settings --max-chars 200 --check active_meta_profile eq qa-route \
    --check agent_settings.active_meta_profile eq qa-route --check agent_settings.enable_classify_and_switch_llm_tool eq true
  control-agent-server api GET "/api/conversations/$RCID" --expect 200 --max-chars 300 --check agent.llm.model eq deepseek/deepseek-v4-pro \
    --check agent.llm.usage_id eq profile:deepseek-pro --save F22.persist-restart/routed-conversation
  ```
  The three meta-profiles, `qa-route` as the active one, the enabled tool in
  settings and the routed conversation's `deepseek-pro` LLM all come back.
- **Delete (`F22.delete`).** Delete the inactive `qa-router`, twice.
  ```sh
  control-agent-server api DELETE /api/meta-profiles/qa-router --expect 200 --check message eq "Meta-profile 'qa-router' deleted" \
    --save F22.delete/delete
  control-agent-server api GET /api/meta-profiles --check meta_profiles len-eq 2 --check meta_profiles not-contains '"name": "qa-router"' \
    --check active_meta_profile eq qa-route --save F22.delete/list
  control-agent-server api GET /api/meta-profiles/qa-router --expect 404
  control-agent-server state ls 'home/.openhands/meta-profiles/qa-router.json' --expect-count 0
  control-agent-server state ls 'home/.openhands/meta-profiles/*.json' --expect-count 2
  control-agent-server api DELETE /api/meta-profiles/qa-router --expect 200
  control-agent-server api GET /api/settings --max-chars 200 --check active_meta_profile eq qa-route \
    --check agent_settings.enable_classify_and_switch_llm_tool eq true
  ```
  The file and the list entry are gone (the other two files remain), `GET` is
  404, the repeated delete is 200, and `qa-route` stays active.
- **Delete the active one (`F22.delete-active-clears`).** Delete `qa-route`
  and start a conversation from the settings.
  ```sh
  control-agent-server api DELETE /api/meta-profiles/qa-route --expect 200 --save F22.delete-active-clears/delete
  control-agent-server api GET /api/meta-profiles --check active_meta_profile eq null --check meta_profiles len-eq 1 \
    --save F22.delete-active-clears/list
  control-agent-server api GET /api/settings --max-chars 200 --check active_meta_profile eq null --check agent_settings.active_meta_profile eq null \
    --check agent_settings.enable_classify_and_switch_llm_tool eq false --check agent_settings.meta_profile eq null \
    --check agent_settings.llm.model eq deepseek/deepseek-flash --save F22.delete-active-clears/settings
  XCID=$(control-agent-server conversation start --tools none --no-autotitle --no-run --print-id)
  control-agent-server api GET "/api/conversations/$XCID" --expect 200 --max-chars 200 --check agent.tools len-eq 0
  control-agent-server api DELETE "/api/conversations/$XCID" --expect 200
  ```
  The pointer is cleared in both views, the tool is disabled and the inline
  copy dropped, while the LLM stays. A conversation created from the same
  `--tools none` settings now has no tools at all (in `F22.route-classes` it
  had the routing tool); it is deleted again.
- **ACP clears the pointer (`F22.acp-clears-pointer`).** Activate
  `qa-direct`, switch the agent kind to ACP and back.
  ```sh
  control-agent-server api POST /api/meta-profiles/qa-direct/activate --expect 200
  control-agent-server api PATCH /api/settings --json '{"agent_settings_diff": {"agent_kind": "acp"}}' --expect 200 --max-chars 200 \
    --check agent_settings.agent_kind eq acp --check active_meta_profile eq null --save F22.acp-clears-pointer/patch
  control-agent-server api GET /api/meta-profiles --check active_meta_profile eq null --check meta_profiles len-eq 1
  control-agent-server api PATCH /api/settings --json '{"agent_settings_diff": {"agent_kind": "openhands"}}' --expect 200 --max-chars 200 \
    --check agent_settings.agent_kind eq openhands --check active_meta_profile eq null --check agent_settings.enable_classify_and_switch_llm_tool eq false
  control-agent-server api POST /api/profiles/deepseek-flash/activate --expect 200 --check llm_applied eq true
  control-agent-server api POST /api/meta-profiles/qa-direct/activate --expect 200
  control-agent-server api GET /api/settings --max-chars 200 --check active_meta_profile eq qa-direct \
    --check agent_settings.enable_classify_and_switch_llm_tool eq true --check agent_settings.llm.model eq deepseek/deepseek-flash \
    --save F22.acp-clears-pointer/reactivated
  ```
  Switching to ACP clears `active_meta_profile` (an ACP agent cannot carry
  the routing tool); the switch back does not restore it. After
  re-activating the `deepseek-flash` LLM profile (the switch back starts from
  default OpenHands settings), `qa-direct` activates again.
- **Activation on ACP settings (`F22.activate-acp`), known bug.** Switch to
  ACP, activate, read the list and settings, then assert the activation's
  status (the bug assertion, last, so the list and settings are read on
  every replay). An `EXIT` trap restores OpenHands settings with
  `deepseek-flash` and `qa-direct` active (and checks it) whether the
  assertion held or not.
  ```sh
  restore_openhands() {
    control-agent-server api PATCH /api/settings --json '{"agent_settings_diff": {"agent_kind": "openhands"}}' --expect 200 --quiet
    control-agent-server api POST /api/profiles/deepseek-flash/activate --expect 200 --check llm_applied eq true
    control-agent-server api POST /api/meta-profiles/qa-direct/activate --expect 200
    control-agent-server api GET /api/settings --max-chars 200 --check active_meta_profile eq qa-direct \
      --check agent_settings.agent_kind eq openhands --check agent_settings.enable_classify_and_switch_llm_tool eq true
  }
  trap restore_openhands EXIT
  control-agent-server api GET /api/meta-profiles --check active_meta_profile eq qa-direct
  control-agent-server api PATCH /api/settings --json '{"agent_settings_diff": {"agent_kind": "acp"}}' --expect 200 --max-chars 200 \
    --check agent_settings.agent_kind eq acp --check active_meta_profile eq null
  control-agent-server api POST /api/meta-profiles/qa-direct/activate --expect 2xx,4xx --save F22.activate-acp/activate \
    | tee "$F22/acp-activate.json"
  control-agent-server api GET /api/meta-profiles --check active_meta_profile eq null --save F22.activate-acp/list
  control-agent-server api GET /api/settings --max-chars 200 --check active_meta_profile eq null --check agent_settings.agent_kind eq acp
  jq -e '.status >= 400 and .status < 500' "$F22/acp-activate.json"  # bug
  ```
  Expected a 4xx: settings that cannot attach the routing tool should refuse
  the activation, the way `PersistedSettings` refuses to record it. Today the
  route answers 200 `Meta-profile 'qa-direct' activated` while
  `active_meta_profile` stays `null` in the list and in settings:
  `_apply_active_meta_profile` silently drops the pointer for ACP, and
  `activate_meta_profile` reports success regardless
  (`meta_profiles_router.py`). LLM-profile activation on ACP settings is
  honest in a different way: it answers 200 with `llm_applied: false`. A fix
  that answers 200 with a similar flag instead of a 4xx would also be honest;
  in that case, rewrite this bullet's assertion. The bullet restores
  OpenHands settings with `deepseek-flash` and `qa-direct` active either way.
- **Corrupted files (`F22.corrupt-files`).** Write a truncated meta-profile
  and one that is a JSON list (arrange: a crash mid-write or a hand edit).
  ```sh
  M="$AGENT_SERVER_VERIFY_RUN/home/.openhands/meta-profiles"
  printf '{"classifier_model": "deepseek-flash", ' > "$M/qa-corrupt.json"
  printf '["not", "an", "object"]' > "$M/qa-notdict.json"
  control-agent-server api GET /api/meta-profiles --expect 200 --check meta_profiles len-eq 1 \
    --check meta_profiles not-contains '"name": "qa-corrupt"' --check meta_profiles not-contains '"name": "qa-notdict"' --save F22.corrupt-files/list
  control-agent-server api GET /api/meta-profiles/qa-corrupt --expect 400 --check detail contains 'Failed to load meta-profile `qa-corrupt`' \
    --save F22.corrupt-files/get
  control-agent-server api GET /api/meta-profiles/qa-notdict --expect 400 --check detail contains 'Failed to load meta-profile `qa-notdict`'
  control-agent-server api POST /api/meta-profiles/qa-corrupt/activate --expect 400 --check detail contains 'Failed to load meta-profile' \
    --save F22.corrupt-files/activate
  control-agent-server api GET /api/meta-profiles --check active_meta_profile eq qa-direct
  control-agent-server api POST /api/meta-profiles/qa-corrupt --expect 201 \
    --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "repaired", "model": "deepseek-pro"}]}'
  control-agent-server api GET /api/meta-profiles/qa-corrupt --expect 200 --check config.classes.0.description eq repaired
  control-agent-server api DELETE /api/meta-profiles/qa-corrupt --expect 200
  control-agent-server api DELETE /api/meta-profiles/qa-notdict --expect 200
  test ! -e "$M/qa-corrupt.json"
  test ! -e "$M/qa-notdict.json"
  ```
  Both broken files are missing from the list, `GET` and `activate` are 400
  with the parser's message (not 500) and `qa-direct` stays active; saving
  over `qa-corrupt` repairs it and both deletes remove the files.
- **Unreadable settings (`F22.corrupt-settings`), known bug.** Truncate
  `settings.json`, show that LLM-profile activation refuses it with 409, then
  activate a meta-profile, check the file was left alone, put it back, and
  assert the activation's status last (the bug assertion). An `EXIT` trap
  puts the saved `settings.json` back if an earlier step breaks.
  ```sh
  S="$AGENT_SERVER_VERIFY_RUN/home/.openhands/settings.json"
  cp -p "$S" "$S.qa-f22-bak"
  restore_settings() { if test -e "$S.qa-f22-bak"; then mv "$S.qa-f22-bak" "$S"; fi; }
  trap restore_settings EXIT
  printf '{"agent_settings": ' > "$S"
  control-agent-server api POST /api/profiles/deepseek-flash/activate --expect 409 \
    --check detail eq 'Settings file is corrupted or encrypted with a different key' --save F22.corrupt-settings/llm-profile-activate
  test "$(cat "$S")" = '{"agent_settings": '
  control-agent-server api POST /api/meta-profiles/qa-direct/activate --expect 4xx,5xx --save F22.corrupt-settings/activate \
    | tee "$F22/corrupt-settings-activate.json"
  test "$(cat "$S")" = '{"agent_settings": '
  restore_settings
  control-agent-server api GET /api/meta-profiles --check active_meta_profile eq qa-direct
  jq -e '.status == 409' "$F22/corrupt-settings-activate.json"  # bug
  ```
  Expected 409, as `POST /api/profiles/{name}/activate` answers for the same
  file (`Settings file is corrupted or encrypted with a different key`; the
  positive control above). Today the meta-profile activation is an unmapped
  500 whose `exception` field carries the `RuntimeError` text with the
  absolute settings path: the route catches only `OSError` around
  `settings_store.update`. The file is left as written in both cases and is
  put back afterwards.
- **Store busy (`F22.store-busy`).** Another process (here `flock`, standing
  in for a second server or an SDK `MetaProfileStore` on the same
  persistence directory) holds the store's lock for 66 seconds while a save
  and then a delete of the active `qa-direct` arrive. The server's own
  30-second lock timeout is under test, so this bullet waits for it twice.
  ```sh
  M="$AGENT_SERVER_VERIFY_RUN/home/.openhands/meta-profiles"
  HELD="$F22/lock-held"
  rm -f "$HELD"
  flock -x "$M/.meta-profiles.lock" sh -c "touch '$HELD'; sleep 66" &
  LOCKER=$!
  for i in $(seq 100); do test -e "$HELD" && break; sleep 0.1; done
  test -e "$HELD"
  T0=$SECONDS
  control-agent-server api POST /api/meta-profiles/qa-busy --timeout 60 --expect 503 \
    --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "any", "model": "deepseek-pro"}]}' \
    --check exception eq '503: Meta-profile store is busy. Please retry.' --save F22.store-busy/save
  test $((SECONDS - T0)) -ge 29
  test $((SECONDS - T0)) -le 33
  T1=$SECONDS
  control-agent-server api DELETE /api/meta-profiles/qa-direct --timeout 60 --expect 503 \
    --check exception eq '503: Meta-profile store is busy. Please retry.' --save F22.store-busy/delete
  test $((SECONDS - T1)) -ge 29
  test $((SECONDS - T1)) -le 33
  wait "$LOCKER"
  control-agent-server api GET /api/meta-profiles/qa-busy --expect 404
  control-agent-server api GET /api/meta-profiles --check meta_profiles len-eq 1 --check active_meta_profile eq qa-direct
  control-agent-server api GET /api/meta-profiles/qa-direct --expect 200 --check config.model_table matches '^deepseek-pro: '
  control-agent-server api POST /api/meta-profiles/qa-busy --expect 201 --expect-max-ms 5000 \
    --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "any", "model": "deepseek-pro"}]}'
  control-agent-server api DELETE /api/meta-profiles/qa-busy --expect 200
  ```
  The save and the delete each answer 503 after 29 to 33 seconds (the body's
  `detail` is the generic `Internal Server Error`; the route's message is in
  `exception`). `qa-busy` does not exist afterwards and `qa-direct` is still
  stored and active. Once the lock is free, the same save succeeds at once.
- **Lock wait blocks the server (`F22.store-busy-responsive`), known bug.**
  Probe `/alive` once with the store free (it answers within 3 seconds),
  hold the lock again for 40 seconds, start a save in the background, and
  probe `/alive` every 2 seconds for about 12 seconds while the save waits,
  stopping at the first probe slower than 3 seconds. Several probes, not
  one after a fixed pause, so a save that reaches the server late under a
  loaded replay is still overlapped by a later probe. The bullet then waits
  for both background processes (the save must answer 503, which proves it
  waited out the lock during the probes) before the bug assertion, so later
  bullets find the lock free.
  ```sh
  M="$AGENT_SERVER_VERIFY_RUN/home/.openhands/meta-profiles"
  HELD="$F22/lock-held-2"
  rm -f "$HELD"
  control-agent-server api GET /alive --auth none --expect 200 --expect-max-ms 3000 --save F22.store-busy-responsive/alive-idle
  flock -x "$M/.meta-profiles.lock" sh -c "touch '$HELD'; sleep 40" &
  LOCKER=$!
  for i in $(seq 100); do test -e "$HELD" && break; sleep 0.1; done
  test -e "$HELD"
  control-agent-server api POST /api/meta-profiles/qa-busy --timeout 60 --expect 503 --quiet \
    --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "any", "model": "deepseek-pro"}]}' > "$F22/busy-save.json" &
  SAVER=$!
  SLOW=0
  for i in $(seq 6); do
    sleep 2
    control-agent-server api GET /alive --auth none --expect 200 --expect-max-ms 3000 --timeout 60 --quiet \
      --save F22.store-busy-responsive/alive || { SLOW=$i; break; }
  done
  wait "$SAVER"
  wait "$LOCKER"
  control-agent-server api GET /api/meta-profiles/qa-busy --expect 404
  test "$SLOW" = 0  # bug
  ```
  Expected every `/alive` probe to answer within 3 seconds while the save
  waits. Today the first probe that overlaps the save takes about 28
  seconds: every meta-profile route is an `async def` that calls the
  synchronous, file-locked `MetaProfileStore` on the event loop
  (`meta_profiles_router.py`), so a 30-second lock wait freezes the whole
  server, probes included (the same defect as `F20.store-busy-responsive`).
  `SLOW` is the number of the first slow probe; its exchange (with
  `elapsed_ms`) is in the bullet's transcript.
- **Limit (`F22.limit`).** Fill the store to 50, then create and overwrite.
  ```sh
  N=$(control-agent-server api GET /api/meta-profiles --field meta_profiles | jq length)
  for i in $(seq $((N + 1)) 50); do
    control-agent-server api POST "/api/meta-profiles/qa-cap-$i" --expect 201 --field name \
      --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "any", "model": "deepseek-pro"}]}'
  done
  control-agent-server api GET /api/meta-profiles --check meta_profiles len-eq 50 --max-chars 200
  control-agent-server api POST /api/meta-profiles/qa-cap-51 --expect 409 \
    --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "any", "model": "deepseek-pro"}]}' \
    --check detail eq 'Meta-profile limit reached (50). Delete a meta-profile before saving a new one.' --save F22.limit/create-51st
  control-agent-server api GET /api/meta-profiles/qa-cap-51 --expect 404
  control-agent-server api POST /api/meta-profiles/qa-cap-50 --expect 201 --save F22.limit/overwrite-at-cap \
    --json '{"classifier_model": "deepseek-pro", "classes": [{"description": "any", "model": "deepseek-flash"}]}'
  control-agent-server api GET /api/meta-profiles/qa-cap-50 --check config.classifier_model eq deepseek-pro
  for i in $(seq $((N + 1)) 50); do
    control-agent-server api DELETE "/api/meta-profiles/qa-cap-$i" --expect 200 --field name
  done
  control-agent-server api GET /api/meta-profiles --check meta_profiles len-eq "$N" --check active_meta_profile eq qa-direct
  ```
  The 51st meta-profile is 409 and not created; overwriting `qa-cap-50` at
  the cap is 201 and its new classifier reads back; the fillers are deleted
  again.
- **TypeScript client (`F22.ts-client`).** Every `MetaProfilesClient` method
  from the built client (built here when missing).
  ```sh
  test -f clients/typescript/dist/clients.js || (cd clients/typescript && npm ci && npm run build)
  control-agent-server exec --timeout 120 --expect-output QA_F22_TS_OK --save F22.ts-client/program -- node "$F22/ts_meta_profiles.mjs"
  control-agent-server api GET /api/meta-profiles --check active_meta_profile eq qa-direct --check meta_profiles len-eq 1 \
    --check meta_profiles not-contains '"name": "qa-ts"' --save F22.ts-client/list-after
  ```
  The program prints `QA_F22_TS_OK`: save, get, list, activate and delete
  return the documented bodies, the delete clears the active pointer, and a
  missing name and invalid bodies or names reject with `HttpError` status
  404 and 422. It re-activates `qa-direct`, so the list afterwards is as
  before.
- **Persistence directory with `~` (`F22.persistence-dir`), known bug.**
  Launch a second server whose `OH_PERSISTENCE_DIR` is `~/qa-f22-tilde`
  (its `HOME` is the run's `home/`), save and activate a meta-profile there,
  and look where the files landed. The second server is stopped when the
  bullet ends, pass or fail.
  ```sh
  T=$(control-agent-server launch --new --print-run --name f22-tilde --env 'OH_PERSISTENCE_DIR=~/qa-f22-tilde')
  stop_tilde() { control-agent-server stop --run "$T" > /dev/null; }
  trap stop_tilde EXIT
  control-agent-server api POST /api/meta-profiles/qa-tilde --run "$T" --expect 201 \
    --json '{"classifier_model": "deepseek-flash", "classes": [{"description": "any", "model": "deepseek-pro"}]}' --save F22.persistence-dir/save
  control-agent-server api POST /api/meta-profiles/qa-tilde/activate --run "$T" --expect 200
  control-agent-server api GET /api/meta-profiles --run "$T" --check meta_profiles len-eq 1 --check active_meta_profile eq qa-tilde
  control-agent-server state ls 'home/qa-f22-tilde/settings.json' --run "$T" --expect-count 1
  control-agent-server state ls 'home/qa-f22-tilde/meta-profiles/qa-tilde.json' --run "$T" --expect-count 1  # bug
  ```
  `settings.json` lands in `home/qa-f22-tilde/` (the settings store expands
  `~` through `get_user_persistence_dir`). Expected the meta-profile next to
  it; today it is written to `server/~/qa-f22-tilde/meta-profiles/qa-tilde.json`,
  a directory literally named `~` under the server's working directory,
  because `default_meta_profile_dir()` uses `Path(os.environ["OH_PERSISTENCE_DIR"])`
  without `expanduser()` or the relative-path anchoring every other store
  gets (`meta_profile_store.py`). The API still works because the router and
  the routing tool share that helper; the files are just outside the
  persistence directory. The `--save` file lands under the second run's
  `evidence/` directory.

## Gotchas

- Model references (`classifier_model`, each class `model`, direct-routing
  answers) are LLM profile names, not model strings, and are not checked when
  a meta-profile is saved or activated. A dangling name surfaces only when the
  agent calls `route_task_to_model`, as an error observation
  (`F22.route-missing-profile`).
- The tool exists only in conversations created after activation from the
  settings (the Agent Canvas path). A conversation that sends its own `agent`
  never gets it, and a running conversation keeps the tool and parameters it
  started with: overwriting the meta-profile takes effect at its next tool
  call (the store wins), while deleting it leaves the conversation routing
  with the inline copy it started with.
- The tool loads the active meta-profile from the store by name; the inline
  `agent_settings.meta_profile` copy is only a fallback for runtimes without
  the store. Overwriting the active meta-profile does not refresh the inline
  copy (re-activate to refresh it), and `PATCH /api/settings` with
  `active_meta_profile` sets the pointer and enables the tool without any
  existence check or inline copy (`F18.dangling-pointers`). With the tool
  enabled, no pointer and no inline copy, it falls back to the alphabetically
  first meta-profile in the store.
- Routing needs the agent to call `route_task_to_model`; the classifier then
  makes one extra LLM call (metered as `classifier:<profile>`), and the switch
  takes effect from the next step. A classifier answer outside the classes
  (or a direct answer naming no saved profile) is an error observation, never
  a silent default. Keep prompts explicit ("Call the route_task_to_model tool
  once") and class sets unambiguous when a recipe must be deterministic.
- `GET /api/meta-profiles` lists files without full validation: unparseable
  and non-object files are skipped, but a valid JSON object that fails the
  schema (for example `{"classes": "x"}`) is listed with `classifier_model:
  null` and is then 400 on `GET` and `activate`. With an unreadable
  `settings.json` the list reports `active_meta_profile: null`.
- Deleting a meta-profile is not guarded the way LLM-profile deletion is:
  `DELETE` answers 200 even while an agent profile names it in
  `meta_profile_ref`, leaving that reference dangling.
- A name ending in `.json` is stored without the suffix: saving `qa-x.json`
  answers `Meta-profile 'qa-x.json' saved` but the file and the list entry
  are `qa-x`, so `qa-x` and `qa-x.json` overwrite each other.
- Switching the settings to ACP clears `active_meta_profile` for good;
  switching back to OpenHands starts from default agent settings (no LLM), so
  re-activate an LLM profile and the meta-profile afterwards.
- `503` and `500` bodies use the server's generic shape: `detail` is
  `Internal Server Error` and the route's message is in `exception`.
- The store directory comes from `OH_PERSISTENCE_DIR` read at call time
  (`default_meta_profile_dir()`), falling back to `~/.openhands/meta-profiles`
  when it is unset; give the server an absolute `OH_PERSISTENCE_DIR`
  (`F22.persistence-dir`).
