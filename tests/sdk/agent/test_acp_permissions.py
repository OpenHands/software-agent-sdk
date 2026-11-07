"""Behavioral coverage for the public ACP permission extension.

The peer only requests permission: no LLM, auth or proposed command is used.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from acp.schema import PermissionOption, ToolCallUpdate

from openhands.sdk.agent import ACPAgent
from openhands.sdk.agent.acp_permissions import resolve_acp_permission
from openhands.sdk.conversation import Conversation
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.settings import ACPAgentSettings


PEER = Path(__file__).with_name("acp_permission_peer.py")


def run_permission_case(
    mode,
    handler=None,
    *,
    restore=False,
    settings=False,
    timeout=1.0,
    reinstall=False,
    partial=False,
    cross_session=False,
):
    with tempfile.TemporaryDirectory(prefix="acp-permission-") as directory:
        workspace = Path(directory)
        transcript = workspace / "peer.json"
        command = [sys.executable, str(PEER), str(transcript)]
        if partial:
            command.append("--partial-permission")
        if cross_session:
            command.append("--cross-session-update")
        if settings:
            config = ACPAgentSettings(
                acp_server="custom",
                acp_command=command,
                acp_permission_mode=mode,
                acp_permission_timeout=timeout,
            )
            # Exercise the real persisted settings -> factory path.
            config = ACPAgentSettings.model_validate_json(config.model_dump_json())
            agent = config.create_agent(acp_permission_handler=handler)
        else:
            agent = ACPAgent(
                acp_command=command,
                acp_permission_mode=mode,
                acp_permission_handler=handler,
                acp_permission_timeout=timeout,
            )
        if restore:
            agent = ACPAgent.model_validate_json(agent.model_dump_json())
        if reinstall:
            agent.set_acp_permission_handler(handler)
        conversation = None
        try:
            conversation = Conversation(
                agent=agent,
                workspace=str(workspace),
                persistence_dir=str(workspace / "conversation"),
                visualizer=None,
            )
            conversation.set_confirmation_policy(AlwaysConfirm())
            conversation.send_message("Request simulated permission and finish.")
            conversation.run()
            record = json.loads(transcript.read_text(encoding="utf-8"))
            assert record["mode_requests"] == (
                ["agent-full-access"] if mode == "legacy-auto-approve" else []
            )
            return record["response"]["outcome"]
        finally:
            if conversation is not None:
                conversation.close()
            agent.close()


def test_deny_and_missing_handler_cancel_without_approval():
    assert run_permission_case("deny") == {"outcome": "cancelled"}
    assert run_permission_case("callback") == {"outcome": "cancelled"}


def test_callback_selects_deny_instead_of_first_option_through_settings():
    async def reject(request):
        assert request.session_id.startswith("permission-test-")
        assert request.tool_call.tool_call_id == "simulated"
        return next(
            option.option_id
            for option in request.options
            if option.kind == "reject_once"
        )

    assert run_permission_case("callback", reject, settings=True) == {
        "outcome": "selected",
        "optionId": "deny-id",
    }


def test_callback_explicitly_selects_offered_approval():
    async def approve(request):
        return request.options[0].option_id

    assert run_permission_case("callback", approve) == {
        "outcome": "selected",
        "optionId": "allow-id",
    }


def test_invalid_choice_and_callback_failure_fail_closed():
    async def invalid(request):
        return "not-offered"

    async def failed(request):
        raise RuntimeError("PERMISSION_EXCEPTION_SENTINEL_DO_NOT_LOG")

    assert run_permission_case("callback", invalid) == {"outcome": "cancelled"}
    assert run_permission_case("callback", failed) == {"outcome": "cancelled"}


def test_timed_out_handler_is_cancelled_before_response():
    stopped = []

    async def delayed(request):
        try:
            await asyncio.sleep(20)
        finally:
            stopped.append(True)

    assert run_permission_case("callback", delayed, timeout=0.01) == {
        "outcome": "cancelled"
    }
    assert stopped == [True]


def test_serialized_callback_mode_does_not_restore_automatic_approval():
    async def approve(request):
        return request.options[0].option_id

    assert run_permission_case("callback", approve, restore=True) == {
        "outcome": "cancelled"
    }


def test_explicit_legacy_mode_retains_existing_behavior():
    assert run_permission_case("legacy-auto-approve") == {
        "outcome": "selected",
        "optionId": "allow-id",
    }


def test_public_handler_installation_restores_control_after_serialization():
    async def reject(request):
        return request.options[1].option_id

    assert run_permission_case("callback", reject, restore=True, reinstall=True) == {
        "outcome": "selected",
        "optionId": "deny-id",
    }


def test_controlled_mode_rejects_bypass_and_invalid_handler_configuration():
    cases = [
        {"acp_permission_mode": "callback", "acp_session_mode": "agent-full-access"},
        {"acp_permission_mode": "deny", "acp_session_mode": "bypassPermissions"},
        {
            "acp_permission_mode": "callback",
            "acp_permission_handler": lambda request: None,
        },
        {"acp_permission_mode": "callback", "acp_permission_timeout": float("inf")},
        {"acp_permission_mode": "callback", "acp_permission_timeout": 0},
    ]
    for values in cases:
        try:
            ACPAgent(acp_command=["fixture"], **values)
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe permission configuration accepted")


def test_cancelled_handler_propagates_without_permission_grant():
    async def cancelled(request):
        raise asyncio.CancelledError

    async def exercise():
        try:
            await resolve_acp_permission(
                mode="callback",
                handler=cancelled,
                timeout=1,
                session_id="session",
                tool_call=ToolCallUpdate(tool_call_id="simulated"),
                options=[
                    PermissionOption(option_id="allow", name="Allow", kind="allow_once")
                ],
            )
        except asyncio.CancelledError:
            return
        raise AssertionError("Handler cancellation was swallowed")

    asyncio.run(exercise())


def test_ambiguous_options_do_not_invoke_handler():
    async def unexpected(request):
        raise AssertionError("Ambiguous request reached handler")

    async def exercise():
        option = PermissionOption(option_id="same", name="Allow", kind="allow_once")
        result = await resolve_acp_permission(
            mode="callback",
            handler=unexpected,
            timeout=1,
            session_id="session",
            tool_call=ToolCallUpdate(tool_call_id="simulated"),
            options=[option, option],
        )
        assert result.outcome.outcome == "cancelled"

    asyncio.run(exercise())


def test_partial_permission_uses_same_session_tool_notification():
    async def approve(request):
        assert request.tool_call.title == "Simulated operation"
        assert request.tool_call.raw_input == {"command": "simulated-approved"}
        return "allow-id"

    assert run_permission_case("callback", approve, partial=True) == {
        "outcome": "selected",
        "optionId": "allow-id",
    }


def test_partial_permission_does_not_borrow_other_session_metadata():
    async def approve_complete_only(request):
        assert request.tool_call.title is None and request.tool_call.raw_input is None
        return None

    assert run_permission_case(
        "callback", approve_complete_only, partial=True, cross_session=True
    ) == {"outcome": "cancelled"}
