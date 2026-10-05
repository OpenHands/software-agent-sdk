# SDK #4976 validation

Validated locally on 2026-10-04: macOS arm64, Python 3.13.3,
LiteLLM 1.93.0. No dependency or lockfile changes.

## Regression coverage

Permanent tests under `tests/sdk/llm/` cover completion selection, generic
LiteLLM output-item events, sync/async streams, output reconstruction without
callbacks, tokenizer shapes/fallbacks, and message metadata compatibility.
The six generic-event reconstruction cases failed before the adapter fix and
passed afterward, preserving the original output-item identity.

## Results

| Check | Result |
| --- | --- |
| Affected test files plus local development matrix | 1,361 passed |
| Local development matrix alone | 1,285 passed, including 240 generic-event reconstruction cases |
| Real local HTTP/SSE transport checks | 12/12 passed |
| Token counting with/without Transformers | 2/2 passed |
| Rebuilt packaged Agent Server | `/health` returned HTTP 200; owned process stopped |
| Repository-wide pre-commit and staged commit hooks | Passed |
| Dynamic-access baseline and diff whitespace checks | Passed |
| Full SDK suite | 6,467 passed, 1 failed, 9 skipped, 12 xfailed |

The full-suite failure is
`test_reasoning_effort_overrides_are_not_redundant`: installed LiteLLM supports
`gpt-5.2-codex`, making an existing registry override redundant. It reproduces
in isolation; the test and model-feature registry are unchanged from the local
`upstream/main` baseline. The full suite is not all-green.

Live provider calls were skipped because no provider credentials were configured.
Local transport checks exercise real LiteLLM HTTP/SSE parsing, not a live model.
Development-only matrix/smoke scripts and detailed reports are retained locally,
not included in this branch's final diff.

## Reproducible repository checks

```sh
uv run pytest tests/sdk/llm/test_llm_completion.py \
  tests/sdk/llm/test_responses_parsing_and_kwargs.py -q -o addopts='--tb=short'
uv run pytest tests/sdk -q -o addopts='--tb=short'
uv run pre-commit run --all-files
uv run python scripts/check_forbidden_dynamic_attributes.py --baseline-ref upstream/main
git diff --check
make build-server
```

The local matrix and smoke-check counts above are recorded evidence; their
development harnesses are not required to run the permanent regression tests.
