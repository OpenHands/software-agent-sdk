# Issue #3141 (OSS-1495) — QA evidence for idle-conversation eviction

Verification for *perf: finished conversations are never evicted from memory*.

Run it yourself:

```bash
uv run python .pr/live_idle_eviction_evidence.py
```

Everything below is a real `ConversationService` with real on-disk persistence
— no mocks, no monkeypatched internals. No API key and no network are needed:
conversations are populated with `send_message(run=False)` and
`autotitle=False`, so neither the agent loop nor the LLM is ever invoked.

Environment: macOS 15 (arm64, APFS SSD), Python 3.13.11, source under test at
`a7739c3d1`. A full run takes ~5 minutes — the `live` phase waits for a real
60 s sweep. Numbers below are from a run with `OPENAI_API_KEY` unset.

## 1. The regression, and that it is gone

Each round starts 20 conversations with 40 messages of 8 KB — roughly a real
working conversation's event history — and then lets them go idle. The two arms
run as separate processes so RSS reflects only that arm's work, and each
discards one unmeasured warmup round so the numbers show the marginal cost of
*retaining* conversations rather than of starting the server.

**Eviction OFF** (what `main` does today):

```
   round | conversations | resident |      RSS | growth
  -------+---------------+----------+----------+--------
       1 |            20 |       40 |  321.6 M |  +19.3 M
       2 |            40 |       60 |  343.0 M |  +40.7 M
       3 |            60 |       80 |  365.1 M |  +62.8 M
       4 |            80 |      100 |  387.2 M |  +84.8 M
       5 |           100 |      120 |  409.2 M | +106.9 M
=> 100 conversations touched, RSS +106.9 MiB (+1095 KiB/conversation)
```

**Eviction ON:**

```
   round | conversations | resident |      RSS | growth
  -------+---------------+----------+----------+--------
       1 |            20 |        0 |  302.6 M |   +0.3 M
       2 |            40 |        0 |  302.9 M |   +0.7 M
       3 |            60 |        0 |  303.3 M |   +1.0 M
       4 |            80 |        0 |  303.6 M |   +1.3 M
       5 |           100 |        0 |  303.9 M |   +1.7 M
=> 100 conversations touched, RSS +1.7 MiB (+17 KiB/conversation)
```

| | eviction OFF | eviction ON |
| --- | ---: | ---: |
| RSS growth over 100 conversations | +106.9 MiB | **+1.7 MiB** |
| Marginal cost per conversation | 1095 KiB | **17 KiB** |

Without eviction, RSS is linear in *conversations ever touched*. With eviction
it tracks *conversations in use* instead. The ~17 KiB that does remain per
conversation is the lightweight catalog record kept on purpose — that record is
what keeps an evicted conversation listable and re-hydratable — plus allocator
noise; it is ~1.5% of the 1095 KiB a resident conversation costs.

One honest note on how to read these numbers: a single evict does **not** drop
RSS, because CPython returns freed objects to its own allocator rather than to
the OS. The fix is not visible as a downward step; it is visible as the flat
line above — memory is reused by the next conversation instead of the process
growing without bound. That is why this is measured as a growth curve.

## 2. The background task does the work by itself

The `live` phase never calls `_evict_idle_conversations` and never rewrites an
idle clock. It starts the service with a 40 s TTL, waits for the real sweep
(the loop wakes every `max(60, TTL/2)` = 60 s), and then inspects the result.

```
  resident at t=0: 6 (3 idle, 1 running, 1 websocket-attached, 1 kept warm)
  waiting for the background sweep ...
  sweep observed after 60.1s

  [PASS] 3 idle conversations evicted by background task            after 60s
  [PASS] catalog records kept, so they stay listable                6 records retained
  [PASS] evicted Conversation objects are garbage collected         3/3 reclaimed
  [PASS] evicted EventService objects are garbage collected         3/3 reclaimed
  [PASS] running conversation NOT evicted                           in-flight run task
  [PASS] websocket-attached conversation NOT evicted                external subscriber
  [PASS] recently-accessed conversation NOT evicted                 accessed every 5s
  [PASS] re-hydrated from disk as a fresh runtime                   new EventService instance
  [PASS] event history identical after re-hydration                 13 events, ids match
  [PASS] event payloads identical after re-hydration                serialized events match
  [PASS] conversation metadata identical after re-hydration         StoredConversation matches
  [PASS] guard defers rather than exempts (subscriber gone)         eligible on the next sweep
```

What each group establishes:

- **It actually evicts.** Removal from `_event_services` is driven by the real
  background loop on the real clock, not by the test poking a private method.
- **Nothing leaks.** Weakrefs confirm both layers are reclaimed — the
  `EventService` and the `LocalConversation` hanging off it (agent, LLM, tools
  and the full event history). Dropping the dict entry would not prove this;
  a lingering referrer anywhere would have kept them alive.
- **The guards hold.** A conversation with an in-flight run, one with a
  websocket client attached, and one being polled every 5 s all survive a real
  sweep. The subscriber guard *defers* rather than exempts: once the client
  disconnects the conversation is eligible again on the next sweep.
- **Re-hydration is faithful.** After eviction, the next access rebuilds a new
  `EventService` from disk whose event count, event ids, serialized event
  payloads and `StoredConversation` all match what was in memory before.

## 3. Default configuration

```
  [PASS] default TTL is 20 minutes                                  1200.0 s
  [PASS] eviction can be turned off                                 null keeps conversations
```

Per review feedback the TTL now defaults to 20 minutes
(`DEFAULT_CONVERSATION_IDLE_TTL_SECONDS`), matching the idle timeout used on
OpenHands Cloud. Setting `conversation_idle_ttl_seconds` to `null` restores the
previous keep-in-memory-until-deleted behaviour.

## 4. Unit tests

```
$ uv run pytest tests/agent_server/test_conversation_eviction.py tests/agent_server/test_config.py -q
12 passed

$ uv run pytest tests/agent_server/ -q
1784 passed, 13 deselected
```
