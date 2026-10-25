<!-- Keep this PR as draft until it is ready for review. -->

<!-- AI/LLM agents:
Do not edit the HUMAN section.
-->

HUMAN:

<!--
Human author: please replace this comment with a short note (at least 20 visible
characters) before marking ready for review.
AI agents: you must not edit this section.
-->

---

AGENT:

## Why

Parents need to await separate child conversations and receive their results
without polling or keeping a browser open. This completes the original wait
foundation with its missing SDK tool and canonical mailbox delivery.

## Summary

- Add bounded direct-child waiting with retain/stop/delete policies and immutable
  per-run outcomes. Persist results in the parent's mailbox before deleting a child.
- Register wait and mailbox agent tools. Sync and async execution release the
  parent state lock, isolate ordinary mutating tools, and drain cancelled HTTP workers.
- Add durable typed mailbox events with agent attribution, evidence validation,
  same-tree scope, idempotent replay, restart recovery and secret redaction.
  Steer preserves work and goals; explicit interrupt records its reason.
- Add typed TypeScript access and event metadata, OpenAPI verification, F35 map
  recipes and [companion documentation](https://github.com/OpenHands/docs/pull/992).

## Issue Number

Closes #6129. Refs #6127. The canonical mailbox dependency is included because
main does not implement it yet; it remains an independently callable operation
and tool. Maintainers may split that dependency if preferred. Canvas rendering
belongs downstream; no queue mode, scheduler or parallel child registry is added.

## How to Test

See `.pr/VALIDATION.md` for exact commands and limits.

- Related Python suite: **596 passed, 4 skipped, 4 deselected, 7 warnings in 227.13s (0:03:47)**. The four exclusions also fail on the
  original PR head in a separate baseline worktree; the unfiltered suite is not green.
- Event-service / coordination subset: **167 passed**. Real HTTP tests cover
  actual Agent tool execution, parent lock release, child progress and cancellation.
- TypeScript: **396 tests passed**, build, changed-file lint/format and public
  type budget passed. Global lint has three existing errors in untouched tests.
- All applicable pre-commit hooks passed, including Pyright and registration checks.
- OpenAPI quality passed; official oasdiff reports only six repository-allowlisted
  additive event-union changes, no other breaks. Published schema pin unchanged.
- Original control CLI attach/doctor/API probes passed against a real server.
  Map check passed; both added routes are owned and driven by F35 recipes.

No live-model eval, stress, Docker/PyInstaller or GitHub CI pass is claimed.
Maintainer action: approve pending fork workflows and apply `integration-test`.

## Video/Screenshots

No UI change. Real HTTP regression and assembled-server probe evidence cover the
browser-independent behavior; deterministic model scripts are identified above.

## Design Doc

[Design](https://htmlpreview.github.io/?https://github.com/karurukaruru/software-agent-sdk/blob/feat/6129-server-side-child-wait/.pr/design.html).

## Type

- [ ] Bug fix
- [x] Feature
- [ ] Refactor
- [ ] Breaking change
- [ ] Docs / chore

## Notes

- The wait deadline starts after validation/runtime loading; lifecycle cleanup
  may extend total duration. Existing arbitrary sync workers remain cooperative.
- Same-backend coordination only; no durable wait recovery or cross-server orchestration.
- The TypeScript generated schema stays at its release pin. Upcoming schema
  generation was validated separately without changing package versions.
- HUMAN remains the original placeholder. The author must personally fill it
  before this draft can be marked ready. No human review is claimed.
