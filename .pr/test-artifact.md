# Test PR Artifact

This file is used to test the PR artifacts workflow.

It should:
1. Cause the `block-merge-with-pr-artifacts` check to **fail** (blocking merge)
2. Be automatically removed when the PR is approved (for non-fork PRs)

## Expected Behavior

- [ ] PR check fails with error about `.pr/` directory
- [ ] On approval, this directory is auto-deleted
- [ ] After cleanup, PR check passes
