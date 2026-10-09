# LLM catalog and provider connections

What a model picker and a credentials screen read and write. The catalog is
three read-only lists served offline from the installed LiteLLM package and
the SDK's curated lists: every provider name, every model (optionally only
one provider's), and the verified models grouped by provider. A provider
connection is a named, shared credential (`display_name`, `provider`,
`api_key`, optional `base_url`) that LLM profiles reference by
`provider_connection_id` instead of carrying a key of their own, so one key
rotation reaches every linked profile the next time it is activated. The
server mints the id, never returns the key (only `api_key_set`), encrypts it
at rest with the server's cipher, refuses to read a file it cannot decrypt
rather than dropping keys, caps the store at 64 connections, and refuses to
delete a connection that a profile or the active settings still reference.

Source: `openhands-agent-server/openhands/agent_server/llm_router.py`, `openhands-agent-server/openhands/agent_server/provider_connections_router.py`, `openhands-sdk/openhands/sdk/llm/provider_connection_store.py`, `openhands-sdk/openhands/sdk/llm/utils/unverified_models.py`, `openhands-sdk/openhands/sdk/llm/utils/verified_models.py`, `clients/typescript/src/client/llm-client.ts`

Needs: `llm`, `node`, `network`

Routes: `GET /api/llm/providers`, `GET /api/llm/models`,
`GET /api/llm/models/verified`, `GET /api/llm/provider-connections`,
`POST /api/llm/provider-connections`,
`PATCH /api/llm/provider-connections/{connection_id}`,
`DELETE /api/llm/provider-connections/{connection_id}`

## Sub-features

- `F23.auth-required`: all seven routes answer 401 `{"detail": "Unauthorized"}` without a valid `X-Session-API-Key`, and nothing is created.
- `F23.providers`: `GET /api/llm/providers` returns LiteLLM's provider names, sorted and unique, including `deepseek`, `openai` and `anthropic`.
- `F23.models`: `GET /api/llm/models` returns every LiteLLM model, sorted and unique, with no `bedrock`-prefixed id, including `deepseek/deepseek-flash`.
- `F23.models-filter`: `?provider=deepseek` returns only DeepSeek models (prefixed and bare verified ids such as `deepseek-chat`), `?provider=openrouter` only `openrouter/` ids (a namespaced verified id like `deepseek/deepseek-chat` stays with DeepSeek), and an unknown provider an empty list.
- `F23.models-no-bedrock`: the provider filters list no AWS Bedrock-only model ids (`deepseek.r1-v1:0`, `anthropic.claude-...-v1:0`), as the route promises without AWS credentials.
- `F23.verified-models`: `GET /api/llm/models/verified` groups the curated models by provider (`openhands`, `anthropic`, `openai`, `deepseek`, `openrouter`, ...), each list non-empty and free of duplicates.
- `F23.catalog-ts-client`: the TypeScript `LLMMetadataClient` returns the same providers, models, filtered models and verified groups as REST, and a wrong key rejects with `HttpError` 401.
- `F23.conn-create`: `POST /api/llm/provider-connections` answers 201 with a server-minted 32-hex `id`, the fields, equal `created_at`/`updated_at` and `api_key_set: true`, never the key; `provider` defaults to `custom`; the list shows the same entries in creation order.
- `F23.conn-key-encrypted`: the connection file `home/.openhands/provider-connections/provider_connections.json` is mode `600` with `schema_version` 1 and a Fernet `gAAAAA` token as `api_key`; the plaintext key appears in no response and in no file under the server's home.
- `F23.no-cipher`: on a server with neither `OH_SECRET_KEY` nor a session key (no cipher), a connection's key is stored in plaintext in the `600` file, the list still never returns it, and activating a linked profile applies it.
- `F23.conn-create-validation`: a missing or empty `api_key` or `display_name`, an empty `provider`, a 129-character `display_name`, and any extra field (`extra_headers`, a client-chosen `id`) are 422 and create nothing.
- `F23.conn-create-blank-key`: a whitespace-only or redacted-placeholder (`**********`) `api_key` is refused like an empty one instead of creating a keyless connection.
- `F23.linked-run`: a profile saved with `provider_connection_id` stores no inline key; activating it writes the connection's key into `agent_settings.llm`, and a conversation started from those settings runs on the connection's DeepSeek key and finishes.
- `F23.conn-update`: `PATCH` renames a connection and sets or clears (`null`) its `base_url`, keeping `id`, `created_at` and the key; the list shows the change, and the next activation of a linked profile applies the connection's `base_url` (and clears it).
- `F23.conn-update-validation`: an empty body, `api_key: null` ("cannot be cleared"), a `null` `display_name` or `provider`, an empty `display_name` and extra fields are 422, an unknown id is 404, and none of them changes the stored connections.
- `F23.conn-patch-blank-key`: a `PATCH` whose `api_key` is empty, whitespace-only or the redacted placeholder never replaces the stored key: the next activation of a linked profile still applies the previous key.
- `F23.conn-rotate-key`: rotating `api_key` leaves already-activated settings on the old key; the next activation (`PATCH /api/settings` `active_profile` or `POST /api/profiles/{name}/activate`) applies the new key, which is what the provider then receives, and rotating back makes conversations finish again.
- `F23.validate-linked-draft`: `POST /api/profiles/{name}/validate` of a draft that references a connection checks it with the connection's key, so a connection holding a working DeepSeek key validates `valid: true` like the same key inline.
- `F23.persist-restart`: connections (ids, names, timestamps, `api_key_set`) survive a restart unchanged, and a linked profile still resolves the key afterwards.
- `F23.strict-decrypt`: after the server's secret key changes, list, create, update and delete answer 400 `api_key is encrypted but cannot be decrypted with the current cipher` and leave the file byte-identical; with the old key back everything works and the key is intact.
- `F23.corrupt-file`: a connection file that is not valid JSON, or has a newer `schema_version`, makes the routes answer 400 without rewriting the file; restoring it restores the connections.
- `F23.store-busy`: while another process holds the store's lock, a create waits out the 30-second lock timeout and answers 503 `Profile store is busy. Please retry.` without creating anything.
- `F23.store-busy-responsive`: while a connection request waits for the store lock, the server keeps answering other requests (`GET /alive` within 3 seconds).
- `F23.delete-guard`: deleting a connection that an LLM profile or the active `agent_settings.llm` references is 409 naming the references, and the connection stays.
- `F23.conn-delete`: deleting an unreferenced connection answers 200 with its fields and `api_key_set: false`, removes it from the list and the file, and a second delete or a `PATCH` of that id is 404.
- `F23.conn-limit`: with 64 connections stored a create is 409 `Provider connection limit reached (64). Delete one before adding another.`; deleting one frees the slot.

## How to get to it (agent POV)

- REST (catalog): `GET /api/llm/providers`, `GET /api/llm/models` with an
  optional `provider` query parameter, `GET /api/llm/models/verified`. No
  body; answers come from the installed `litellm` package and
  `VERIFIED_MODELS`, without network access.
- REST (connections): `GET /api/llm/provider-connections` (a JSON array),
  `POST /api/llm/provider-connections`
  (`{"display_name": "...", "provider": "deepseek", "api_key": "...", "base_url": null}`,
  201), `PATCH /api/llm/provider-connections/{connection_id}` (any subset of
  the same four fields; only `base_url` may be `null`),
  `DELETE /api/llm/provider-connections/{connection_id}`. Responses are
  `{id, display_name, provider, base_url, created_at, updated_at, api_key_set}`.
