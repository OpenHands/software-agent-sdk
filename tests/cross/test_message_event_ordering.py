"""Test for MessageEvent ordering bug in remote conversations.

This test verifies that MessageEvent is present in the event stream
when polling events immediately after send_message() and during run().

Bug report: MessageEvent with role=user is not logged despite calling
conversation.send_message() before conversation.run(). The MessageEvent
that must be triggered from send_message() isn't present in the event
stream though it is a part of the LLM prompt.
"""

import json
import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest
import uvicorn
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.conversation import RemoteConversation
from openhands.sdk.event import (
    MessageEvent,
)
from openhands.sdk.workspace import RemoteWorkspace
from openhands.workspace.docker.workspace import find_available_tcp_port


@pytest.fixture
def server_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[dict, None, None]:
    """Launch a real FastAPI server backed by temp workspace and conversations."""
    import shutil

    conversations_path = tmp_path / "conversations"
    workspace_path = tmp_path / "workspace"

    # Clean up
    if conversations_path.exists():
        shutil.rmtree(conversations_path)
    if workspace_path.exists():
        shutil.rmtree(workspace_path)

    conversations_path.mkdir(parents=True, exist_ok=True)
    workspace_path.mkdir(parents=True, exist_ok=True)

    cfg = {
        "session_api_keys": [],
        "conversations_path": str(conversations_path),
        "workspace_path": str(workspace_path),
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    monkeypatch.setenv("OPENHANDS_AGENT_SERVER_CONFIG_PATH", str(cfg_file))
    monkeypatch.delenv("SESSION_API_KEY", raising=False)

    from openhands.agent_server.api import create_app
    from openhands.agent_server.config import Config

    cfg_obj = Config.model_validate_json(cfg_file.read_text())
    app = create_app(cfg_obj)

    port = find_available_tcp_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    import httpx

    base_url = f"http://127.0.0.1:{port}"
    for attempt in range(50):
        try:
            with httpx.Client() as client:
                response = client.get(f"{base_url}/health", timeout=2.0)
                if response.status_code == 200:
                    break
        except (httpx.RequestError, httpx.TimeoutException):
            pass
        time.sleep(0.1)
    else:
        raise RuntimeError("Server failed to start within timeout")

    try:
        yield {"host": base_url}
    finally:
        server.should_exit = True
        thread.join(timeout=2)


def test_message_event_present_before_first_action(server_env, monkeypatch):
    """Test that MessageEvent is returned by send_message and added to local cache.

    This tests the fix where send_message() returns the created MessageEvent
    and adds it to the local cache immediately, ensuring it's available for
    polling without waiting for WebSocket delivery.
    """

    def fake_completion(
        self,
        messages,
        tools,
        return_metrics=False,
        add_security_risk_prediction=False,
        **kwargs,
    ):
        from openhands.sdk.llm.llm_response import LLMResponse
        from openhands.sdk.llm.message import Message
        from openhands.sdk.llm.utils.metrics import MetricsSnapshot

        litellm_msg = LiteLLMMessage.model_validate(
            {"role": "assistant", "content": "Hello"}
        )
        raw_response = ModelResponse(
            id="test-resp",
            created=int(time.time()),
            model="test-model",
            choices=[Choices(index=0, finish_reason="stop", message=litellm_msg)],
        )

        message = Message.from_llm_chat_message(litellm_msg)
        metrics_snapshot = MetricsSnapshot(
            model_name="test-model",
            accumulated_cost=0.0,
            max_budget_per_task=None,
            accumulated_token_usage=None,
        )

        return LLMResponse(
            message=message, metrics=metrics_snapshot, raw_response=raw_response
        )

    monkeypatch.setattr(LLM, "completion", fake_completion, raising=True)

    llm = LLM(model="gpt-4", api_key=SecretStr("test"))
    agent = Agent(llm=llm, tools=[])

    workspace = RemoteWorkspace(
        host=server_env["host"], working_dir="/tmp/workspace/project"
    )
    conv: RemoteConversation = Conversation(agent=agent, workspace=workspace)

    # Check events immediately after conversation creation - should be empty or
    # have only system events
    events_after_init = list(conv.state.events)
    user_messages_before = [
        e
        for e in events_after_init
        if isinstance(e, MessageEvent) and e.source == "user"
    ]
    assert len(user_messages_before) == 0, (
        f"No user MessageEvents should exist before send_message(). "
        f"Found: {[e.kind for e in user_messages_before]}"
    )

    # Send message - the fix should return the MessageEvent and add it to local cache
    message_event = conv.send_message("Test message for event ordering")

    # Verify the returned value is a MessageEvent
    assert isinstance(message_event, MessageEvent), (
        f"send_message() should return a MessageEvent, got {type(message_event)}"
    )
    assert message_event.source == "user", (
        f"MessageEvent source should be 'user', got {message_event.source}"
    )

    # Check events immediately after send_message - MessageEvent should be present
    events_after_send = list(conv.state.events)
    user_messages_after = [
        e
        for e in events_after_send
        if isinstance(e, MessageEvent) and e.source == "user"
    ]

    # This is the key fix: MessageEvent should be available immediately
    # without waiting for WebSocket delivery
    assert len(user_messages_after) > 0, (
        f"MessageEvent should be present immediately after send_message(). "
        f"Events: {[e.kind for e in events_after_send]}"
    )

    # Verify the event in the list matches the returned event
    assert any(e.id == message_event.id for e in user_messages_after), (
        f"The returned MessageEvent should be in the event list. "
        f"Returned id: {message_event.id}, "
        f"List ids: {[e.id for e in user_messages_after]}"
    )

    conv.close()


def test_message_event_present_after_send_message(server_env, monkeypatch):
    """Test that MessageEvent is immediately available after send_message.

    This is a simpler version of the bug reproduction that checks if the
    MessageEvent is present in the event stream right after send_message().
    """

    def fake_completion(
        self,
        messages,
        tools,
        return_metrics=False,
        add_security_risk_prediction=False,
        **kwargs,
    ):
        from openhands.sdk.llm.llm_response import LLMResponse
        from openhands.sdk.llm.message import Message
        from openhands.sdk.llm.utils.metrics import MetricsSnapshot

        litellm_msg = LiteLLMMessage.model_validate(
            {"role": "assistant", "content": "Hello"}
        )
        raw_response = ModelResponse(
            id="test-resp",
            created=int(time.time()),
            model="test-model",
            choices=[Choices(index=0, finish_reason="stop", message=litellm_msg)],
        )

        message = Message.from_llm_chat_message(litellm_msg)
        metrics_snapshot = MetricsSnapshot(
            model_name="test-model",
            accumulated_cost=0.0,
            max_budget_per_task=None,
            accumulated_token_usage=None,
        )

        return LLMResponse(
            message=message, metrics=metrics_snapshot, raw_response=raw_response
        )

    monkeypatch.setattr(LLM, "completion", fake_completion, raising=True)

    llm = LLM(model="gpt-4", api_key=SecretStr("test"))
    agent = Agent(llm=llm, tools=[])

    workspace = RemoteWorkspace(
        host=server_env["host"], working_dir="/tmp/workspace/project"
    )
    conv: RemoteConversation = Conversation(agent=agent, workspace=workspace)

    # Send message
    conv.send_message("Test message")

    # Wait a bit for WebSocket to deliver the event
    time.sleep(1.0)

    # Check if MessageEvent is present
    events = list(conv.state.events)
    message_events = [
        e for e in events if isinstance(e, MessageEvent) and e.source == "user"
    ]

    assert len(message_events) > 0, (
        f"Expected MessageEvent to be present after send_message(). "
        f"Events: {[e.kind for e in events]}"
    )

    conv.close()
