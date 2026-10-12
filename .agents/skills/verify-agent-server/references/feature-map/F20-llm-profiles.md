# LLM profiles

Named LLM configurations a consumer saves once and switches between: save a
model, key and options under a name, list the saved names with the active
one, read one back (key hidden, encrypted or plaintext on request), check a
draft against the real provider before saving it, make one the active LLM of
the server's settings, rename it (the active pointer and agent-profile
references follow), and delete it (refused while an agent profile still uses
it). Each profile is one JSON file under `OH_PERSISTENCE_DIR/profiles`, with
its key encrypted by the server's cipher, so profiles survive restarts. The
pre-flight `validate` route sends a real one-token completion with the posted
config and answers `valid` true or false; it never reads or writes the store.
The SDK's `RemoteWorkspace.get_llm()` and the TypeScript `ProfilesClient` are
the programmatic consumers.

Source: `openhands-agent-server/openhands/agent_server/profiles_router.py`, `openhands-agent-server/openhands/agent_server/_secrets_exposure.py`, `openhands-sdk/openhands/sdk/llm/llm_profile_store.py`, `openhands-sdk/openhands/sdk/profiles/profile_refs.py`, `openhands-sdk/openhands/sdk/workspace/remote/base.py`, `clients/typescript/src/client/profiles-client.ts`

Needs: `llm`, `node`

Routes: `GET /api/profiles`, `GET /api/profiles/{name}`,
`POST /api/profiles/{name}`, `POST /api/profiles/{name}/validate`,
`DELETE /api/profiles/{name}`, `POST /api/profiles/{name}/rename`,
`POST /api/profiles/{name}/activate`

## Sub-features

