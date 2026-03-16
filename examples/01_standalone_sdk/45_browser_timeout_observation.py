"""Demonstrate browser timeouts surfacing as normal observations.

This example starts a local web service with a slow endpoint and registers a
minimal browser-style executor that performs a live HTTP request to that
service. A deterministic in-process LLM asks the agent to call
`browser_navigate` and then finish, so the example stays fully self-contained
and can be run without external model credentials.
"""

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock
from urllib.request import urlopen

from litellm.types.utils import ModelResponse

from openhands.sdk import Agent, Conversation, Event, LLMConvertibleEvent
from openhands.sdk.llm import LLM, LLMResponse, Message, MessageToolCall, TextContent
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage
from openhands.sdk.tool import Tool, register_tool
from openhands.sdk.utils.async_executor import AsyncExecutor
from openhands.tools.browser_use.definition import BrowserNavigateTool
from openhands.tools.browser_use.impl import BrowserToolExecutor


if TYPE_CHECKING:
    from collections.abc import Iterator


ACTION_TIMEOUT_SECONDS = 2
SLOW_RESPONSE_SECONDS = 8


class _ThreadedSlowServer(ThreadingHTTPServer):
    daemon_threads = True


class SlowHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        time.sleep(SLOW_RESPONSE_SECONDS)
        body = b"slow response"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A003
        _ = (format, args)
        return


class SlowServiceBrowserExecutor(BrowserToolExecutor):
    """A lightweight browser executor for timeout observation demos."""

    def __init__(self, action_timeout_seconds: float):
        self._server = cast(Any, SimpleNamespace(_is_recording=False))
        self._config = {}
        self._initialized = True
        self._async_executor = AsyncExecutor()
        self._cleanup_initiated = False
        self._action_timeout_seconds = action_timeout_seconds
        self.full_output_save_dir = None

    async def navigate(self, url: str, new_tab: bool = False) -> str:
        del new_tab
        return await asyncio.to_thread(self._fetch_url, url)

    def close(self) -> None:
        return

    @staticmethod
    def _fetch_url(url: str) -> str:
        with urlopen(url, timeout=30) as response:
            return response.read().decode()


class DemoLLM(LLM):
    def __init__(self, slow_url: str):
        super().__init__(model="demo-model", usage_id="demo-llm")
        self._slow_url = slow_url
        self._call_count = 0

    def completion(self, *, messages, tools=None, **kwargs) -> LLMResponse:  # type: ignore[override]
        del messages, tools, kwargs
        self._call_count += 1

        if self._call_count == 1:
            tool_call = MessageToolCall(
                id="call-browser",
                name=BrowserNavigateTool.name,
                arguments=json.dumps({"url": self._slow_url}),
                origin="completion",
            )
            message = Message(
                role="assistant",
                content=[TextContent(text="I'll check the slow page.")],
                tool_calls=[tool_call],
            )
        else:
            tool_call = MessageToolCall(
                id="call-finish",
                name="finish",
                arguments=json.dumps(
                    {
                        "message": (
                            "The slow web service timed out, but the browser tool "
                            "returned a normal error observation instead of "
                            "crashing the conversation."
                        )
                    }
                ),
                origin="completion",
            )
            message = Message(
                role="assistant",
                content=[TextContent(text="The timeout was handled cleanly.")],
                tool_calls=[tool_call],
            )

        return LLMResponse(
            message=message,
            metrics=MetricsSnapshot(
                model_name="demo-model",
                accumulated_cost=0.0,
                max_budget_per_task=0.0,
                accumulated_token_usage=TokenUsage(model="demo-model"),
            ),
            raw_response=MagicMock(spec=ModelResponse, id=f"demo-{self._call_count}"),
        )


@contextmanager
def run_demo_server() -> "Iterator[str]":
    server = _ThreadedSlowServer(("127.0.0.1", 0), SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        host = server.server_address[0]
        port = server.server_address[1]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


llm_messages = []


def conversation_callback(event: Event):
    if isinstance(event, LLMConvertibleEvent):
        llm_messages.append(event.to_llm_message())


with run_demo_server() as slow_url:
    executor = SlowServiceBrowserExecutor(action_timeout_seconds=ACTION_TIMEOUT_SECONDS)
    browser_navigate_tool = BrowserNavigateTool.create(executor)[0]
    register_tool(BrowserNavigateTool.name, browser_navigate_tool)

    tools = [Tool(name=BrowserNavigateTool.name)]
    agent = Agent(llm=DemoLLM(slow_url), tools=tools)

    conversation = Conversation(
        agent=agent,
        callbacks=[conversation_callback],
        max_iteration_per_run=4,
    )

    print("=" * 80)
    print("Browser timeout observation example")
    print("=" * 80)
    print(f"Slow URL: {slow_url}")
    print(f"Browser action timeout: {ACTION_TIMEOUT_SECONDS} seconds")

    conversation.send_message(
        "Use browser_navigate to open the slow web service. Do not retry. After "
        "the tool returns, explain what happened in one sentence."
    )
    conversation.run()

    print("\nConversation completed without a fatal timeout.\n")
    print("Collected LLM messages:")
    for index, message in enumerate(llm_messages):
        print(f"Message {index}: {str(message)[:400]}")

    cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
    print(f"Conversation ID: {conversation.id}")
    print(f"EXAMPLE_COST: {cost}")
    conversation.close()
