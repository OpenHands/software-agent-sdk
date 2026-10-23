# Release gate follow-up: shared-runner variance

The October 15 review reproduced timing failures on healthy trees and requested
a bounded retry or runner-specific budgets before making this suite a release
prerequisite. This change uses one complete rerun for pytest exit code 1. It
does not change any test, timing budget, dependency, or production code.

Both attempts run the same merge commit and appear in the job log. A second
failure retains its exit status. Other exit codes stop immediately, and the
existing ten-minute job timeout remains. Exit code 1 can also mean a fixture
failure; this is a bounded rerun, not a classifier for timing failures.
Dependency installation remains a separate required step.

## Executed on October 23, 2026

- Merged upstream `dd0892c80fffb1e59bac77242adafbd8b1168649` without conflicts.
- `make build` completed with CPython 3.13.11 on macOS arm64.
- `CI=true uv run --frozen python -m pytest -q -m stress --durations=10
  tests/agent_server/stress --no-cov`: **17 passed in 128.21 s**, including all
  four historical canaries. No retry was needed in this local run.
- Applicable pre-commit hooks passed on the changed files. The temporary Python
  verifier is excluded by the repository hook configuration and was also checked
  directly with Ruff.
- `uv run --frozen python .pr/verify_stress_retry.py` executes the actual workflow
  shell with a controlled `uv` executable: immediate success calls once;
  `[1, 0]` passes on the second call; `[1, 1]` fails after two calls; statuses
  `2`, `3`, `4`, `5`, and `130` each fail immediately without a second call.
- The same `[1, 0]` sequence against the original workflow from `6aecf791`
  exits 1 after its first call; the updated workflow exits 0 after two calls.

The controlled exit sequences verify workflow control flow. They do not recreate
the reviewer's four-vCPU runner or prove that all intermittent failures are
harmless. The original historical production-regression evidence remains in
[regression-evidence.md](regression-evidence.md); its canaries and budgets are
unchanged. Upstream fork workflows still require maintainer approval when GitHub
marks them `action_required`; local results are not upstream CI results.
