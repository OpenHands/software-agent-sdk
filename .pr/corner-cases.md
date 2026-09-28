# Tool-call batch corner-case audit

Implementation reviewed: `0a6b4a491d85889f0412e3699b0083b1936b8979`. No live provider requests were made in this audit.

## Fixed in this PR

When one deferred validation-error callback fails, the remaining error callbacks
are now attempted once each. If action preparation already failed, that original
exception is propagated; otherwise the first notification failure is propagated.
Additional failures are logged. Cancellation is re-raised after the bounded list
of already-created results is attempted. No failed event is retried: the callback
may have persisted it before raising.

Twelve new sync/async cases failed before this change and now pass, covering the
first callback failing, every callback failing, and a cancellation during error
notification, both with and without a prior dispatch failure.

This is best-effort notification, not a durability guarantee. A callback that
fails before saving an event can still leave that event absent. Permanent disk
failure and process termination cannot be repaired by an in-memory error queue.

## Verified behavior

| Case | Evidence / outcome |
| --- | --- |
| Invalid first/middle/last call, multiple invalid calls, all invalid calls | Existing 32 parameterized cases retain one assistant batch and a result per call. |
| Unknown tool or malformed JSON | Validation result belongs to the original batch; later valid calls still execute. |
| Validation failure + executor ValueError + successful sibling | Added tests retain all results; ValueError remains an agent-action error. |
| Validation failure + executor RuntimeError + successful sibling | Added tests retain all results; RuntimeError remains an internal error. |
| Validation failure + Observation(is_error=True) | Added tests preserve the error observation and successful sibling result. |
| Approval / rejection without closing the conversation | Added tests preserve one batch, one validation error and two execution or rejection results. |
| Interrupt during async execution, then resume in the same process | Added concurrency-1/3 tests stop the cooperative executor and retain the validation result plus interrupted-call results. |
| Original exception while flushing, multiple notification failures | All queued results are attempted, and the original dispatch or first notification failure remains the raised exception. |
| Streaming identity and failed durable callbacks | Existing stream-context and stream-identity tests pass in the expanded suite. |
| Hooks, view filtering, event conversion, normal conversation behavior | Relevant existing suites pass. This is not exhaustive coverage of every cross-product of these features. |

The batch regression file now contains **68 cases** (38 original + 30 added).

```bash
OPENHANDS_SUPPRESS_BANNER=1 LITELLM_LOCAL_MODEL_COST_MAP=True uv run --no-sync pytest -q tests/sdk/agent tests/sdk/event tests/sdk/conversation tests/sdk/context/view tests/sdk/hooks
```

**2,282 passed, 6 deselected, 42 warnings**, with an additional unawaited-coroutine
warning at interpreter shutdown. See [corner-tests.txt](corner-tests.txt).
Per-file Ruff / pycodestyle / Pyright / repository pre-commit gates passed.
The original `.pr/repro_mixed_validation.py --check --responses --anthropic`
also passes after this change.

## Four independently reproduced existing problems

These are **not fixed by this PR**. They reproduce with both the current dispatch
and the merge-base dispatch. The comparison loads the original dispatch module
in memory, leaving the checkout unchanged; this PR changes no other SDK
production module. It is a dispatch-isolation comparison, not a second full
repository build. All probes use synthetic tool calls and local counters.

```bash
OPENHANDS_SUPPRESS_BANNER=1 LITELLM_LOCAL_MODEL_COST_MAP=True uv run --no-sync python .pr/probe_corner_cases.py
OPENHANDS_SUPPRESS_BANNER=1 LITELLM_LOCAL_MODEL_COST_MAP=True uv run --no-sync python .pr/probe_corner_cases.py --base-dispatch
```

Captured results: [base dispatch](corner-probes-base.json),
[current dispatch](corner-probes-pr.json).

1. **Closing and reloading while waiting for confirmation loses the batch from
   subsequent LLM context.** The pending valid calls still execute after approval,
   but the next request has zero tool-call groups. The persisted events are not
   deleted; the reconstructed view removes the incomplete batch before its
   results arrive. Inspect [View.from_events](https://github.com/OpenHands/software-agent-sdk/blob/3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14/openhands-sdk/openhands/sdk/context/view/view.py#L143)
   and [view reconstruction](https://github.com/OpenHands/software-agent-sdk/blob/3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14/openhands-sdk/openhands/sdk/conversation/state.py#L395).
2. **A valid call after `finish` is executed by a later run.** The first run
   finishes without executing it, but leaves it as a pending ActionEvent. A later
   `run()` executes the supposedly discarded call. Inspect
   [_truncate_at_finish](https://github.com/OpenHands/software-agent-sdk/blob/3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14/openhands-sdk/openhands/sdk/agent/agent.py#L235) and
   [pending-action execution](https://github.com/OpenHands/software-agent-sdk/blob/3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14/openhands-sdk/openhands/sdk/agent/agent.py#L691).
3. **An execution-result callback failure can cause duplicate tool execution on
   resume.** Both valid tools have already run. The first observation is saved,
   then its user callback fails; the later result is never emitted. The next
   `run()` executes that later tool again (synthetic counter: 1 becomes 2).
   Inspect [_ActionBatch.emit](https://github.com/OpenHands/software-agent-sdk/blob/3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14/openhands-sdk/openhands/sdk/agent/agent.py#L349). This can matter for
   tools with non-idempotent side effects. The new validation-error helper does
   not change this separate execution-result path.
4. **A ValueError from the ActionEvent callback for an unknown tool is mistaken
   for validation failure.** The callback runs after the first ActionEvent is
   persisted, and that exception is caught by the validation handler, causing a
   duplicate ActionEvent with the same call ID. Inspect the unknown-tool branch
   inside [_get_action_event](https://github.com/OpenHands/software-agent-sdk/blob/3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14/openhands-sdk/openhands/sdk/agent/agent.py#L1265).

Keep these as separate follow-ups: each needs changes to recovery, finish
semantics, execution-result delivery or validation exception boundaries beyond
the event-ordering fix in issue #5354. These probes record current behavior;
they are not silently skipped or passing assertions of the desired behavior.

## Other boundaries

- Duplicate call IDs supplied by a provider are not normalized by this patch.
  Identity repair requires its own policy; the verified cases have unique IDs.
- A hard process kill between action persistence and result persistence still
  leaves an incomplete batch. No transaction or persisted error queue was added.
- The interrupt test uses a cooperative executor; arbitrary blocking external
  tools or operating-system process cleanup are not proven by this test.
- Native Responses-origin transport and other live providers remain untested.
