> Historical nonnegative-usage candidate evidence. Current positive-only fix and final application findings: [positive-only/README.md](positive-only/README.md).

# OHE-3895 reviewer evidence

Start with `../design.html`, then the production diff and the two regression files.
This directory contains synthetic data only. No customer payloads or credentials.

## Exact source and installed artifacts

- Baseline source: `232d0b05fa5cd798c9c827d1a1c1aafa541757ad`.
- Candidate production code: `ec94bbdb6b01c3cc67b6e35cade97ed78aa38926`.
- Later commits add tests/evidence only; the wheel code is unchanged.
- `build-manifest.json` records both wheel SHA256s, the dependency-lock SHA256,
  and a byte comparison. The only differing wheel members are telemetry.py and
  wheel RECORD. Wheels were built normally, not patched after installation.
- `requirements.txt` is exported from the committed uv.lock with hashes. The
  Kubernetes comparison installed it once and used it for both variants.

## Observed results

| Check | Baseline | Candidate |
| --- | --- | --- |
| New regression files | 25 failed, 6 passed | All pass in surrounding suite |
| Focused + conversation statistics suite | Not used as baseline | 142 passed |
| Positive usage, missing header | $0 in all 8 transport cases | $0.04 in all 8 |
| Header $0.09 plus usage $0.04 | $0.09 | $0.09, once |
| Valid zero header | $0, no cost entry | $0, confirmed-zero entry |
| Conversation after first request | $0 | $0.04 |
| Save/reopen total | $0 | $0.04 |
| After second request | $0 | $0.08 |

`results.json` is a compact machine-readable record. The exact portable helper was also run locally end to end and passed; see `portable-results.json`. `eks-packaged.log` contains
raw output, including the intentional baseline assertion failure. The runner
required the two real completed conversation results, not merely a nonzero exit.
32 transport cases per wheel cover two aliases, sync/async and streaming/non-
streaming, missing/zero/positive headers and usage zero. `cost_demo.py` exercises
real SDK Conversation, HTTP, disk persistence and continuation with a finish tool.

## Reproduce

From a candidate checkout, build its wheel:

```sh
uv build --package openhands-sdk --wheel --out-dir /tmp/ohe3895-candidate
```

In a separate checkout at the immutable baseline above, run the same build with
`--out-dir /tmp/ohe3895-baseline`. Then, from the candidate checkout:

```sh
python3.13 .pr/compare_wheels.py   --baseline-wheel /tmp/ohe3895-baseline/openhands_sdk-1.60.1-py3-none-any.whl   --candidate-wheel /tmp/ohe3895-candidate/openhands_sdk-1.60.1-py3-none-any.whl
```

Both wheels intentionally report SDK version 1.60.1; identify them by source and
SHA256, not their version strings. The helper installs dependencies in temporary
directories and requires the baseline demo to fail on its expected $0.08 cost
assertion while both conversations actually finish. It requires the candidate
to pass, and cleans its temporary files. No model key is needed. PyPI access is
needed only to install the hash-locked dependencies.

For permanent regressions:

```sh
uv run pytest tests/sdk/llm/test_reported_usage_cost.py   tests/sdk/llm/test_reported_usage_cost_http.py   tests/sdk/llm/test_llm_telemetry.py tests/sdk/llm/test_llm_span_cost.py   tests/sdk/llm/test_llm_metrics.py tests/sdk/llm/test_llm_retry_telemetry.py   tests/sdk/conversation/test_conversation_stats.py
```

Run with developer telemetry disabled. Our local runner additionally blocked
external sockets while allowing loopback. No product test used a customer or
LiteLLM service endpoint. A local-substitute result does not establish the
customer's two-proxy configuration, upstream user attribution, hidden selection
charges or Enterprise UI totals.

## Environment, cleanup and review boundaries

Kubernetes execution used an isolated EKS Job, Python 3.13.16, SDK 1.60.1 source
wheels and LiteLLM 1.105.1. The pinned Python image and Job status are retained in
the private/local investigation record. The test namespace and all Job resources
were removed. No shared application rollout or database change occurred.

The first packaged attempt exposed an unsupported argument in the demo fixture
(`profile_store_dir` on the public Conversation factory). That attempt was not
counted as a successful reproduction. The fixture was corrected without changing
product code; the final run requires completed conversation result records and
expected totals, preventing an import/API error from masquerading as a red test.

A wheel build also left duplicate source under build/lib; the dynamic-attribute
hook initially scanned it. Only generated build output was removed; no gate was
weakened. Final hook status is recorded in `pre-commit.txt`.

Self-review only. GitHub CI, independent maintainer review, a released SDK pin,
and customer transport validation remain separate gates. Companion documentation
must publish with/after the fix-containing release. No new full Helm install is
required to isolate this SDK defect; the fresh Enterprise demo is a separate task.
