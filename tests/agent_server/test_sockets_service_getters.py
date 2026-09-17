"""Tests for the per-app service getters in sockets.py.

The websocket handlers must read ``ConversationService`` and
``BashEventService`` from ``app.state`` (set by the lifespan or by
``POST /api/init`` in deferred-init mode) rather than the module-level
singletons captured at import time. These helpers encapsulate that lookup
and gracefully fall back to the module-level default when ``app.state``
is not configured (e.g. when sockets.py is imported as a library without
a lifespan).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect

import openhands.agent_server.sockets as sockets_mod
from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.sockets import (
    _get_bash_event_service,
    _get_conversation_service,
)


def _make_ws(state: dict[str, object] | None = None) -> MagicMock:
    """Build a mock WebSocket whose ``app.state`` exposes ``state``."""
    ws = MagicMock()
    ws.app.state = SimpleNamespace()
    if state is not None:
        ws.app.state = SimpleNamespace(**state)
    return ws


@pytest.fixture
def conv_service():
    return MagicMock(spec=ConversationService)


@pytest.fixture
def bash_service():
    return MagicMock(spec=BashEventService)


# -- _get_conversation_service --


def test_get_conversation_service_prefers_app_state(conv_service):
    """When the app registers a service, that one is returned (not the
    module-level singleton)."""
    ws = _make_ws({"conversation_service": conv_service})
    assert _get_conversation_service(ws) is conv_service


def test_get_conversation_service_falls_back_to_module_singleton():
    """When ``app.state`` has no conversation service, the module-level
    default is returned (preserves the behaviour of tests that patch the
    module-level singleton)."""
    ws = _make_ws()
    assert _get_conversation_service(ws) is sockets_mod.conversation_service


def test_get_conversation_service_ignores_wrong_type():
    """If ``app.state.conversation_service`` is set to something that is not
    a ConversationService (e.g. None or some other object), fall back to the
    module-level default rather than blow up."""
    for bogus in (None, "not a service", 42):
        ws = _make_ws({"conversation_service": bogus})
        # Must not raise — just return the module-level default.
        result = _get_conversation_service(ws)
        assert isinstance(result, ConversationService)


# -- _get_bash_event_service --


def test_get_bash_event_service_prefers_app_state(bash_service):
    ws = _make_ws({"bash_event_service": bash_service})
    assert _get_bash_event_service(ws) is bash_service


def test_get_bash_event_service_falls_back_to_module_singleton():
    ws = _make_ws()
    assert _get_bash_event_service(ws) is sockets_mod.bash_event_service


def test_get_bash_event_service_ignores_wrong_type():
    for bogus in (None, "not a service", 42):
        ws = _make_ws({"bash_event_service": bogus})
        result = _get_bash_event_service(ws)
        assert isinstance(result, BashEventService)


# -- Integration with Config --


def test_get_conversation_service_handles_real_config(tmp_path):
    """Sanity check: the helper returns a real ConversationService built
    from a Config on ``app.state`` rather than the import-time default."""
    from openhands.agent_server import conversation_service as cs_mod

    original_default = sockets_mod.conversation_service
    cs_mod._conversation_service = None
    try:
        new_service = ConversationService(
            conversations_dir=tmp_path / "convs",
        )
        cfg = Config(
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        ws = _make_ws({"conversation_service": new_service, "config": cfg})
        assert _get_conversation_service(ws) is new_service
        assert _get_conversation_service(ws) is not original_default
    finally:
        cs_mod._conversation_service = None


def test_get_bash_event_service_handles_real_config(tmp_path):
    """Sanity check: the helper returns a real BashEventService built
    from a Config on ``app.state`` rather than the import-time default."""
    from openhands.agent_server import bash_service as bash_mod

    original_default = sockets_mod.bash_event_service
    bash_mod._bash_event_service = None
    try:
        new_service = BashEventService(bash_events_dir=tmp_path / "user" / "bash")
        cfg = Config(
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "user" / "bash",
        )
        ws = _make_ws({"bash_event_service": new_service, "config": cfg})
        assert _get_bash_event_service(ws) is new_service
        assert _get_bash_event_service(ws) is not original_default
    finally:
        bash_mod._bash_event_service = None


# -- End-to-end: WebSocket handlers use per-app services after /api/init --


@pytest.mark.asyncio
async def test_events_socket_uses_app_state_conversation_service():
    """events_socket must use app.state.conversation_service (set by /api/init)
    rather than the module-level singleton captured at import time."""
    from openhands.agent_server.sockets import events_socket

    mock_event_svc = MagicMock(spec=EventService)
    mock_event_svc.subscribe_to_events = AsyncMock(return_value=uuid4())
    mock_event_svc.unsubscribe_from_events = AsyncMock(return_value=True)

    per_app_conv_svc = MagicMock(spec=ConversationService)
    per_app_conv_svc.get_event_service = AsyncMock(return_value=mock_event_svc)

    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.receive_text = AsyncMock()
    ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.headers = {}
    # Simulate app.state after /api/init delivers a per-user service.
    ws.app.state = SimpleNamespace(
        conversation_service=per_app_conv_svc,
        config=Config(session_api_keys=[]),
    )

    await events_socket(uuid4(), ws, session_api_key=None)

    per_app_conv_svc.get_event_service.assert_called_once()
    mock_event_svc.subscribe_to_events.assert_called_once()
    mock_event_svc.unsubscribe_from_events.assert_called_once()


@pytest.mark.asyncio
async def test_bash_events_socket_uses_app_state_bash_event_service():
    """bash_events_socket must use app.state.bash_event_service (set by /api/init)
    rather than the module-level singleton captured at import time."""
    from openhands.agent_server.sockets import bash_events_socket

    per_app_bash_svc = MagicMock(spec=BashEventService)
    per_app_bash_svc.subscribe_to_events = AsyncMock(return_value=uuid4())
    per_app_bash_svc.unsubscribe_from_events = AsyncMock(return_value=True)

    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.receive_text = AsyncMock()
    ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.headers = {}
    # Simulate app.state after /api/init delivers a per-user service.
    ws.app.state = SimpleNamespace(
        bash_event_service=per_app_bash_svc,
        config=Config(session_api_keys=[]),
    )

    await bash_events_socket(ws, session_api_key=None)

    per_app_bash_svc.subscribe_to_events.assert_called_once()
    per_app_bash_svc.unsubscribe_from_events.assert_called_once()
