"""Tests for the token streaming callback wiring in EventService."""

import asyncio
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices
from pydantic import SecretStr

from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk import Event
from openhands.sdk.agent import Agent
from openhands.sdk.event import StreamingDeltaEvent
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace


def _make_chunk(
    content: str | None = None, reasoning_content: str | None = None
) -> ModelResponseStream:
    """Build a minimal ModelResponseStream chunk for testing."""
    delta_kwargs: dict = {"role": "assistant"}
    if content is not None:
        delta_kwargs["content"] = content
    delta = Delta(**delta_kwargs)
    if reasoning_content is not None:
        object.__setattr__(delta, "reasoning_content", reasoning_content)
    choice = StreamingChoices(delta=delta, index=0, finish_reason=None)
    return ModelResponseStream(id="chunk-id", choices=[choice], model="test-model")


class _CollectorSubscriber(Subscriber):
    """Subscriber that collects events for assertions."""

    def __init__(self):
        self.events: list[Event] = []

    async def __call__(self, event: Event):
        self.events.append(event)

    async def close(self):
        pass


@pytest.fixture
def event_service(tmp_path):
    with patch("openhands.sdk.llm.utils.model_info.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        service = EventService(
            stored=StoredConversation(
                id=uuid4(),
                agent=Agent(
                    llm=LLM(
                        usage_id="test-llm",
                        model="test-model",
                        api_key=SecretStr("test-key"),
                    ),
                    tools=[],
                ),
                workspace=LocalWorkspace(working_dir=str(tmp_path / "workspace")),
            ),
            conversations_dir=tmp_path / "conversations",
        )
        yield service


def _mock_local_conversation():
    """Return a patch context manager for LocalConversation."""
    return patch("openhands.agent_server.event_service.LocalConversation")


async def _start_and_capture_callback(event_service, tmp_path):
    """
    Start the event service with a mocked LocalConversation
    and return the token callback.
    """
    (tmp_path / "workspace").mkdir(exist_ok=True)

    with _mock_local_conversation() as MockConv:
        mock_conv = MagicMock()
        mock_conv.state = MagicMock()
        mock_conv.state.execution_status = "idle"
        mock_conv._state = MagicMock()
        mock_conv._on_event = MagicMock()
        MockConv.return_value = mock_conv

        await event_service.start()
        return MockConv.call_args.kwargs["token_callbacks"][0]


@pytest.mark.asyncio
async def test_start_wires_token_callback(event_service, tmp_path):
    (tmp_path / "workspace").mkdir(exist_ok=True)

    with _mock_local_conversation() as MockConv:
        mock_conv = MagicMock()
        mock_conv.state = MagicMock()
        mock_conv.state.execution_status = "idle"
        mock_conv._state = MagicMock()
        mock_conv._on_event = MagicMock()
        MockConv.return_value = mock_conv

        await event_service.start()

        call_kwargs = MockConv.call_args
        assert "token_callbacks" in call_kwargs.kwargs
        assert len(call_kwargs.kwargs["token_callbacks"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunk_kwargs, expected_content, expected_reasoning",
    [
        ({"content": "Hello"}, "Hello", None),
        ({"reasoning_content": "Let me think"}, None, "Let me think"),
        ({"content": "answer", "reasoning_content": "thought"}, "answer", "thought"),
    ],
    ids=["content-delta", "reasoning-delta", "both-deltas"],
)
async def test_callback_publishes_delta(
    event_service, tmp_path, chunk_kwargs, expected_content, expected_reasoning
):
    collector = _CollectorSubscriber()
    event_service._pub_sub.subscribe(collector)

    callback = await _start_and_capture_callback(event_service, tmp_path)

    callback(_make_chunk(**chunk_kwargs))
    await asyncio.sleep(0.05)

    delta_events = [e for e in collector.events if isinstance(e, StreamingDeltaEvent)]
    assert len(delta_events) == 1
    assert delta_events[0].content == expected_content
    assert delta_events[0].reasoning_content == expected_reasoning


@pytest.mark.asyncio
async def test_callback_ignores_empty_delta(event_service, tmp_path):
    collector = _CollectorSubscriber()
    event_service._pub_sub.subscribe(collector)

    callback = await _start_and_capture_callback(event_service, tmp_path)

    callback(_make_chunk())
    await asyncio.sleep(0.05)

    delta_events = [e for e in collector.events if isinstance(e, StreamingDeltaEvent)]
    assert len(delta_events) == 0


@pytest.mark.asyncio
async def test_multiple_chunks_produce_multiple_events(event_service, tmp_path):
    collector = _CollectorSubscriber()
    event_service._pub_sub.subscribe(collector)

    callback = await _start_and_capture_callback(event_service, tmp_path)

    words = ["Hello", " ", "world", "!"]
    for word in words:
        callback(_make_chunk(content=word))

    await asyncio.sleep(0.05)

    delta_events = [e for e in collector.events if isinstance(e, StreamingDeltaEvent)]
    assert len(delta_events) == 4
    assert [e.content for e in delta_events] == words
