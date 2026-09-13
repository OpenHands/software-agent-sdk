# SDK5010: actual Canvas failure and public client error discovery

A fresh isolated Canvas was configured through its LLM settings UI to use a disclosed local OpenAI-compatible fixture with a dummy API key. Sending a disposable prompt caused a real `LLMBadRequestError`; Canvas displayed **Error**. No external model, application repository or GitHub call was used.

The exact public sync and async clients before and after the event-kind correction queried that same failed conversation. Both baseline queries returned `[]`; both corrected queries returned the actual error event `6f7976d0-26ae-44b2-8385-cb68d62f8f20`, code `LLMBadRequestError`, containing the disclosed fixture marker.

- [Actual Canvas failure](sdk5010-canvas-failure.png)
- [Actual UI setup/send/failure sequence](sdk5010-error-discovery.gif)
- [Exact sync/async comparison](sdk5010-error-comparison.json)
- [Comparison driver](sdk5010-query.py)

Client revisions: before `d76aa33d2`; after `34ef2b29a`. The comparison driver loads each exact revision's public client module and uses unchanged request machinery. Server SDK integration `ac6d12b0b9d76f1cc38a6eb1ea51cd92e34a0bf2`, Canvas `4cd1513`, Automation `6375a68`. Conversation `d86043b9-c22e-43d4-905a-67fd3dc3a4b9`.

This fix affects SDK client error discovery. Current Automation and Canvas do not use `get_errors` to render this status, so there is no claim that the correction changes the UI failure message. The same real failed Canvas conversation supplies the causal before/after client comparison. The GIF shows the UI setup and genuine failure; the JSON separately proves the public-client distinction.

This scenario specifically covers the latest error-filter correction. The broader public-client/attachment enhancement also has the separately published exact documentation examples and real Canvas agent execution in docs793; this report does not imply it individually exercises every client method. Full local evidence: `factory-state/evidence/sdk-runtime-fixes`.
