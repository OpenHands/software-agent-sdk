# Evidence for #4192 fix

## Root-cause repro (service level): `.pr/repro_4192.py`

Four restart shapes against `ConversationService` on unmodified `main`:

```
== A: graceful stop, then restart ==
  lease exists after graceful stop: False
  [restarted server] listed=True (n=1) info=YES event_service=YES
== B: crash (dead pid, same host), quick restart ==
  [restarted server] listed=True (n=1) info=YES event_service=YES
== C: docker-style restart (different host in lease) ==
  [restarted server] listed=True (n=1) info=YES event_service=NO (lease held?)
== D: quick restart while old process still alive ==
  [restarted server] listed=True (n=1) info=YES event_service=NO (lease held?)
```

Graceful restarts (A) and same-host crash recovery (B, from #3184) already
work. The reported symptom comes from C/D: the conversation is still *listed*,
but `_get_or_load_event_service` swallows `ConversationLeaseHeldError` and
returns `None`, so every `/events/*` route 404s for up to the 45 s TTL — in a
UI the history is effectively gone.

## Live end-to-end (real server over REST): `.pr/live_test_4192.sh`

Create a conversation via `POST /api/conversations`, SIGKILL the server,
rewrite the lease's `owner_host` to a vanished hostname (what a recreated
container looks like — pid liveness can't be verified cross-host), restart on
the same storage, and query:

```
=== OLD default (leasing on, OH_LEASE_TTL_SECONDS=45) ===
created conversation: 924dbea3-2a10-40a5-b256-359e5c218f44
lease rewritten to foreign host (still live)
restarted server: conversations listed=1 events endpoint HTTP=404

=== NEW default (leasing disabled) ===
created conversation: 54b41ef0-d906-40e3-aace-6037107abebe
no lease file present (leasing disabled)
restarted server: conversations listed=1 events endpoint HTTP=200
```

The OLD run also confirms `OH_LEASE_TTL_SECONDS` still enables leasing
end-to-end (the opt-in path for shared-storage deployments).

## Test suites

- `tests/agent_server/test_config.py`, `tests/agent_server/test_env_parser.py`,
  `tests/agent_server/test_conversation_lease.py`,
  `tests/cross/test_conversation_lease_behavior.py`: all pass.
- Full `tests/cross`: 362 passed, 1 skipped.
- Full `tests/agent_server` + `tests/cross` in one run: only failure is
  `test_openai_chat_completions_gateway_over_real_server`, which fails
  identically on unmodified `main` under full-suite load (flaky live-server
  test, unrelated).
