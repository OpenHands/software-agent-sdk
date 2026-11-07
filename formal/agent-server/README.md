# Lean models of the agent-server

These are small Lean 4 models (core Lean only, no Mathlib) of three
concurrency protocols in `openhands-agent-server`, taken from main @ e4160d118.
Each model has an `Impl.main` branch that mirrors the shipped code and,
where a bug was found, an `Impl.fixed` branch that mirrors the fix on this
branch. Each `Act` is one atomic stretch of asyncio code between two `await`s.

```
cd formal/agent-server && lake build      # Lean v4.35.0, ~1 min
```

| File | Code it models | Main result |
|---|---|---|
| `AgentServer/RunAdmission.lean` | `EventService.run`, `_run_and_publish`, `settle`, `_maybe_end_run_session`, `pause`, `close` | `main_pause_is_overridden`, `main_run_starts_after_close` (counterexamples); `tokens_conserved`, `idle_holds_no_token` (both impls); `fixed_stop_wins`, `fixed_rearm_progress` |
| `AgentServer/EventDelivery.lean` | `session_socket.py` subscribe → mark → replay → `go_live`, plus the run thread's persist → schedule → publish | `main_duplicates` (counterexample); `fixed_no_dup_in_order`, `fixed_complete` |
| `AgentServer/Lease.lean` | `ConversationLease` and `EventService._write_guard` | `fenced_until_reclaim` (holds on main); `main_closed_service_write_unfenced` |

The counterexample theorems are computed traces (`rfl`/`decide`). Python
tests replay the same traces against the real code:

* `tests/agent_server/test_rearm_stop_wins.py`: a pause, close or delete that
  lands while a re-arm waits for run capacity is overridden. The delete case
  writes `base_state.json` and event files back into the deleted
  conversation directory.
* `tests/agent_server/test_session_socket.py::test_event_persisted_before_mark_but_published_after_go_live`:
  an event appended before the mark but fanned out after `go_live` reaches
  the client twice.

What the models leave out: the ACP supersede/pin path, `/goal` loops and
`reserve_run_slot` in run admission; replay from a non-zero cursor and
transient frames in event delivery; time and TTL expiry in the lease (`dead`
abstracts both).