- Where connections take effect (second views, other families' routes):
  `POST /api/profiles/{name}` with `llm.provider_connection_id` saves a linked
  profile (its inline `api_key` and `base_url` are dropped);
  `GET /api/profiles` reports `provider_connection_id`,
  `provider_connection_broken` and `api_key_set` from the connection;
  `POST /api/profiles/{name}/activate` and `PATCH /api/settings` with
  `active_profile` resolve the connection into `agent_settings.llm`
  (`GET /api/settings` with `X-Expose-Secrets: plaintext` shows it);
  `POST /api/conversations/{conversation_id}/switch_llm` resolves it for a
  running conversation (model switching family). `POST /api/profiles/{name}/validate`
  does not resolve it today (`F23.validate-linked-draft`).
- SDK: no Python client calls these routes. In-process code uses
  `ProviderConnectionStore` and `LLMProfileStore(provider_store=...)`
  (`openhands-sdk/openhands/sdk/llm/`). `RemoteWorkspace.get_llm()` of a
  linked profile does not resolve the connection (LLM profiles family).
- TypeScript client: `LLMMetadataClient.getProviders()`, `getModels(provider?)`
  and `getVerifiedModels()` (`clients/typescript/src/client/llm-client.ts`);
  provider connections have only generated types
  (`clients/typescript/src/generated/agent-server-schema.ts`), no client
  method.
- Configuration: `OH_PERSISTENCE_DIR` (the store lives in
  `provider-connections/` under it, else `~/.openhands`), `OH_SECRET_KEY`
  (the cipher; without it the session key is used, and with neither keys
  are stored in plaintext, `F23.no-cipher`).
- Agent Canvas fills its model picker from the catalog routes and manages
  shared keys through the connection routes (context only).
- Recipes: `api` drives every route; `state cat`/`state grep` read the store
  at rest; `restart`, `restart --rotate-secret-key` and
  `restart --restore-secret-key` reach persistence and cipher changes;
  `launch --new --no-auth --no-secret-key` gives a second server without a
  cipher; `conversation start` proves a resolved key reaches DeepSeek;
  `exec` runs the TypeScript program and the 64-connection fill with `curl`.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported (`launch --new`, default flags: a
  session key and an `OH_SECRET_KEY` cipher), `doctor` is ok, and no
  provider connection exists yet.
- `$DEEPSEEK_API_KEY` is set; the block below saves the DeepSeek preset
  (`deepseek-flash` active, used to move settings off a connection), creates
  the fixture directory `F23` and writes `ts_catalog.mjs`, the TypeScript
  client program. `node` runs it from the built client
  (`clients/typescript/dist`, built by the bullet when missing); `jq`,
  `curl`, `flock` and `sha256sum` are on `PATH`. Outbound HTTPS (`network`)
  reaches DeepSeek for the model-backed bullets and the npm registry when
  the client still has to be built.
- Bullets run top to bottom: `F23.conn-create` sets `CONN` (the DeepSeek
  connection) and `DEF` (a `custom` connection with a fake key) used until
  `F23.conn-delete`; `F23.linked-run` saves the linked profile
  `qa-f23-linked`, kept active until `F23.delete-guard`. `F23.no-cipher`
  launches and stops a second server; `F23.store-busy` and
  `F23.store-busy-responsive` hold the store's lock (about 34 and 16
  seconds) and release it before they end.

  ```sh
  control-agent-server llm preset deepseek
  F23="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f23"
  mkdir -p "$F23"
  CF="$AGENT_SERVER_VERIFY_RUN/home/.openhands/provider-connections/provider_connections.json"
  cat > "$F23/ts_catalog.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const { LLMMetadataClient } = await import(pathToFileURL(resolve('clients/typescript/dist/clients.js')).href);
  const client = new LLMMetadataClient({ host: process.env.AGENT_SERVER_URL, apiKey: process.env.SESSION_API_KEY });
  const providers = await client.getProviders();
  assert.equal(providers.length, Number(process.env.QA_PROVIDERS));
  assert.ok(providers.includes('deepseek'));
  const models = await client.getModels();
  assert.equal(models.length, Number(process.env.QA_MODELS));
  const deepseek = await client.getModels('deepseek');
  assert.equal(deepseek.length, Number(process.env.QA_DEEPSEEK_MODELS));
  assert.ok(deepseek.includes('deepseek/deepseek-flash'));
  const verified = await client.getVerifiedModels();
  assert.deepEqual(Object.keys(verified).sort(), process.env.QA_VERIFIED_KEYS.split(','));
  assert.ok(verified.deepseek.includes('deepseek-chat'));
  const stranger = new LLMMetadataClient({ host: process.env.AGENT_SERVER_URL, apiKey: 'qa-f23-wrong-key' });
  await assert.rejects(stranger.getProviders(), (err) => err.name === 'HttpError' && err.status === 401);
  console.log('QA_F23_TS_OK');
  JS
  control-agent-server api GET /api/llm/provider-connections --expect 200 --check . len-eq 0
  ```

- **Key required (`F23.auth-required`).** Call every route without a key,
  and one with a wrong key.
  ```sh
  control-agent-server api GET /api/llm/providers --auth none --expect 401 --check detail eq Unauthorized --save F23.auth-required/providers
  control-agent-server api GET /api/llm/models --auth none --expect 401 --check detail eq Unauthorized
  control-agent-server api GET /api/llm/models/verified --auth none --expect 401 --check detail eq Unauthorized
  control-agent-server api GET /api/llm/provider-connections --auth bad --expect 401 --check detail eq Unauthorized
  control-agent-server api POST /api/llm/provider-connections --auth none --expect 401 --check detail eq Unauthorized \
    --json '{"display_name": "qa-f23-noauth", "api_key": "sk-qa-f23-noauth"}' --save F23.auth-required/create
  control-agent-server api PATCH /api/llm/provider-connections/qa-f23-any --auth none --expect 401 --check detail eq Unauthorized \
    --json '{"display_name": "qa-f23-noauth"}'
  control-agent-server api DELETE /api/llm/provider-connections/qa-f23-any --auth none --expect 401 --check detail eq Unauthorized
  control-agent-server api GET /api/llm/provider-connections --expect 200 --check . len-eq 0
  ```
  Every call is 401 `{"detail": "Unauthorized"}`; with the run's key the
  list is still empty.
- **Providers (`F23.providers`).** The provider picker.
  ```sh
  control-agent-server api GET /api/llm/providers --expect 200 --check providers contains deepseek \
    --check providers contains openai --check providers contains anthropic --check providers len-ge 50 --save F23.providers/providers
  control-agent-server api GET /api/llm/providers --field providers | jq -e '. == unique'
  ```
  The list holds well over 50 names, including the three, sorted and without
  duplicates (`jq -e` fails otherwise).
- **All models (`F23.models`).** The unfiltered model list.
  ```sh
  control-agent-server api GET /api/llm/models --expect 200 --quiet --check models len-ge 1000 --save F23.models/all
  control-agent-server api GET /api/llm/models --field models \
    | jq -e '. == unique and all(.[]; startswith("bedrock") | not) and any(.[]; . == "deepseek/deepseek-flash")'
  ```
  Thousands of ids, sorted and unique, none starting with `bedrock`, and the
  exact id `deepseek/deepseek-flash` among them.