- `F20.auth-required`: every profile route answers 401 without a valid `X-Session-API-Key`, and nothing changes.
- `F20.save`: `POST /api/profiles/{name}` with a DeepSeek model and key answers 201 `Profile '<name>' saved`; the list shows the summary `{name, model, base_url, provider_connection_id, provider_connection_broken, api_key_set: true}` and the active profile, and `GET` by name returns the config.
- `F20.key-encrypted-at-rest`: the profile file `home/.openhands/profiles/<name>.json` is mode `600` with `schema_version` 1 and a Fernet `gAAAAA` token as `api_key`; the plaintext key appears nowhere under the server's home.
- `F20.get-secrets`: `GET /api/profiles/{name}` returns `config.api_key: null` with `api_key_set: true` by default, the raw key with `X-Expose-Secrets: plaintext`, a `gAAAAA` token with `encrypted` (or the legacy `true`), and 400 for any other header value.
- `F20.save-encrypted-roundtrip`: posting back the `gAAAAA` token from an `encrypted` read stores the original key (the server decrypts it before re-encrypting), not the token.
- `F20.save-without-secrets`: `include_secrets: false` stores the placeholder `**********` instead of the key; both the list and `GET` report `api_key_set: false` and plaintext reads return `null`.
- `F20.not-found`: `GET`, `activate` and `rename` of an unknown name answer 404 `Profile '<name>' not found`, also for a same-name rename.
- `F20.name-rules`: names must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`: a leading `-` is 422 on all six named routes, 64 characters are accepted and 65 are 422; a save or validate body without `llm`, or a save without `llm.model`, is 422 and saves nothing.
- `F20.corrupt-files`: a profile file that is not valid JSON (or not an object) is left out of the list and answers 400 `Failed to load profile ...` on `GET` and `activate`, saving over it repairs it, and an unreadable `settings.json` makes activation 409 without touching the file.
- `F20.store-busy`: while another process holds the profile store's lock, a save waits out the 30-second lock timeout and answers 503 `Profile store is busy. Please retry.` without saving; once the lock is free the same save is 201.
- `F20.store-busy-responsive`: while a profile request waits for the store lock, the server keeps answering other requests (`GET /alive` within 3 seconds).
- `F20.profile-limit`: with 50 profiles stored, creating another is 409 `Profile limit reached (50)` while overwriting an existing one still answers 201.
- `F20.validate-live`: `validate` with a working DeepSeek flash config answers `{valid: true, error: null}`, also when the key is the `gAAAAA` token from an `encrypted` read; the draft is not saved.
- `F20.validate-rejects`: `validate` answers 200 `valid: false` with a typed error for a model the provider does not serve (`LLMBadRequestError`) and for an invalid DeepSeek key, without echoing the key.
- `F20.validate-bad-key-type`: an invalid DeepSeek key (provider HTTP 401) is reported as `LLMAuthenticationError`, like any other provider's 401.
- `F20.validate-provider-errors`: with retries off, a provider 401 is `valid: false` `LLMAuthenticationError`, a 500 or an unreachable `base_url` is `valid: false` `LLMServiceUnavailableError`, and a 429 or a per-attempt timeout is `valid: true` (transient; the timeout verdict arrives after one attempt of the posted `timeout`, long before the hung provider would answer); the provider receives a system message `Reply with one token.`, a user `ping` and `max_tokens: 1`.
- `F20.validate-subscription-login`: `validate` of an `auth_type: subscription` config on a server with no stored OpenAI subscription login answers 200 `valid: false` (`ValueError`, `OpenAI subscription login is required`) at once, not a 500.
- `F20.validate-unreachable-bounded`: `validate` of a config whose `base_url` refuses connections answers `valid: false` within 30 seconds with the default retry policy.
- `F20.validate-hang-bounded`: `validate` of a config whose provider never answers returns a verdict within 30 seconds with the default timeout and retry policy.
- `F20.activate`: `POST /api/profiles/{name}/activate` answers 200 `llm_applied: true`; `GET /api/settings` then has the profile's LLM (model, key, options) in `agent_settings.llm` and `active_profile` set, the list reports the same `active_profile`, and `settings.json` holds the key encrypted.
- `F20.activate-conversation`: a conversation started from the saved settings after an activation runs on the activated profile's model and options and finishes.
- `F20.activate-acp`: with ACP agent settings, activation only moves the pointer: 200 `llm_applied: false`, `active_profile` set, no `agent_settings.llm`.
- `F20.sdk-get-llm`: the SDK's `RemoteWorkspace.get_llm(profile_name=...)` returns the stored model, options and plaintext key with `usage_id` `profile:<name>`, `get_llm()` follows `active_profile`, keyword overrides win, and an unknown name raises `FileNotFoundError`.
- `F20.ts-client`: the TypeScript `ProfilesClient` lists, saves, reads (hidden, `plaintext`, `encrypted`), validates, renames, activates and deletes a profile, and surfaces 404 and 422 as `HttpError`s.
- `F20.activate-dangling-provider`: a profile linked to a provider connection that does not exist is saved without its inline key and `base_url`, is listed as `provider_connection_broken: true`, reads fine, and its activation is 422 without moving the active pointer.
- `F20.activate-linked-provider`: a profile linked to an existing provider connection is saved without its inline key, is listed and read with `api_key_set: true` (the connection's key) and a `null` key, and its activation writes the connection's key into `agent_settings.llm`.
- `F20.sdk-get-llm-linked`: an SDK conversation whose LLM comes from `RemoteWorkspace.get_llm(profile_name=<linked profile>)` runs on the provider connection's key and finishes.
- `F20.no-cipher`: on a server with neither `OH_SECRET_KEY` nor a session key (no cipher), a saved key is stored in plaintext in a `600` file, plaintext reads and activation work, and `X-Expose-Secrets: encrypted` answers 503.
- `F20.persist-restart`: profiles, their encrypted keys, options and the active pointer survive a restart with the same secret key.
- `F20.rename`: `POST /api/profiles/{name}/rename` moves the file and keeps the key; the old name is 404, `active_profile` follows the rename in the list and in settings, and agent profiles whose `llm_profile_ref` cited the old name now cite the new one.
- `F20.rename-errors`: renaming onto an existing name is 409 and changes nothing, a same-name rename is 200 `unchanged (same name)`, and an invalid or missing `new_name` is 422.
- `F20.delete-guarded`: deleting a profile that an agent profile references is 409 naming the referrer, and the profile stays.
- `F20.delete`: `DELETE /api/profiles/{name}` removes the file and the list entry and clears `active_profile` in both views, leaves the applied `agent_settings.llm` in place, and answers 200 again for a name that is already gone.
- `F20.json-alias-guard`: a name with a `.json` suffix addresses the same profile (`GET /api/profiles/<name>.json` reads `<name>`), and deleting through that alias is refused with 409 while an agent profile references `<name>`, as it is for the plain name.
- `F20.secret-key-rotation`: after the server's secret key changes, activating a profile whose stored key can no longer be decrypted is refused instead of silently applying a keyless LLM.

## How to get to it (agent POV)

- REST: `GET /api/profiles`, `GET /api/profiles/{name}` (optional
  `X-Expose-Secrets: plaintext|encrypted`), `POST /api/profiles/{name}`
  (`{"llm": {...LLM fields...}, "include_secrets": true}`),
  `POST /api/profiles/{name}/validate` (`{"llm": {...}}`; the path name is
  only used for logging; `auth_type: subscription` first loads the stored
  OpenAI subscription login), `POST /api/profiles/{name}/activate` (no body),
  `POST /api/profiles/{name}/rename` (`{"new_name": "..."}`),
  `DELETE /api/profiles/{name}`. Every recipe below drives these routes.
- Second views used here: `GET /api/settings` (`active_profile`,
  `agent_settings.llm`, `llm_api_key_is_set`; settings family), the profile
  files under `home/.openhands/profiles/` (`state cat`), and
  `GET /api/agent-profiles/{name}` (`llm_profile_ref`; agent profiles family).
  `PATCH /api/settings` with `active_profile` is a second way to activate a
  profile (settings family). `POST` and `DELETE /api/llm/provider-connections`
  (provider connections family) arrange the linked-profile bullets.
- SDK: `RemoteWorkspace.get_llm(profile_name=...)` reads
  `GET /api/profiles/{name}` with `X-Expose-Secrets: plaintext` and builds an
  `LLM`; without a name it resolves `active_profile` from `GET /api/settings`
  first (`openhands-sdk/openhands/sdk/workspace/remote/base.py`). Driven by
  `F20.sdk-get-llm` and `F20.sdk-get-llm-linked` (Python programs run with
  `exec`). The SDK's local `LLMProfileStore` is the same file format and lock
  without the server (`F20.store-busy` stands in for it with `flock`).
- TypeScript client: `ProfilesClient.listProfiles`, `getProfile(name,
  {exposeSecrets})`, `saveProfile`, `validateProfile`, `activateProfile`,
  `renameProfile`, `deleteProfile` (default request timeout 60 s), and the
  same calls on `SettingsClient`. Driven by `F20.ts-client` (a `node` program
  on `clients/typescript/dist`).
- Configuration: `OH_SECRET_KEY` is the cipher for keys at rest; without it
  the first session key is the cipher, and with neither there is none
  (`F20.no-cipher`, `launch --no-auth --no-secret-key`). `OH_PERSISTENCE_DIR`
  holds `profiles/`.
- Conversations: a conversation started from the saved settings (the Agent
  Canvas path, `conversation start` without agent fields) uses whatever the
  last activation wrote into `agent_settings.llm`
  (`F20.activate-conversation`).
- Agent Canvas (context only): the LLM settings page validates a draft before
  saving it, round-trips keys as `encrypted` tokens, and activates the chosen
  profile.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported (`launch --new`), `doctor` is ok,
  `$DEEPSEEK_API_KEY` is set, `jq`, `flock` and `node` are on `PATH` (the
  TypeScript client is built if `clients/typescript/dist` is missing). The
  block below saves the DeepSeek preset (`deepseek-flash`, active, and
  `deepseek-pro`) and an agent profile `qa-f20-agent` whose `llm_profile_ref`
  is `qa-flash`; that reference stays soft until `F20.save` creates
  `qa-flash`, and `F20.rename` and `F20.delete-guarded` rely on it. It also
  writes the SDK and TypeScript programs that `F20.sdk-get-llm`,
  `F20.ts-client` and `F20.sdk-get-llm-linked` run; they read
  `$DEEPSEEK_API_KEY` from the environment `exec` passes on.
  `sdk_conversation.py <profile>` runs a one-message SDK conversation (no
  tools, no visualizer, a workspace under this run's fixtures) on
  `RemoteWorkspace.get_llm(profile_name=<profile>)` and prints whether that
  LLM carries a key.
- Bodies that carry the real key are piped from the shell builtin `printf`
  into `api ... --stdin`, so the key never appears on a command line; the CLI
  redacts it from output and evidence.
- `F20.store-busy` and `F20.store-busy-responsive` hold the store lock for
  about 34 seconds each; `F20.validate-unreachable-bounded` waits about
  two minutes for the server's verdict; `F20.validate-hang-bounded`
  hard-restarts the run to cancel a request the server would hold for 27
  minutes; `F20.no-cipher` launches and stops a second server;
  `F20.persist-restart` restarts the run; `F20.secret-key-rotation` restarts
  it with a new secret key and must stay last.

  ```sh
  control-agent-server llm preset deepseek
  control-agent-server api GET /api/profiles --expect 200 --check active_profile eq deepseek-flash --check profiles len-eq 2
  control-agent-server api POST /api/agent-profiles/qa-f20-agent --json '{"llm_profile_ref": "qa-flash"}' --expect 201
  F20="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f20"
  mkdir -p "$F20"
  cat > "$F20/sdk_get_llm.py" <<'EOF'
  import os

  from openhands.sdk.workspace import RemoteWorkspace

  ws = RemoteWorkspace(
      host=os.environ["AGENT_SERVER_URL"],
      api_key=os.environ["SESSION_API_KEY"],
      working_dir=os.path.join(os.environ["AGENT_SERVER_VERIFY_RUN"], "fixtures/qa-f20"),
  )
  named = ws.get_llm(profile_name="qa-flash")
  assert named.model == "deepseek/deepseek-flash", named.model
  assert named.num_retries == 0, named.num_retries
  assert named.usage_id == "profile:qa-flash", named.usage_id
  assert named.api_key is not None
  assert named.api_key.get_secret_value() == os.environ["DEEPSEEK_API_KEY"], "key differs"
  active = ws.get_llm()
  assert active.usage_id == "profile:qa-flash", active.usage_id
  assert active.api_key is not None
  assert active.api_key.get_secret_value() == os.environ["DEEPSEEK_API_KEY"], "key differs"
  override = ws.get_llm(profile_name="qa-nokey", temperature=0.5)
  assert override.model == "openai/qa-nokey", override.model
  assert override.temperature == 0.5, override.temperature
  assert override.api_key is None, "qa-nokey has no stored key"
  try:
      ws.get_llm(profile_name="qa-missing")
  except FileNotFoundError:
      pass
  else:
      raise AssertionError("an unknown profile did not raise FileNotFoundError")
  print("QA_F20_SDK_OK")
  EOF
  cat > "$F20/sdk_conversation.py" <<'EOF'
  import os
  import sys

  from openhands.sdk import Agent, Conversation
  from openhands.sdk.workspace import RemoteWorkspace

  workdir = os.path.join(os.environ["AGENT_SERVER_VERIFY_RUN"], "fixtures/qa-f20/workspace")
  os.makedirs(workdir, exist_ok=True)
  ws = RemoteWorkspace(
      host=os.environ["AGENT_SERVER_URL"],
      api_key=os.environ["SESSION_API_KEY"],
      working_dir=workdir,
  )
  llm = ws.get_llm(profile_name=sys.argv[1])
  print("QA_F20_CONV_KEY_SET", llm.api_key is not None, flush=True)
  conversation = Conversation(agent=Agent(llm=llm, tools=[]), workspace=ws, visualizer=None)
  try:
      conversation.send_message("Reply with one word: pong")
      conversation.run()
      status = conversation.state.execution_status.value
      print("QA_F20_CONV_STATUS", status)
      assert status == "finished", status
  finally:
      conversation.close()
  print("QA_F20_CONV_OK")
  EOF
  cat > "$F20/ts_profiles.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const { ProfilesClient } = await import(pathToFileURL(resolve('clients/typescript/dist/clients.js')).href);
  const client = new ProfilesClient({ host: process.env.AGENT_SERVER_URL, apiKey: process.env.SESSION_API_KEY });
  const before = await client.listProfiles();
  assert.equal(before.active_profile, 'qa-flash');
  assert.deepEqual(await client.saveProfile('qa-ts', { llm: { model: 'openai/qa-ts', api_key: 'sk-qa-f20-ts' } }),
    { name: 'qa-ts', message: "Profile 'qa-ts' saved" });
  const hidden = await client.getProfile('qa-ts');
  assert.equal(hidden.config.api_key, null);
  assert.equal(hidden.api_key_set, true);
  const plain = await client.getProfile('qa-ts', { exposeSecrets: 'plaintext' });
  assert.equal(plain.config.api_key, 'sk-qa-f20-ts');
  const enc = await client.getProfile('qa-ts', { exposeSecrets: 'encrypted' });
  assert.match(enc.config.api_key, /^gAAAAA/);
  const verdict = await client.validateProfile('qa-ts', {
    llm: { model: 'openai/qa-ts', base_url: 'http://127.0.0.1:9/v1', api_key: 'sk-qa-f20-ts', num_retries: 0 },
  });
  assert.equal(verdict.valid, false);
  assert.equal(verdict.error.type, 'LLMServiceUnavailableError');
  assert.equal((await client.renameProfile('qa-ts', 'qa-ts2')).name, 'qa-ts2');
  assert.equal((await client.activateProfile('qa-ts2')).llm_applied, true);
  assert.equal((await client.listProfiles()).active_profile, 'qa-ts2');
  assert.equal((await client.activateProfile('qa-flash')).llm_applied, true);
  assert.equal((await client.deleteProfile('qa-ts2')).message, "Profile 'qa-ts2' deleted");
  await assert.rejects(client.getProfile('qa-ts2'), (err) => err.status === 404);
  await assert.rejects(client.saveProfile('-qa-bad', { llm: { model: 'openai/qa' } }), (err) => err.status === 422);
  const after = await client.listProfiles();
  assert.equal(after.active_profile, 'qa-flash');
  assert.equal(after.profiles.length, before.profiles.length);
  client.close();
  console.log('QA_F20_TS_OK');
  JS
  ```

- **Key required (`F20.auth-required`).** Call every route without a key or
  with a wrong one.
  ```sh
  control-agent-server api GET /api/profiles --auth none --expect 401 --check detail eq Unauthorized --save F20.auth-required/list
  control-agent-server api GET /api/profiles/deepseek-flash --auth bad --expect 401
  control-agent-server api POST /api/profiles/qa-noauth --auth none --json '{"llm": {"model": "openai/qa"}}' --expect 401
  control-agent-server api POST /api/profiles/qa-noauth/validate --auth none --json '{"llm": {"model": "openai/qa"}}' --expect 401
  control-agent-server api POST /api/profiles/deepseek-pro/activate --auth none --expect 401
  control-agent-server api POST /api/profiles/deepseek-pro/rename --auth none --json '{"new_name": "qa-noauth"}' --expect 401
  control-agent-server api DELETE /api/profiles/deepseek-pro --auth none --expect 401
  control-agent-server api GET /api/profiles --expect 200 --check active_profile eq deepseek-flash --check profiles len-eq 2 --check profiles contains '"name": "deepseek-pro"'
  ```
  Every call is 401 `{"detail": "Unauthorized"}`; with the key the list still
  holds the two preset profiles and `deepseek-flash` is still active.
- **Save (`F20.save`).** Save the DeepSeek flash model with the real key and
  `num_retries: 0` (the marker later bullets use to tell this profile apart
  from the preset's `deepseek-flash`).
  ```sh
  printf '{"llm": {"model": "deepseek/deepseek-flash", "api_key": "%s", "num_retries": 0}}' "$DEEPSEEK_API_KEY" \
    | control-agent-server api POST /api/profiles/qa-flash --stdin --expect 201 \
      --check name eq qa-flash --check message eq "Profile 'qa-flash' saved" --save F20.save/save
  control-agent-server api GET /api/profiles --expect 200 --check profiles len-eq 3 --check active_profile eq deepseek-flash \
    --check profiles contains '"name": "qa-flash", "model": "deepseek/deepseek-flash", "base_url": null, "provider_connection_id": null, "provider_connection_broken": false, "api_key_set": true' \
    --save F20.save/list
  control-agent-server api GET /api/profiles/qa-flash --expect 200 --check name eq qa-flash \
    --check config.model eq deepseek/deepseek-flash --check config.num_retries eq 0 --check api_key_set eq true
  ```
  The save is 201, the list has three profiles including the `qa-flash`
  summary, and saving did not change the active profile.
- **Encrypted at rest (`F20.key-encrypted-at-rest`).** Read the file the save
  wrote.
  ```sh
  control-agent-server state cat home/.openhands/profiles/qa-flash.json --mode 600 \
    --check api_key matches '^gAAAAA' --check model eq deepseek/deepseek-flash --check schema_version eq 1
  control-agent-server state grep deepseek/deepseek-flash --glob 'home/**/*'
  control-agent-server state grep --env-value DEEPSEEK_API_KEY --glob 'home/**/*' --expect-none
  ```
  The file is `600`, its `api_key` is a Fernet token (printed as
  `<redacted:encrypted>`), and no file under the server's home contains the
  plaintext key (the preset's profiles and `settings.json` included). The
  first `state grep` is the positive control: the same glob reaches the
  hidden `home/.openhands/profiles/` files and finds the model name there.
- **Reading the key (`F20.get-secrets`).** Read the profile in each exposure
  mode.
  ```sh
  control-agent-server api GET /api/profiles/qa-flash --expect 200 --check config.api_key missing --check api_key_set eq true --save F20.get-secrets/default
  control-agent-server api GET /api/profiles/qa-flash --header 'X-Expose-Secrets: plaintext' --expect 200 \
    --check config.api_key eq "$DEEPSEEK_API_KEY" --save F20.get-secrets/plaintext
  control-agent-server api GET /api/profiles/qa-flash --header 'X-Expose-Secrets: encrypted' --expect 200 \
    --check config.api_key matches '^gAAAAA' --save F20.get-secrets/encrypted
  control-agent-server api GET /api/profiles/qa-flash --header 'X-Expose-Secrets: true' --expect 200 --check config.api_key matches '^gAAAAA'
  control-agent-server api GET /api/profiles/qa-flash --header 'X-Expose-Secrets: yes' --expect 400 \
    --check detail contains 'Invalid X-Expose-Secrets header value' --save F20.get-secrets/invalid-mode
  ```
  Without the header `config.api_key` is `null` and `api_key_set` is `true`;
  `plaintext` returns the DeepSeek key (redacted in the output), `encrypted`
  and the legacy `true` a `gAAAAA` token, and `yes` is 400.
- **Encrypted round trip (`F20.save-encrypted-roundtrip`).** Save a copy whose
  key is the token from an `encrypted` read, as Agent Canvas does.
  ```sh
  ENC=$(control-agent-server api GET /api/profiles/qa-flash --header 'X-Expose-Secrets: encrypted' --field config.api_key --unsafe-unredacted)
  control-agent-server api POST /api/profiles/qa-flash-copy \
    --json "{\"llm\": {\"model\": \"deepseek/deepseek-flash\", \"api_key\": \"$ENC\"}}" --expect 201 --save F20.save-encrypted-roundtrip/save
  control-agent-server api GET /api/profiles/qa-flash-copy --header 'X-Expose-Secrets: plaintext' --expect 200 \
    --check config.api_key eq "$DEEPSEEK_API_KEY" --save F20.save-encrypted-roundtrip/plaintext
  control-agent-server state cat home/.openhands/profiles/qa-flash-copy.json --check api_key matches '^gAAAAA'
  control-agent-server api DELETE /api/profiles/qa-flash-copy --expect 200
  ```
  The copy's plaintext key is the DeepSeek key itself, not the token; the
  copy is deleted at the end.
- **Save without secrets (`F20.save-without-secrets`).** Save a profile with
  a key and `include_secrets: false`.
  ```sh
  control-agent-server api POST /api/profiles/qa-nokey \
    --json '{"llm": {"model": "openai/qa-nokey", "api_key": "sk-qa-f20-not-stored"}, "include_secrets": false}' \
    --expect 201 --save F20.save-without-secrets/save
  control-agent-server state cat home/.openhands/profiles/qa-nokey.json --check api_key eq '**********' --not-contains sk-qa-f20-not-stored
  control-agent-server api GET /api/profiles \
    --check profiles contains '"name": "qa-nokey", "model": "openai/qa-nokey", "base_url": null, "provider_connection_id": null, "provider_connection_broken": false, "api_key_set": false' \
    --save F20.save-without-secrets/list
  control-agent-server api GET /api/profiles/qa-nokey --header 'X-Expose-Secrets: plaintext' --expect 200 \
    --check config.api_key missing --check api_key_set eq false
  ```
  The file holds `**********`, never the posted key; the list and `GET` say
  no key is set and a plaintext read returns `null`.
- **Unknown names (`F20.not-found`).** Read, activate and rename a name that
  was never saved.
  ```sh
  control-agent-server api GET /api/profiles/qa-missing --expect 404 --check detail eq "Profile 'qa-missing' not found" --save F20.not-found/get
  control-agent-server api POST /api/profiles/qa-missing/activate --expect 404 --check detail eq "Profile 'qa-missing' not found" --save F20.not-found/activate
  control-agent-server api POST /api/profiles/qa-missing/rename --json '{"new_name": "qa-other"}' --expect 404 --save F20.not-found/rename
  control-agent-server api POST /api/profiles/qa-missing/rename --json '{"new_name": "qa-missing"}' --expect 404
  control-agent-server api GET /api/profiles/qa-other --expect 404
  control-agent-server api GET /api/profiles --check active_profile eq deepseek-flash
  ```
  All are 404 and the active profile is unchanged.
- **Name rules (`F20.name-rules`).** Use a name with a leading `-`, the
  64-character limit, and bodies without `llm` or `model`.
  ```sh
  control-agent-server api GET /api/profiles/-qa-bad --expect 422 --check detail.0.type eq string_pattern_mismatch --check detail.0.loc.1 eq name --save F20.name-rules/get
  control-agent-server api POST /api/profiles/-qa-bad --json '{"llm": {"model": "openai/qa"}}' --expect 422
  control-agent-server api POST /api/profiles/-qa-bad/validate --json '{"llm": {"model": "openai/qa"}}' --expect 422
  control-agent-server api POST /api/profiles/-qa-bad/activate --expect 422
  control-agent-server api POST /api/profiles/-qa-bad/rename --json '{"new_name": "qa-ok"}' --expect 422
  control-agent-server api DELETE /api/profiles/-qa-bad --expect 422
  LONG="qa-$(printf 'x%.0s' $(seq 61))"
  test "${#LONG}" = 64
  control-agent-server api POST "/api/profiles/$LONG" --json '{"llm": {"model": "openai/qa"}}' --expect 201
  control-agent-server api GET "/api/profiles/$LONG" --expect 200 --check name eq "$LONG" --check config.model eq openai/qa
  control-agent-server api GET "/api/profiles/${LONG}x" --expect 422 --check detail.0.type eq string_too_long --save F20.name-rules/too-long
  control-agent-server api DELETE "/api/profiles/$LONG" --expect 200
  control-agent-server api POST /api/profiles/qa-bad-body --json '{}' --expect 422 --check detail.0.loc.1 eq llm --save F20.name-rules/no-llm
  control-agent-server api POST /api/profiles/qa-bad-body --json '{"llm": {"api_key": "sk-qa-f20"}}' --expect 422 \
    --check detail.0.msg contains 'model must be specified' --save F20.name-rules/no-model
  control-agent-server api POST /api/profiles/qa-bad-body/validate --json '{}' --expect 422 --check detail.0.loc.1 eq llm \
    --save F20.name-rules/validate-no-llm
  control-agent-server api GET /api/profiles/qa-bad-body --expect 404
  control-agent-server api GET /api/profiles --check profiles len-eq 4
  ```
  Every `-qa-bad` call is 422 on the `name` path parameter, 64 characters
  save and 65 are 422 `string_too_long`, the three bad bodies are 422, and
  the list is back to four profiles (two preset, `qa-flash`, `qa-nokey`).
- **Corrupted files (`F20.corrupt-files`).** Write a truncated profile, a
  profile that is a JSON list, and then a truncated `settings.json` (arrange:
  a crash mid-write or a hand edit), and drive the routes over them.
  ```sh
  P="$AGENT_SERVER_VERIFY_RUN/home/.openhands/profiles"
  printf '{"model": "openai/qa-corrupt", ' > "$P/qa-corrupt.json"
  printf '["not", "an", "object"]' > "$P/qa-notdict.json"
  control-agent-server api GET /api/profiles --expect 200 --check profiles len-eq 4 \
    --check profiles not-contains '"name": "qa-corrupt"' --check profiles not-contains '"name": "qa-notdict"' --save F20.corrupt-files/list
  control-agent-server api GET /api/profiles/qa-corrupt --expect 400 --check detail contains 'Failed to load profile `qa-corrupt`' --save F20.corrupt-files/get
  control-agent-server api GET /api/profiles/qa-notdict --expect 400 --check detail contains 'Failed to load profile `qa-notdict`'
  control-agent-server api POST /api/profiles/qa-corrupt/activate --expect 400 --check detail contains 'Failed to load profile' --save F20.corrupt-files/activate
  control-agent-server api GET /api/profiles --check active_profile eq deepseek-flash
  control-agent-server api POST /api/profiles/qa-corrupt --json '{"llm": {"model": "openai/qa-repaired"}}' --expect 201
  control-agent-server api GET /api/profiles/qa-corrupt --expect 200 --check config.model eq openai/qa-repaired
  control-agent-server api DELETE /api/profiles/qa-corrupt --expect 200
  control-agent-server api DELETE /api/profiles/qa-notdict --expect 200
  test ! -e "$P/qa-notdict.json"
  S="$AGENT_SERVER_VERIFY_RUN/home/.openhands/settings.json"
  cp -p "$S" "$S.qa-f20-bak"
  printf '{"agent_settings": ' > "$S"
  control-agent-server api POST /api/profiles/qa-nokey/activate --expect 409 \
    --check detail eq 'Settings file is corrupted or encrypted with a different key' --save F20.corrupt-files/corrupt-settings
  test "$(cat "$S")" = '{"agent_settings": '
  mv "$S.qa-f20-bak" "$S"
  control-agent-server api GET /api/profiles --check active_profile eq deepseek-flash --check profiles len-eq 4
  ```
  Both broken profiles are missing from the list, `GET` and `activate` are
  400 with the parser's message (not 500) and the active profile stays;
  saving over `qa-corrupt` repairs it and both deletes are 200. With the
  truncated `settings.json` the activation is 409 and the file is byte for
  byte what was written; the original is then put back.
- **Store busy (`F20.store-busy`).** Another process (here `flock`, standing
  in for the SDK's `LLMProfileStore` or a second server on the same
  persistence directory) holds the profile store's lock for 34 seconds while
  a save arrives. The server's own 30-second lock timeout is under test, so
  this bullet waits for it.
  ```sh
  P="$AGENT_SERVER_VERIFY_RUN/home/.openhands/profiles"
  HELD="$F20/lock-held"
  rm -f "$HELD"
  flock -x "$P/.profiles.lock" sh -c "touch '$HELD'; sleep 34" &
  LOCKER=$!
  for i in $(seq 100); do test -e "$HELD" && break; sleep 0.1; done
  test -e "$HELD"
  T0=$SECONDS
  control-agent-server api POST /api/profiles/qa-busy --json '{"llm": {"model": "openai/qa-busy"}}' --timeout 60 --expect 503 \
    --check exception eq '503: Profile store is busy. Please retry.' --save F20.store-busy/save
  test $((SECONDS - T0)) -ge 29
  test $((SECONDS - T0)) -le 33
  wait "$LOCKER"
  control-agent-server api GET /api/profiles/qa-busy --expect 404
  control-agent-server api POST /api/profiles/qa-busy --json '{"llm": {"model": "openai/qa-busy"}}' --expect 201 --expect-max-ms 5000
  control-agent-server api DELETE /api/profiles/qa-busy --expect 200
  ```
  The save answers 503 after 29 to 33 seconds (the body's `detail` is the
  generic `Internal Server Error`; the route's message is in `exception`),
  `qa-busy` does not exist afterwards, and the same save succeeds at once
  when the lock is free.
- **Lock wait blocks the server (`F20.store-busy-responsive`), known bug.**
  Probe `/alive` once with the lock free (positive control), hold the lock
  again, start a save in the background, and probe `/alive` while the save
  waits. The five-second pause only lets the background save reach the
  server first (with margin for a slow CLI start), so the probe falls inside
  the save's 30-second lock wait.
  ```sh
  P="$AGENT_SERVER_VERIFY_RUN/home/.openhands/profiles"
  HELD="$F20/lock-held-2"
  rm -f "$HELD"
  control-agent-server api GET /alive --auth none --expect 200 --expect-max-ms 3000
  flock -x "$P/.profiles.lock" sh -c "touch '$HELD'; sleep 34" &
  LOCKER=$!
  for i in $(seq 100); do test -e "$HELD" && break; sleep 0.1; done
  test -e "$HELD"
  (control-agent-server api POST /api/profiles/qa-busy --json '{"llm": {"model": "openai/qa-busy"}}' --timeout 60 --expect 503 --quiet > "$F20/busy-save.json") &
  SAVER=$!
  sleep 5
  control-agent-server api GET /alive --auth none --expect 200 --timeout 60 --save F20.store-busy-responsive/alive | tee "$F20/busy-alive.json"
  wait "$SAVER"
  wait "$LOCKER"
  control-agent-server api GET /api/profiles/qa-busy --expect 404
  jq -e '.elapsed_ms <= 3000' "$F20/busy-alive.json"  # bug
  ```
  Expected `/alive` to answer within 3 seconds while the save waits, as it
  does with the lock free. Today it answers 200 only when the save gives up,
  about 25 seconds later, so the `jq` bound marked `# bug` fails: every
  profile route is an `async def` that calls the synchronous, file-locked
  `LLMProfileStore` on the event loop (`profiles_router.py`), so a 30-second
  lock wait freezes the whole server, liveness and readiness probes
  included. Before the bound is checked, `wait "$SAVER"` proves the
  background save really waited and was refused with 503, both background
  processes have ended (so later bullets find the lock free), and `qa-busy`
  was not created.
