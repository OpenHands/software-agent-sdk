"""Conversation-scoped pending decisions; no model or execution required."""

import asyncio
from uuid import uuid4

from acp.schema import PermissionOption, ToolCallUpdate
from pydantic import SecretStr

from openhands.agent_server.acp_permissions import (
    ACPPermissionBroker,
    ACPPermissionDecision,
)
from openhands.sdk.agent.acp_permissions import ACPPermissionRequest


def request(details="safe"):
    return ACPPermissionRequest(
        session_id="session",
        tool_call=ToolCallUpdate(
            tool_call_id="tool",
            title="secret title",
            status="pending",
            raw_input={"command": details},
        ),
        options=(
            PermissionOption(option_id="native-allow", name="Allow", kind="allow_once"),
            PermissionOption(
                option_id="native-deny", name="Reject", kind="reject_once"
            ),
        ),
    )


def test_decisions_are_scoped_one_shot_masked_and_not_implicitly_granted():
    async def scenario():
        broker = ACPPermissionBroker(
            1, lambda text: text.replace("secret", "[redacted]")
        )
        other = ACPPermissionBroker(1, str)
        task = asyncio.create_task(broker.request(request("secret")))
        await asyncio.sleep(0)
        view = broker.snapshot().items[0]
        assert "secret" not in view.model_dump_json()
        assert not task.done()
        assert not other.decide(
            view.request_id, ACPPermissionDecision(option_id=view.options[0].id)
        )
        try:
            broker.decide(view.request_id, ACPPermissionDecision(option_id=uuid4()))
        except ValueError:
            pass
        else:
            raise AssertionError("Unknown option accepted")
        assert broker.decide(
            view.request_id, ACPPermissionDecision(option_id=view.options[1].id)
        )
        assert await task == "native-deny"
        assert not broker.decide(
            view.request_id, ACPPermissionDecision(option_id=view.options[0].id)
        )
        assert not broker.snapshot().items

    asyncio.run(scenario())


def test_truncation_expiry_and_close_fail_closed():
    async def scenario():
        broker = ACPPermissionBroker(1, str)
        task = asyncio.create_task(broker.request(request("x" * 17000)))
        await asyncio.sleep(0)
        view = broker.snapshot().items[0]
        assert view.details_truncated
        try:
            broker.decide(
                view.request_id, ACPPermissionDecision(option_id=view.options[0].id)
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Truncated operation approved")
        broker.cancel_pending(close=True)
        assert await task is None
        assert await broker.request(request()) is None
        broker = ACPPermissionBroker(0.01, str)
        task = asyncio.create_task(broker.request(request()))
        await asyncio.sleep(0.02)
        assert not broker.snapshot().items
        assert await task is None

    asyncio.run(scenario())


def test_cancellation_removes_request_and_explicit_allow_maps_native_id():
    async def scenario():
        broker = ACPPermissionBroker(1, str)
        task = asyncio.create_task(broker.request(request()))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert not broker.snapshot().items
        task = asyncio.create_task(broker.request(request()))
        await asyncio.sleep(0)
        view = broker.snapshot().items[0]
        assert broker.decide(
            view.request_id, ACPPermissionDecision(option_id=view.options[0].id)
        )
        assert await task == "native-allow"

    asyncio.run(scenario())


def test_conversation_scope_configuration_roundtrips_and_rejects_mismatch():
    import tempfile
    from pathlib import Path

    from openhands.agent_server.config import Config, ConversationScope

    identifier = uuid4()
    workspace = Path(tempfile.gettempdir()).resolve() / "admitted-workspace"
    settings = Config(
        workspace_path=workspace,
        conversation_scope=ConversationScope(id=identifier, workspace_path=workspace),
    )
    restored = Config.model_validate_json(settings.model_dump_json())
    assert restored.conversation_scope is not None
    assert restored.conversation_scope.id == identifier
    assert restored.conversation_scope.workspace_path == workspace
    for values in (
        {
            "workspace_path": workspace.parent / "other",
            "conversation_scope": {
                "id": str(identifier),
                "workspace_path": workspace,
            },
        },
        {
            "workspace_path": workspace,
            "conversation_scope": {"id": str(identifier), "workspace_path": "relative"},
        },
    ):
        try:
            Config(**values)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid conversation scope accepted")


def test_client_api_scope_denies_unknown_routes_and_outside_paths():
    import tempfile
    from pathlib import Path

    from openhands.agent_server.api_access import (
        APIAccessRule,
        ClientAPIAccessMiddleware,
        ClientAPIPolicy,
    )

    async def scenario(root):
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "outside-link").symlink_to(root / "private")

        async def application(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"allowed"})

        policy = ClientAPIPolicy(
            operator_api_key=SecretStr("operator-" + "x" * 40),
            http_rules=[
                APIAccessRule(
                    method="GET",
                    path=r"/api/file/download",
                    workspace_query_parameters=["path"],
                )
            ],
        )
        wrapped = ClientAPIAccessMiddleware(
            application, policy=policy, workspace_path=workspace
        )
        from urllib.parse import urlencode

        async def receive():
            return {"type": "http.disconnect"}

        for path, values, expected in (
            ("/api/file/download", {"path": str(workspace / "ok.txt")}, 200),
            ("/api/file/download", {"path": str(root / "private")}, 403),
            ("/api/file/download", {"path": str(workspace / "outside-link")}, 403),
            ("/api/file/download", {"path": "relative"}, 403),
            ("/api/file/download", {}, 403),
            (
                "/api/file/download",
                [("path", str(workspace)), ("path", str(root))],
                403,
            ),
            ("/api/bash/execute_bash_command", {}, 403),
            ("/api/acp/models", {}, 403),
        ):
            messages = []

            async def send(value):
                messages.append(value)

            await wrapped(
                {
                    "type": "http",
                    "method": "GET",
                    "path": path,
                    "headers": [],
                    "query_string": urlencode(values).encode(),
                },
                receive,
                send,
            )
            assert messages[0]["status"] == expected, (path, values, messages)
        messages = []

        async def send(value):
            messages.append(value)

        await wrapped(
            {"type": "websocket", "path": "/sockets/bash", "headers": []}, receive, send
        )
        assert messages == [{"type": "websocket.close", "code": 1008}]
        assert "operator-" not in policy.model_dump_json()

    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(scenario(Path(directory)))


def test_client_api_scope_requires_separate_authenticated_operator():
    from openhands.agent_server.config import Config

    for keys, operator in (([], "a" * 40), (["a" * 40], "a" * 40)):
        try:
            Config.model_validate(
                {
                    "session_api_keys": keys,
                    "client_api_policy": {"operator_api_key": operator},
                }
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Anonymous or shared operator credential accepted")
