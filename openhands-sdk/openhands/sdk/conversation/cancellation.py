"""Cooperative cancellation token for tool execution.

A ``CancellationToken`` is a thread-safe flag that propagates an
``interrupt()`` signal down to in-flight tool calls.  The token is
created fresh for each ``run()`` / ``arun()`` invocation and stored
on the :class:`LocalConversation` so that:

* ``interrupt()`` can set it immediately,
* :class:`ParallelToolExecutor` can skip pending tools, and
* individual tools can check it for early exit.

The token is deliberately **not** an ``asyncio`` primitive — it must
be usable from both the event-loop thread and thread-pool workers.
"""

from __future__ import annotations

import threading


class CancellationToken:
    """Thread-safe cancellation flag.

    >>> token = CancellationToken()
    >>> token.is_cancelled
    False
    >>> token.cancel()
    >>> token.is_cancelled
    True
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Signal cancellation.  Idempotent."""
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()
