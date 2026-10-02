"""Tests for in-process resolution of this server's own LookupSecret URLs."""

from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from openhands.agent_server.config import Config
from openhands.agent_server.local_secret_resolver import (
    _secret_name_if_local,
    build_local_secret_resolver,
)
from openhands.agent_server.persistence import get_secrets_store
from openhands.sdk.secret import (
    LookupSecret,
    register_local_secret_resolver,
    unregister_local_secret_resolver,
)


class _ForeignSecretHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"foreign-value")

    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def _foreign_secret_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ForeignSecretHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = server.server_address[0]
        port = server.server_address[1]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_matches_loopback_secret_url(monkeypatch):
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://127.0.0.1:8000")
    assert (
        _secret_name_if_local("http://127.0.0.1:8000/api/settings/secrets/TOKEN")
        == "TOKEN"
    )
    assert (
        _secret_name_if_local("http://localhost:8000/api/settings/secrets/TOKEN")
        == "TOKEN"
    )


def test_ignores_remote_host(monkeypatch):
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://127.0.0.1:8000")
    assert (
        _secret_name_if_local("https://remote.example/api/settings/secrets/T") is None
    )


def test_ignores_other_routes_and_malformed_names(monkeypatch):
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://127.0.0.1:8000")
    assert _secret_name_if_local("http://127.0.0.1:8000/api/settings") is None
    assert _secret_name_if_local("http://127.0.0.1:8000/api/settings/secrets/") is None
    assert (
        _secret_name_if_local("http://127.0.0.1:8000/api/settings/secrets/a/b") is None
    )


def test_foreign_loopback_port_uses_its_own_server(monkeypatch):
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://127.0.0.1:18000")
    config = Config()
    get_secrets_store(config).set_secret("TOKEN", "local-value")
    resolver = build_local_secret_resolver(config)
    register_local_secret_resolver(resolver)
    try:
        with _foreign_secret_server() as base_url:
            secret = LookupSecret(url=f"{base_url}/api/settings/secrets/TOKEN")
            assert secret.get_value() == "foreign-value"
    finally:
        unregister_local_secret_resolver(resolver)
