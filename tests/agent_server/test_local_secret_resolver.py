"""Tests for in-process resolution of this server's own LookupSecret URLs."""

from openhands.agent_server.local_secret_resolver import _secret_name_if_local


def test_matches_loopback_secret_url():
    assert (
        _secret_name_if_local("http://127.0.0.1:8000/api/settings/secrets/TOKEN")
        == "TOKEN"
    )
    assert (
        _secret_name_if_local("http://localhost:18000/api/settings/secrets/TOKEN")
        == "TOKEN"
    )


def test_ignores_remote_host():
    assert (
        _secret_name_if_local("https://remote.example/api/settings/secrets/T") is None
    )


def test_ignores_other_routes_and_malformed_names():
    assert _secret_name_if_local("http://127.0.0.1:8000/api/settings") is None
    assert _secret_name_if_local("http://127.0.0.1:8000/api/settings/secrets/") is None
    assert (
        _secret_name_if_local("http://127.0.0.1:8000/api/settings/secrets/a/b") is None
    )