- **Profile limit (`F20.profile-limit`).** Fill the store to 50, then create
  and overwrite.
  ```sh
  N=$(control-agent-server api GET /api/profiles --field profiles | jq length)
  for i in $(seq $((N + 1)) 50); do
    control-agent-server api POST "/api/profiles/qa-cap-$i" --json '{"llm": {"model": "openai/qa-cap"}}' --expect 201 --field name
  done
  control-agent-server api GET /api/profiles --check profiles len-eq 50 --max-chars 200
  control-agent-server api POST /api/profiles/qa-cap-51 --json '{"llm": {"model": "openai/qa-cap"}}' --expect 409 \
    --check detail eq 'Profile limit reached (50). Delete a profile before saving a new one.' --save F20.profile-limit/create-51st
  control-agent-server api GET /api/profiles/qa-cap-51 --expect 404
  control-agent-server api POST /api/profiles/qa-nokey --json '{"llm": {"model": "openai/qa-nokey", "temperature": 0.25}, "include_secrets": false}' \
    --expect 201 --save F20.profile-limit/overwrite-at-cap
  control-agent-server api GET /api/profiles/qa-nokey --check config.temperature eq 0.25
  for i in $(seq $((N + 1)) 50); do
    control-agent-server api DELETE "/api/profiles/qa-cap-$i" --expect 200 --field name
  done
  control-agent-server api GET /api/profiles --check profiles len-eq "$N"
  ```
  The 51st profile is 409 and not created; overwriting `qa-nokey` at the cap
  is 201 and its new `temperature` reads back; the filler profiles are
  deleted again.
