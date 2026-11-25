"""Local HTTP server for completion-callback retry tests."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


# An int is an HTTP status. A tuple is a status plus response headers.
# "drop" closes the socket. "stall" waits and sends no response.
Action = int | tuple[int, dict[str, str]] | str


class CallbackServer:
    """Threading server so a stalled attempt does not block the retry."""

    def __init__(self, actions: list[Action], stall_seconds: float = 1.0) -> None:
        self.actions = list(actions)
        self.stall_seconds = stall_seconds
        self.bodies: list[dict[str, Any]] = []
        self.authorization: list[str | None] = []
        self.times: list[float] = []
        self._lock = threading.Lock()
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                body = json.loads(raw.decode()) if raw else {}
                with parent._lock:
                    index = len(parent.bodies)
                    parent.bodies.append(body)
                    parent.authorization.append(self.headers.get("Authorization"))
                    parent.times.append(time.monotonic())
                    action: Action = (
                        parent.actions[index] if index < len(parent.actions) else 500
                    )
                if action == "drop":
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return
                if action == "stall":
                    time.sleep(parent.stall_seconds)
                    return
                if isinstance(action, int):
                    status = action
                    extra: dict[str, str] = {}
                elif isinstance(action, tuple):
                    status, extra = action
                else:
                    return
                self.send_response(status)
                for key, value in extra.items():
                    self.send_header(key, value)
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

        host, port = self._httpd.server_address[:2]
        self.url = f"http://{host}:{port}/complete"

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@contextmanager
def callback_server(
    actions: list[Action], stall_seconds: float = 1.0
) -> Iterator[CallbackServer]:
    server = CallbackServer(actions, stall_seconds=stall_seconds)
    server.start()
    try:
        yield server
    finally:
        server.close()
