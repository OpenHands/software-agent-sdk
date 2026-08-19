import atexit
import inspect
import threading
import weakref
from collections.abc import Callable
from typing import Any

import anyio
from anyio.from_thread import BlockingPortal, start_blocking_portal

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# Upper bound on how long close() waits for the portal thread to wind down.
# The portal thread is a daemon thread, so abandoning it is safe; blocking the
# caller forever is not.
#
# The default is intentionally short for lifecycle teardown: a successful
# portal stop + join normally completes in milliseconds once remaining tasks
# are cancelled, so 10s is already a generous margin for a healthy shutdown.
# It is deliberately *not* the 30s inherited from BrowserToolExecutor cleanup
# — that value predates the lifecycle-lock path and is too long for teardown
# that may hold a conversation lock while waiting. See PR #4548 and the
# discussion of #4598 in its description.
DEFAULT_CLOSE_TIMEOUT = 10.0


class AsyncExecutor:
    """
    Thin wrapper around AnyIO's BlockingPortal to execute async code
    from synchronous contexts with proper resource and timeout handling.
    """

    def __init__(self):
        self._portal = None
        self._portal_cm = None
        self._lock = threading.Lock()
        self._atexit_registered = False

    @property
    def portal(self) -> BlockingPortal:
        """The lazily-started ``BlockingPortal`` — public accessor.

        Callers that need to schedule work directly on the portal loop
        (e.g. ``ACPAgent.astep`` bridges ACP awaits across loops via
        ``portal.start_task_soon`` + ``asyncio.wrap_future``) use this
        instead of ``run_async`` because they need the future, not the
        result.
        """
        return self._ensure_portal()

    def _ensure_portal(self) -> BlockingPortal:
        with self._lock:
            if self._portal is None:
                self._portal_cm = start_blocking_portal()
                self._portal = self._portal_cm.__enter__()
                # Register atexit handler to ensure cleanup on interpreter shutdown
                if not self._atexit_registered:
                    # Use weakref to avoid keeping the executor alive
                    weak_self = weakref.ref(self)

                    def cleanup():
                        executor = weak_self()
                        if executor is not None:
                            try:
                                executor.close()
                            except Exception:
                                pass

                    atexit.register(cleanup)
                    self._atexit_registered = True
            return self._portal

    def run_async(
        self,
        awaitable_or_fn: Callable[..., Any] | Any,
        *args,
        timeout: float | None = None,
        **kwargs,
    ) -> Any:
        """
        Run a coroutine or async function from sync code.

        Args:
            awaitable_or_fn: coroutine or async function
            *args: positional arguments (only used if awaitable_or_fn is a function)
            timeout: optional timeout in seconds
            **kwargs: keyword arguments (only used if awaitable_or_fn is a function)
        """
        portal = self._ensure_portal()

        # Construct coroutine
        if inspect.iscoroutine(awaitable_or_fn):
            coro = awaitable_or_fn
        elif inspect.iscoroutinefunction(awaitable_or_fn):
            coro = awaitable_or_fn(*args, **kwargs)
        else:
            raise TypeError("run_async expects a coroutine or async function")

        # Apply timeout by wrapping in an async function with fail_after
        if timeout is not None:

            async def _with_timeout():
                with anyio.fail_after(timeout):
                    return await coro

            return portal.call(_with_timeout)
        else:

            async def _execute():
                return await coro

            return portal.call(_execute)

    def close(self, timeout: float | None = DEFAULT_CLOSE_TIMEOUT):
        """Shut down the portal, without ever blocking the caller forever.

        Semantics
        ---------
        This is **bounded, best-effort shutdown, not guaranteed cleanup.**

        - It first cancels any remaining portal tasks (``portal.stop(True)``)
          and then waits up to ``timeout`` seconds for the portal thread to
          exit.
        - If the portal thread is stuck on work that does *not* honour
          cancellation — for example a task awaiting inside a worker thread,
          which anyio cannot interrupt until the thread returns (see #4598) —
          the wait expires and the helper thread is **abandoned**. The portal
          thread is a daemon, so the process can still exit; but the thread
          and any resources it holds (subprocess handles, sockets, file
          descriptors) may remain alive until process exit. This is a
          deliberate trade-off: blocking the caller forever is worse.
        - The shutdown path is non-raising. Failures inside the portal
          teardown are logged (with traceback) and swallowed so that
          ``close()`` is always safe to call from a destructor or
          ``__exit__``.
        - **Idempotent.** Calling ``close()`` on an already-closed executor
          is a no-op.

        Args:
            timeout: seconds to wait for the portal thread to exit. ``None``
                waits indefinitely (the previous, pre-#4548 behaviour) and
                is kept only for compatibility — **do not use it on any
                production path**, since it reintroduces the unbounded hang
                this fix exists to prevent. See PR #4548 / issue #4598.
        """
        with self._lock:
            portal_cm = self._portal_cm
            portal = self._portal
            self._portal_cm = None
            self._portal = None

        if portal_cm is None:
            return

        # Stamp the owner into the thread name so an abandoned thread is
        # identifiable in py-spy / py-dump traces (the portal thread itself
        # does not carry the executor identity).
        owner = type(self).__qualname__
        thread_name = f"{owner}-close"

        def _shutdown() -> None:
            try:
                # Cancel whatever is still running. Without this, anyio's
                # graceful path (portal.stop(cancel_remaining=False)) waits
                # for in-flight tasks that may never complete on their own.
                if portal is not None:
                    portal.call(portal.stop, True)
            except RuntimeError:
                pass  # portal already stopped
            except Exception:
                # Teardown must stay non-raising: close() is called from
                # __del__/__exit__/atexit. Log with traceback so the failure
                # remains diagnosable instead of being reduced to str(exc).
                logger.warning(
                    "Error stopping BlockingPortal during AsyncExecutor.close "
                    "(owner=%s); teardown continues.",
                    owner,
                    exc_info=True,
                )
            try:
                portal_cm.__exit__(None, None, None)
            except Exception:
                logger.warning(
                    "Error closing BlockingPortal context manager during "
                    "AsyncExecutor.close (owner=%s); teardown continues.",
                    owner,
                    exc_info=True,
                )

        # Run the shutdown on a helper thread so we can bound the wait: the
        # portal thread can be stuck on work that does not honour cancellation
        # (for example an await blocked inside a worker thread), and anyio
        # joins it with no timeout.
        try:
            waiter = threading.Thread(
                target=_shutdown, name=thread_name, daemon=True
            )
            waiter.start()
        except RuntimeError:
            # Interpreter is shutting down and will not start new threads.
            # The portal thread is a daemon; let the process reap it.
            return

        waiter.join(timeout)
        if waiter.is_alive():
            # Abandonment is observable: name the owner, the timeout, and
            # that cancellation was already attempted, so the operator can
            # correlate with py-spy / the wedged resource. Per #4598 the
            # thread may genuinely be un-interruptible from Python.
            logger.warning(
                "AsyncExecutor (owner=%s): BlockingPortal did not shut down "
                "within %.1fs; abandoning its helper thread. Cancellation was "
                "attempted but the portal thread appears to be stuck on work "
                "that does not honour cancellation. Its resources (subprocess "
                "handles, sockets, fds) may remain alive until process exit. "
                "See issue #4598.",
                owner,
                float(timeout) if timeout is not None else -1.0,
            )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
