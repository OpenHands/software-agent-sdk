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

Parents of separate child conversations need a server-owned bounded wait that works with no
browser attached. Repeated status requests or agent turns add coordination cost and latency.

## Summary

- Add a parent-scoped REST wait for existing direct children, using lifecycle notifications and
  immutable per-run completion futures with no ConversationState lock held while waiting.
- Add deadlines, parent interruption / HTTP disconnect cleanup, retain/stop/delete policies,
  runtime eviction pins and generation guards that preserve successor runs.
- Add typed TypeScript client access and deterministic plus real HTTP regressions.

## Issue Number

Refs #6129. This is an independently useful backend foundation, not the complete accepted scope.
Mailbox finish-result delivery (#6127) and a lock-releasing SDK agent tool are not implemented.

## How to Test

See `.pr/VALIDATION.md` for exact commands, scope and limitations.

- Python related server suite: **475 passed**, 5 warnings.
- Real HTTP auth/disconnect/concurrency/completion test: **1 passed**, 34 deselected.
- TypeScript complete unit suite: **395 passed**, 28 files; lint/build/format passed.
- All applicable changed-file pre-commit hooks, including Pyright: passed.
- OpenAPI type quality: passed (63 existing allowlisted locations).
- REST compatibility with actual oasdiff 1.19.1: no breaking changes.
- Normal pinned TypeScript contract regeneration: unchanged and current. Upcoming local-schema
  generation also succeeded, without advancing the release pin.

The implementing agent performed a separate maintainer-style self-review and corrected
provisional terminal notifications, cleanup/restart races, eviction pins, cleanup cancellation,
blocking interrupt dispatch and overly broad error mapping. No human review is claimed.

## Video/Screenshots

No UI changes. The real uvicorn HTTP regression exercises sockets and authentication without
attaching any browser or WebSocket, and verifies the exact response. Model work is controlled
in that test; it is not a live-model evaluation.

## Design Doc

`.pr/design.html` is self-contained. Before submission, link it with htmlpreview to the actual
verified fork/head branch. No fork or PR exists yet in the implementation environment.

## Type

- [ ] Bug fix
- [x] Feature
- [ ] Refactor
- [ ] Breaking change
- [ ] Docs / chore

## Notes

- Additive API; existing REST contracts, persisted settings and serialized event unions stay
  unchanged. No package version bump.
- No finish text is returned/injected. #6127 remains the sole owner of mailbox result delivery.
  Retain children when their history/result is still needed.
- Native synchronous tools hold the parent state lock. An ordinary synchronous HTTP tool
  wrapper would violate #6129; tool integration needs a cancellable lock-release seam.
- The deadline bounds waiting after loading, not stop/delete lifecycle cleanup. Synchronous
  worker termination remains cooperative. This is same-instance waiting, not durable workflow
  orchestration. These limits must not be described as completed acceptance criteria.
- The checked-in generated TypeScript schema follows the published v1.58.1 pin; new client
  models are typed and additive until a release advances the generated contract.
- Feature-map CLI verification is blocked by process-namespace/psutil failures in this
  environment. The proposed recipe is in `.pr/FEATURE_MAP_DRAFT.md` and is not a verified map entry.
- No full stress, live-model behavior, Windows, Docker/PyInstaller or GitHub CI result is claimed.
- A companion docs change is committed locally; create its PR and cross-link once authenticated.
- Keep this PR draft. The HUMAN section remains exactly the template placeholder; the human
  author must supply their own note before readiness. Integration and outstanding checks also
  require maintainer review.
