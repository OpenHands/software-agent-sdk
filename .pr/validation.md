# Preserve tool-call batches in common LLM history

A validation error between tool calls must not divide one LLM response into
multiple assistant messages. This patch changes event-to-message projection and
valid context-view split positions. Agent dispatch and callback order are
unchanged from the baseline.

Baseline: `3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14`.
The exact tested source contents are recorded in
[source-manifest.json](source-manifest.json). The local evidence below was
captured on 2026-09-28 with Python 3.13.12, SDK 1.49.6, LiteLLM 1.93.0,
Pydantic 2.12.5, and macOS 26.7 arm64, using the workspace lockfile.

## Supported-entry-point reproduction

After `make build`, run from the SDK checkout:

```bash
OPENHANDS_SUPPRESS_BANNER=1 LITELLM_LOCAL_MODEL_COST_MAP=True \
  uv run --no-sync python .pr/repro_mixed_validation.py --check --responses --anthropic
```

[repro_mixed_validation.py](repro_mixed_validation.py) calls `Conversation.run()`
with sequential tool execution. It scripts LLM transport to return one response
containing valid A, invalid B (missing `thought`), and valid C. Validation, agent
dispatch, built-in `think` execution, event handling, and request construction
use the SDK. A formatter observer records the common `Message` list and delegates
to the real Chat Completions formatter.

| Layer | Baseline | Candidate |
| --- | --- | --- |
| Common SDK history | assistant[A,B], error B, assistant[C], results A/C | assistant[A,B,C], error B, results A/C |
| Chat Completions transport payload | Calls split into [A,B] and [C] | One [A,B,C] group followed by all three actual results |
| SDK Responses serialization | calls A/B, output B, call C, outputs A/C | calls A/B/C, outputs B/A/C |
| LiteLLM Anthropic serialization | A's actual result replaced by a skipped/interrupted placeholder | All three tool-use blocks followed by their actual results |
| `--check` exit status | 1 | 0 |

Full output: [before.txt](before.txt), [after.txt](after.txt). `before.txt` is the
captured run of the original issue reproducer on the pinned baseline;
`after.txt` was freshly generated from the source snapshot in the manifest.
The check flag asserts common-history and Chat Completions grouping. Responses
and Anthropic output were also inspected for call IDs and actual result contents.
Local checkout paths in the logs are replaced with `<repo>`.

To repeat the baseline, copy the script outside the checkout, use a separate
checkout of the pinned baseline, run `make build` there, and execute the copied
script with the same flags. This avoids altering a checkout with local changes.

This reproduction sends no provider API requests. Responses and Anthropic checks
serialize the same Chat-origin history; they do not exercise a native
Responses-origin or Anthropic-origin model response. Events are held in memory;
this is not a disk-reload test. The synthetic model emits a cost-estimation
warning, included in the logs.

## Regression coverage

The patch adds 62 parametrized cases relative to the baseline:

| Cases | Behavior checked | Source |
| --- | --- | --- |
| 32 | Invalid-call positions, multiple/all invalid calls, unknown tools, malformed JSON, confirmation, sync/async, concurrency 1/3, response metadata | `tests/sdk/agent/test_validation_error_batch.py:40` |
| 2 | Callback RuntimeError propagates as ERROR; cancellation results in PAUSED before later action callbacks | `tests/sdk/agent/test_validation_error_batch.py:112` |
| 12 | Validation errors mixed with executor ValueError, RuntimeError, or an error observation | `tests/sdk/agent/test_validation_error_batch.py:152` |
| 4 | Approving and rejecting pending valid calls | `tests/sdk/agent/test_validation_error_batch.py:215` |
| 2 | Cooperative interruption during execution and continuation in the same conversation | `tests/sdk/agent/test_validation_error_batch.py:254` |
| 6 | Grouping stops at another response, user/assistant message, unrelated error, executable-action error, or observation | `tests/sdk/event/test_events_to_messages.py:753` |
| 4 | Interleaved validation errors cannot expose a cut inside one response; distinct responses keep their boundary | `tests/sdk/context/view/test_view_manipulation_indices.py:30` |

```bash
OPENHANDS_SUPPRESS_BANNER=1 LITELLM_LOCAL_MODEL_COST_MAP=True \
  uv run --no-sync pytest -q \
  tests/sdk/agent tests/sdk/event tests/sdk/conversation \
  tests/sdk/context/view tests/sdk/context/condenser tests/sdk/hooks tests/sdk/critic
```

Result: **2,398 passed, 6 deselected, 42 warnings** in 41.75 seconds.
[tests.txt](tests.txt) includes the complete output and the shutdown warning that
`_apply_acp_model` was never awaited. This is the related suite, not the entire
repository suite. Local test execution needs permission to use the SDK profile
lock and localhost test servers.

Pre-commit checks passed for the changed Python files: Ruff formatting/lint,
pycodestyle, Pyright, import rules, tool registration, and applicable repository
gates. Markdown, HTML, JSON, and log files also went through per-file pre-commit;
Python-only hooks skip those files. `git diff --check` passed.

## Compatibility and scope

- The converter collects only matching `AgentErrorEvent` results for an invalid
  action (`action is None` and the same `tool_call_id`) while grouping one
  `llm_response_id`. It constructs new messages; stored events stay unchanged.
- The view property removes cut positions between actions sharing one response
  ID, including when error results lie between them. Existing action/result
  atomicity also contributes to the final `View.manipulation_indices`.
- Public signatures, serialized schemas, settings, callback ordering, exception
  propagation, confirmation decisions, and executor concurrency are unchanged.
  There is no new exception handler or provider-specific branch.
- Complete event histories already containing this interleaving benefit when
  projected again. This is not a storage migration or recovery mechanism for
  missing results, incomplete batches, duplicated call IDs, or process crashes.
- Confirmation and interruption cases continue the same conversation instance.
  Cold reload of pending actions and exactly-once execution after callback/storage
  failures are outside the verified scope.
- Other live providers, live Responses, native Responses-origin runs, and
  maintainer integration/evaluation workflows remain untested. Contributor-supplied
  DeepSeek candidate reruns are described below.

## DeepSeek reproduction

[live_deepseek.md](live_deepseek.md) contains three contributor-supplied candidate
reruns: **HTTP 200 in all three cases**, compared with baseline HTTP 400. The
requests preserve one complete assistant batch, its reasoning, and every actual
tool result. After temporary-path normalization, API parameters, system/tools
hashes, user message, tool calls, and result contents/order match the baseline;
combining the baseline assistant messages yields the candidate messages exactly.
Validation errors still precede later ActionEvents in the event log.

These runs exercise the real second request against DeepSeek through OpenRouter;
the first completion is scripted. The responses request another tool call and
the conversation ends in ERROR, consistent with the configured two-iteration
cap. This demonstrates request acceptance, not full task completion. The recorder
does not include the final error-event payload or source revision; see the guide
for the interpretation and provenance limits. Curated files preserve raw-file
hashes and remove session/request identifiers and local paths. The supplied API
calls were not repeated while updating this packet.

## Review packet

[design.html](design.html) explains the two production changes with before/after
figures and source excerpts. [pr-description.md](pr-description.md) contains the
PR body with relative artifact links. Resolve these to commit-pinned GitHub URLs
(and the public HTML preview for the design) when posting it. Preserve its HUMAN
section exactly.

This is a fork PR. Remove temporary `.pr/` files before merge, retaining
commit-pinned review links. Keep Draft until the human note and maintainer
readiness requirements are satisfied.
