"""The one way async code reaches a persistence store.

The stores (settings, secrets, workspaces, LLM/agent/meta profiles, provider
connections, MCP OAuth tokens) are synchronous: each call reads or writes
files and may wait up to its lock timeout for another writer. Called from an
``async def`` they would stall the event loop, and with it every other
request, websocket and background task, for that whole wait.

``run_store`` runs a store call, or a sync function doing several store
calls, on a worker thread and awaits it. Keep a read-modify-write in one
function so it makes one hop::

    settings = await run_store(store.load)

    def add(workspaces: PersistedWorkspaces) -> PersistedWorkspaces: ...
    await run_store(store.update, add)

``scripts/check_async_store_access.py`` (pre-commit) rejects store calls made
directly in async code or hopped with ``asyncio.to_thread`` /
``run_in_executor`` instead of this function.
"""

import asyncio
from collections.abc import Callable


async def run_store[**P, R](
    fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs
) -> R:
    """Run blocking store work ``fn(*args, **kwargs)`` off the event loop.

    Uses the loop's default executor and copies the caller's context
    variables (``asyncio.to_thread`` semantics). Cancelling the awaiting task
    does not stop a write already running on the worker thread.
    """
    return await asyncio.to_thread(fn, *args, **kwargs)
