# Current candidate evidence

Production/test commit: `fc29b402fa20f0a6f17bff053e8f4e7744fbe860`.
Baseline: `232d0b05fa5cd798c9c827d1a1c1aafa541757ad`.
Files in the parent evidence directory are historical evidence for the earlier
nonnegative-usage candidate; use this directory for the current positive-only fix.

- `tests-candidate.txt`: 139 relevant SDK/accounting tests pass.
- `tests-baseline.txt`: the new regressions give 20 expected failures and 8 passes
  on immutable baseline production code; failures are behavior, not imports.
- `pre-commit.txt`: all required repository hooks pass.
- `portable-results.json`: built wheels, identical hash-locked dependencies,
  32 public sync/async HTTP controls per wheel, and a real persisted/reopened SDK
  Conversation: baseline $0, candidate $0.04 then $0.08. Zero usage preserves
  existing pricing; explicit zero headers remain authoritative.
- `build-manifest.json`: exact code, wheel, telemetry and private runtime-image
  identities. Normal wheels differ only in telemetry.py and RECORD.
- `application/`: real Enterprise app chat/manual automation qualification on
  the approved isolated EKS lab. Read its limits and cleanup findings before
  treating it as a customer-deployment or all-costs acceptance result.

Reproduce package comparison after building baseline/current wheels separately:

```bash
python .pr/compare_wheels.py \
  --baseline-wheel /path/to/baseline/openhands_sdk-1.60.1-py3-none-any.whl \
  --candidate-wheel /path/to/candidate/openhands_sdk-1.60.1-py3-none-any.whl
```

The helper installs the same parent `requirements.txt` with hashes for both
wheels. It requires two completed HTTP-backed SDK Conversations, not a source
module monkeypatch or successful import. Provider credentials are not required.
No GitHub CI approval, maintainer approval, released SDK or customer rollout is
claimed. Final independent Opus review is recorded separately after completion.

Publication source: `6650f289c86dd8ad4500e5bcf0f8c59b3aee166a`. [Source equivalence](../../source-equivalence.json) records that source and tests are byte-identical to the captured build. The original research history remains local; recorded build SHAs identify the original runs, while the publication source commit is the accessible code reference.
