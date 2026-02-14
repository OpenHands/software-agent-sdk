# Agent-server: events via REST + WebSocket

This package implements the agent-server APIs that clients (including `RemoteConversation` in the SDK) use to consume events.

Key code:
- REST: `event_router.py`, `event_service.py`
- WebSocket: `sockets.py` (and publish/subscribe plumbing in `pub_sub.py`)

## Design stance

- **REST is the source of truth** for events (persisted state).
- **WebSocket is a streaming convenience** for freshness (best-effort delivery).

Clients must be able to reconnect and still converge by using REST.

## REST contract: `/api/conversations/{conversation_id}/events/search`

### Pagination
- The endpoint supports pagination using `limit` and `page_id`.
- Responses include `next_page_id`.

**Important:** current server implementation treats `page_id` as an **inclusive** cursor:
- when `page_id` is provided, the returned page may include the event whose `id == page_id`.
- clients that want “strictly after page_id” semantics must drop the first event if it matches the cursor.

(If you change this behavior, update clients and tests accordingly.)

### Ordering / stability
- Event ordering must be stable enough that paging does not skip/duplicate items (other than the intentional inclusivity behavior above).
- If ordering changes (e.g. sort order flips), cursor semantics must be revisited.

### Scaling constraints
- The server must assume runs can produce **tens of thousands of events**.
- Both server and clients must avoid patterns that require scanning the full event history.

This implies:
- enforce reasonable `limit` bounds
- keep pagination efficient (indexes / storage access)

## WebSocket contract: `/events/{conversation_id}`

WS provides low-latency streaming, but should be treated as **best-effort**:
- connections can drop
- clients can lag
- callbacks can be delayed

As a result:
- WS should not be the only correctness mechanism.
- On reconnect / run completion, clients should reconcile via REST using a cursor (`page_id`).

## SDK coupling (where the policy is implemented)

The SDK’s `RemoteConversation` implements client-side reconciliation and bounded paging logic.
See:
- `openhands-sdk/openhands/sdk/conversation/impl/AGENTS.md`
