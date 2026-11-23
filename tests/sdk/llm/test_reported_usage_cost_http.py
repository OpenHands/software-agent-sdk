"""Exercise custom-alias cost accounting through real SDK HTTP transports."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.conversation.types import ConversationID
from openhands.sdk.llm import Message, TextContent


@pytest.fixture
def reported_cost_provider():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            base = {"id": "reported-cost", "model": body["model"], "created": 1}
            usage = {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "cost": 0.04,
            }
            if body.get("stream"):
                chunks = [
                    dict(
                        base,
                        object="chat.completion.chunk",
                        choices=[
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": "ok"},
                                "finish_reason": None,
                            }
                        ],
                    ),
                    dict(
                        base,
                        object="chat.completion.chunk",
                        choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    ),
                    dict(base, object="chat.completion.chunk", choices=[], usage=usage),
                ]
                text = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
                payload = (text + "data: [DONE]\n\n").encode()
                content_type = "text/event-stream"
            else:
                message = {"role": "assistant", "content": "ok"}
                if body.get("tools"):
                    message = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"finish-{len(requests)}",
                                "type": "function",
                                "function": {
                                    "name": "finish",
                                    "arguments": json.dumps({"message": "done"}),
                                },
                            }
                        ],
                    }
                payload = json.dumps(
                    dict(
                        base,
                        object="chat.completion",
                        choices=[
                            {
                                "index": 0,
                                "message": message,
                                "finish_reason": "tool_calls"
                                if body.get("tools")
                                else "stop",
                            }
                        ],
                        usage=usage,
                    )
                ).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["gpt-luna", "cost-auto"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("use_async", [False, True])
async def test_reported_cost_through_http(
    reported_cost_provider, alias, streaming, use_async
):
    base_url, requests = reported_cost_provider
    llm = LLM(
        model=f"openai/{alias}",
        api_key=SecretStr("synthetic-only"),
        base_url=base_url,
        stream=streaming,
        num_retries=0,
        max_output_tokens=32,
    )
    messages = [
        Message(role="system", content=[TextContent(text="Accounting probe.")]),
        Message(role="user", content=[TextContent(text="Say ok.")]),
    ]
    tokens = []
    callback = tokens.append if streaming else None
    if use_async:
        await llm.acompletion(messages, on_token=callback)
    else:
        llm.completion(messages, on_token=callback)
    assert llm.metrics.accumulated_cost == 0.04
    assert len(llm.metrics.costs) == 1
    assert llm.metrics.token_usages[0].prompt_tokens == 100
    assert len(requests) == 1
    if streaming:
        assert requests[0]["stream_options"]["include_usage"] is True
        assert tokens


def test_reported_cost_survives_conversation_restart(reported_cost_provider, tmp_path):
    base_url, requests = reported_cost_provider

    def make_agent():
        return Agent(
            llm=LLM(
                model="openai/cost-auto",
                api_key=SecretStr("synthetic-only"),
                base_url=base_url,
                usage_id="reported-cost",
                stream=False,
                native_tool_calling=True,
                num_retries=0,
                max_output_tokens=32,
            ),
            tools=[],
        )

    def make_conversation(conversation_id: ConversationID | None = None):
        return Conversation(
            agent=make_agent(),
            workspace=tmp_path,
            persistence_dir=tmp_path / "state",
            delete_on_close=False,
            visualizer=None,
            max_iteration_per_run=2,
            conversation_id=conversation_id,
        )

    first = make_conversation()
    try:
        first.send_message("Complete this accounting probe.")
        first.run()
        conversation_id = first.id
        assert first.conversation_stats.get_combined_metrics().accumulated_cost == 0.04
    finally:
        first.close()

    resumed = make_conversation(conversation_id)
    try:
        assert (
            resumed.conversation_stats.get_combined_metrics().accumulated_cost == 0.04
        )
        resumed.send_message("Complete one more accounting probe.")
        resumed.run()
        assert (
            resumed.conversation_stats.get_combined_metrics().accumulated_cost == 0.08
        )
        assert len(requests) == 2
    finally:
        resumed.close()
