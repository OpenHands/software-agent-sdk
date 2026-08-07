# Conversation search latency / event-loop GC benchmark

Reproduces and measures the agent-server event-loop stall ("wedge") caused by
heavy `ConversationInfo` Pydantic construction running synchronously on the
single asyncio event-loop thread during `GET /api/conversations/search`.

## Usage

```
python3 .pr/conversation_search_latency.py <base_url> <session_key> \
    --search-concurrency 12 --duration 12
```

Sends N concurrent `conversations/search` requests while streaming lightweight
`/server_info` probes. Large gaps between consecutive probe responses indicate
the event-loop thread stalled (e.g. in the cyclic garbage collector).

## Baseline (pre-fix) vs Fixed

Measured against a live agent-server with ~370 persisted conversations on disk.

| metric | pre-fix | post-fix |
|---|---|---|
| search p50 latency | ~10.3 s | ~1.0 s (single) |
| event-loop gap p50 | ~1.7 s | ~0.05 s |
| event-loop gap max | ~5.1 s | ~0.2 s |
| probe responses in 12 s (12-conc) | 5-7 | 84 |

The fix offloads `_compose_conversation_info` to a worker thread via
`asyncio.to_thread`, keeping GC/allocation off the event loop.
