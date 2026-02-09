"""Reproduction test for the event loss race condition.

This test demonstrates that without proper synchronization, events can be lost
when the WebSocket callback is delayed and run() returns before events are
delivered to the client.

This is a regression test for the issue observed in PR #1829:
https://github.com/OpenHands/software-agent-sdk/actions/runs/21364607784/job/61492749827?pr=1829#step:7:5709

Run with: uv run pytest tests/cross/test_event_loss_repro.py -v
"""

import json
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.conversation import RemoteConversation
from openhands.sdk.event import ActionEvent, Event, ObservationEvent
from openhands.sdk.workspace import RemoteWorkspace
from openhands.workspace.docker.workspace import find_available_tcp_port


@pytest.fixture
def server_env_for_repro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Launch a real FastAPI server for the reproduction test."""
    import shutil

    cwd_conversations = Path("workspace/conversations")
    if cwd_conversations.exists():
        shutil.rmtree(cwd_conversations)

    conversations_path = tmp_path / "conversations"
    workspace_path = tmp_path / "workspace"
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

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            with httpx.Client() as client:
                response = client.get(f"{base_url}/health", timeout=2.0)
                if response.status_code == 200:
                    break
        except (httpx.RequestError, httpx.TimeoutException):
            pass
        time.sleep(0.1)

    try:
        yield {"host": base_url}
    finally:
        server.should_exit = True
        thread.join(timeout=2)
        if cwd_conversations.exists():
            shutil.rmtree(cwd_conversations)


def test_event_loss_race_condition_with_ws_delay(
    server_env_for_repro, monkeypatch: pytest.MonkeyPatch
):
    """Reliably reproduce the event loss race condition.

    This test injects a delay in the WebSocket callback to simulate the race
    condition where run() returns before events are delivered. This reproduces
    the CI failure observed in PR #1829.

    The race condition occurs when:
    1. Server emits events (ActionEvent, ObservationEvent)
    2. Client polls and sees "finished" status
    3. run() returns before WebSocket delivers those events

    Without proper handling, the client will be missing the finish ActionEvent
    and ObservationEvent that the REST API has.
    """

    def fake_completion_with_finish_tool(
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
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_finish",
                        "type": "function",
                        "function": {
                            "name": "finish",
                            "arguments": '{"message": "Task complete"}',
                        },
                    }
                ],
            }
        )

        raw_response = ModelResponse(
            id="test-resp-finish",
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

    monkeypatch.setattr(
        LLM, "completion", fake_completion_with_finish_tool, raising=True
    )

    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test"))
    agent = Agent(llm=llm, tools=[])
    workspace = RemoteWorkspace(
        host=server_env_for_repro["host"], working_dir="/tmp/workspace/project"
    )
    conv: RemoteConversation = Conversation(agent=agent, workspace=workspace)

    # KEY: Inject a delay in the WebSocket callback for finish events
    # This simulates the race condition where run() returns before events
    # are delivered. A 3s delay ensures the events are definitely missed
    # if there's no synchronization mechanism.
    ws_delay_s = 3.0
    assert conv._ws_client is not None
    orig_cb = conv._ws_client.callback

    def delayed_cb(event: Event) -> None:
        if (
            isinstance(event, (ActionEvent, ObservationEvent))
            and getattr(event, "tool_name", None) == "finish"
        ):
            time.sleep(ws_delay_s)
        orig_cb(event)

    conv._ws_client.callback = delayed_cb

    conv.send_message("Complete the task")
    conv.run()

    # Get events IMMEDIATELY after run() returns
    ws_events = list(conv.state.events)

    # Fetch events from REST API to see what the server has
    with httpx.Client(base_url=server_env_for_repro["host"]) as client:
        response = client.get(
            f"/api/conversations/{conv._id}/events/search",
            params={"limit": 100},
        )
        response.raise_for_status()
        rest_data = response.json()
        rest_events = [Event.model_validate(item) for item in rest_data["items"]]

    ws_action_events = [
        e for e in ws_events if isinstance(e, ActionEvent) and e.tool_name == "finish"
    ]
    rest_action_events = [
        e for e in rest_events if isinstance(e, ActionEvent) and e.tool_name == "finish"
    ]

    ws_event_summary = [
        f"{type(e).__name__}({getattr(e, 'tool_name', 'N/A')})" for e in ws_events
    ]
    rest_event_summary = [
        f"{type(e).__name__}({getattr(e, 'tool_name', 'N/A')})" for e in rest_events
    ]

    conv.close()

    # Verify REST API has the expected events (sanity check)
    assert len(rest_action_events) >= 1, (
        f"REST API should have ActionEvent. REST events: {rest_event_summary}"
    )

    # This assertion verifies that the fix works - client should have all events
    # even with the WebSocket delay, because the fix ensures events are fetched
    # before run() returns.
    ws_has_action = len(ws_action_events) >= 1
    assert ws_has_action, (
        f"ActionEvent with finish tool not found in client events. "
        f"REST API has {len(rest_action_events)} ActionEvent(s) but client has "
        f"{len(ws_action_events)}. This demonstrates the race condition! "
        f"Client events: {ws_event_summary}. REST events: {rest_event_summary}"
    )
