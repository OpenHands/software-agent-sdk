"""Readiness polling uses real HTTP responses and preserves backend failures."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import Mock

import pytest

from openhands.sdk.utils.health import wait_for_server_health


@pytest.fixture
def health_server():
    responses = []
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(responses.pop(0) if responses else 503)
            self.end_headers()

        def log_message(self, *args: object, **kwargs: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", responses, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_unready_server_is_retried_while_runtime_is_alive(health_server):
    host, responses, requests = health_server
    responses.extend([503, 200])
    check_running = Mock()
    wait_for_server_health(host + "/", timeout=3, check_running=check_running)
    assert requests == ["/health", "/health"]
    check_running.assert_called_once_with()


def test_exited_runtime_keeps_its_backend_diagnostic(health_server):
    host, _, _ = health_server
    check_running = Mock(side_effect=RuntimeError("Container exited with code 9"))
    with pytest.raises(RuntimeError, match="exited with code 9"):
        wait_for_server_health(host, timeout=3, check_running=check_running)
    check_running.assert_called_once_with()


def test_live_but_unready_runtime_still_reaches_deadline(health_server):
    host, _, requests = health_server
    with pytest.raises(RuntimeError, match="failed to become healthy"):
        wait_for_server_health(host, timeout=0.05, check_running=lambda: None)
    assert requests == ["/health"]