- **Provider filter (`F23.models-filter`).** One provider's models.
  ```sh
  control-agent-server api GET /api/llm/models --query provider=deepseek --expect 200 --check models contains deepseek-chat --save F23.models-filter/deepseek
  control-agent-server api GET /api/llm/models --query provider=deepseek --field models \
    | jq -e 'length > 0 and . == unique and all(.[]; startswith("deepseek")) and ([.[] | select(. == "deepseek/deepseek-flash" or . == "deepseek/deepseek-chat" or . == "deepseek-chat")] | length == 3)'
  control-agent-server api GET /api/llm/models --query provider=openrouter --field models \
    | jq -e 'length > 0 and all(.[]; startswith("openrouter/")) and any(.[]; . == "openrouter/deepseek/deepseek-chat")'
  control-agent-server api GET /api/llm/models --query provider=qa-no-such-provider --expect 200 --check models len-eq 0 --save F23.models-filter/unknown
  ```
  The DeepSeek list has only DeepSeek ids: `deepseek/deepseek-flash`, the
  namespaced `deepseek/deepseek-chat` and the bare verified `deepseek-chat`;
  the OpenRouter list has only `openrouter/` ids, although
  `deepseek/deepseek-chat` is also an OpenRouter verified entry; an unknown
  provider gets `{"models": []}`.
- **No Bedrock ids under a provider (`F23.models-no-bedrock`), known bug.**
  The route says Bedrock models are excluded without AWS credentials. Both
  lists are first shown to hold the provider's own ids (positive controls),
  then each is searched for Bedrock ids, which `debug` prints to stderr.
  ```sh
  control-agent-server api GET /api/llm/models --query provider=deepseek --quiet --check models contains deepseek/deepseek-flash \
    --save F23.models-no-bedrock/deepseek
  control-agent-server api GET /api/llm/models --query provider=anthropic --quiet --check models len-ge 1 --save F23.models-no-bedrock/anthropic
  control-agent-server api GET /api/llm/models --query provider=anthropic --field models | jq -e 'any(.[]; startswith("claude-"))'
  control-agent-server api GET /api/llm/models --query provider=deepseek --field models | jq -e 'map(select(test("^deepseek\\."))) | debug | length == 0'  # bug
  control-agent-server api GET /api/llm/models --query provider=anthropic --field models | jq -e 'map(select(test("^anthropic\\."))) | debug | length == 0'  # bug
  ```
  Expected: no id in the Bedrock `vendor.model` form under either provider.
  Today `?provider=deepseek` lists `deepseek.r1-v1:0`, `deepseek.v3-v1:0`
  and `deepseek.v3.2`, and `?provider=anthropic` about 30
  `anthropic.claude-...` ids (29 with the installed LiteLLM). LiteLLM files them under `bedrock_converse`, but `get_supported_llm_models`
  only drops ids that start with `bedrock`, and `_extract_model_and_provider`
  reads the part before the first `.` as the provider. The unfiltered list
  carries about 330 such ids (`us.anthropic.*`, `amazon.nova-*`). A picker
  that offers them under DeepSeek or Anthropic sends a direct-API user to a
  Bedrock route that needs AWS credentials.
- **Verified models (`F23.verified-models`).** The curated short list.
  ```sh
  control-agent-server api GET /api/llm/models/verified --expect 200 --check models.deepseek contains deepseek-chat \
    --check models.openai len-ge 1 --check models.anthropic len-ge 1 --check models.openhands len-ge 1 \
    --check models.openrouter contains deepseek/deepseek-chat --save F23.verified-models/verified
  control-agent-server api GET /api/llm/models/verified --field models | jq -e 'all(.[]; length > 0 and length == (unique | length))'
  ```
  Every provider key maps to a non-empty list without duplicates; the
  OpenRouter list holds namespaced ids (`deepseek/deepseek-chat`), the others
  bare ids. The lists are curated, not sorted.
- **TypeScript client (`F23.catalog-ts-client`).** `LLMMetadataClient`
  against the same server, compared with the REST counts.
  ```sh
  test -f clients/typescript/dist/clients.js || (cd clients/typescript && npm ci && npm run build)
  QA_PROVIDERS=$(control-agent-server api GET /api/llm/providers --field providers | jq length)
  QA_MODELS=$(control-agent-server api GET /api/llm/models --field models | jq length)
  QA_DEEPSEEK_MODELS=$(control-agent-server api GET /api/llm/models --query provider=deepseek --field models | jq length)
  QA_VERIFIED_KEYS=$(control-agent-server api GET /api/llm/models/verified --field models | jq -r 'keys | join(",")')
  control-agent-server exec --timeout 120 --env "QA_PROVIDERS=$QA_PROVIDERS" --env "QA_MODELS=$QA_MODELS" \
    --env "QA_DEEPSEEK_MODELS=$QA_DEEPSEEK_MODELS" --env "QA_VERIFIED_KEYS=$QA_VERIFIED_KEYS" \
    --expect-output QA_F23_TS_OK --save F23.catalog-ts-client/program -- node "$F23/ts_catalog.mjs"
  ```
  The program prints `QA_F23_TS_OK`: the client's lists have the REST
  lengths, the verified groups the same keys, and a client with a wrong key
  rejects with an `HttpError` of status 401.
- **Create (`F23.conn-create`).** Create the DeepSeek connection from
  `$DEEPSEEK_API_KEY` (piped, so the key never appears in a command line)
  and a second one without a `provider`.
  ```sh
  CONN=$(printf '{"display_name": "qa-f23-deepseek", "provider": "deepseek", "api_key": "%s"}' "$DEEPSEEK_API_KEY" \
    | control-agent-server api POST /api/llm/provider-connections --stdin --expect 201 --check id matches '^[0-9a-f]{32}$' \
      --check display_name eq qa-f23-deepseek --check provider eq deepseek --check base_url eq null \
      --check api_key_set eq true --check api_key missing --save F23.conn-create/create --field id)
  DEF=$(control-agent-server api POST /api/llm/provider-connections --json '{"display_name": "qa-f23-default", "api_key": "sk-qa-f23-default"}' \
    --expect 201 --check provider eq custom --check api_key_set eq true --field id)
  CREATED=$(control-agent-server api GET /api/llm/provider-connections --field 0.created_at)
  control-agent-server api GET /api/llm/provider-connections --expect 200 --check . len-eq 2 \
    --check 0.id eq "$CONN" --check 0.updated_at eq "$CREATED" --check 0.api_key_set eq true \
    --check 1.id eq "$DEF" --check 1.provider eq custom --save F23.conn-create/list
  ```
  Both are 201 with server-minted 32-hex ids; the list returns them in
  creation order with `api_key_set: true` and no key field.