- **Validate a working config (`F20.validate-live`).** Pre-flight the real
  DeepSeek flash model, once with the plaintext key and once with the
  `encrypted` token of `qa-flash`.
  ```sh
  printf '{"llm": {"model": "deepseek/deepseek-flash", "api_key": "%s", "num_retries": 0}}' "$DEEPSEEK_API_KEY" \
    | control-agent-server api POST /api/profiles/qa-draft/validate --stdin --timeout 120 --expect 200 \
      --check valid eq true --check error missing --save F20.validate-live/plaintext-key
  ENC=$(control-agent-server api GET /api/profiles/qa-flash --header 'X-Expose-Secrets: encrypted' --field config.api_key --unsafe-unredacted)
  control-agent-server api POST /api/profiles/qa-draft/validate --timeout 120 --expect 200 \
    --json "{\"llm\": {\"model\": \"deepseek/deepseek-flash\", \"api_key\": \"$ENC\", \"num_retries\": 0}}" \
    --check valid eq true --save F20.validate-live/encrypted-key
  control-agent-server api GET /api/profiles/qa-draft --expect 404
  control-agent-server api GET /api/profiles --check profiles len-eq 4
  ```
  Both answer `{"valid": true, "error": null}` (the token was decrypted
  before the call; sent as-is DeepSeek would reject it), and `qa-draft` was
  never saved.
- **Validate rejects (`F20.validate-rejects`).** Pre-flight a model DeepSeek
  does not serve, then an invalid key.
  ```sh
  printf '{"llm": {"model": "deepseek/qa-no-such-model", "api_key": "%s", "num_retries": 0}}' "$DEEPSEEK_API_KEY" \
    | control-agent-server api POST /api/profiles/qa-draft/validate --stdin --timeout 120 --expect 200 \
      --check valid eq false --check error.type eq LLMBadRequestError --save F20.validate-rejects/unknown-model
  control-agent-server api POST /api/profiles/qa-draft/validate --timeout 120 --expect 200 \
    --json '{"llm": {"model": "deepseek/deepseek-flash", "api_key": "sk-qa-f20-invalid-key", "num_retries": 0}}' \
    --check valid eq false --check error.type exists --check error.message not-contains sk-qa-f20-invalid-key \
    --save F20.validate-rejects/invalid-key
  control-agent-server api GET /api/profiles/qa-draft --expect 404
  ```
  Both are 200 with `valid: false` and a typed `error`; the provider's
  message is passed through redacted and does not contain the posted key.
