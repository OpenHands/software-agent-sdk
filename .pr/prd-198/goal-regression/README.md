# Live Agent Canvas /goal regression evidence

The existing `/goal` path completed successfully on the SDK main baseline, the PR candidate with default public content enabled, and the candidate with both public-content controls disabled. This is a focused regression result, not a claim that every agent workflow is unchanged.

| Case | Public skill policy | Loaded skills | Goal outcome | Audit rounds | Independent file check |
|---|---|---:|---|---:|---|
| SDK main baseline | Defaults unchanged | 74 | complete | 1 | Pass |
| PR candidate | Defaults unchanged | 74 | complete | 1 | Pass |
| PR candidate | Public skills and catalogs off | 1 runtime host-context skill | complete | 1 | Pass |

All three were submitted with the actual Agent Canvas composer, which recognized `/goal` and displayed the goal lifecycle banner. Real agent execution and judging produced persisted `running` and terminal `complete` goal events. The objective was identical in every case:

```text
/goal --max 2 Verify a local artifact with evidence: use the terminal to write exactly GOAL_CANVAS_OK followed by a newline to /tmp/goal-canvas-proof.txt, then use a separate terminal command to read the file and assert its exact contents. Report the command output as evidence. Do not contact external services or modify any repository.
```

Each case used a distinct sandbox, with the proof file verified absent before submission. After the goal completed, a separate authenticated terminal read asserted the exact 15-byte output (`GOAL_CANVAS_OK` plus one newline). Default-on skill names were identical on baseline and candidate. The public-off case loaded only runtime-generated host context, and both public catalogs returned empty collections with `disabled: true`.

SDK baseline: `9f7d5cb11b1256c1878f5584a657401feca9ad16`. SDK candidate: `46892b09e7483e0c838a1f7bb2076a180d7d5700`. The same retained Canvas and Enterprise candidate build was used throughout; only the sandbox SDK image and public-content environment controls varied. The SDK goal driver, loop and routes are unchanged in this PR. The screenshots were captured directly from the application with the sidebar and profile controls outside the captured region. Reopened result views may show session-resume notices below the completed goal.

- [Baseline screenshot](baseline-complete.jpg)
- [Candidate, defaults unchanged](candidate-default-complete.jpg)
- [Candidate, public defaults off](candidate-off-complete.jpg)
- [Machine-readable results](results.json)

This test intentionally used no registered marketplaces, so a marketplace preview warning would not confound the `/goal` comparison. Custom organization `github` execution was covered separately by the earlier human acceptance test. No product code was changed for this test. This does not qualify live Helm warm pools, resume/cancellation behavior, or every skill-ingestion path. Enterprise's compatible SDK release/pin dependency still applies. Docs PR #1126 remains paused.
