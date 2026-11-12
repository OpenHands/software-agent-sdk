# Standalone prompt enhancement

Enhance an unsent draft using a named saved LLM profile, without starting a
conversation. Availability and input failures can be verified without provider
credentials. Successful rewriting requires a compatible API-key profile.

Source: `openhands-agent-server/openhands/agent_server/prompt_enhancement_router.py`, `clients/typescript/src/client/prompt-enhancement-client.ts`

Routes: `GET /api/prompt-enhancement/availability/{name}`, `POST /api/prompt-enhancement/enhance`

## Sub-features

- `F35.auth`: both routes require the session key when authentication is enabled.
- `F35.availability`: an unknown profile reports unavailable with a stable code.
- `F35.missing-profile`: enhancement with an unknown profile returns 404.
- `F35.empty-input`: whitespace-only drafts return 400 before loading a profile.
- `F35.invalid-request`: wrong JSON field types return a sanitized 422 response.
- `F35.rewrite`: a real model returns a complete draft retaining the language and constraints, without creating a conversation.
- `F35.typescript`: the compiled TypeScript client checks availability and enhances a draft through the real server.

## How to get to it (agent POV)

- REST: `GET /api/prompt-enhancement/availability/{name}` and
  `POST /api/prompt-enhancement/enhance` with `profile_name` and `text`.
- Discovery: `/server_info` advertises `prompt_enhancement_v1`.
- TypeScript: `PromptEnhancementClient.checkAvailability()` and `.enhancePrompt()`;
  enhancement accepts an `AbortSignal`. The recipes below cover REST and TypeScript.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live, exported, and passes `doctor`; authentication is on.
- No profile named `qa-missing-enhancement` exists in the run's isolated store.
- No model credentials are required for these rejection-path recipes.

- **Authentication (`F35.auth`).** Omit the session key on both routes.
  ```sh
  control-agent-server api GET /api/prompt-enhancement/availability/qa-missing-enhancement --auth none --expect 401 --save F35.auth/availability
  control-agent-server api POST /api/prompt-enhancement/enhance --auth none --json '{"profile_name":"qa-missing-enhancement","text":"Draft."}' --expect 401 --save F35.auth/enhance
  ```
  Both calls return 401 before accessing the profile or model.
- **Availability (`F35.availability`).** Query an absent profile with valid auth.
  ```sh
  control-agent-server api GET /api/prompt-enhancement/availability/qa-missing-enhancement --expect 200 --check available eq false --check code eq profile_not_found --save F35.availability/missing
  ```
  The profile is unavailable with code `profile_not_found`.
- **Missing profile (`F35.missing-profile`).** Submit a nonempty draft.
  ```sh
  control-agent-server api POST /api/prompt-enhancement/enhance --json '{"profile_name":"qa-missing-enhancement","text":"Draft."}' --expect 404 --check code eq profile_not_found --save F35.missing-profile/missing
  ```
  The request fails with code `profile_not_found`.
- **Empty draft (`F35.empty-input`).** Submit only whitespace.
  ```sh
  control-agent-server api POST /api/prompt-enhancement/enhance --json '{"profile_name":"qa-missing-enhancement","text":"   "}' --expect 400 --check code eq empty_input --save F35.empty-input/empty
  ```
  The input error takes precedence over the absent profile.
- **Invalid request (`F35.invalid-request`).** Submit a non-string draft.
  ```sh
  control-agent-server api POST /api/prompt-enhancement/enhance --json '{"profile_name":"qa-missing-enhancement","text":{"private":"QA_PRIVATE_INPUT"}}' --expect 422 --check detail.0.type eq string_type --check detail.0.input missing --check detail.0.ctx missing --save F35.invalid-request/type
  ```
  The validation response retains the error type but omits raw input and context.
- **Real-model rewrite (`F35.rewrite`).** Use a tiny synthetic Chinese draft.

  Needs: `llm`
  ```sh
  control-agent-server llm preset deepseek --no-validate
  F35_COUNT=$(control-agent-server api GET /api/conversations/count --field .)
  control-agent-server api GET /api/prompt-enhancement/availability/deepseek-flash --expect 200 --check available eq true
  control-agent-server api POST /api/prompt-enhancement/enhance --json '{"profile_name":"deepseek-flash","text":"仅改 /src/views/Map.vue。保持 tenantId=acme，运行 pnpm test，超时为 30 秒。保留 https://example.com。"}' --expect 200 --check enhanced_text contains /src/views/Map.vue --check enhanced_text contains tenantId=acme --check enhanced_text contains 'pnpm test' --check enhanced_text contains 30 --check enhanced_text contains https://example.com --check enhanced_text matches '[一-龥]' --save F35.rewrite/result
  test "$(control-agent-server api GET /api/conversations/count --field .)" = "$F35_COUNT"
  control-agent-server api GET /api/profiles --check active_profile eq deepseek-flash
  ```
  Inspect the saved result for preserved intent and requirements as well as the
  asserted markers. Missing model credentials block this recipe; a scripted
  provider cannot establish rewrite quality.
- **Compiled TypeScript (`F35.typescript`).** Drive both typed methods.

  Needs: `llm`, `node`, `network`
  Requires: `F35.rewrite`
  ```sh
  npm --prefix clients/typescript ci
  control-agent-server exec --timeout 60 --expect-output F35_TYPESCRIPT_OK --save F35.typescript/program -- node --input-type=module - <<'JS'
  import assert from 'node:assert/strict';
  import { PromptEnhancementClient } from './clients/typescript/dist/clients.js';
  const host = process.env.AGENT_SERVER_URL;
  const apiKey = process.env.SESSION_API_KEY;
  const headers = { 'X-Session-API-Key': apiKey };
  const count = async () => (await fetch(`${host}/api/conversations/count`, { headers })).json();
  const before = await count();
  const client = new PromptEnhancementClient({ host, apiKey });
  try {
    assert.deepEqual(await client.checkAvailability('deepseek-flash'), { available: true });
    const result = await client.enhancePrompt({ profile_name: 'deepseek-flash', text: '仅改 /src/views/Map.vue。保持 tenantId=acme，运行 pnpm test，超时为 30 秒。' });
    for (const marker of ['/src/views/Map.vue', 'tenantId=acme', 'pnpm test', '30']) {
      assert(result.enhanced_text.includes(marker));
    }
    assert(/[一-龥]/.test(result.enhanced_text));
    assert.deepEqual(await count(), before);
    console.log(JSON.stringify(result));
    console.log('F35_TYPESCRIPT_OK');
  } finally {
    client.close();
  }
  JS
  ```
  The saved program captures a response from the compiled client and verifies
  preservation markers. It remains blocked with the real-model recipe when
  credentials are absent.

## Gotchas

- The first five recipes require no provider. The final two need a compatible
  saved API-key profile, disabled payload
  tracing/logging, and credentials. Preserve intent, language, constraints, code,
  paths, URLs and numbers when evaluating the result; a mock response cannot
  establish this quality.
- Profiles with custom provider body options or subscription authentication are
  unavailable because the endpoint must bound output and disable tools/storage.
- Input and output character limits and the server timeout bound the request.
  Client cancellation stops waiting but may not cancel provider generation.
- There are no conversation, event, tool, or settings mutations in this API.
