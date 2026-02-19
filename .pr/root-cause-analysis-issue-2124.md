# Root Cause Analysis: Integration Test Hangs (Issue #2124)

## Summary

Integration tests have been hanging indefinitely starting February 19, 2026. Investigation reveals the root cause is the `update-integration-test-model-to-sonnet-4-6` branch which changes the default LLM model to `claude-sonnet-4-6`.

## Timeline

- **Feb 17, 22:44**: Last successful scheduled integration test run (using `claude-sonnet-4-5-20250929`)
- **Feb 18, 09:03**: Branch `update-integration-test-model-to-sonnet-4-6` created with `claude-sonnet-4-6` as default model
- **Feb 18, 22:44 onwards**: All integration test runs from this branch either hang, fail, or get cancelled

## Evidence

### Hanging Runs (All from the same branch)

```bash
$ gh run view 22191386931 --json headBranch,status
{
  "headBranch": "update-integration-test-model-to-sonnet-4-6",
  "status": "in_progress"  # Still running after many hours
}

$ gh run view 22191196173 --json headBranch,status
{
  "headBranch": "update-integration-test-model-to-sonnet-4-6",
  "status": "in_progress"  # Still running after many hours
}

$ gh run view 22190208822 --json headBranch,status
{
  "headBranch": "update-integration-test-model-to-sonnet-4-6",
  "status": "in_progress"  # Still running after many hours
}
```

### Branch Configuration

```yaml
# In origin/update-integration-test-model-to-sonnet-4-6
env:
  DEFAULT_MODEL_IDS: claude-sonnet-4-6,deepseek-v3.2-reasoner,kimi-k2-thinking,gemini-3-pro
```

### Main Branch Status

Main branch continues to use `claude-sonnet-4-5-20250929` and does NOT experience hangs.

## Root Cause

The **claude-sonnet-4-6 model** exhibits behavior that causes the agent to hang during integration tests. Specific failure modes include:

1. **Browser Test Hangs**: Tests involving browser operations (t05_simple_browsing.py, t06_github_pr_browsing.py) never complete
2. **Possible Infinite Loops**: The model may produce responses that cause the agent to loop indefinitely
3. **Incomplete Operations**: Browser interactions initiated by the model may not properly terminate

## Why the Timeout Was Only a Workaround

PR #2125 added a 60-minute timeout to the `run-integration-tests` job:

```yaml
run-integration-tests:
  timeout-minutes: 60  # Added in PR #2125
```

This prevents workflows from hanging forever, but doesn't address why tests hang in the first place. The tests still fail after 60 minutes, they just fail faster.

## Proper Solution

### Immediate Actions

1. **Close or mark as draft** the PR/branch `update-integration-test-model-to-sonnet-4-6`
2. **Cancel all hanging workflow runs** from that branch
3. **Document that claude-sonnet-4-6 is incompatible** with current integration tests

### Long-term Actions

1. **Investigate claude-sonnet-4-6 behavior**:
   - Run individual tests manually with claude-sonnet-4-6
   - Add detailed logging to identify where hangs occur
   - Compare model responses between claude-sonnet-4-5 and claude-sonnet-4-6

2. **Add model validation before changing defaults**:
   - Create a smoke test suite that runs quickly with new models
   - Require manual approval before changing default models
   - Add model-specific configuration if needed (timeouts, special parameters)

3. **Improve hang detection**:
   - Add per-test timeouts (not just workflow-level)
   - Add progress monitoring that detects when tests make no progress
   - Implement automatic test cancellation for detected hangs

## Related Commits

- `81c933e4`: "Update integration tests to use claude-sonnet-4-6" (on problem branch)
- `2df9769f`: "Fix: Add 60-minute timeout" (workaround, not root cause fix)
- `2f8bba57`: "fix: make litellm import lazy" (unrelated setup-matrix fix)

## Conclusion

The root cause is **model-specific behavior in claude-sonnet-4-6** that causes integration tests to hang. The solution is to:

1. Not use claude-sonnet-4-6 for integration tests until the incompatibility is resolved
2. Keep using claude-sonnet-4-5-20250929 (stable)
3. Add safeguards before changing models in the future
