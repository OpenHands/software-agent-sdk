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
Prepared with Codex assistance on `feat/notes-history-storage-safety`. A real-provider strict test passed all eight post-reset retrieval checks; a paired live evaluation completed 18/18 trials. Offline fault injection and real HTTP/WebSocket tests cover persistence and recovery. The supplementary live challenge suite is paused after provider HTTP 503, with no completed trials; it is not reported as passing.

[Design preview](https://htmlpreview.github.io/?https://github.com/cbinhan/software-agent-sdk/blob/feat/notes-history-storage-safety/.pr/design.html) · [Validation evidence](https://github.com/cbinhan/software-agent-sdk/blob/feat/notes-history-storage-safety/.pr/validation.md)

## Why

Long-running agents need exact earlier information after context reset. Conversation-local notes preserve selected information, while history retrieval can recover original events omitted from those notes. Optional disk protection pauses new work before free space falls below the configured reserve and makes persistence failures explicit.

## Summary

- Add native `context_notes`, `conversation_history`, and optional `new_context` tools with an opt-in `NotesRetrievalCondenser`. Notes append complete deltas, support versioned pagination, and survive resets without deleting source events.
- Add opt-in storage admission, periodic checks, atomic-write headroom, and explicit recovery across local conversations, Agent Server, and subagents. Mirror the recovery API in Python and TypeScript clients; uncertain tool outcomes are not replayed automatically.
- Add a runnable offline/live example, persistence fault-injection coverage, and reproducible paired evaluation scripts. A companion docs change explains configuration and recovery.

## Issue Number

Closes #4916. Disk protection is an additional engineering requirement; the issue does not prescribe a capacity threshold.

## How to Test

```sh
uv run examples/01_standalone_sdk/notes_retrieval/main.py --offline
uv run pytest -q tests/sdk/tool/test_context_memory_tools.py tests/sdk/agent/test_notes_retrieval.py tests/sdk/context/condenser/test_notes_retrieval_condenser.py tests/sdk/io/test_storage_safety.py tests/sdk/conversation/local/test_storage_safety.py tests/agent_server/storage
uv run pytest -q tests/cross/test_remote_conversation_live_server.py -k storage_admission_and_recovery_over_real_http
```

[Validation details](https://github.com/cbinhan/software-agent-sdk/blob/feat/notes-history-storage-safety/.pr/validation.md) include reproduction commands, reports, and limitations:

- Broad Python regression: **1,970 passed**, 70 skipped, 6 deselected; later affected-scope runs also passed. Full TypeScript suite: **336 passed**. Pre-commit, persisted-settings compatibility, and OpenAPI checks passed.
- [Strict live test](https://github.com/cbinhan/software-agent-sdk/blob/feat/notes-history-storage-safety/.pr/evidence/strict-notes/run-528nrz0g/report.json): **8/8 checks passed**, including actual notes read and search/read of a hidden source after reset. Its old all-zero usage fields are invalid metrics and are not used as cost evidence.
- [Live paired evaluation](https://github.com/cbinhan/software-agent-sdk/blob/feat/notes-history-storage-safety/.pr/evidence/notes-comparison/report.json): **18/18 exact matches**, six samples per strategy, 162 provider attempts. All three strategies scored 6/6; history was searched once and never read. This does not demonstrate a retrieval advantage or token savings.
- [Live challenge suite](https://github.com/cbinhan/software-agent-sdk/blob/feat/notes-history-storage-safety/.pr/evidence/notes-challenge/report.json): **0/9 completed**, paused after eight attempts because of HTTP 503. Its offline scripted counterpart passed 9/9 and validates the harness only.
- Actual temporary-file, partial-result, and state-snapshot failures are covered without automatically repeating uncertain tools. [Offline example output](https://github.com/cbinhan/software-agent-sdk/blob/feat/notes-history-storage-safety/.pr/evidence/offline-example.log) confirms persisted reopen.

The repository integration/benchmark workflows have not run for this PR. Maintainer review should determine any additional benchmark requirements.

## Video/Screenshots

SDK/server change with no UI changes. Reproduction commands and logs are provided above.

## Design Doc

[Rendered design and before/after diagrams](https://htmlpreview.github.io/?https://github.com/cbinhan/software-agent-sdk/blob/feat/notes-history-storage-safety/.pr/design.html) · [HTML source](https://github.com/cbinhan/software-agent-sdk/blob/feat/notes-history-storage-safety/.pr/design.html). The preview uses this public fork and feature branch; implementation source links are pinned to the tested revision.

## Type

- [ ] Bug fix
- [x] Feature
- [ ] Refactor
- [ ] Breaking change
- [x] Docs / chore

## Notes

The condenser and storage protection both default to disabled; existing settings and events continue to load. Explicitly configuring `StorageSafety()` selects a 5% reserve. Server policy uses `OH_STORAGE_SAFETY_MIN_FREE_RATIO=0.05` and cannot be disabled by an agent.

The guard is an admission policy, not an OS quota. It preserves committed content, reports uncommitted outputs, and neither deletes history nor resumes execution automatically. Recovery APIs require a server containing this feature.

Request the `integration-test` label for prompt/agent decision coverage. Companion docs PR: [OpenHands/docs#837](https://github.com/OpenHands/docs/pull/837). The same changes are preserved in [docs.patch](https://github.com/cbinhan/software-agent-sdk/blob/feat/notes-history-storage-safety/.pr/docs.patch). Merge implementation before docs. Remove temporary `.pr/` artifacts before merging a fork PR.
