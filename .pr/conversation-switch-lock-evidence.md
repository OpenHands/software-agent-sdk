# Conversation switch routes vs store locks

Baseline: `6a4ee729c11d2e23a8a3667f57a92bd6dc5a1fb5` (origin/main at PR time).
The working-tree fix offloads the two store waits in
`switch_conversation_profile` / `switch_conversation_llm` with `asyncio.to_thread`,
the same treatment the profile-store routes received in #5794 (issue #5632).

Verification ran on macOS with Python 3.13, twice per cell: once against the
baseline (fix stashed) and once with the fix, using both an in-process ASGI app
(`tests/agent_server/test_conversation_switch_lock_responsiveness.py`) and a real
launched agent-server with real HTTP requests and a `FileLock` held by the test
process (`tests/cross/test_remote_conversation_live_server.py::test_conversation_switch_keeps_live_server_responsive`).
No LLM calls are made; no server responses are mocked. The safety timer releases
each held lock after five seconds so the red runs cannot hang. Both routes'
terminal behavior is asserted after the release (404 for the missing profile,
422 for the dangling provider connection).

| Waiting request | Baseline `/alive` | Fixed `/alive` | Status after release |
| --- | --- | --- | --- |
| POST switch_profile, profile=qa-busy (ASGI) | 5.0s | <0.4s | 404 |
| POST switch_llm, provider_connection_id=qa-missing (ASGI) | 5.1s | <0.4s | 422 |
| POST switch_profile (live server) | 4.7s | <1.7s | 404 |
| POST switch_llm (live server) | 4.7s | <1.7s | 422 |

Reproduce from the checkout root:

```sh
uv run pytest tests/agent_server/test_conversation_switch_lock_responsiveness.py -q
uv run pytest "tests/cross/test_remote_conversation_live_server.py::test_conversation_switch_keeps_live_server_responsive" -q
```

On the baseline both latency assertions fail (`/alive took ~5s ... the wait is
blocking the event loop`); with the fix all four pass. The pre-existing profile,
settings, tags and responsiveness neighbors passed unchanged
(`test_profile_store_lock_responsiveness.py`, `test_conversation_tags.py`: 13 passed).
