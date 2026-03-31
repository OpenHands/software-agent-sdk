# PR-only reproduction artifacts

These files are review-only evidence for PR #2622 and intentionally live under
`.pr/` so they help reviewers without becoming long-term product code.

## Included artifacts

- `repro_event_loop_block.py` — reproduces the original production failure from
  PR #2622: a websocket reconnect blocks the FastAPI event loop when
  `subscribe_to_events()` acquires `ConversationState`'s synchronous `FIFOLock`
  on the event loop.
- `repro_state_snapshot_consistency.py` — demonstrates why PR #1732 kept full
  state snapshot creation under the state lock. An unlocked snapshot can observe
  mismatched field epochs; the current PR keeps the lock and only moves the wait
  into a worker thread, so those consistency errors do not occur.
- local run summaries under `.pr/logs/` — generated when you execute the scripts
  locally; these are intentionally not versioned.

## Historical context

- PR #1732 (`fix: move async operations outside sync lock in event subscription`)
  intentionally kept `ConversationStateUpdateEvent.from_conversation_state(...)`
  inside `with state:` so the full-state snapshot stayed internally consistent,
  while moving the async send outside the lock.
- PR #2296 (`fix: events search API blocks during agent step`) later removed the
  state lock for event-log reads only, because `EventLog` is append-only and its
  stored events are immutable after append.
- PR #2622 preserves the PR #1732 consistency guarantee. It only changes *where*
  the lock wait happens: the async task delegates that synchronous wait to a
  worker thread via `run_in_executor(...)` so the FastAPI event loop stays
  responsive.

## Run them

From the repository root:

```bash
OPENHANDS_SUPPRESS_BANNER=1 uv run python .pr/repro_event_loop_block.py
OPENHANDS_SUPPRESS_BANNER=1 uv run python .pr/repro_state_snapshot_consistency.py
```
