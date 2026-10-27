# Validation-loop detection evidence

Base: SDK main `f7cbea4ff` (includes the merged Retry-After fix). Python 3.13.12,
locked LiteLLM 1.93.0. No dependency, release version, tool schema or threshold
changes. The raw results include imported source paths and candidate file hashes.

## Real HTTP before/after

The [probe](validation_loop_probe.py) runs the real agent, TaskTrackerTool,
validation/event emission, stuck detector and Conversation.run/arun against a
local HTTP model. Only model responses are scripted. Requests must begin with a
system message. Every conversation and owned server is closed in finally blocks.

| Case | Base | Candidate |
| --- | --- | --- |
| identical | stuck, 4 calls, 1 nudges | stuck, 4 calls, 1 nudges |
| vary_thought | error, 12 calls, 0 nudges | stuck, 4 calls, 1 nudges |
| vary_invalid_args | stuck, 4 calls, 1 nudges | stuck, 4 calls, 1 nudges |
| repair_different_fields | stuck, 4 calls, 1 nudges | finished, 8 calls, 0 nudges |
| repair_control | finished, 8 calls, 0 nudges | finished, 8 calls, 0 nudges |
| recover_after_two | finished, 4 calls, 0 nudges | finished, 4 calls, 0 nudges |

All six cases produce the same outcomes in async mode. All 24 cases have asserted
outcomes. Incremental repair failures decrease 6 → 5 → 4 → 3 → 2 → 1 before success;
the baseline stops at three remaining errors. The disabled-detector repair control
and two-failures-then-success control finish on both revisions.

[Raw results and source hashes](validation-loop-results.json).

## Reproduce

From the candidate checkout, after `make build`:

```sh
git worktree add --detach ../sdk-validation-base f7cbea4ff
candidate_root="$PWD"
base_root="$(cd ../sdk-validation-base && pwd)"
for variant in base candidate; do
  source_root="$candidate_root"
  if [ "$variant" = base ]; then source_root="$base_root"; fi
  for mode in sync async; do
    async_flag=""
    if [ "$mode" = async ]; then async_flag="--asynchronous"; fi
    PYTHONPATH="$source_root/openhands-sdk:$source_root/openhands-tools:$source_root/openhands-workspace:$source_root/openhands-agent-server" \
      LITELLM_LOCAL_MODEL_COST_MAP=True "$candidate_root/.venv/bin/python" \
      .pr/validation_loop_probe.py --expect "$variant" $async_flag \
      --out "/tmp/validation-loop-$variant-$mode.json"
  done
done
```

## Regression and contract checks

- `uv run pytest tests/cross/test_validation_loops.py tests/cross/test_stuck_detector.py tests/cross/test_stuck_detector_config.py tests/sdk/agent tests/sdk/conversation tests/sdk/event tests/sdk/context/view -n4 -q`: **2,222 passed**, including 26 new cases.
- Changed-file pre-commit: Ruff, pycodestyle, Pyright and repository gates pass.
- `make test-server-schema`: deterministic export, OpenAPI type-quality check and schema validation pass.
- Coverage includes thought/irrelevant-argument/index changes, alternating invalid values, progressive repairs, successful-call and user-message resets, sync/async execution, actual parallel validation emission, reordered/missing call associations, old-event loading and persisted conversation replay.

`AgentErrorEvent.validation_errors` is optional. Its entries retain only Pydantic
error code and location, not input values, exception context or error URLs. Old
events load without it and use argument comparison. Generic value/assertion
validators also use argument comparison to avoid collapsing distinct repairs into
one model-level error. No persisted settings shape or version changes.

The TypeScript transport schema remains pinned to its released agent-server
version. The normal SDK release/bump workflow regenerates it from the new server
OpenAPI; this change does not retarget that pin or alter the independent TypeScript
local agent loop.

## Related work and limits

Existing SDK PR #5355 fixes tool-call batch serialization; #6238 sizes the scan
window for custom thresholds; #6239/#6242 preserve those settings across fork/server
boundaries. None implements this validation-failure comparison. No changes from
those PRs were cherry-picked.

The detector remains a bounded-history heuristic, not a proof of progress. Generic
custom-validator errors and legacy events cannot detect every equivalent failure.
The existing alternating-loop and successful-action detectors remain enabled;
this does not guarantee a nudge for every possible loop pattern. Model responses
are synthetic, not a live model evaluation or a deployed OHE/Replicated test.
The PR requests integration evaluation because this changes agent behavior.
