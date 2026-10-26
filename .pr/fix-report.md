# Follow-up product fixes — 2026-10-23

Four separate draft PRs address the four new defects found by the [full verification pass](verification-report.md). Each uses an isolated worktree based on the frozen fork commit `5966c781ed014efc96c028a99bddd4ff78426741`. All issues, branches and PRs are in `enyst/agent-sdk`; nothing was merged or written upstream.

These results are separate from the maintenance snapshot. They do not change its **864 pass, 82 known-bug xfail, one blocked and one retired** entries, or advance the accepted baseline. The product PRs do not include the maintenance recipe changes. Their focused replays use the CLI from [PR #43](https://github.com/enyst/agent-sdk/pull/43) with `--checkout` selecting each fixed source tree.

## Changes and regression protection

| Defect | Fix PR | Result | Automated checks |
| --- | --- | --- | --- |
| [#39](https://github.com/enyst/agent-sdk/issues/39): embedded skill paths return 422 | [#44](https://github.com/enyst/agent-sdk/pull/44) | The fetcher and installer share resolved source/subpath information, preserving embedded-path names and repository-root naming. | 156 SDK extension/skill/plugin tests and 93 server skill tests pass. The new real-Git regression failed before the patch. Three network-marked tests were deselected; actual GitHub installs were exercised live. |
| [#40](https://github.com/enyst/agent-sdk/issues/40): exhausted quota validates successfully | [#47](https://github.com/enyst/agent-sdk/pull/47) | Exact structured quota codes survive provider exception normalization through the preserved cause/context chain. Ordinary rate limits still retry; quota permits model fallback; budget denial stops fallback. | 272 related tests pass, including 20 sync/async chat/Responses HTTP cases and fallback controls. The new transport matrix failed eight cases before the patch. |
| [#41](https://github.com/enyst/agent-sdk/issues/41): missing TypeScript `public_skills` | [#46](https://github.com/enyst/agent-sdk/pull/46) | Public profile and diagnostic types expose the optional nullable field. The contract test now actually invokes the TypeScript compiler, so type-only assertions cannot silently pass through transpilation. | 396 tests in 27 files pass, plus build, lint and formatting. The compiler-backed regression failed before the fields were added. Lint reports five pre-existing warnings in untouched files. |
| [#42](https://github.com/enyst/agent-sdk/issues/42): child launch fails in a new workspace | [#45](https://github.com/enyst/agent-sdk/pull/45) | A repository with no usable commit uses the existing shared-workspace fallback and reports that isolation. Committed repositories retain worktrees and upstream tracking; no initial commit is created. | 41 related tests pass, including a real HTTP regression, parent-file/index/HEAD preservation, committed/subdirectory worktrees, and an unborn HEAD with a usable remote base. |

All changed code passes file-scoped repository pre-commit checks. Independent review covered each product patch and its before/after evidence. The child review caught an upstream-tracking regression in the first patch: validating a commit and then passing its SHA to `git worktree add` lost the remote tracking configuration. The corrected patch validates the selected ref but retains that ref for worktree creation; an assertion first reproduced the loss and now passes.

## Exact source and live proof

Product code and regression tests were committed before launching the proof servers. Doctor and run metadata identify these exact revisions. Later evidence-only commits do not change the tested product code.

| Issue | Tested product revision | Fresh fixed runs | Reviewed packet |
| --- | --- | --- | --- |
| #39 | `31897798e8bd8e62ef56bdb86dba6588efbbc557` | `20261023-065443-4190-map-f25`, `20261023-065459-fd2d-map-f25` | [Commands and HTTP exchanges](https://github.com/enyst/agent-sdk/blob/1973797e6a6d8ff7ecc9993cf990663d918c7aa1/.pr/fix-39-verification.md) |
| #40 | `06a52395b85039fa5b9c9d0ce18f7085a8b942ab` | `20261023-070627-5271-map-f20`, `20261023-070641-8fe2-map-f20` | [Commands and HTTP exchanges](https://github.com/enyst/agent-sdk/blob/fd5d519e14c9f6bf8c0ad7177d80429fb3147d8b/.pr/quota-validation-evidence.md) |
| #41 | `c250e521e1f0d7e091ce7599a626945bd6c7943a` | `20261023-070220-2697-map-f21`, `20261023-070238-9833-map-f21` | [Live payloads and compiler results](https://github.com/enyst/agent-sdk/blob/c567fc33340e3a60a0823b5bead10b686354fcbe/.pr/issue-41-evidence.json) |
| #42 | `b2c0490647963cb877d1f0c20394311fa245d6d4` | `20261023-071012-2856-map-f04`, `20261023-071101-a644-map-f04` | [Commands and observations](https://github.com/enyst/agent-sdk/blob/0178da1e0e66fb0753e122d5eea6577baba8f3b4/.pr/issue-42-evidence.md) |

The original frozen source reproduced each defect twice. All four fixed pairs report **XPASS** because the separate maintenance branch still marks the defects present at its frozen target. This is the expected fix-verification result: the recipe assertions exit zero, while the CLI summary returns exit one to flag the changed known-bug status. It is not an unexplained test failure. Each pair also retains its passing setup and distinguishing controls, with zero unexpected failures or blockers.

For #42, the corrected source has additionally passed a fresh real-model tool run, `20261023-071023-f9ef-fix42-default-child-tool`. The parent invokes `start_child_conversation` without an isolation argument; the child finishes, reports shared isolation, appears in the child list, and leaves the parent's unborn HEAD and existing file intact through deletion. Earlier proof at `46ba8bda` is retained as evidence of the initial implementation, not substituted for the corrected source.

The #41 live proof used official Node 24.21.0 after an earlier attempt under the environment's default Node 24.19.0 stalled in the generated-contract compiler. That owned attempt was stopped through coordinator cleanup and preserved. The same compiler command completed on Node 24.21.0, and the complete fresh pair then passed without a product workaround.

## Limits and handoff

The fixes were exercised on local Linux source servers. Skill installation used the actual public GitHub repository. Child workflows used DeepSeek flash. Quota classification used a deterministic loopback HTTP provider through the real SDK/LiteLLM/OpenAI transports; it does not claim external billing-provider behavior. TypeScript proof compiled live payloads against the fixed worktree's own generated and public client builds. This follow-up does not claim Agent Canvas UI coverage or benchmark results.

GitHub CI remains pending. Completed current-head checks have shown no failures at the latest inspection; several broader Python jobs remain queued on `blacksmith-2vcpu-ubuntu-2404`. PR #45 has the required `integration-test` label for its tool-description change. Its [earlier model-integration results](https://github.com/enyst/agent-sdk/pull/45#issuecomment-6359527253) report **0/50 passes because the fork lacks `LLM_API_KEY` and `LLM_BASE_URL`**, despite the workflow's successful conclusion. That run used superseded head `691d720`, and supplies neither benchmark nor final-head proof. Local test/live results above are independent of these remote CI limits. All PRs remain drafts with the repository's HUMAN placeholder unchanged.

All task-owned servers and helpers are stopped, with reviewed evidence retained. The original maintenance report's model-cost snapshot excludes this follow-up fix work.