- **Key hidden and encrypted (`F23.conn-key-encrypted`).** Read the list and
  the store at rest.
  ```sh
  control-agent-server api GET /api/llm/provider-connections --check . not-contains "$DEEPSEEK_API_KEY" \
    --check . not-contains sk-qa-f23-default --check 0.api_key missing --save F23.conn-key-encrypted/list
  control-agent-server state cat home/.openhands/provider-connections/provider_connections.json --mode 600 \
    --check schema_version eq 1 --check connections.0.id eq "$CONN" --check connections.0.api_key matches '^gAAAAA' \
    --check connections.1.api_key matches '^gAAAAA' --not-contains "$DEEPSEEK_API_KEY" --not-contains sk-qa-f23-default
  control-agent-server state grep qa-f23-deepseek --glob 'home/**/*'
  control-agent-server state grep --env-value DEEPSEEK_API_KEY --glob 'home/**/*' --expect-none
  ```
  The response has neither key; the file is `600` and holds both keys as
  Fernet tokens (printed as `<redacted:encrypted>`); the first `state grep`
  is the positive control (the same glob reaches the hidden
  `provider-connections/` file), the second finds the plaintext DeepSeek key
  nowhere under the server's home.
- **No cipher (`F23.no-cipher`).** Launch a second server with neither a
  session key nor `OH_SECRET_KEY`, create a connection there, link a
  profile to it and activate it.
  ```sh
  B=$(control-agent-server launch --new --print-run --name f23-nocipher --no-auth --no-secret-key)
  trap 'control-agent-server stop --run "$B" > /dev/null 2>&1 || true' EXIT
  PLAIN=$(control-agent-server api POST /api/llm/provider-connections --run "$B" --expect 201 --check api_key_set eq true \
    --json '{"display_name": "qa-f23-plain", "api_key": "sk-qa-f23-plain-key"}' --save F23.no-cipher/create --field id)
  control-agent-server state cat home/.openhands/provider-connections/provider_connections.json --run "$B" --mode 600 \
    --check connections.0.id eq "$PLAIN" --check connections.0.api_key eq sk-qa-f23-plain-key
  control-agent-server api GET /api/llm/provider-connections --run "$B" --expect 200 --check 0.api_key_set eq true \
    --check . not-contains sk-qa-f23-plain-key --save F23.no-cipher/list
  control-agent-server api POST /api/profiles/qa-f23-plain --run "$B" --expect 201 \
    --json "{\"llm\": {\"model\": \"openai/qa-f23-plain\", \"provider_connection_id\": \"$PLAIN\"}}"
  control-agent-server api POST /api/profiles/qa-f23-plain/activate --run "$B" --expect 200 --check llm_applied eq true
  control-agent-server state cat home/.openhands/settings.json --run "$B" --max-chars 200 \
    --check agent_settings.llm.api_key eq sk-qa-f23-plain-key --check agent_settings.llm.provider_connection_id eq "$PLAIN"
  control-agent-server stop --run "$B"
  ```
  The connection file (mode `600`) holds the key in plaintext, the list
  reports `api_key_set: true` without the key, and activation writes the
  key into `settings.json`. The second server is stopped, by the `trap` as
  well when a check fails; the `--save` files land under its `evidence/`
  directory. The main run's files stay encrypted (`F23.conn-key-encrypted`).
- **Create validation (`F23.conn-create-validation`).** Bodies the request
  model refuses.
  ```sh
  control-agent-server api POST /api/llm/provider-connections --json '{"display_name": "qa-f23-bad"}' --expect 422 \
    --check detail.0.type eq missing --check detail.0.loc contains api_key --save F23.conn-create-validation/no-key
  control-agent-server api POST /api/llm/provider-connections --json '{"api_key": "sk-qa-f23-bad"}' --expect 422 \
    --check detail.0.type eq missing --check detail.0.loc contains display_name
  control-agent-server api POST /api/llm/provider-connections --json '{"display_name": "qa-f23-bad", "api_key": ""}' --expect 422 \
    --check detail.0.type eq too_short --check detail.0.loc contains api_key
  control-agent-server api POST /api/llm/provider-connections --json '{"display_name": "", "api_key": "sk-qa-f23-bad"}' --expect 422 \
    --check detail.0.type eq string_too_short --check detail.0.loc contains display_name
  control-agent-server api POST /api/llm/provider-connections --json '{"display_name": "qa-f23-bad", "provider": "", "api_key": "sk-qa-f23-bad"}' --expect 422 \
    --check detail.0.type eq string_too_short --check detail.0.loc contains provider
  control-agent-server api POST /api/llm/provider-connections --expect 422 --check detail.0.type eq string_too_long \
    --json "{\"display_name\": \"$(printf 'q%.0s' $(seq 129))\", \"api_key\": \"sk-qa-f23-bad\"}"
  control-agent-server api POST /api/llm/provider-connections --expect 422 --check detail.0.type eq extra_forbidden --check detail.0.loc contains extra_headers \
    --json '{"display_name": "qa-f23-bad", "api_key": "sk-qa-f23-bad", "extra_headers": {"Authorization": "Bearer qa-f23"}}' --save F23.conn-create-validation/extra
  control-agent-server api POST /api/llm/provider-connections --expect 422 --check detail.0.type eq extra_forbidden --check detail.0.loc contains id \
    --json '{"id": "qa-f23-chosen", "display_name": "qa-f23-bad", "api_key": "sk-qa-f23-bad"}'
  control-agent-server api GET /api/llm/provider-connections --check . len-eq 2 --check . not-contains qa-f23-bad
  ```
  Each is 422 with the pydantic error type and location; the list still
  has the two connections.
- **Blank keys on create (`F23.conn-create-blank-key`), known bug.**
  An empty key is refused (positive control); then create with a
  whitespace-only key and with the redacted placeholder, record both
  statuses, and assert them together. An `EXIT` trap deletes whatever was
  created, however the bullet ends.
  ```sh
  trap 'BLANK=$(control-agent-server api GET /api/llm/provider-connections --field . \
      | jq -r "[.[] | select(.display_name | startswith(\"qa-f23-blank-\")) | .id] | join(\" \")") || true
    for id in $BLANK; do control-agent-server api DELETE "/api/llm/provider-connections/$id" --quiet > /dev/null 2>&1 || true; done' EXIT
  control-agent-server api POST /api/llm/provider-connections --json '{"display_name": "qa-f23-blank-empty", "api_key": ""}' \
    --expect 422 --check detail.0.loc contains api_key
  S_SPACE=$(control-agent-server api POST /api/llm/provider-connections --json '{"display_name": "qa-f23-blank-space", "api_key": "   "}' \
    --expect 201,422 --save F23.conn-create-blank-key/space | jq -r .status)
  S_MASK=$(control-agent-server api POST /api/llm/provider-connections --json '{"display_name": "qa-f23-blank-mask", "api_key": "**********"}' \
    --expect 201,422 --save F23.conn-create-blank-key/placeholder | jq -r .status)
  control-agent-server api GET /api/llm/provider-connections --save F23.conn-create-blank-key/list
  echo "whitespace key: $S_SPACE, placeholder key: $S_MASK"
  test "$S_SPACE $S_MASK" = '422 422'  # bug
  control-agent-server api GET /api/llm/provider-connections --check . len-eq 2 --check . not-contains qa-f23-blank-
  ```
  Expected: both are 422 like `""`, and nothing is created. Today both are
  201 and the list shows them with `api_key_set: false`: `min_length=1`
  accepts them, and the stored model's validator then turns a blank or
  `**********` key into `null`, so a keyless connection exists although the
  API says a connection always has a key. The trap deletes them, so later
  bullets see the same two connections.
