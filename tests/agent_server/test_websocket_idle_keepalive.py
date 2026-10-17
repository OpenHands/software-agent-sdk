"""Regression tests for WebSocket keep-alive of the runtime idle timer.

A connected-but-silent WebSocket used to let ``_last_event_time`` go stale, so
``idle_time`` climbed 1:1 while a user merely watched a conversation.  The
runtime reaps a pod once ``idle_time`` crosses ``OH_RUNTIME_IDLE_TIMEOUT_SECONDS``
(default 20 min), which drops *every* socket on that pod at once -- the
mass-disconnect symptom this fix targets.

The contract asserted here is the one the runtime consumes: while a socket is
attached, ``idle_time`` (derived from ``_last_event_time``) must not grow.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import openhands.agent_server.server_details_router as sdr
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config


@pytest.fixture(autouse=True)
def reset_keepalive_state():
    """Keep the module-level socket counter from leaking between tests."""
    with patch.object(sdr, "_active_ws_connections", 0):
        yield


@pytest.fixture(autouse=True)
def isolate_tmux():
    """Never touch the host's shared tmux socket from a test.

    ``api_lifespan`` calls ``_cleanup_stale_tmux_sessions()``, which kills every
    session on the shared ``openhands`` socket. Under a test runner that itself
    lives in a tmux session this would terminate the runner.
    """
    from openhands.agent_server import api as api_mod

    with patch.object(api_mod, "_cleanup_stale_tmux_sessions"):
        yield


@pytest.fixture
def client():
    """App with auth disabled so a socket is accepted on connect."""
    app = create_app(Config(static_files_path=None, session_api_keys=[]))
    return TestClient(app)


# -- tracker unit behaviour --


def test_register_refreshes_idle_timer():
    with patch.object(sdr, "_last_event_time", 0.0):
        sdr.register_websocket_connection()
        assert sdr._active_ws_connections == 1
        assert sdr._last_event_time > 0.0


def test_keepalive_tick_refreshes_while_attached():
    sdr.register_websocket_connection()
    with patch.object(sdr, "_last_event_time", 0.0):
        sdr.refresh_websocket_keepalive()
        assert sdr._last_event_time > 0.0


def test_keepalive_tick_is_inert_with_no_socket():
    with patch.object(sdr, "_last_event_time", 0.0):
        sdr.refresh_websocket_keepalive()
        assert sdr._last_event_time == 0.0


def test_unregister_stops_refresh_and_never_goes_negative():
    sdr.register_websocket_connection()
    sdr.unregister_websocket_connection()
    assert sdr._active_ws_connections == 0
    with patch.object(sdr, "_last_event_time", 0.0):
        sdr.refresh_websocket_keepalive()
        assert sdr._last_event_time == 0.0
    # A double close must not underflow the counter.
    sdr.unregister_websocket_connection()
    assert sdr._active_ws_connections == 0


def test_signal_activity_only_counts_when_attached():
    with patch.object(sdr, "_last_event_time", 0.0):
        sdr.signal_websocket_activity()
        assert sdr._last_event_time == 0.0
        sdr.register_websocket_connection()
        with patch.object(sdr, "_last_event_time", 0.0):
            sdr.signal_websocket_activity()
            assert sdr._last_event_time > 0.0


# -- end-to-end through the real bash-events WebSocket --


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    """Poll until ``predicate`` holds; the handler runs on another thread."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_attached_socket_holds_idle_time_down(client):
    """A silent, connected socket must keep ``idle_time`` near zero."""
    with client.websocket_connect("/sockets/bash-events") as ws:
        assert _wait_for(lambda: sdr._active_ws_connections == 1)
        # Force the timer far into the past; a refresh must pull it back.
        sdr._last_event_time = time.time() - 3600
        sdr.refresh_websocket_keepalive()
        idle = client.get("/server_info").json()["idle_time"]
        assert idle < 5, f"idle_time grew while a socket was attached: {idle}"
        ws.send_json({"type": "auth", "session_api_key": "ignored"})
    # After the socket closes, the counter is released and idle can grow again.
    assert _wait_for(lambda: sdr._active_ws_connections == 0)


def test_socket_close_releases_the_runtime_claim(client):
    """Once no socket is attached, the timer must be allowed to go stale."""
    with client.websocket_connect("/sockets/bash-events"):
        assert _wait_for(lambda: sdr._active_ws_connections == 1)
    assert _wait_for(lambda: sdr._active_ws_connections == 0)
    with patch.object(sdr, "_last_event_time", 0.0):
        sdr.refresh_websocket_keepalive()
        assert sdr._last_event_time == 0.0


def test_counter_released_when_conversation_is_missing(client):
    """A rejected socket must not leak a permanent claim on the timer.

    Driven against the endpoint directly: the missing-conversation branch closes
    before accepting, which the TestClient surfaces as an opaque stream error.
    """
    from uuid import uuid4

    from openhands.agent_server.sockets import events_socket

    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.receive_text = AsyncMock(return_value="{}")

    with patch.object(sdr, "_last_event_time", time.time() - 3600):
        asyncio.run(events_socket(websocket=ws, conversation_id=uuid4()))

    assert sdr._active_ws_connections == 0
    ws.close.assert_awaited()


# -- the background loop actually ticks --


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_the_keepalive_loop():
    """The loop must refresh an attached socket and exit cleanly on shutdown."""
    from openhands.agent_server import api as api_mod

    ticks = 0

    def counting_refresh():
        nonlocal ticks
        ticks += 1

    fake_service = MagicMock()
    fake_service.__aenter__ = AsyncMock(return_value=fake_service)
    fake_service.__aexit__ = AsyncMock(return_value=None)

    app = create_app(Config(static_files_path=None))
    with (
        patch.object(
            api_mod, "get_default_conversation_service", return_value=fake_service
        ),
        patch.object(
            api_mod, "websocket_keepalive_interval_seconds", return_value=0.01
        ),
        patch.object(api_mod, "refresh_websocket_keepalive", counting_refresh),
        patch.object(api_mod, "mark_initialization_complete"),
    ):
        async with api_mod.api_lifespan(app):
            import asyncio

            await asyncio.sleep(0.05)

    assert ticks >= 1, "keep-alive loop never ticked"


def test_keepalive_interval_is_below_the_runtime_idle_timeout():
    """The tick must be frequent enough to never let the timer lapse."""
    assert sdr.websocket_keepalive_interval_seconds() > 0
    assert sdr.websocket_keepalive_interval_seconds() <= 60.0
