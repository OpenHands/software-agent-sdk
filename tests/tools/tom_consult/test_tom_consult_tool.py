"""Tests for TomConsultTool declared_resources."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import BaseModel

from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm.utils import openhands_provider
from openhands.sdk.llm.utils.openhands_provider import OPENHANDS_SYSTEM_IDENTITY
from openhands.sdk.tool import DeclaredResources
from openhands.tools.tom_consult.definition import (
    ConsultTomAction,
    ConsultTomObservation,
    TomConsultTool,
)
from openhands.tools.tom_consult.executor import TomConsultExecutor


@pytest.mark.parametrize(
    "action",
    [
        ConsultTomAction(reason="unclear intent", use_user_message=True),
        ConsultTomAction(
            reason="need guidance",
            use_user_message=False,
            custom_query="What does the user prefer?",
        ),
    ],
    ids=["use-user-message", "custom-query"],
)
def test_consult_tom_declared_resources(action):
    """TomConsultTool always declares safe with no resource keys."""
    tool = TomConsultTool(
        action_type=ConsultTomAction,
        observation_type=ConsultTomObservation,
        description="test",
        executor=None,
    )

    resources = tool.declared_resources(action)

    assert isinstance(resources, DeclaredResources)
    assert resources.declared is True
    assert resources.keys == ()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("explicit_base", [False, True])
def test_tom_uses_sdk_provider_routing(
    tmp_path, monkeypatch, asynchronous, repair, explicit_base
):
    class Answer(BaseModel):
        answer: str

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            payload = json.dumps(
                {
                    "id": "chatcmpl-tom",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "claude-haiku-4-5-20251001",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": (
                                    "not valid JSON"
                                    if repair and len(requests) == 1
                                    else '{"answer":"routed"}'
                                ),
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        base_url = f"http://127.0.0.1:{server.server_port}"
        # Recognize the local HTTP fixture as the proxy without mocking transport.
        monkeypatch.setattr(
            openhands_provider, "_OPENHANDS_PROXY_BASE_URLS", frozenset({base_url})
        )
        monkeypatch.setitem(
            openhands_provider._ROUTED_PROVIDERS,
            "openhands/",
            ("litellm_proxy/", base_url),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            executor = TomConsultExecutor(
                LocalFileStore(str(tmp_path)),
                enable_rag=False,
                llm_model="openhands/claude-haiku-4-5-20251001",
                api_key="test-key",
                api_base=base_url if explicit_base else None,
            )
            agent = executor._get_tom_agent()
            assert agent.tom_analyzer.llm_client is agent.llm_client
            if asynchronous:
                result = asyncio.run(
                    agent.llm_client.call_structured_async("Question", Answer)
                )
            else:
                result = agent.llm_client.call_structured_messages(
                    [
                        {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Use pytest."}],
                        },
                        {"role": "user", "content": "Question"},
                    ],
                    Answer,
                )
            assert result == Answer(answer="routed")
            assert len(requests) == (2 if repair else 1)
            for request in requests:
                identity = request["messages"][0]
                assert identity["role"] == "system"
                content = identity["content"]
                if isinstance(content, list):
                    content = "".join(part["text"] for part in content)
                assert content.startswith(OPENHANDS_SYSTEM_IDENTITY)
            if repair:
                assert requests[1]["model"] == "gpt-5-nano"
            assert requests[0]["model"] == "claude-haiku-4-5-20251001"
            if not asynchronous:
                history = [
                    m for m in requests[0]["messages"] if m["role"] == "assistant"
                ]
                assert len(history) == 1
                assert "Use pytest." in json.dumps(history[0]["content"])
        finally:
            server.shutdown()
            thread.join(timeout=5)
