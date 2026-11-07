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

## How to get to it (agent POV)

- REST: `GET /api/prompt-enhancement/availability/{name}` and
  `POST /api/prompt-enhancement/enhance` with `profile_name` and `text`.
- Discovery: `/server_info` advertises `prompt_enhancement_v1`.
- TypeScript: `PromptEnhancementClient.checkAvailability()` and `.enhancePrompt()`;
  requests accept an `AbortSignal`. The recipes below exercise REST.

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

## Gotchas

- These recipes do not prove real-model rewriting or the TypeScript entry points.
  A model-backed check needs a compatible saved API-key profile, disabled payload
  tracing/logging, and credentials. Preserve intent, language, constraints, code,
  paths, URLs and numbers when evaluating the result; a mock response cannot
  establish this quality.
- Profiles with custom provider body options or subscription authentication are
  unavailable because the endpoint must bound output and disable tools/storage.
- Input and output character limits and the server timeout bound the request.
  Client cancellation stops waiting but may not cancel provider generation.
- There are no conversation, event, tool, or settings mutations in this API.