- **Linked profile runs on the connection (`F23.linked-run`).** Save a
  profile linked to `CONN` with a stray inline key, activate it, and run a
  tiny conversation from the resulting settings.
  ```sh
  control-agent-server api POST /api/profiles/qa-f23-linked --expect 201 \
    --json "{\"llm\": {\"model\": \"deepseek/deepseek-flash\", \"provider_connection_id\": \"$CONN\", \"api_key\": \"sk-qa-f23-inline\"}}"
  control-agent-server state cat home/.openhands/profiles/qa-f23-linked.json --not-contains sk-qa-f23-inline \
    --check api_key missing --check provider_connection_id eq "$CONN"
  control-agent-server api GET /api/profiles \
    --check profiles contains "\"name\": \"qa-f23-linked\", \"model\": \"deepseek/deepseek-flash\", \"base_url\": null, \"provider_connection_id\": \"$CONN\", \"provider_connection_broken\": false, \"api_key_set\": true"
  control-agent-server api POST /api/profiles/qa-f23-linked/activate --expect 200 --check llm_applied eq true
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --max-chars 200 --check active_profile eq qa-f23-linked \
    --check agent_settings.llm.api_key eq "$DEEPSEEK_API_KEY" --check agent_settings.llm.provider_connection_id eq "$CONN" --save F23.linked-run/settings
  CID=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Reply with one word: ok' --wait --timeout 180 --print-id)
  control-agent-server api GET "/api/conversations/$CID" --max-chars 300 --check execution_status eq finished \
    --check agent.llm.provider_connection_id eq "$CONN" --check agent.llm.model eq deepseek/deepseek-flash \
    --check stats.usage_to_metrics.default.accumulated_token_usage.completion_tokens gt 0 --save F23.linked-run/conversation
  control-agent-server conversation events "$CID" --kinds MessageEvent,ActionEvent --contains '"source": "agent"' --expect-min 1 --save F23.linked-run/reply
  ```
  The profile file keeps only the connection id; the list reports
  `api_key_set: true` from the connection; activation writes the DeepSeek
  key into `agent_settings.llm` and keeps `provider_connection_id`; the
  conversation finishes after a real model turn on that key (completion
  tokens are recorded and the agent answered, as a message or through the
  `finish` tool, whichever the model chose).
- **Update (`F23.conn-update`).** Rename `CONN` and point it at a
  `base_url`, re-activate, then clear the `base_url` and restore the name.
  ```sh
  CREATED=$(control-agent-server api GET /api/llm/provider-connections --field 0.created_at)
  control-agent-server api PATCH "/api/llm/provider-connections/$CONN" --expect 200 \
    --json '{"display_name": "qa-f23-renamed", "base_url": "http://127.0.0.1:9/v1"}' \
    --check id eq "$CONN" --check display_name eq qa-f23-renamed --check provider eq deepseek \
    --check base_url eq http://127.0.0.1:9/v1 --check api_key_set eq true --check created_at eq "$CREATED" \
    --check updated_at gt "$CREATED" --save F23.conn-update/patch
  control-agent-server api GET /api/llm/provider-connections --check 0.display_name eq qa-f23-renamed \
    --check 0.base_url eq http://127.0.0.1:9/v1 --check 0.api_key_set eq true --save F23.conn-update/list
  control-agent-server api POST /api/profiles/qa-f23-linked/activate --expect 200
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --max-chars 200 \
    --check agent_settings.llm.base_url eq http://127.0.0.1:9/v1 --check agent_settings.llm.api_key eq "$DEEPSEEK_API_KEY"
  control-agent-server api PATCH "/api/llm/provider-connections/$CONN" --expect 200 --json '{"display_name": "qa-f23-deepseek", "base_url": null}' \
    --check display_name eq qa-f23-deepseek --check base_url eq null --check api_key_set eq true
  control-agent-server api POST /api/profiles/qa-f23-linked/activate --expect 200
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --max-chars 200 \
    --check agent_settings.llm.base_url eq null --check agent_settings.llm.api_key eq "$DEEPSEEK_API_KEY" --save F23.conn-update/settings-cleared
  ```
  The `PATCH` keeps `id`, `created_at`, `provider` and the key, the list
  shows the new values, and each activation applies the connection's
  current `base_url` (set, then `null`) together with the unchanged key.
- **Update validation (`F23.conn-update-validation`).** Bodies and ids the
  `PATCH` refuses.
  ```sh
  BEFORE=$(control-agent-server api GET /api/llm/provider-connections --field .)
  control-agent-server api PATCH "/api/llm/provider-connections/$CONN" --json '{}' --expect 422 \
    --check detail eq 'Provide at least one provider connection field to update' --save F23.conn-update-validation/empty
  control-agent-server api PATCH "/api/llm/provider-connections/$CONN" --json '{"api_key": null}' --expect 422 \
    --check detail eq 'api_key cannot be cleared; provide a new key to rotate it' --save F23.conn-update-validation/null-key
  control-agent-server api PATCH "/api/llm/provider-connections/$CONN" --json '{"display_name": null}' --expect 422 \
    --check detail.0.msg eq 'Value error, display_name cannot be set to null'
  control-agent-server api PATCH "/api/llm/provider-connections/$CONN" --json '{"provider": null}' --expect 422 \
    --check detail.0.msg eq 'Value error, provider cannot be set to null'
  control-agent-server api PATCH "/api/llm/provider-connections/$CONN" --json '{"display_name": ""}' --expect 422 --check detail.0.type eq string_too_short
  control-agent-server api PATCH "/api/llm/provider-connections/$CONN" --json '{"api_key_set": false}' --expect 422 --check detail.0.type eq extra_forbidden
  control-agent-server api PATCH /api/llm/provider-connections/qa-f23-missing --json '{"display_name": "qa-f23-x"}' --expect 404 \
    --check detail eq "Provider connection 'qa-f23-missing' not found" --save F23.conn-update-validation/unknown
  test "$(control-agent-server api GET /api/llm/provider-connections --field .)" = "$BEFORE"
  ```
  Each is refused with the quoted message; the list is byte-for-byte the
  same afterwards (no `updated_at` bump).
