# Retry-After validation

Base: ff62a333b (current SDK main at investigation start). SDK workspace locked LiteLLM 1.93.0; separate proxy LiteLLM 1.103.6, matching the current OpenHands-Cloud chart pin. No dependency or release version changes.

## Real HTTP reproduction

The supplied script starts a local HTTP provider with a 12-second temporary rate limit and a real LiteLLM proxy with RateLimitErrorRetries: 0 (other proxy retries stay 3). It invokes the real SDK completion/acompletion API in separate processes using base and candidate package paths. Both calls start with a system message. No commercial provider or customer data is involved.

| SDK / route | Provider requests | Offsets (seconds) |
| --- | --- | --- |
| baseline-direct | 3 | 0.0, 8.027, 24.052 |
| baseline-proxy | 3 | 0.0, 8.078, 24.129 |
| candidate-direct | 2 | 0.0, 12.027 |
| candidate-proxy | 2 | 0.0, 12.06 |
| candidate-async-direct | 2 | 0.0, 12.023 |
| candidate-async-proxy | 2 | 0.0, 12.04 |

All completed with OK. The proxy forwards `llm_provider-retry-after`; the direct path exposes `retry-after`. Upstream forwarding was fixed by BerriAI/litellm commit 8577d63ff5212173aa5b30bdfe8118b666c74179, already in the tested 1.103.6 package. The previously investigated 1.100.5 limitation does not require a new proxy patch for the current chart. All subprocess groups and the HTTP server are stopped in finally blocks.

Reproduce with a detached base worktree and the candidate's locked environment:

```sh
make build
git worktree add --detach ../sdk-retry-base ff62a333b
uv run python .pr/retry_http_probe.py probe --baseline ../sdk-retry-base --candidate . --out /tmp/oh-retry-proof
```

The probe installs only the separate proxy test environment; it does not change SDK dependencies. Raw structured results: [retry-http-results.json](retry-http-results.json).

## Azure proxy route

The same real proxy (1.103.6) was also tested with `model: azure/retry-probe`,
`api_version: "2024-10-21"`, and zero proxy rate-limit retries. The local endpoint
asserts that every request uses the Azure deployment path and API-key header:
`/openai/deployments/retry-probe/chat/completions?api-version=2024-10-21`.

| SDK / Azure route | Provider requests | Offsets (seconds) |
| --- | ---: | --- |
| baseline-azure-proxy | 3 | 0.0, 8.066, 24.118 |
| baseline-async-azure-proxy | 3 | 0.0, 8.038, 24.082 |
| candidate-azure-proxy | 2 | 0.0, 12.044 |
| candidate-async-azure-proxy | 2 | 0.0, 12.043 |

All four calls completed with OK. Every run captured
`llm_provider-retry-after: 12` on the SDK error. Sync and async baseline runs
ignored that hint and retried early; both patched runs honored it. The successful
retry had one upstream request per SDK attempt. No SDK product-code changes were
needed for this additional route. All owned servers stopped after the run.

```sh
uv run python .pr/retry_http_probe.py probe --baseline ../sdk-retry-base --candidate . --provider-route azure --out /tmp/oh-retry-azure-proof
```

[Azure raw results](retry-azure-http-results.json) include the observed upstream
paths, retry headers and source checkout for each client. This is an Azure-format
local endpoint, not a call to Azure infrastructure or a Replicated installation.

## Regression coverage

- 1,362 LLM tests pass, including 61 new retry-hint tests and existing retry, auth, quota/budget, timeout, completion and Responses tests.
- Changed-file pre-commit: Ruff, pycodestyle, Pyright, dynamic-attribute/import/tool-registration gates pass.
- New tests cover seconds, fractional seconds, HTTP dates, milliseconds, proxy-prefixed headers, all observed header locations, conflicting/malformed/nonfinite/negative hints, attempt limits, long-hint rejection, and async cancellation.
- No SDK field defaults or serialized shapes change. retry_max_wait now also bounds provider hints: a hint beyond that configured limit surfaces the original transport error through normal SDK mapping rather than retrying early. No second retry layer or newly retryable exception category is introduced.

## Limits

The network provider is a controlled HTTP substitute, not Azure. This verifies timing/header propagation through the exact tested proxy and locked SDK transport; it does not prove sustained Azure capacity, all proxy routes, or deployment of a future SDK release. No live OHE instance was changed. Long-delay handling and malformed-header cases are regression tests with captured waits; the real HTTP probe exercises a 12-second window.
