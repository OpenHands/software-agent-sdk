from __future__ import annotations

import asyncio
import json
import logging
import socket
import tempfile
import threading
import time
from typing import Any

import mcp.types as mcp_types
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_context
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.llm import TextContent
from openhands.sdk.mcp.tool import MCPToolExecutor


RPC_TRAFFIC: list[dict[str, Any]] = []


def record_rpc(direction: str, payload: object) -> None:
    messages = payload if isinstance(payload, list) else [payload]
    for message in messages:
        if not isinstance(message, dict):
            continue
        record = {
            "direction": direction,
            "jsonrpc": message.get("jsonrpc"),
            "id": message.get("id"),
            "method": message.get("method"),
        }
        RPC_TRAFFIC.append(record)
        print("JSONRPC " + json.dumps(record, sort_keys=True), flush=True)


class JsonRpcTrafficMiddleware:
    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_body = bytearray()
        request_recorded = False

        async def receive_with_traffic() -> dict:
            nonlocal request_recorded
            message = await receive()
            if message.get("type") == "http.request":
                request_body.extend(message.get("body", b""))
                if not message.get("more_body", False) and not request_recorded:
                    request_recorded = True
                    if request_body:
                        try:
                            record_rpc(
                                "client->server",
                                json.loads(request_body.decode()),
                            )
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            pass
            return message

        async def send_with_traffic(message: dict) -> None:
            body = message.get("body", b"")
            if (
                message.get("type") == "http.response.body"
                and b"notifications/tools/list_changed" in body
            ):
                record_rpc(
                    "server->client",
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/tools/list_changed",
                    },
                )
            await send(message)

        await self.app(scope, receive_with_traffic, send_with_traffic)


class RegistrationLogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "dynamically advertised MCP tools" in message:
            self.messages.append(message)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def observation_text(observation: Any) -> str:
    return " | ".join(
        block.text for block in observation.content if isinstance(block, TextContent)
    )


def wait_until(predicate: Any, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return bool(predicate())


def main() -> None:
    mcp = FastMCP("pr3894-progressive-live")

    async def notify_list_changed() -> None:
        notification = mcp_types.ToolListChangedNotification()
        context = get_context()
        asyncio.get_running_loop().create_task(context.send_notification(notification))

    @mcp.tool()
    async def gateway() -> str:
        """Always-available gateway tool."""
        return "gateway-ok"

    @mcp.tool()
    async def register_extra_tool() -> str:
        """Add an extra tool and notify the client."""

        @mcp.tool()
        def extra(value: int) -> int:
            """Double an integer after progressive disclosure."""
            return value * 2

        await notify_list_changed()
        return "registered"

    @mcp.tool()
    async def repeat_list_changed() -> str:
        """Send a duplicate notification without changing the tool list."""
        await notify_list_changed()
        return "repeated"

    @mcp.tool()
    async def remove_extra_tool() -> str:
        """Remove the progressively disclosed tool and notify the client."""
        mcp.local_provider.remove_tool("extra")
        await notify_list_changed()
        return "removed"

    port = free_port()
    app = JsonRpcTrafficMiddleware(mcp.http_app(path="/mcp", transport="http"))
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    if not wait_until(lambda: server.started, timeout=15.0):
        raise RuntimeError("progressive MCP server did not start")

    capture = RegistrationLogCapture()
    logger = logging.getLogger("openhands.sdk.agent.base")
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)

    result: dict[str, Any] = {}
    try:
        config = {
            "progressive": {
                "transport": "http",
                "url": f"http://127.0.0.1:{port}/mcp",
            }
        }
        agent = Agent(
            llm=LLM(model="test/model", api_key=SecretStr("sanitized-test-key")),
            tools=[],
            mcp_config=config,
        )
        with tempfile.TemporaryDirectory(prefix="pr3894-live-") as workspace:
            conversation = Conversation(
                agent=agent,
                workspace=workspace,
                visualizer=None,
            )
            try:
                conversation._ensure_agent_ready()
                initial_names = sorted(conversation.agent.tools_map)
                register = conversation.agent.tools_map["register_extra_tool"]
                register_result = register(register.action_from_arguments({}))
                appeared = wait_until(
                    lambda: "extra" in conversation.agent.tools_map,
                    timeout=8.0,
                )
                after_register_names = sorted(conversation.agent.tools_map)

                result.update(
                    {
                        "initial_agent_tools": initial_names,
                        "register_result": observation_text(register_result),
                        "extra_appeared": appeared,
                        "after_register_agent_tools": after_register_names,
                        "registration_logs": list(capture.messages),
                    }
                )

                if appeared:
                    extra = conversation.agent.tools_map["extra"]
                    extra_executor = extra.executor
                    assert isinstance(extra_executor, MCPToolExecutor)
                    client = extra_executor.client

                    extra_result = extra(extra.action_from_arguments({"value": 21}))
                    repeat = conversation.agent.tools_map["repeat_list_changed"]
                    repeat_result = repeat(repeat.action_from_arguments({}))
                    time.sleep(1.0)
                    logs_after_duplicate = list(capture.messages)

                    remove = conversation.agent.tools_map["remove_extra_tool"]
                    remove_result = remove(remove.action_from_arguments({}))
                    removed_from_client = wait_until(
                        lambda: "extra" not in {tool.name for tool in client.tools},
                        timeout=8.0,
                    )
                    stale_result = extra(extra.action_from_arguments({"value": 7}))

                    result.update(
                        {
                            "extra_result": observation_text(extra_result),
                            "extra_is_error": extra_result.is_error,
                            "duplicate_result": observation_text(repeat_result),
                            "registration_logs_after_duplicate": logs_after_duplicate,
                            "removed_result": observation_text(remove_result),
                            "removed_from_client": removed_from_client,
                            "client_tools_after_removal": sorted(
                                tool.name for tool in client.tools
                            ),
                            "agent_snapshot_retains_removed_definition": (
                                "extra" in conversation.agent.tools_map
                            ),
                            "stale_removed_invocation_is_error": stale_result.is_error,
                            "stale_removed_invocation": observation_text(stale_result),
                        }
                    )
            finally:
                conversation.close()
    finally:
        logger.removeHandler(capture)
        server.should_exit = True
        thread.join(timeout=15)

    result["jsonrpc_traffic"] = RPC_TRAFFIC
    result["server_stopped"] = not thread.is_alive()
    print("RESULT " + json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
