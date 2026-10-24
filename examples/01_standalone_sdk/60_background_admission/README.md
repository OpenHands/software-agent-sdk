# Explicit Background Admission

Use an existing OpenAI-compatible admission gateway to share one inference
endpoint between normal work and explicitly designated background workers. The
example uses the public `LLM` configuration; it adds no SDK provider, scheduler,
agent-loop change, or model replacement.

[Adaptive Inference v0.2.0](https://github.com/midhatn/adaptive-inference/releases/tag/v0.2.0)
is one gateway with these routes. Its `/v1` endpoint admits normal work;
`/background/v1` marks requests as background. Both routes use the same queue.
Start it separately using its [backend configuration guide](https://github.com/midhatn/adaptive-inference/blob/v0.2.0/docs/integrations.md).
Do not point the gateway back at itself. The gateway is the **client of your
existing model server**, not the OpenHands agent-server's OpenAI endpoint.

## Run the Example

1. Save your working SDK `LLM` configuration as `llm.json`, without a secret.
   Retain its model, `api_mode: "chat"`, context/output caps, sampling,
   `capability_overrides` and any endpoint-specific `litellm_extra_body`.
   Retain the working provider prefix. For a custom endpoint/model alias, the
   SDK's existing `litellm_proxy/<served-model>` route forwards provider-neutral
   options through the OpenAI-compatible transport. An unknown `openai/` alias
   may instead reject model-specific options in LiteLLM before HTTP dispatch.
   The helper changes only the destination, gateway credential and retry count.
2. Set `ADAPTIVE_API_KEY` if the gateway requires a credential. The default
   `local` placeholder is suitable only for an unauthenticated local gateway.
   Backend credentials belong in the gateway's configuration, not this profile.
3. Save SDK messages as `messages.json`, beginning with a system message:

```json
[
  {"role": "system", "content": [{"type": "text", "text": "Answer directly."}]},
  {"role": "user", "content": [{"type": "text", "text": "What is six times seven?"}]}
]
```

Run from the repository root:

```sh
uv run python examples/01_standalone_sdk/60_background_admission/main.py llm.json messages.json
uv run python examples/01_standalone_sdk/60_background_admission/main.py llm.json messages.json --background
```

Use `--gateway-root http://127.0.0.1:18100` to select the listener root, without
`/v1`. The host chooses `--background`; the helper does not classify message
text. It makes one SDK completion call, prints the response and `EXAMPLE_COST`,
and disables automatic retries. It does not execute tools or start a server.
The same returned `LLM` can be passed to your existing `Agent` with its existing
tools; this example does not configure those tools or change their permissions.

Choose an SDK `timeout` that covers the gateway's queue and execution limits.
For the gateway defaults, `timeout: 1250` exceeds a 300-second queue deadline
plus a 900-second execution deadline. The SDK's normal parameter conversion
still applies: `max_output_tokens` may be serialized as `max_completion_tokens`,
and capability settings must accurately describe the selected backend.

## Quality and Lifecycle Boundaries

The gateway does not lower the output budget, disable reasoning or choose a
different model. Preserve a validated profile rather than copying settings from
an unrelated provider. For example, a Strata endpoint that has already been
qualified at context 65,536, output cap 10,923 and high reasoning can retain
those SDK limits and its explicit `litellm_extra_body.reasoning_budget_tokens`
value of 8,192 plus `experimental_speed_projection: false`. Those extra fields
are Strata-specific; they are not portable reasoning controls for every engine.
SDK context limits do not resize the native model's context window.

This gateway admits one active request at a time. That can reduce native batching
concurrency. Pending requests may be reordered or canceled; a dispatched request
is not preempted. After a client disconnect the gateway drains the active response.
An ambiguous backend timeout blocks further dispatch until an operator checks
the backend and restarts the gateway. Cancellation therefore does not guarantee
immediate GPU-resource release. Do not replay a request with an uncertain outcome.

The endpoint supports Chat Completions, not Responses or native model-management
APIs. The example does not establish native cancellation, GPU memory reservation,
model quality or a throughput gain.

## Validation

```sh
uv run pytest tests/sdk/llm/test_background_admission_example.py -q
```

The tests execute the actual SDK against a loopback HTTP fixture. They check both
routes, preserved generation values, input immutability, one failed POST without
retry and invalid gateway roots. No model inference is performed. The example
requires a separately configured gateway and profile, so it is excluded from
automatic model-backed example runs.
