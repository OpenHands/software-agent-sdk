# WS tail reconcile repro notes

## Goal
Document the test gap follow-up for PR #1865 and the manual repro work done against `main`.

## Code changes on this branch
- Added a deterministic unit test in `tests/sdk/conversation/remote/test_remote_conversation.py` that proves `run(blocking=True)` returns only after `_finalize_events_after_run()` reconciles a missing tail from REST.
- Strengthened `tests/cross/test_remote_conversation_live_server.py::test_post_run_reconcile_needed_under_ws_callback_lag` so it now asserts the finish action is already present when `run()` returns in the targeted tail-lag scenario.
- Narrowed the `RemoteConversation._finalize_events_after_run()` docstring to describe the real guarantee: bounded **post-run tail repair**, not arbitrary hole repair.

## Main clone repro setup
- Clone used for repro only: `/workspace/project/software-agent-sdk-main-repro`
- Branch checked out in that clone: `main`
- I did **not** commit anything in that clone.

### Temporary hack in the main clone
I appended a temporary test named `test_tmp_main_repro_ws_callback_lag_missing_tail` to:

- `/workspace/project/software-agent-sdk-main-repro/tests/cross/test_remote_conversation_live_server.py`

That temporary test monkeypatches the client-side event cache append path so finish `ActionEvent` / `ObservationEvent` are applied asynchronously and late, while the terminal status path remains fast. This reproduces the `hole-before-cursor` case on `main`.

## Commands run

### Current branch validation
```bash
cd /workspace/project/software-agent-sdk
uv run pre-commit run --files \
  openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py \
  tests/sdk/conversation/remote/test_remote_conversation.py \
  tests/cross/test_remote_conversation_live_server.py

uv run pytest \
  tests/sdk/conversation/remote/test_remote_conversation.py \
  tests/cross/test_remote_conversation_live_server.py::test_post_run_reconcile_needed_under_ws_callback_lag
```

### Main clone repro
```bash
cd /workspace/project/software-agent-sdk-main-repro
uv run pytest -s \
  tests/cross/test_remote_conversation_live_server.py::test_tmp_main_repro_ws_callback_lag_missing_tail
```

## Main repro result
The temporary repro test passed on `main` and showed this exact behavior after `run()` returned:

- Client events after `run()`:
  - `event_1: ConversationStateUpdateEvent`
  - `event_2: SystemPromptEvent`
  - `event_3: MessageEvent`
  - `event_4: ConversationStateUpdateEvent`
  - `event_5: ConversationStateUpdateEvent`
- REST events after `run()`:
  - `event_1: SystemPromptEvent`
  - `event_2: MessageEvent`
  - `event_3: ConversationStateUpdateEvent`
  - `event_4: ConversationStateUpdateEvent`
  - `event_5: ActionEvent(tool=finish)`
  - `event_6: ObservationEvent(tool=finish)`
  - `event_7: ConversationStateUpdateEvent`
- Counts before manual reconcile:
  - client finish actions: `0`
  - client finish observations: `0`
  - REST finish actions: `1`
  - REST finish observations: `1`
- After a manual `conv.state.events.reconcile()` on `main`, the client contained both missing finish events.

## Event-by-event interpretation

### `main` bug repro
A concrete semantic sequence for the repro is:

1. `event_1`: `SystemPromptEvent`
2. `event_2`: user `MessageEvent`
3. `event_3`: early `ConversationStateUpdateEvent`
4. `event_4`: another non-terminal `ConversationStateUpdateEvent`
5. `event_5`: `ActionEvent(tool=finish)`
6. `event_6`: `ObservationEvent(tool=finish)`
7. `event_7`: terminal `ConversationStateUpdateEvent(execution_status=finished)`

What happens on `main` in the repro:
- the local cache has already advanced to the terminal state update,
- `event_5` and `event_6` are still missing locally,
- `run()` returns anyway,
- a later manual REST reconcile fills the gap.

### After this PR
For the **tail-lag scenario this PR targets**, the sequence is the same up to the terminal update, but after `_wait_for_run_completion()` returns, `_finalize_events_after_run()` repeatedly reconciles from the last cached event ID and merges any missing **tail** events before `run()` returns.

That means, for a contiguous-prefix / missing-tail case:
- `run()` returns with the finish `ActionEvent` already present,
- a follow-up `reconcile()` is idempotent / additive only,
- the targeted live test now asserts that behavior.

## Important caveat
The stronger repro in the main clone demonstrates a **hole-before-cursor** case: the local cache already contains a later event while earlier finish events are still missing. This PR does **not** claim to repair that class of hole. That is why the PR wording was narrowed from broad “REST is authoritative after run returns” language to explicit **post-run tail reconciliation** language.
