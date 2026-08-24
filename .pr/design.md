# Slice 1 design — per-operation timing instrumentation (issue #4589)

Scope for this PR: **conversation lifecycle create / delete / close**, plus
**event-service load**, **ConversationInfo compose**, and **idle eviction** on
the agent-server, plus the shared telemetry contract that later slices reuse.

## Design decisions (confirmed with requester)

### 1. Raw numeric durations are allowlisted for lifecycle ops only

The existing contract in `telemetry/models.py` deliberately buckets magnitudes
(`ConversationOutcomeProperties`) because "raw counts joined with a timestamp are a
re-identification vector". This issue is a deliberate, documented departure for
*operation latency* only:

- New field type `DurationMs = Annotated[int, Field(ge=0, le=MAX_DURATION_MS)]`
  (integer milliseconds, 24h cap, validation-enforced like every other property).
- A single new event type `agent_server.operation_timing` carrying **no
  high-cardinality dimensions**: no `conversation_ref`, no event counts, no
  user id (the factory's anonymous per-process `distinct_id` is used).
- A lone duration is not re-identifying (unlike counts joined with a timestamp),
  so lifecycle-op latency passes the allowlist. Any future dimension must be
  approved through `EXPECTED_PROPERTY_NAMES` and the schema tests.

The Pydantic schema *is* the allowlist (`models.py` docstring): a leak becomes a
construction-time `ValidationError`, not a review-time observation.

### 2. Stuck vs slow: per-operation watchdog budget, default 20s

- Each operation supplies its **expected elapsed-time budget** (`budget_ms`). If
  omitted, `DEFAULT_STUCK_BUDGET_MS = 20_000` is used.
- A background watchdog task arms `asyncio.sleep(budget_ms / 1000)`. If the
  operation is still in-flight when the budget elapses, it emits a
  `stuck=True` event with the elapsed duration so far and marks the timer stuck.
  The measurement site is never blocked (watchdog is a separate task; emit is
  non-blocking).
- On completion, a **completion event is always emitted** with the final
  duration and `stuck` set to whether the watchdog fired.

Emitting both events makes the two failure shapes distinguishable:

| Case | Events |
|---|---|
| Fast completion | completion only (`stuck=False`, real ms) |
| Slow but completes | stuck event + completion event (`stuck=True`, real ms) |
| Deadlock (no completion) | stuck event only — no completion event ever arrives |

The completion event list is the percentile source (p50/p95/p99 over real
durations); the stuck events are the alert stream.

## Wire contract

New event (one type, `operation` property names the metric):

```
agent_server.operation_timing
  kind: "operation_timing"
  operation: SafeToken        # "conversation_create" | "conversation_delete" | "conversation_close"
                             # | "event_service_load" | "conversation_info_compose" | "conversation_evict"
  duration_ms: DurationMs     # raw ms (wall clock)
  stuck: bool
  stuck_budget_ms: DurationMs # budget that was armed (informational)
  evicted_count: Bucket|null  # only on conversation_evict; bucketed, never raw
```

## Slice-1 mapping to historical failures (#4514 -> fixed by #4570)

The #4570 fix replaced the global `_lifecycle_lock` with per-conversation
`_conversation_lifecycle(cid)` plus an exclusive `_exclusive_lifecycle()`. The
operations that serialize through those locks are the ones the issue wants timed:

- `conversation_create` — `ConversationService._start_conversation`
  (the POST /conversations path; both new-create and resume go through the
  lifecycle lock, so a wedged lock makes start slow/stuck).
- `conversation_delete` — `ConversationService.delete_conversation`.
- `conversation_close` — the per-conversation event-service teardown
  (`EventService.__aexit__`) performed under `_exclusive_lifecycle()` at
  `ConversationService.__aexit__` (server shutdown). A wedged close here is the
  original "stuck close() blocked everything" shape; timing each conversation's
  close individually makes the blocked one visible without blocking measurement.
- `event_service_load` — `ConversationService._get_or_load_event_service`,
  timed from before `_conversation_lifecycle` acquisition through
  `_get_or_load_event_service_locked` (disk hydration + runtime prep). A load
  wedged on the lifecycle lock (#4514) surfaces as stuck-without-completion.
- `conversation_info_compose` — both `_compose_conversation_info*` call sites in
  `_conversation_info` (live in-memory path incl. `get_state()`, and the
  persisted `to_thread` path). The #4417 shape was a GC wedge composing
  `ConversationInfo` off the event loop.
- `conversation_evict` — one pass of `_evict_idle_conversations` (only when
  at least one conversation is evicted; empty passes emit nothing so they do
  not deflate the latency percentile). Emits `evicted_count` as a **bucketed**
  magnitude (`COUNT_BOUNDS`), never a raw count — raw counts joined with a
  timestamp are the original re-identification vector.

## Bucketed counts (design decision)

Per-operation **duration** is allowlisted raw (see decision 1). Magnitudes that
are *counts* — e.g. how many conversations an eviction pass closed — stay on
the bucketed vocabulary (`Bucket` / `COUNT_BOUNDS`), because a raw count joined
with a timestamp is re-identifying while a lone elapsed time is not. The
`operation_timing` event carries at most one such magnitude per operation.

All sites resolve the sink/factory lazily at emit time (`get_telemetry_sink()`,
`get_event_factory()`), matching the existing `_maybe_subscribe_telemetry`
convention so the live consent decision is honored. Emit is best-effort and
cannot raise out (mirrors `TelemetrySink.emit` "never raise").

## Files

- `telemetry/models.py` — `DurationMs`, `OperationTimingProperties`,
  `EventName.OPERATION_TIMING`, union + `EXPECTED_PROPERTY_NAMES`.
- `telemetry/timing.py` (new) — `timed_operation` asynccontextmanager +
  watchdog + default emitter (`DEFAULT_EMITTER`, monkeypatchable in tests).
- `telemetry/__init__.py` — re-export `timed_operation`.
- `conversation_service.py` — wrap create/delete/close.
- Tests: `tests/agent_server/telemetry/test_telemetry_timing.py` (helper +
  model + schema allowlist), service-level tests in
  `tests/agent_server/test_conversation_service.py` (blocked-conversation
  scenario asserts stuck terminal value).

## Follow-up slices (not in this PR)

`event_service_load`, `event_search`/`bash_event_search`, `conversation_info_compose`,
`llm_call`, `switch_llm`, `stats_streaming`, `subscribe_init_push`,
`acp_restart_secret_lookup`, `model_info_discovery`, `summary_render`,
`conversation_evict`, lease/autosave/pubsub/condensation, and the #4588 gate wiring.