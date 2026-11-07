"""Provider failures through the real HTTP, LLM, and conversation paths."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from litellm.exceptions import NotFoundError
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.conversation.base import BaseConversation
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.event.error_classification import FailureKind


@pytest.fixture
def missing_model_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[BaseConversation]:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.dumps(
                {
                    "error": {
                        "message": "private-provider-payload",
                        "code": "model_not_found",
                    }
                }
            ).encode()
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    worker.start()
    try:
        llm = LLM(
            model="openai/gpt-4o",
            api_key=SecretStr("local-test-key"),
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            num_retries=0,
        )
        conversation = Conversation(agent=Agent(llm=llm, tools=[]), workspace=tmp_path)
        assert isinstance(conversation, BaseConversation)
        try:
            conversation.send_message("Hello")
            yield conversation
        finally:
            conversation.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _assert_missing_model_error(error: ConversationRunError) -> None:
    event = error.conversation_error
    assert event is not None
    assert event.code == "LLMNotFoundError"
    assert "private-provider-payload" not in event.model_dump_json()
    assert event.classification is not None
    assert event.classification.kind == FailureKind.CONFIG
    assert isinstance(error.original_exception.__cause__, NotFoundError)


def test_run_emits_safe_provider_error(
    missing_model_conversation: BaseConversation,
) -> None:
    with pytest.raises(ConversationRunError) as error:
        missing_model_conversation.run()

    _assert_missing_model_error(error.value)


async def test_arun_emits_safe_provider_error(
    missing_model_conversation: BaseConversation,
) -> None:
    with pytest.raises(ConversationRunError) as error:
        await missing_model_conversation.arun()

    _assert_missing_model_error(error.value)
