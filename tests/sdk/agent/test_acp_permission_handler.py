"""Custom permission decisions preserve defaults and propagate failures."""

from typing import cast

import pytest
from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse

from openhands.sdk.agent.acp_agent import (
    ACPAgent,
    ACPPermissionHandler,
    _OpenHandsACPBridge,
)


async def test_default_preserves_existing_approval(permission_request):
    result = await _OpenHandsACPBridge().request_permission(*permission_request)
    assert result.outcome == AllowedOutcome(outcome="selected", option_id="once")


async def test_async_handler_receives_request_and_can_cancel(permission_request):
    async def decide(session_id, tool_call, options):
        assert (session_id, tool_call, options) == permission_request
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    bridge = _OpenHandsACPBridge()
    bridge.permission_handler = decide
    result = await bridge.request_permission(*permission_request)
    assert result.outcome == DeniedOutcome(outcome="cancelled")


async def test_sync_handler_can_select_an_offered_option(permission_request):
    bridge = _OpenHandsACPBridge()
    bridge.permission_handler = lambda *_: RequestPermissionResponse(
        outcome=AllowedOutcome(outcome="selected", option_id="once")
    )
    result = await bridge.request_permission(*permission_request)
    assert result.outcome == AllowedOutcome(outcome="selected", option_id="once")


async def test_callback_failure_does_not_fall_back_to_approval(permission_request):
    async def decide(*_):
        raise RuntimeError("policy unavailable")

    bridge = _OpenHandsACPBridge()
    bridge.permission_handler = decide
    with pytest.raises(RuntimeError, match="policy unavailable"):
        await bridge.request_permission(*permission_request)


async def test_invalid_return_and_unoffered_option_are_rejected(permission_request):
    bridge = _OpenHandsACPBridge()
    bridge.permission_handler = cast(ACPPermissionHandler, lambda *_: True)
    with pytest.raises(TypeError):
        await bridge.request_permission(*permission_request)
    bridge.permission_handler = lambda *_: RequestPermissionResponse(
        outcome=AllowedOutcome(outcome="selected", option_id="invented")
    )
    with pytest.raises(ValueError):
        await bridge.request_permission(*permission_request)


async def test_setter_updates_active_requests_and_removal_restores_default(
    permission_request,
):
    agent = ACPAgent(acp_command=["fixture"])
    bridge = _OpenHandsACPBridge()
    agent._client = bridge

    def decide(*_):
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    original = agent.model_dump()
    agent.set_permission_handler(decide)
    result = await bridge.request_permission(*permission_request)
    assert result.outcome == DeniedOutcome(outcome="cancelled")
    assert agent.model_dump() == original
    agent.set_permission_handler(None)
    result = await bridge.request_permission(*permission_request)
    assert result.outcome == AllowedOutcome(outcome="selected", option_id="once")
