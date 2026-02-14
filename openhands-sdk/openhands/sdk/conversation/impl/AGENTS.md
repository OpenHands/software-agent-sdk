# Conversation impl: RemoteConversation event sync (WS + REST)

This directory contains the concrete `Conversation` implementations:
- `local_conversation.py`
- `remote_conversation.py`

The **RemoteConversation** client consumes events via two interfaces:
- **WebSocket** (`/events/{conversation_id}`) for low-latency streaming
- **REST** (`/api/conversations/{conversation_id}/events/search`) for authoritative state

## Goals / non-goals

**Goals**
- WS provides *freshness* (events arrive quickly when the connection is healthy).
- REST is the *source of truth* (persisted events).
- Client behavior must **scale** to large runs (60k+ events).

**Non-goals**
- No SDK API should ever require iterating the **entire** event history to be correct.

## Contract we implement on the client (policy)

### 1) WS is best-effort; REST is authoritative
Treat WS delivery as potentially delayed / dropped (disconnects, callback lag, buffering).
If correctness depends on an event being present, verify via REST.

### 2) Cursor-based, bounded reconciliation
When reconciling via REST, **never full-scan** the full history.
Reconciliation must be:
- **incremental** (start from a known cursor, usually the last seen event id)
- **paged** (`page_id` / `next_page_id`)
- **bounded** (max cycles / reasonable limits)

RemoteConversation currently uses a post-run reconcile loop to fetch missed tail events without relying on WS timing.

### 3) `run(blocking=True)` expectations
If we block until run completion, we should not return with a *terminal status* but a **missing tail** of events.
The post-run reconcile is intended to make the event cache converge to REST after the run finishes.

This is a trade-off:
- we accept some extra REST calls and possibly a slightly longer wall time
- in exchange for determinism (REST truth wins even if WS lags)

## What to check when changing this code

- **Do we still avoid unbounded iteration?**
  - No loops that can walk all 60k+ events unless explicitly bounded and paged.
- **Does reconciliation have a clear cursor?**
  - Prefer “fetch after last known id” semantics.
- **Do we assume WS delivery is complete?**
  - Don’t.

## Related server-side contracts

Agent-server owns REST/WS semantics for events. See:
- `openhands-agent-server/openhands/agent_server/AGENTS.md`