- **DeepSeek 401 type (`F20.validate-bad-key-type`), known bug.** The invalid
  DeepSeek key should be classified like any provider 401.
  ```sh
  control-agent-server api POST /api/profiles/qa-draft/validate --timeout 120 --expect 200 \
    --json '{"llm": {"model": "deepseek/deepseek-flash", "api_key": "sk-qa-f20-invalid-key", "num_retries": 0}}' \
    --check valid eq false --check error.message contains authentication_error --check error.message not-contains sk-qa-f20-invalid-key \
    --save F20.validate-bad-key-type/deepseek-401 | tee "$F20/bad-key.json"
  jq -e '.response.body.error.type == "LLMAuthenticationError"' "$F20/bad-key.json"  # bug
  ```
  The call itself proves the case: `valid: false`, and the provider's error
  passed through in `error.message` is DeepSeek's typed
  `"type":"authentication_error"`. Expected `error.type`
  `LLMAuthenticationError` (what an OpenAI-compatible 401 gives in
  `F20.validate-provider-errors`). Today it is `LLMBadRequestError`, so the
  `jq` check marked `# bug` fails: DeepSeek answers HTTP 401 with
  `Authentication Fails, Your api key: ... is invalid`, LiteLLM raises it as
  `BadRequestError`, and the SDK's auth heuristics (`AUTH_PATTERNS` in
  `openhands-sdk/openhands/sdk/llm/exceptions/classifier.py`) match neither
  that text nor the `authentication_error` type, so clients cannot tell a bad
  key from a bad request.
- **Provider failures (`F20.validate-provider-errors`).** Script an
  `llm-stub` to answer 401, 500, 429 and to hang, and point a draft with
  retries off at it and at a closed port.
  ```sh
  STUB=$(control-agent-server fixture llm-stub --name qa-f20-stub --step status:401 --print-path)
  STUB_LLM="{\"model\": \"openai/qa-stub\", \"base_url\": \"$STUB/v1\", \"api_key\": \"qa-stub-key\", \"num_retries\": 0}"
  control-agent-server api POST /api/profiles/qa-stub/validate --json "{\"llm\": $STUB_LLM}" --timeout 60 --expect 200 \
    --check valid eq false --check error.type eq LLMAuthenticationError --save F20.validate-provider-errors/401
  control-agent-server sink read --name qa-f20-stub --contains 'Reply with one token.' --expect-min 1 --save F20.validate-provider-errors/stub-request
  control-agent-server sink read --name qa-f20-stub --contains '"max_tokens": 1' --expect-min 1
  control-agent-server sink read --name qa-f20-stub --expect-min 1 \
    --contains '"role": "system"}, {"content": [{"type": "text", "text": "ping"}], "role": "user"}'
  control-agent-server fixture llm-stub --name qa-f20-stub --step status:500
  control-agent-server api POST /api/profiles/qa-stub/validate --json "{\"llm\": $STUB_LLM}" --timeout 60 --expect 200 \
    --check valid eq false --check error.type eq LLMServiceUnavailableError --save F20.validate-provider-errors/500
  control-agent-server fixture llm-stub --name qa-f20-stub --step status:429
  control-agent-server api POST /api/profiles/qa-stub/validate --json "{\"llm\": $STUB_LLM}" --timeout 60 --expect 200 \
    --check valid eq true --check error missing --save F20.validate-provider-errors/429
  control-agent-server fixture llm-stub --name qa-f20-stub --step hang:30
  control-agent-server api POST /api/profiles/qa-stub/validate --timeout 60 --expect 200 --expect-max-ms 15000 \
    --json "{\"llm\": {\"model\": \"openai/qa-stub\", \"base_url\": \"$STUB/v1\", \"api_key\": \"qa-stub-key\", \"num_retries\": 0, \"timeout\": 3}}" \
    --check valid eq true --save F20.validate-provider-errors/timeout
  control-agent-server sink read --name qa-f20-stub --contains '"hang": 30' --expect-min 1 --expect-max 1
  control-agent-server api POST /api/profiles/qa-unreachable/validate --timeout 60 --expect 200 \
    --json '{"llm": {"model": "openai/qa-unreachable", "base_url": "http://127.0.0.1:9/v1", "api_key": "qa-key", "num_retries": 0}}' \
    --check valid eq false --check error.type eq LLMServiceUnavailableError --save F20.validate-provider-errors/unreachable
  control-agent-server api GET /api/profiles --check profiles len-eq 4
  ```
  The stub's first request carries the system message `Reply with one
  token.` directly before the user `ping` (the repository's system-first
  invariant), and `max_tokens: 1`; 401 maps to
  `LLMAuthenticationError`, 500 and the closed port to
  `LLMServiceUnavailableError` (`valid: false`), while 429 and the 3-second
  timeout are transient (`valid: true`). The timeout verdict arrives in about
  3 seconds (under the 15-second bound) although the stub, which logged
  exactly one request on its `hang` step, would only answer after 30 s; a
  server that ignored the posted `timeout` would wait for that reply and
  fail the bound. Nothing is saved.
- **Subscription without a login (`F20.validate-subscription-login`).**
  Pre-flight a ChatGPT-subscription config (`auth_type: subscription`, the
  Agent Canvas subscription path) on this run, whose private home holds no
  OpenAI subscription login.
  ```sh
  control-agent-server api POST /api/profiles/qa-sub/validate --timeout 60 --expect 200 --expect-max-ms 10000 \
    --json '{"llm": {"model": "openai/gpt-5-mini", "auth_type": "subscription"}}' \
    --check valid eq false --check error.type eq ValueError --check error.message eq 'OpenAI subscription login is required' \
    --save F20.validate-subscription-login/no-login
  control-agent-server api GET /api/profiles/qa-sub --expect 404
  ```
  The server looks for stored subscription credentials before any provider
  call, finds none, and answers `valid: false` with the factory's
  `ValueError` within a second (the handler maps credential errors to a
  verdict instead of a 500). A real subscription login is not needed for
  this path; validating with one is out of scope here.
- **Unreachable endpoint is slow (`F20.validate-unreachable-bounded`), known bug.**
  The same closed port with the default retry policy (no `num_retries` in the
  body).
  ```sh
  control-agent-server api POST /api/profiles/qa-unreachable/validate --timeout 240 --expect 200 \
    --json '{"llm": {"model": "openai/qa-unreachable", "base_url": "http://127.0.0.1:9/v1", "api_key": "qa-key"}}' \
    --check valid eq false --check error.type eq LLMServiceUnavailableError \
    --save F20.validate-unreachable-bounded/default-retries | tee "$F20/unreachable.json"
  jq -e '.elapsed_ms <= 30000' "$F20/unreachable.json"  # bug
  ```
  The client waits long enough for the server's own answer, so the call
  proves the server is up and its verdict is right (`valid: false`,
  `LLMServiceUnavailableError`); the `jq` bound marked `# bug` then checks
  how long that took. Expected a verdict within 30 s. Today it takes about
  120 s (`elapsed_ms` near 121000): `validate` calls the posted LLM
  unchanged, so a connection error is retried with the LLM's defaults
  (`num_retries` 5 means five attempts, with waits of 8, 16, 32 and 64 s),
  twice the TypeScript `ProfilesClient`'s 60 s default timeout. Because the
  CLI waits for the answer, no server-side retry outlives the bullet.