- **Blank keys on update (`F23.conn-patch-blank-key`), known bug.** Link a
  probe profile to `DEF` (key `sk-qa-f23-default`), then for an empty, a
  whitespace-only and the placeholder key: `PATCH`, activate the probe,
  record the applied key, and put the original key back.
  ```sh
  trap 'control-agent-server api PATCH "/api/llm/provider-connections/$DEF" --json "{\"api_key\": \"sk-qa-f23-default\"}" --quiet > /dev/null 2>&1 || true
    control-agent-server api POST /api/profiles/qa-f23-linked/activate --quiet > /dev/null 2>&1 || true
    control-agent-server api DELETE /api/profiles/qa-f23-probe --quiet > /dev/null 2>&1 || true' EXIT
  control-agent-server api POST /api/profiles/qa-f23-probe --expect 201 \
    --json "{\"llm\": {\"model\": \"openai/qa-f23-probe\", \"provider_connection_id\": \"$DEF\"}}"
  control-agent-server api POST /api/profiles/qa-f23-probe/activate --expect 200 --check llm_applied eq true
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --max-chars 200 \
    --check agent_settings.llm.api_key eq sk-qa-f23-default --check agent_settings.llm.provider_connection_id eq "$DEF"
  APPLIED=""
  I=0
  for k in '' '   ' '**********'; do
    I=$((I + 1))
    control-agent-server api PATCH "/api/llm/provider-connections/$DEF" --json "{\"api_key\": \"$k\"}" --expect 200,422 \
      --save "F23.conn-patch-blank-key/patch-$I" > /dev/null
    control-agent-server api POST /api/profiles/qa-f23-probe/activate --expect 200 --quiet > /dev/null
    APPLIED="$APPLIED[$(control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --field agent_settings.llm \
      | jq -r '.api_key // "null"')]"
    control-agent-server api PATCH "/api/llm/provider-connections/$DEF" --json '{"api_key": "sk-qa-f23-default"}' --expect 200 --quiet > /dev/null
  done
  echo "applied keys: $APPLIED"
  test "$APPLIED" = '[sk-qa-f23-default][sk-qa-f23-default][sk-qa-f23-default]'  # bug
  ```
  Before any blank `PATCH`, activating the probe applies
  `sk-qa-f23-default` (positive control).
  Expected: each `PATCH` is 422 (or leaves the key alone), so all three
  activations apply `sk-qa-f23-default`. Today each is 200 and the key is
  replaced: `""` and `"   "` clear it (response `api_key_set: false`,
  activation applies no key, although `api_key: null` is refused as
  "cannot be cleared"), and `**********` becomes the literal key (response
  `api_key_set: true`, activation applies `**********`). The handler copies
  the body into the stored model with `model_copy`, which skips the
  validator that would drop blank or redacted keys. While the key is blank,
  `GET /api/profiles` still lists the probe with `api_key_set: true` (it
  counts the encrypted token without decrypting it) and `GET` of the probe
  says `false`. An `EXIT` trap restores the key and `qa-f23-linked` as the
  active profile and deletes the probe, however the bullet ends.
- **Rotate the key (`F23.conn-rotate-key`).** Rotate `CONN` to a key
  DeepSeek rejects, check that active settings keep the old key until the
  next activation, run a conversation on the rotated key, then rotate back.
  ```sh
  control-agent-server api PATCH "/api/llm/provider-connections/$CONN" --json '{"api_key": "sk-qa-f23-rotated-QA23"}' --expect 200 \
    --check api_key_set eq true --save F23.conn-rotate-key/rotate
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --max-chars 200 --check agent_settings.llm.api_key eq "$DEEPSEEK_API_KEY"
  control-agent-server api PATCH /api/settings --json '{"active_profile": "qa-f23-linked"}' --expect 200 --quiet
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --max-chars 200 --check active_profile eq qa-f23-linked \
    --check agent_settings.llm.api_key eq sk-qa-f23-rotated-QA23 --save F23.conn-rotate-key/settings-rotated
  BAD=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Reply with one word: ok' --wait --until error --timeout 180 --print-id)
  control-agent-server conversation events "$BAD" --kinds ConversationErrorEvent --contains QA23 --expect-kind ConversationErrorEvent \
    --full --save F23.conn-rotate-key/provider-rejects
  printf '{"api_key": "%s"}' "$DEEPSEEK_API_KEY" | control-agent-server api PATCH "/api/llm/provider-connections/$CONN" --stdin --expect 200
  control-agent-server api POST /api/profiles/qa-f23-linked/activate --expect 200
  GOOD=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Reply with one word: ok' --wait --timeout 180 --print-id)
  control-agent-server api GET "/api/conversations/$GOOD" --max-chars 200 --check execution_status eq finished --save F23.conn-rotate-key/rotated-back
  ```
  Right after the rotation `agent_settings.llm` still holds the DeepSeek
  key (nothing is copied on rotation); re-applying the same profile through
  `PATCH /api/settings` `active_profile` resolves the rotated key, and the
  next conversation ends in `error` with a
  `ConversationErrorEvent` in which DeepSeek names the key it received
  (`****QA23`). With the real key rotated back and the profile activated
  through `POST /api/profiles/{name}/activate`, a new conversation
  finishes.
- **Validate a linked draft (`F23.validate-linked-draft`), known bug.**
  Pre-flight the same DeepSeek model with the key inline (positive
  control), then as a draft linked to `CONN`.
  ```sh
  printf '{"llm": {"model": "deepseek/deepseek-flash", "api_key": "%s", "num_retries": 0}}' "$DEEPSEEK_API_KEY" \
    | control-agent-server api POST /api/profiles/qa-f23-draft/validate --stdin --timeout 120 --expect 200 --check valid eq true
  control-agent-server api POST /api/profiles/qa-f23-draft/validate --timeout 120 --expect 200 \
    --json "{\"llm\": {\"model\": \"deepseek/deepseek-flash\", \"provider_connection_id\": \"$CONN\", \"num_retries\": 0}}" \
    --save F23.validate-linked-draft/linked --check valid eq true --check error eq null  # bug
  ```
  Expected: both `valid: true`, since a linked draft runs on the
  connection's key once saved and activated. Today the linked draft is
  `valid: false` with `LLMAuthenticationError` (`Authentication Fails`):
  `validate_profile` sends the posted LLM unchanged and never calls
  `resolve_provider_connection`, unlike activation and `switch_llm`. A
  client cannot work around it by sending the key, because the connection
  routes never return it.
- **Restart (`F23.persist-restart`).** Restart the run and read everything
  back.
  ```sh
  BEFORE=$(control-agent-server api GET /api/llm/provider-connections --field .)
  control-agent-server restart
  control-agent-server api GET /api/llm/provider-connections --expect 200 --check . len-eq 2 --check 0.id eq "$CONN" \
    --check 0.api_key_set eq true --save F23.persist-restart/list
  test "$(control-agent-server api GET /api/llm/provider-connections --field .)" = "$BEFORE"
  control-agent-server api POST /api/profiles/qa-f23-linked/activate --expect 200
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --max-chars 200 \
    --check agent_settings.llm.api_key eq "$DEEPSEEK_API_KEY" --save F23.persist-restart/settings
  ```
  The list is identical (ids, names, timestamps) and activation after the
  restart still decrypts and applies the DeepSeek key.
- **Secret key changed (`F23.strict-decrypt`).** Restart with a new
  `OH_SECRET_KEY`, try every connection route, then restore the old key.
  ```sh
  SUM=$(sha256sum "$CF" | cut -d' ' -f1)
  control-agent-server restart --rotate-secret-key
  control-agent-server api GET /api/llm/provider-connections --expect 400 \
    --check detail contains 'api_key is encrypted but cannot be decrypted with the current cipher' \
    --check detail contains OH_SECRET_KEY --save F23.strict-decrypt/list
  control-agent-server api POST /api/llm/provider-connections --json '{"display_name": "qa-f23-rotated", "api_key": "sk-qa-f23-rotated"}' \
    --expect 400 --check detail contains 'cannot be decrypted with the current cipher'
  control-agent-server api PATCH "/api/llm/provider-connections/$CONN" --json '{"display_name": "qa-f23-rotated"}' \
    --expect 400 --check detail contains 'cannot be decrypted with the current cipher'
  control-agent-server api DELETE "/api/llm/provider-connections/$DEF" --expect 400 \
    --check detail contains 'cannot be decrypted with the current cipher' --save F23.strict-decrypt/delete
  test "$(sha256sum "$CF" | cut -d' ' -f1)" = "$SUM"
  control-agent-server restart --restore-secret-key
  control-agent-server api GET /api/llm/provider-connections --expect 200 --check . len-eq 2 --check 0.display_name eq qa-f23-deepseek
  control-agent-server api POST /api/profiles/qa-f23-linked/activate --expect 200
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --max-chars 200 \
    --check agent_settings.llm.api_key eq "$DEEPSEEK_API_KEY" --save F23.strict-decrypt/restored
  ```
  Under the new key all four answers are 400 naming `OH_SECRET_KEY`, and
  the file's SHA-256 is unchanged (nothing was rewritten or dropped). With
  the old key back both connections are listed and the DeepSeek key still
  resolves.
