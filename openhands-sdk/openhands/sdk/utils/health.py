"""Shared readiness polling for agent-server workspaces."""

import time
from collections.abc import Callable
from urllib.error import URLError
from urllib.request import urlopen


def wait_for_server_health(
    host: str, *, timeout: float, check_running: Callable[[], None]
) -> None:
    """Poll HTTP readiness while the caller checks its container or process.

    ``check_running`` raises when the underlying runtime has exited, preserving
    each workspace's existing diagnostics without coupling the SDK to a backend.
    """
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            with urlopen(
                host.rstrip("/") + "/health", timeout=min(1, remaining)
            ) as response:
                if 200 <= response.status < 300:
                    return
        except (URLError, TimeoutError, ConnectionError):
            pass
        check_running()
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise RuntimeError("Container failed to become healthy in time")
