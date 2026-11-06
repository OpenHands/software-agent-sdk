# Cost reporting: before and after

A real SDK Conversation called a scripted HTTP provider reporting `usage.cost`
of $0.04 per request, without a cost header. Both runs completed successfully.
The conversation was saved, reopened, then continued for a second request.

| Recorded cost | Baseline | Fixed SDK |
| --- | --- | --- |
| After first request | $0.00 | $0.04 |
| After reopening | $0.00 | $0.04 |
| After second request | $0.00 | $0.08 |

The comparison used SDK 1.60.1 and LiteLLM 1.105.1 with identical dependencies;
the built wheels differed only in `telemetry.py` and wheel metadata.
Baseline: `232d0b05fa5cd798c9c827d1a1c1aafa541757ad`.
Fixed production source: `6650f289c86dd8ad4500e5bcf0f8c59b3aee166a`
(byte-identical to the captured candidate's production code).
This uses synthetic charges, not a customer endpoint or live model inference.

Run the retained HTTP and persistence regressions:

```bash
uv run pytest tests/sdk/llm/test_reported_usage_cost_http.py -q
```

The tests cover sync/async and streaming/non-streaming responses, authoritative
header precedence, and cost persistence across conversation restart.

Zero-header scope: our two-proxy lab exercised configured missing and explicit
zero headers; zero remained authoritative even with positive usage. These are
controlled contracts, not proof of what a customer's gateway automatically emits
for an unpriced alias. That deployed response path is still unverified. This fix
preserves header precedence; it does not interpret zero as an unknown estimate.