- **Corrupted file (`F23.corrupt-file`).** Truncate the store, then give it
  a newer schema, then put the original back.
  ```sh
  cp "$CF" "$F23/connections-backup.json"
  printf '{"schema_version": 1, "connections": [' > "$CF"
  control-agent-server api GET /api/llm/provider-connections --expect 400 \
    --check detail contains 'Provider connections file is unreadable' --save F23.corrupt-file/list
  control-agent-server api POST /api/llm/provider-connections --json '{"display_name": "qa-f23-corrupt", "api_key": "sk-qa-f23-corrupt"}' \
    --expect 400 --check detail contains 'Provider connections file is unreadable'
  test "$(cat "$CF")" = '{"schema_version": 1, "connections": ['
  printf '{"schema_version": 2, "connections": []}' > "$CF"
  control-agent-server api GET /api/llm/provider-connections --expect 400 --check detail eq 'schema_version 2 is newer than supported 1' \
    --save F23.corrupt-file/newer-schema
  control-agent-server api DELETE "/api/llm/provider-connections/$DEF" --expect 400 --check detail eq 'schema_version 2 is newer than supported 1'
  test "$(cat "$CF")" = '{"schema_version": 2, "connections": []}'
  cp "$F23/connections-backup.json" "$CF"
  control-agent-server api GET /api/llm/provider-connections --expect 200 --check . len-eq 2 --check 0.id eq "$CONN"
  ```
  Each call is 400 with the parse or version error and the file keeps the
  bytes it was given; the restored file serves both connections again.