- **Hanging provider is slower (`F20.validate-hang-bounded`), known bug.**
  An `llm-stub` that never answers, with the default timeout and retries.
  ```sh
  HANG=$(control-agent-server fixture llm-stub --name qa-f20-hang --step hang:900 --print-path)
  RC=0
  control-agent-server api POST /api/profiles/qa-hang/validate --timeout 30 --expect 200 \
    --json "{\"llm\": {\"model\": \"openai/qa-stub\", \"base_url\": \"$HANG/v1\", \"api_key\": \"qa-stub-key\"}}" \
    --check valid exists --save F20.validate-hang-bounded/default-timeout | tee "$F20/hang.json" || RC=$?
  control-agent-server sink read --name qa-f20-hang --contains '"hang": 900' --expect-min 1 --save F20.validate-hang-bounded/stub-request
  control-agent-server api GET /alive --auth none --expect 200 --expect-max-ms 3000
  control-agent-server restart --hard
  control-agent-server api GET /api/profiles --expect 200 --check active_profile eq deepseek-flash
  test "$RC" = 0 || jq -e '.exit == 3 and (.error // "" | startswith("ReadTimeout"))' "$F20/hang.json"
  test "$RC" = 0  # bug
  ```
  Expected a verdict within 30 s (the CLI's `--timeout 30` is the bound).
  Today the CLI gives up with a client `ReadTimeout`, so `test "$RC" = 0`
  marked `# bug` fails. The lines before it make that failure mean the
  bug and nothing else: the stub logged the request (the server reached
  the provider; today it is still waiting on that first attempt),
  `/alive` answers at once afterwards (the server is up and its event loop
  free; the slowness is the route's own), and the guard accepts a failed
  call only when it is a client `ReadTimeout` (a refused connection, a 5xx
  or a wrong verdict fails the bullet instead). Each attempt waits for the
  LLM's 300 s `timeout` and the timeout is retried, so the request would
  stay open for about 27 minutes (five attempts of 300 s plus 120 s of
  backoff) and then answer `valid: true` (a timeout counts as transient),
  approving a provider that never answered. Measured with `"timeout": 2` in
  the body: 130 s, five requests at the stub, then `valid: true`. The
  `restart --hard` (SIGKILL) cancels that server-side request so it does
  not outlive the bullet; profiles and settings are on disk and come back
  unchanged.
- **Activate (`F20.activate`).** Make `qa-flash` the active LLM and read the
  settings back.
  ```sh
  control-agent-server api POST /api/profiles/qa-flash/activate --expect 200 --check name eq qa-flash --check llm_applied eq true \
    --check message eq "Profile 'qa-flash' activated and applied to current settings" --save F20.activate/activate
  control-agent-server api GET /api/profiles --check active_profile eq qa-flash --save F20.activate/list
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --expect 200 --max-chars 300 \
    --check active_profile eq qa-flash --check agent_settings.llm.model eq deepseek/deepseek-flash \
    --check agent_settings.llm.api_key eq "$DEEPSEEK_API_KEY" --check agent_settings.llm.num_retries eq 0 \
    --check llm_api_key_is_set eq true --save F20.activate/settings
  control-agent-server state cat home/.openhands/settings.json --max-chars 300 \
    --check active_profile eq qa-flash --check agent_settings.llm.api_key matches '^gAAAAA'
  ```
  The response says the LLM was applied; settings carry `qa-flash`'s model,
  key and `num_retries: 0` (the preset's profile has 5), and `settings.json`
  stores the key encrypted.
- **Activation drives conversations (`F20.activate-conversation`).** Start a
  conversation from the saved settings with no tools and a one-word prompt.
  ```sh
  CID=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Reply with one word: pong' --wait --timeout 180 --print-id)
  control-agent-server api GET "/api/conversations/$CID" --expect 200 --max-chars 300 \
    --check execution_status eq finished --check agent.llm.model eq deepseek/deepseek-flash --check agent.llm.num_retries eq 0 \
    --check stats.usage_to_metrics.default.model_name eq deepseek/deepseek-flash \
    --check stats.usage_to_metrics.default.accumulated_token_usage.prompt_tokens gt 0 --save F20.activate-conversation/conversation
  control-agent-server api DELETE "/api/conversations/$CID" --expect 200
  ```
  The conversation finishes on `deepseek/deepseek-flash` with
  `num_retries: 0` (so the agent came from `qa-flash`, not the preset), and
  its usage metrics show real prompt tokens. The conversation is deleted.
- **ACP settings (`F20.activate-acp`).** Switch the agent kind to ACP,
  activate, then restore.
  ```sh
  control-agent-server api PATCH /api/settings --json '{"agent_settings_diff": {"agent_kind": "acp"}}' --expect 200 --max-chars 200 \
    --check agent_settings.agent_kind eq acp
  control-agent-server api POST /api/profiles/qa-nokey/activate --expect 200 --check llm_applied eq false \
    --check message eq "Profile 'qa-nokey' activated" --save F20.activate-acp/activate
  control-agent-server api GET /api/settings --expect 200 --max-chars 200 --check active_profile eq qa-nokey \
    --check agent_settings.agent_kind eq acp --check agent_settings.llm missing --save F20.activate-acp/settings
  control-agent-server api PATCH /api/settings --json '{"agent_settings_diff": {"agent_kind": "openhands"}}' --expect 200 --max-chars 200 \
    --check agent_settings.agent_kind eq openhands
  control-agent-server api POST /api/profiles/qa-flash/activate --expect 200 --check llm_applied eq true
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --max-chars 200 --check active_profile eq qa-flash \
    --check agent_settings.llm.model eq deepseek/deepseek-flash --check agent_settings.llm.api_key eq "$DEEPSEEK_API_KEY"
  ```
  On ACP settings the activation answers `llm_applied: false` and only moves
  `active_profile`; back on OpenHands settings `qa-flash` is re-activated for
  the next bullets.
- **SDK consumer (`F20.sdk-get-llm`).** `RemoteWorkspace.get_llm()` by name,
  from the active pointer, with an override, and for an unknown name.
  ```sh
  control-agent-server exec --env OPENHANDS_SUPPRESS_BANNER=1 --timeout 120 --expect-output QA_F20_SDK_OK \
    --save F20.sdk-get-llm/program -- .venv/bin/python "$F20/sdk_get_llm.py"
  ```
  The program prints `QA_F20_SDK_OK`: `get_llm(profile_name="qa-flash")` is
  `deepseek/deepseek-flash` with `num_retries: 0`, the DeepSeek key and
  `usage_id` `profile:qa-flash`; `get_llm()` resolves the active `qa-flash`;
  `temperature=0.5` overrides `qa-nokey`'s stored `0.25` (and its key stays
  unset); `qa-missing` raises `FileNotFoundError` from the server's 404.
- **TypeScript client (`F20.ts-client`).** Every `ProfilesClient` method
  from the built client (built here when missing).
  ```sh
  test -f clients/typescript/dist/clients.js || (cd clients/typescript && npm ci && npm run build)
  control-agent-server exec --timeout 120 --expect-output QA_F20_TS_OK --save F20.ts-client/program -- node "$F20/ts_profiles.mjs"
  control-agent-server api GET /api/profiles --check active_profile eq qa-flash --check profiles len-eq 4 \
    --check profiles not-contains '"name": "qa-ts' --save F20.ts-client/list-after
  ```
  The program prints `QA_F20_TS_OK`: the saved key is hidden, plaintext and
  `gAAAAA` in the three reads, `validateProfile` against the closed port is
  `valid: false` `LLMServiceUnavailableError`, the rename and activation
  move `active_profile`, and the deleted name and an invalid name reject
  with `HttpError` status 404 and 422. The list afterwards has the same four
  profiles and `qa-flash` active again.
- **Dangling provider link (`F20.activate-dangling-provider`).** Save a
  profile linked to a provider connection id that does not exist, with an
  inline key and `base_url`.
  ```sh
  control-agent-server api POST /api/profiles/qa-linked --expect 201 \
    --json '{"llm": {"model": "openai/qa-linked", "api_key": "sk-qa-f20-linked", "base_url": "http://127.0.0.1:9/v1", "provider_connection_id": "qa-no-such-connection"}}'
  control-agent-server state cat home/.openhands/profiles/qa-linked.json --not-contains sk-qa-f20-linked \
    --check provider_connection_id eq qa-no-such-connection --check api_key missing --check base_url missing
  control-agent-server api GET /api/profiles \
    --check profiles contains '"name": "qa-linked", "model": "openai/qa-linked", "base_url": null, "provider_connection_id": "qa-no-such-connection", "provider_connection_broken": true, "api_key_set": false' \
    --save F20.activate-dangling-provider/list
  control-agent-server api GET /api/profiles/qa-linked --expect 200 \
    --check config.provider_connection_id eq qa-no-such-connection --check config.api_key missing --check api_key_set eq false
  control-agent-server api GET /api/profiles/qa-linked --header 'X-Expose-Secrets: plaintext' --expect 422 \
    --check detail eq "Profile 'qa-linked' references provider connection 'qa-no-such-connection', which does not exist. Update the profile or recreate the connection."
  control-agent-server api POST /api/profiles/qa-linked/activate --expect 422 \
    --check detail eq "Profile 'qa-linked' references provider connection 'qa-no-such-connection', which does not exist. Update the profile or recreate the connection." \
    --save F20.activate-dangling-provider/activate
  control-agent-server api GET /api/profiles --check active_profile eq qa-flash
  control-agent-server api DELETE /api/profiles/qa-linked --expect 200
  ```
  The inline key and `base_url` are dropped on save (the connection owns
  them), the list flags the link as broken, the default `GET` still works,
  and both a plaintext `GET` (which resolves the link, since #4952) and the
  activation are 422 with the same message, with `qa-flash` still active.
- **Linked provider (`F20.activate-linked-provider`).** Create a provider
  connection that holds the DeepSeek key (arrange), link a profile to it with
  a stray inline key, and activate it.
  ```sh
  CONN=$(printf '{"display_name": "qa-f20-conn", "provider": "deepseek", "api_key": "%s"}' "$DEEPSEEK_API_KEY" \
    | control-agent-server api POST /api/llm/provider-connections --stdin --expect 201 --field id)
  control-agent-server api POST /api/profiles/qa-linked-live --expect 201 \
    --json "{\"llm\": {\"model\": \"deepseek/deepseek-flash\", \"provider_connection_id\": \"$CONN\", \"api_key\": \"sk-qa-f20-inline\", \"num_retries\": 0}}"
  control-agent-server state cat home/.openhands/profiles/qa-linked-live.json --not-contains sk-qa-f20-inline \
    --check api_key missing --check provider_connection_id eq "$CONN"
  control-agent-server api GET /api/profiles --save F20.activate-linked-provider/list \
    --check profiles contains "\"name\": \"qa-linked-live\", \"model\": \"deepseek/deepseek-flash\", \"base_url\": null, \"provider_connection_id\": \"$CONN\", \"provider_connection_broken\": false, \"api_key_set\": true"
  control-agent-server api GET /api/profiles/qa-linked-live --expect 200 \
    --check config.api_key missing --check config.provider_connection_id eq "$CONN" --check api_key_set eq true --save F20.activate-linked-provider/get
  control-agent-server api GET /api/profiles/qa-linked-live --header 'X-Expose-Secrets: plaintext' --expect 200 --quiet \
    --check config.api_key eq "$DEEPSEEK_API_KEY" --check config.provider_connection_id eq "$CONN"
  control-agent-server api POST /api/profiles/qa-linked-live/activate --expect 200 --check llm_applied eq true --save F20.activate-linked-provider/activate
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --max-chars 200 --check active_profile eq qa-linked-live \
    --check agent_settings.llm.api_key eq "$DEEPSEEK_API_KEY" --check agent_settings.llm.provider_connection_id eq "$CONN" \
    --check llm_api_key_is_set eq true --save F20.activate-linked-provider/settings
  control-agent-server api POST /api/profiles/qa-flash/activate --expect 200 --check llm_applied eq true
  control-agent-server api DELETE /api/profiles/qa-linked-live --expect 200
  control-agent-server api DELETE "/api/llm/provider-connections/$CONN" --expect 200
  control-agent-server api GET /api/profiles --check active_profile eq qa-flash --check profiles len-eq 4
  ```
  The file keeps only the connection id (no inline key); the list and `GET`
  report `api_key_set: true` from the connection; the default `GET` shows a
  `null` key, and a plaintext `GET` resolves the link and returns the
  connection's key (since #4952); the activation resolves the connection and
  writes its key into `agent_settings.llm`, keeping `provider_connection_id`.
  `qa-flash` is re-activated, then the profile and the connection are
  deleted.
- **SDK on a linked profile (`F20.sdk-get-llm-linked`).** Build an
  SDK conversation from `get_llm()` of a profile linked to a connection that
  holds the DeepSeek key, after the same program finishes on the plain
  `qa-flash` profile (positive control: same key, model and program).
  ```sh
  CONN=$(printf '{"display_name": "qa-f20-sdk-conn", "provider": "deepseek", "api_key": "%s"}' "$DEEPSEEK_API_KEY" \
    | control-agent-server api POST /api/llm/provider-connections --stdin --expect 201 --field id)
  control-agent-server api POST /api/profiles/qa-linked-sdk --expect 201 \
    --json "{\"llm\": {\"model\": \"deepseek/deepseek-flash\", \"provider_connection_id\": \"$CONN\", \"num_retries\": 0}}"
  control-agent-server exec --env OPENHANDS_SUPPRESS_BANNER=1 --timeout 180 --expect-output 'QA_F20_CONV_KEY_SET True' \
    --expect-output QA_F20_CONV_OK --save F20.sdk-get-llm-linked/plain-profile -- .venv/bin/python "$F20/sdk_conversation.py" qa-flash
  control-agent-server exec --env OPENHANDS_SUPPRESS_BANNER=1 --timeout 180 --expect-output 'QA_F20_CONV_KEY_SET True' \
    --expect-output QA_F20_CONV_OK --save F20.sdk-get-llm-linked/program -- .venv/bin/python "$F20/sdk_conversation.py" qa-linked-sdk
  control-agent-server api DELETE /api/profiles/qa-linked-sdk --expect 200
  control-agent-server api DELETE "/api/llm/provider-connections/$CONN" --expect 200
  ```
  The linked-profile conversation finishes on the connection's key, like the
  plain-profile control: `get_llm()` reads `GET /api/profiles/{name}` with
  `X-Expose-Secrets: plaintext`, which resolves `provider_connection_id`
  since #4952 (before that it returned a keyless LLM and `run()` failed with
  `LLMAuthenticationError`).
- **No cipher (`F20.no-cipher`).** Launch a second server with neither a
  session key nor `OH_SECRET_KEY` (so no cipher at all), save a keyed
  profile, read it and activate it there.
  ```sh
  B=$(control-agent-server launch --new --print-run --name f20-nocipher --no-auth --no-secret-key)
  trap 'control-agent-server stop --run "$B" > /dev/null 2>&1 || true' EXIT
  control-agent-server api POST /api/profiles/qa-plain --run "$B" --expect 201 \
    --json '{"llm": {"model": "openai/qa-plain", "api_key": "sk-qa-f20-plain-key"}}' --save F20.no-cipher/save
  control-agent-server state cat home/.openhands/profiles/qa-plain.json --run "$B" --mode 600 --check api_key eq sk-qa-f20-plain-key
  control-agent-server api GET /api/profiles --run "$B" --check profiles contains '"name": "qa-plain", "model": "openai/qa-plain", "base_url": null, "provider_connection_id": null, "provider_connection_broken": false, "api_key_set": true'
  control-agent-server api GET /api/profiles/qa-plain --run "$B" --header 'X-Expose-Secrets: plaintext' --expect 200 \
    --check config.api_key eq sk-qa-f20-plain-key --save F20.no-cipher/plaintext
  control-agent-server api GET /api/profiles/qa-plain --run "$B" --header 'X-Expose-Secrets: encrypted' --expect 503 \
    --check exception contains 'Encryption not available: OH_SECRET_KEY is not configured' --save F20.no-cipher/encrypted
  control-agent-server api POST /api/profiles/qa-plain/activate --run "$B" --expect 200 --check llm_applied eq true
  control-agent-server state cat home/.openhands/settings.json --run "$B" --max-chars 200 --check agent_settings.llm.api_key eq sk-qa-f20-plain-key
  control-agent-server stop --run "$B"
  ```
  The profile file (mode `600`) and `settings.json` hold the key in
  plaintext, the plaintext read returns it, and the `encrypted` read is 503
  (message in `exception`). The second server is stopped, by the `trap` as
  well when a check fails, so a failing bullet never leaks it; the main run's
  files stay encrypted (`F20.key-encrypted-at-rest`). The `--save` files of
  this bullet land under the second run's `evidence/` directory.
- **Restart (`F20.persist-restart`).** Restart the run and read everything
  back.
  ```sh
  control-agent-server restart
  control-agent-server api GET /api/profiles --expect 200 --check active_profile eq qa-flash --check profiles len-eq 4 \
    --check profiles contains '"name": "qa-flash", "model": "deepseek/deepseek-flash", "base_url": null, "provider_connection_id": null, "provider_connection_broken": false, "api_key_set": true' \
    --check profiles contains '"name": "qa-nokey", "model": "openai/qa-nokey", "base_url": null, "provider_connection_id": null, "provider_connection_broken": false, "api_key_set": false' \
    --save F20.persist-restart/list
  control-agent-server api GET /api/profiles/qa-flash --header 'X-Expose-Secrets: plaintext' --expect 200 \
    --check config.api_key eq "$DEEPSEEK_API_KEY" --check config.num_retries eq 0 --save F20.persist-restart/plaintext
  control-agent-server api GET /api/profiles/qa-nokey --check config.temperature eq 0.25
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --max-chars 200 \
    --check active_profile eq qa-flash --check agent_settings.llm.api_key eq "$DEEPSEEK_API_KEY"
  ```
  The four profiles, `qa-flash`'s decryptable key and options, `qa-nokey`'s
  overwrite and the active pointer all survive.
- **Rename (`F20.rename`).** Rename the active profile that `qa-f20-agent`
  cites.
  ```sh
  control-agent-server api POST /api/profiles/qa-flash/rename --json '{"new_name": "qa-fast"}' --expect 200 --check name eq qa-fast \
    --check message eq "Profile 'qa-flash' renamed to 'qa-fast'" --save F20.rename/rename
  control-agent-server api GET /api/profiles/qa-flash --expect 404
  control-agent-server api GET /api/profiles/qa-fast --header 'X-Expose-Secrets: plaintext' --expect 200 \
    --check config.model eq deepseek/deepseek-flash --check config.api_key eq "$DEEPSEEK_API_KEY"
  control-agent-server api GET /api/profiles --check active_profile eq qa-fast --check profiles not-contains '"name": "qa-flash"' --save F20.rename/list
  control-agent-server api GET /api/settings --max-chars 200 --check active_profile eq qa-fast --check agent_settings.llm.model eq deepseek/deepseek-flash
  control-agent-server api GET /api/agent-profiles/qa-f20-agent --expect 200 --max-chars 200 --check profile.llm_profile_ref eq qa-fast \
    --save F20.rename/agent-profile
  test -f "$AGENT_SERVER_VERIFY_RUN/home/.openhands/profiles/qa-fast.json"
  test ! -e "$AGENT_SERVER_VERIFY_RUN/home/.openhands/profiles/qa-flash.json"
  ```
  The old name is gone, the new one keeps the key, `active_profile` is
  `qa-fast` in the list and in settings, and the agent profile's
  `llm_profile_ref` follows.
- **Rename errors (`F20.rename-errors`).** Rename onto a taken name, onto
  itself, and to invalid names.
  ```sh
  control-agent-server api POST /api/profiles/qa-fast/rename --json '{"new_name": "qa-nokey"}' --expect 409 \
    --check detail eq "Profile 'qa-nokey' already exists" --save F20.rename-errors/conflict
  control-agent-server api GET /api/profiles/qa-nokey --check config.model eq openai/qa-nokey --check config.temperature eq 0.25
  control-agent-server api POST /api/profiles/qa-fast/rename --json '{"new_name": "qa-fast"}' --expect 200 \
    --check message eq "Profile 'qa-fast' unchanged (same name)" --save F20.rename-errors/same-name
  control-agent-server api POST /api/profiles/qa-fast/rename --json '{"new_name": ".qa-hidden"}' --expect 422 --check detail.0.loc.1 eq new_name
  control-agent-server api POST /api/profiles/qa-fast/rename --json '{}' --expect 422 --check detail.0.type eq missing
  control-agent-server api GET /api/profiles --check active_profile eq qa-fast --check profiles len-eq 4
  ```
  The conflict is 409 and both profiles are untouched; the same-name rename
  is a 200 no-op; the bad bodies are 422.
- **Guarded delete (`F20.delete-guarded`).** Delete the profile
  `qa-f20-agent` still cites.
  ```sh
  control-agent-server api DELETE /api/profiles/qa-fast --expect 409 \
    --check detail eq 'LLM profile is referenced by 1 agent profile(s): qa-f20-agent' --save F20.delete-guarded/delete
  control-agent-server api GET /api/profiles/qa-fast --expect 200
  control-agent-server api GET /api/profiles --check active_profile eq qa-fast
  control-agent-server api DELETE /api/agent-profiles/qa-f20-agent --expect 200
  ```
  The delete is 409 naming the referrer and the profile stays active; the
  agent profile is then deleted so `F20.delete` can proceed.
- **Delete (`F20.delete`).** Delete the active profile, twice.
  ```sh
  control-agent-server api DELETE /api/profiles/qa-fast --expect 200 --check name eq qa-fast \
    --check message eq "Profile 'qa-fast' deleted" --save F20.delete/delete
  control-agent-server api GET /api/profiles/qa-fast --expect 404
  control-agent-server api GET /api/profiles --check active_profile missing --check profiles len-eq 3 \
    --check profiles not-contains '"name": "qa-fast"' --save F20.delete/list
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --max-chars 200 --check active_profile missing \
    --check agent_settings.llm.model eq deepseek/deepseek-flash --check agent_settings.llm.api_key eq "$DEEPSEEK_API_KEY" \
    --save F20.delete/settings
  test ! -e "$AGENT_SERVER_VERIFY_RUN/home/.openhands/profiles/qa-fast.json"
  control-agent-server api DELETE /api/profiles/qa-fast --expect 200 --check message eq "Profile 'qa-fast' deleted" --save F20.delete/repeat
  control-agent-server api POST /api/profiles/deepseek-flash/activate --expect 200 --check llm_applied eq true
  ```
  The profile and its file are gone and `active_profile` is `null` in both
  views, while `agent_settings.llm` still holds the deleted profile's model
  and key; the repeated delete is 200 too. `deepseek-flash` is re-activated
  to restore the preset state.
- **Delete through the `.json` alias (`F20.json-alias-guard`), known bug.**
  Save `qa-alias`, cite it from a new agent profile, and delete it as
  `qa-alias.json`.
  ```sh
  control-agent-server api POST /api/profiles/qa-alias --json '{"llm": {"model": "openai/qa-alias"}}' --expect 201
  control-agent-server api POST /api/agent-profiles/qa-f20-alias-agent --json '{"llm_profile_ref": "qa-alias"}' --expect 201
  control-agent-server api DELETE /api/profiles/qa-alias --expect 409 \
    --check detail eq 'LLM profile is referenced by 1 agent profile(s): qa-f20-alias-agent'
  control-agent-server api GET /api/profiles/qa-alias.json --expect 200 --check config.model eq openai/qa-alias
  control-agent-server api DELETE /api/profiles/qa-alias.json --expect 200,409 --save F20.json-alias-guard/delete | tee "$F20/alias-delete.json"
  control-agent-server api GET /api/profiles/qa-alias --expect 200,404 --save F20.json-alias-guard/after | tee "$F20/alias-after.json"
  control-agent-server api DELETE /api/agent-profiles/qa-f20-alias-agent --expect 200
  control-agent-server api DELETE /api/profiles/qa-alias --expect 200
  control-agent-server api GET /api/profiles --check profiles not-contains '"name": "qa-alias"'
  jq -e '.status == 409' "$F20/alias-delete.json"  # bug
  jq -e '.status == 200' "$F20/alias-after.json"  # bug
  ```
  Expected the alias delete to be 409 like the plain one (positive control:
  `DELETE /api/profiles/qa-alias` is 409, and `GET .../qa-alias.json` reads
  the same profile), and `qa-alias` to survive it. The alias delete and the
  read after it accept either outcome and are recorded; cleanup runs; then
  the first `jq` marked `# bug` checks the delete's status (the second, the
  profile's survival, each alone encoding the bug). Today the alias delete
  answers 200 `Profile 'qa-alias.json' deleted`
  and `qa-alias` is gone while `qa-f20-alias-agent` still cites it: the store
  strips a `.json` suffix (`_get_profile_path` in
  `openhands-sdk/openhands/sdk/llm/llm_profile_store.py`), but the FK scan
  (`_scan_referrers` in `openhands-sdk/openhands/sdk/profiles/profile_refs.py`)
  and `_set_active_profile_if_matches` (`profiles_router.py`) compare the raw
  path name. The same mismatch, checked by hand on a review run, leaves
  `active_profile` naming a deleted profile after an alias delete, and an
  alias rename (`POST /api/profiles/qa-alias.json/rename`) moves the file
  without moving `active_profile` or the agent profile's `llm_profile_ref`.
  The bullet removes the agent profile and the (possibly already deleted)
  profile, and checks the list no longer has it, before it fails.
- **Secret key rotation (`F20.secret-key-rotation`), known bug.** Save a
  profile, restart with a new secret key, then activate it.
  ```sh
  control-agent-server api POST /api/profiles/qa-rotate --json '{"llm": {"model": "openai/qa-rotate", "api_key": "sk-qa-f20-rotate"}}' --expect 201
  control-agent-server api GET /api/profiles/qa-rotate --header 'X-Expose-Secrets: plaintext' --expect 200 --check config.api_key eq sk-qa-f20-rotate
  control-agent-server api GET /api/settings --max-chars 200 --check active_profile eq deepseek-flash --check llm_api_key_is_set eq true
  control-agent-server restart --rotate-secret-key
  control-agent-server state cat home/.openhands/profiles/qa-rotate.json --check api_key matches '^gAAAAA'
  control-agent-server api GET /api/profiles/qa-rotate --header 'X-Expose-Secrets: plaintext' --expect 2xx,4xx --save F20.secret-key-rotation/detail
  control-agent-server api GET /api/profiles --expect 2xx,4xx --save F20.secret-key-rotation/list
  control-agent-server api POST /api/profiles/qa-rotate/activate --expect 4xx --save F20.secret-key-rotation/activate  # bug
  control-agent-server api GET /api/settings --max-chars 200 --check active_profile eq deepseek-flash
  control-agent-server state cat home/.openhands/settings.json --max-chars 200 --check agent_settings.llm.api_key matches '^gAAAAA'
  ```
  Before the restart the key reads back and `deepseek-flash` is active with
  a key (positive control). Expected the activation to be refused (4xx)
  while the stored key cannot be decrypted, leaving `deepseek-flash` active
  and the settings' old ciphertext in place. Today the file still holds the
  old token, `GET` by name says `api_key_set: false` with a `null` key while
  the list still says `api_key_set: true`, and the activation marked
  `# bug` answers 200 `activated and applied to current settings` after
  writing an LLM with `api_key: null` over the settings' stored key
  (`llm_api_key_is_set` becomes `false`), so the next conversation fails at
  the provider. The cipher returns `None`
  for a token it cannot decrypt instead of raising
  (`openhands-sdk/openhands/sdk/utils/cipher.py`), and the profile store
  loads that silently. The same happens without `OH_SECRET_KEY` when only
  the session key rotates, because the first session key is then the cipher
  (checked on a `launch --no-secret-key` run: after `restart --rotate-key`
  a plaintext read returns `null` and `api_key_set: false`). Run last: every
  stored secret of this run is now unreadable.

## Gotchas

- The DeepSeek flash model string is `deepseek/deepseek-flash` (what the
  `deepseek` preset saves). `deepseek/deepseek-v4.1-flash` saves fine (models
  are not checked on save) but `validate` answers `valid: false`,
  `LLMBadRequestError`: DeepSeek only serves `deepseek-flash` and
  `deepseek-v4-pro`.
- `validate` uses the posted LLM's own retry and timeout policy (defaults
  `num_retries` 5, `retry_min_wait` 8, `retry_max_wait` 64, `timeout` 300).
  Always send `"num_retries": 0` (and a small `timeout` against stubs) unless
  the duration is what you test, and give the CLI a `--timeout` longer than
  the policy; `llm set` and `llm preset` validate with a 120 s client timeout.
  Requests the CLI gave up on keep running in the server until the policy
  ends.
- `validate` never reads the stored profile: the body's `llm` is required and
  the path name only appears in the log. To pre-flight a stored profile, read
  it with `X-Expose-Secrets: encrypted` and post that config back.
- Rate limits and timeouts count as transient and answer `valid: true`; a
  provider 503 or a refused connection is `valid: false`. A `valid: true` is
  therefore not proof that the provider answered.
- A real DeepSeek bad key is `LLMBadRequestError`, not
  `LLMAuthenticationError` (`F20.validate-bad-key-type`); match on `valid`,
  not on `error.type`, when the provider is DeepSeek.
- The list's `api_key_set` is computed from the raw file without decrypting
  (any non-empty value except `**********` counts), while `GET` by name
  decrypts; they disagree when a token cannot be decrypted
  (`F20.secret-key-rotation`). A linked provider connection's key counts as
  the profile's key in both.
- Saving never changes `active_profile`, even when the active profile is
  overwritten: `agent_settings.llm` keeps the old copy until the profile is
  activated again. Deleting the active profile clears the pointer but leaves
  the applied LLM in `agent_settings.llm`.
- Activation on ACP settings only moves the pointer. Switching
  `agent_kind` back to `openhands` with `PATCH /api/settings` resets
  `agent_settings.llm` to the defaults while `active_profile` keeps naming the
  profile, so `RemoteWorkspace.get_llm()` (which follows the pointer) and a
  conversation started from settings disagree until the profile is activated
  again.
- `GET /api/agent-profiles` (the list) seeds a `default` agent profile that
  references the active LLM profile when the agent-profile store is empty;
  after that, deleting the active LLM profile is 409. This family only reads
  agent profiles by name, which seeds nothing.
- Names ending in `.json` are stored without the suffix, so `foo` and
  `foo.json` are the same profile for `GET`, save, delete and rename, but the
  agent-profile delete guard, the rename cascade and the `active_profile`
  pointer compare names literally (`F20.json-alias-guard`). Never address a
  profile through the alias.
- `launch` sets `OH_SECRET_KEY`. `--no-secret-key` alone still leaves a
  cipher, derived from the first session key (and `restart --rotate-key` then
  loses every stored key, as in `F20.secret-key-rotation`); only
  `--no-auth --no-secret-key` gives a server without a cipher
  (`F20.no-cipher`).
- 5xx answers from these routes (503 store busy, 503 encryption not
  available) carry `detail: "Internal Server Error"`; the route's own message
  is in `exception` (the server's 5xx handler rewrites `detail`). Assert the
  status and `exception`, not `detail`.
- Profile routes are `async def` handlers that call the synchronous,
  file-locked store; while the lock is held elsewhere the whole server stops
  answering for up to 30 seconds (`F20.store-busy-responsive`). Never hold
  the lock (`flock` on `profiles/.profiles.lock`) longer than a bullet needs.
- A corrupted profile file is skipped by the list without an error (a
  warning in the log), but `GET` and `activate` by name are 400. While
  `settings.json` is unreadable, `GET /api/profiles` reports
  `active_profile: null` and activation is 409.
- A profile linked to a provider connection shows no key in the default
  `GET /api/profiles/{name}`; `X-Expose-Secrets: plaintext` resolves the
  connection and returns its key (since #4952), which is what SDK programs
  using `get_llm(profile_name=...)` rely on (`F20.sdk-get-llm-linked`).
- Profile routes emit no WebSocket events; the second views are
  `GET /api/settings`, the list and the files on disk.
- The closed port `127.0.0.1:9` stands in for an unreachable provider; if
  something listens there on your machine, pick another closed port.
