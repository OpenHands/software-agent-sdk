# Cross-repository issue readiness: live before/after

September 13, 2026. The exact current body of SDK PR #4931 was supplied to both the base and PR checker with repository `OpenHands/software-agent-sdk` and authenticated read-only GitHub access. The linked issue is `OpenHands/OpenHands#17236`.

- [Before](readiness-before.log): missing linked issue plus missing human note; two errors.
- [After](readiness-after.log): the linked issue is recognized and passes readiness; only the independent missing human note remains.

Both commands correctly exit 1 while that human requirement is unsatisfied. No issue or PR was edited to bypass a gate. This changes GitHub CI validation, not Canvas runtime behavior.

[Passing cross-tests job](https://github.com/OpenHands/software-agent-sdk/actions/runs/34741395406/job/103681577644).

Reproduce by fetching PR #4931's body, placing it in a standard `pull_request` event with the repository identity, and running each version of `.github/scripts/check_pr_description.py --event-path <event.json>` with authenticated GitHub reads. Preserve the identical event for both runs.