- **Store busy (`F23.store-busy`).** Another process (here `flock`,
  standing in for a second server on the same persistence directory) holds
  the store's lock for 34 seconds while a create arrives. The server's own
  30-second lock timeout is under test, so this bullet waits for it.
  ```sh
  HELD="$F23/lock-held"
  rm -f "$HELD"
  flock -x "$(dirname "$CF")/.provider-connections.lock" sh -c "touch '$HELD'; sleep 34" &
  LOCKER=$!
  for i in $(seq 100); do test -e "$HELD" && break; sleep 0.1; done
  test -e "$HELD"
  T0=$SECONDS
  control-agent-server api POST /api/llm/provider-connections --json '{"display_name": "qa-f23-busy", "api_key": "sk-qa-f23-busy"}' \
    --timeout 60 --expect 503 --check exception eq '503: Profile store is busy. Please retry.' --save F23.store-busy/create
  test $((SECONDS - T0)) -ge 29
  test $((SECONDS - T0)) -le 33
  wait "$LOCKER"
  control-agent-server api GET /api/llm/provider-connections --expect 200 --expect-max-ms 5000 --check . len-eq 2 --check . not-contains qa-f23-busy
  ```
  The create answers 503 after 29 to 33 seconds (the body's `detail` is the
  generic `Internal Server Error`; the route's message is in `exception`),
  and once the lock is free the list answers at once without `qa-f23-busy`.
- **Lock wait blocks the server (`F23.store-busy-responsive`), known bug.**
  `/alive` answers within 3 seconds while the lock is free (positive
  control). Then hold the lock for 16 seconds, start a list in the
  background, and probe `/alive` while the list waits. The five-second
  pause only lets the background list reach the server first (with margin
  for a slow CLI start), so the probe falls inside its lock wait; the list
  itself gets the lock when `flock` lets go, well inside the 30-second
  timeout, and its duration (at least 10 seconds) shows the probe was
  concurrent with a lock wait. An `EXIT` trap waits for both background
  processes however the bullet ends, so later bullets find the lock free.
  ```sh
  control-agent-server api GET /alive --auth none --expect 200 --expect-max-ms 3000
  HELD="$F23/lock-held-2"
  rm -f "$HELD"
  LISTER=""
  flock -x "$(dirname "$CF")/.provider-connections.lock" sh -c "touch '$HELD'; sleep 16" &
  LOCKER=$!
  trap 'wait $LISTER $LOCKER > /dev/null 2>&1 || true' EXIT
  for i in $(seq 100); do test -e "$HELD" && break; sleep 0.1; done
  test -e "$HELD"
  T0=$SECONDS
  control-agent-server api GET /api/llm/provider-connections --timeout 60 --expect 200 --quiet --save F23.store-busy-responsive/list > /dev/null &
  LISTER=$!
  sleep 5
  control-agent-server api GET /alive --auth none --expect 200 --expect-max-ms 3000 --timeout 60 --save F23.store-busy-responsive/alive  # bug
  wait "$LISTER"
  test $((SECONDS - T0)) -ge 10
  wait "$LOCKER"
  ```
  Expected `/alive` to answer within 3 seconds while the list waits. Today
  it answers only when the lock is released, about 10 seconds later (17.6 s
  measured with a 20-second hold and a two-second head start): every
  connection route is an `async def` that calls the synchronous, file-locked
  `ProviderConnectionStore` on the event loop
  (`provider_connections_router.py`), so a lock wait of up to 30 seconds
  freezes the whole server, liveness and readiness probes included (the
  same defect as `F20.store-busy-responsive` for the profile store). The
  marked probe fails with `took N ms, limit 3000 ms` and a `200`, not with a
  timeout or an error status.
- **Delete guard (`F23.delete-guard`).** Try to delete `CONN` while
  `qa-f23-linked` and the active settings use it, while only the profile
  does, and while only the settings do.
  ```sh
  control-agent-server api DELETE "/api/llm/provider-connections/$CONN" --expect 409 --save F23.delete-guard/profile-and-settings \
    --check detail eq 'Provider connection cannot be deleted while it is referenced by LLM profile(s): qa-f23-linked and referenced by the active agent settings. Update those references before deleting it.'
  control-agent-server api POST /api/profiles/deepseek-flash/activate --expect 200
  control-agent-server api DELETE "/api/llm/provider-connections/$CONN" --expect 409 --save F23.delete-guard/profile \
    --check detail eq 'Provider connection cannot be deleted while it is referenced by LLM profile(s): qa-f23-linked. Update those references before deleting it.'
  control-agent-server api POST /api/profiles/qa-f23-linked/activate --expect 200
  control-agent-server api DELETE /api/profiles/qa-f23-linked --expect 200
  control-agent-server api DELETE "/api/llm/provider-connections/$CONN" --expect 409 --save F23.delete-guard/settings \
    --check detail eq 'Provider connection cannot be deleted while it is referenced by the active agent settings. Update those references before deleting it.'
  control-agent-server api GET /api/llm/provider-connections --check . len-eq 2 --check 0.id eq "$CONN" --check 0.api_key_set eq true
  control-agent-server api POST /api/profiles/deepseek-flash/activate --expect 200
  control-agent-server api GET /api/settings --check active_profile eq deepseek-flash --check agent_settings.llm.provider_connection_id eq null
  ```
  All three deletes are 409 and name exactly the remaining references; the
  connection stays. Deleting the active linked profile clears
  `active_profile` but leaves its resolved LLM (with the connection id) in
  `agent_settings.llm`, which still blocks the delete until another profile
  is activated.
- **Delete (`F23.conn-delete`).** Delete both connections, now unreferenced.
  ```sh
  control-agent-server api DELETE "/api/llm/provider-connections/$CONN" --expect 200 --check id eq "$CONN" \
    --check display_name eq qa-f23-deepseek --check provider eq deepseek --check api_key_set eq false --save F23.conn-delete/delete
  control-agent-server api GET /api/llm/provider-connections --check . len-eq 1 --check . not-contains "$CONN" --check 0.id eq "$DEF"
  control-agent-server state cat home/.openhands/provider-connections/provider_connections.json --check connections len-eq 1 --not-contains "$CONN"
  control-agent-server api DELETE "/api/llm/provider-connections/$CONN" --expect 404 \
    --check detail eq "Provider connection '$CONN' not found" --save F23.conn-delete/again
  control-agent-server api PATCH "/api/llm/provider-connections/$CONN" --json '{"display_name": "qa-f23-x"}' --expect 404
  control-agent-server api DELETE "/api/llm/provider-connections/$DEF" --expect 200 --check api_key_set eq false
  control-agent-server api GET /api/llm/provider-connections --check . len-eq 0
  ```
  The delete answers with the connection's fields and `api_key_set: false`;
  the list and the file no longer have it; a repeated delete and a `PATCH`
  are 404.
- **Limit (`F23.conn-limit`).** Fill the store to 64 connections (with
  `curl` through `exec`, so 64 creates take a second), try a 65th, free one
  slot, then delete them all.
  ```sh
  control-agent-server exec --env QA_FILL=64 --expect-output created=64 -- bash -c 'n=0; for i in $(seq "$QA_FILL"); do
    s=$(curl -sS --noproxy "*" -o /dev/null -w "%{http_code}" -H "X-Session-API-Key: $SESSION_API_KEY" -H "Content-Type: application/json" \
      -d "{\"display_name\": \"qa-f23-fill-$i\", \"api_key\": \"sk-qa-f23-fill\"}" "$AGENT_SERVER_URL/api/llm/provider-connections")
    test "$s" = 201 || exit 1; n=$((n + 1)); done; echo "created=$n"'
  control-agent-server api GET /api/llm/provider-connections --quiet --check . len-eq 64
  control-agent-server api POST /api/llm/provider-connections --json '{"display_name": "qa-f23-over", "api_key": "sk-qa-f23-over"}' --expect 409 \
    --check detail eq 'Provider connection limit reached (64). Delete one before adding another.' --save F23.conn-limit/over
  control-agent-server api GET /api/llm/provider-connections --quiet --check . len-eq 64 --check . not-contains qa-f23-over
  FIRST=$(control-agent-server api GET /api/llm/provider-connections --field 0.id)
  control-agent-server api DELETE "/api/llm/provider-connections/$FIRST" --expect 200 --quiet
  control-agent-server api POST /api/llm/provider-connections --json '{"display_name": "qa-f23-over", "api_key": "sk-qa-f23-over"}' --expect 201 \
    --save F23.conn-limit/after-delete
  IDS=$(control-agent-server api GET /api/llm/provider-connections --field . | jq -r '[.[].id] | join(" ")')
  control-agent-server exec --env "QA_IDS=$IDS" --expect-output deleted=64 -- bash -c 'n=0; for id in $QA_IDS; do
    s=$(curl -sS --noproxy "*" -o /dev/null -w "%{http_code}" -X DELETE -H "X-Session-API-Key: $SESSION_API_KEY" "$AGENT_SERVER_URL/api/llm/provider-connections/$id")
    test "$s" = 200 || exit 1; n=$((n + 1)); done; echo "deleted=$n"'
  control-agent-server api GET /api/llm/provider-connections --check . len-eq 0
  ```
  The 65th create is 409 and nothing is added; after one delete the same
  create is 201; the cleanup leaves the store empty.

## Gotchas

- Catalog answers depend on the installed `litellm` version (thousands of
  ids, changing with every upgrade) and on `VERIFIED_MODELS` (curated, kept
  to the two latest versions per model line). Assert membership, ordering
  and filtering, never exact counts or a specific new model.
- `GET /api/llm/models?provider=` (empty value) is not "no filter": it
  returns the ids whose provider is not recognized (LiteLLM image sizes,
  region-prefixed Bedrock ids). A verified bare id (`deepseek-chat`) is
  attributed to its verified provider; an unknown prefix (`moonshotai/...`)
  to none.
- `?provider=X` only filters ids the installed LiteLLM knows: a verified
  model LiteLLM does not list (`deepseek-v4.1-flash`, `deepseek-v3.2-reasoner`
  under `deepseek`) appears only in `GET /api/llm/models/verified`, so a
  picker needs both routes.
- The connection id is minted by the server (uuid4 hex); a client-chosen
  `id` is 422. There is no `GET` for a single connection: read the list.
- `PATCH` copies the body onto the stored connection without re-running its
  validators, which is why blank keys slip through
  (`F23.conn-patch-blank-key`); the response is built from that copy, not
  from a re-read.
- Resolution is read-at-use: a rotation or `base_url` change reaches a
  profile only on its next activation (or `PATCH /api/settings` with
  `active_profile`, or `switch_llm`); `agent_settings.llm` keeps the copy
  made at activation, and conversations already running keep theirs. The
  connection's `base_url` always wins, including `null`.
- Deleting the active linked profile does not free the connection: the
  active `agent_settings.llm` keeps `provider_connection_id`, so activate
  another profile (or `PATCH /api/settings` with
  `{"agent_settings_diff": {"llm": {"provider_connection_id": null}}}`)
  first. ACP agent settings never block a delete.
- Profile saves never check that `provider_connection_id` exists; a
  dangling link is listed as `provider_connection_broken: true` and its
  activation is 422 (LLM profiles family). A corrupted connection file also
  makes `GET /api/profiles` and `GET /api/profiles/{name}` of a linked
  profile 400 while a linked profile exists, because both consult the
  connection store (the list reads it without a cipher, so a changed secret
  key does not break the list).
- Strict decryption is deliberate data-loss protection (the settings,
  secrets and profile stores degrade silently instead): after an
  `OH_SECRET_KEY` change every connection route is 400 until the old key
  is back or the file is removed. The 400 `detail` quotes a truncated slice
  of the ciphertext, which is harmless without the key.
- The rotation bullet (`F23.conn-rotate-key`) relies on DeepSeek naming
  the last four characters of a rejected key (`****QA23`) in its error; if
  the provider changes that message, check the `ConversationErrorEvent` by
  hand before calling it a product failure.
- The connection store is synchronous inside `async` handlers, so while
  its lock is held elsewhere the whole server stops answering for up to 30
  seconds (`F23.store-busy-responsive`). Never hold the lock (`flock` on
  `provider-connections/.provider-connections.lock`) longer than a bullet
  needs, and do not run other bullets in parallel with `F23.store-busy`.
- Writing to `provider_connections.json` by hand (`F23.corrupt-file`) is an
  arrange step on this run's private home only; always restore the backup
  before the bullet ends.
- None of these routes emits WebSocket events or webhooks: the second
  views are the list, the store on disk, `GET /api/settings` after an
  activation, and a conversation run on the resolved key.
