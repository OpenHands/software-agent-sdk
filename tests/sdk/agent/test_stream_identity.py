"""The durable event a stream produces carries the id that stream minted.

Both :class:`Agent` entry points: the sync path streams too, so an async-only
fix would cover half the problem.

See https://github.com/OpenHands/software-agent-sdk/issues/4682.
"""

from collections.abc import Sequence
from typing import cast

import pytest
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices
from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.agent.stream_context import (
    StreamAborted,
    StreamDelta,
    StreamStarted,
)
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.llm.exceptions import LLMNoResponseError
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import (
    Action,
    Observation,
    Tool,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)


class _StreamEchoAction(Action):
    text: str


class _StreamEchoObservation(Observation):
    pass


class _StreamEchoExecutor(ToolExecutor[_StreamEchoAction, _StreamEchoObservation]):
    def __call__(
        self, action: _StreamEchoAction, conversation=None
    ) -> _StreamEchoObservation:
        return _StreamEchoObservation.from_text(action.text)


class _StreamEchoTool(ToolDefinition[_StreamEchoAction, _StreamEchoObservation]):
    name = "stream_echo"

    @classmethod
    def create(cls, conv_state=None) -> Sequence["_StreamEchoTool"]:
        return [
            cls(
                description="Echo the given text",
                action_type=_StreamEchoAction,
                observation_type=_StreamEchoObservation,
                executor=_StreamEchoExecutor(),
            )
        ]


register_tool("EchoStreamTool", _StreamEchoTool)


class StreamingTestLLM(TestLLM):
    """A ``TestLLM`` that emits the scripted text as chunks first."""

    _chunks: list[str] = PrivateAttr(default_factory=list)
    _raises: BaseException | None = PrivateAttr(default=None)
    _seen_on_token: list = PrivateAttr(default_factory=list)

    def script(self, chunks: list[str], raises: BaseException | None = None):
        self._chunks = chunks
        self._raises = raises
        return self

    def _stream(self, on_token) -> None:
        for text in self._chunks:
            if on_token is None:
                continue
            on_token(
                ModelResponseStream(
                    id="completion-1",
                    model="test-model",
                    choices=[
                        StreamingChoices(
                            delta=Delta(role="assistant", content=text),
                            index=0,
                            finish_reason=None,
                        )
                    ],
                )
            )

    def completion(self, messages, tools=None, on_token=None, **kwargs):  # type: ignore[override]
        self._seen_on_token.append(on_token)
        self._stream(on_token)
        if self._raises is not None:
            raise self._raises
        return super().completion(messages, tools=tools, **kwargs)

    async def acompletion(self, messages, tools=None, on_token=None, **kwargs):  # type: ignore[override]
        # TestLLM.acompletion drops on_token, so stream here instead.
        self._stream(on_token)
        if self._raises is not None:
            raise self._raises
        return await super().acompletion(messages, tools=tools, **kwargs)


def _conversation(tmp_path, llm, frames: list, tools: Sequence[Tool] = ()):
    agent = Agent(llm=llm, tools=list(tools))
    convo = LocalConversation(
        agent=agent,
        workspace=str(tmp_path / "workspace"),
        persistence_dir=str(tmp_path / "conversations"),
        visualizer=None,
        stream_callbacks=[frames.append],
    )
    convo._ensure_agent_ready()
    return convo


def _llm(messages, chunks, raises=None) -> StreamingTestLLM:
    llm = cast(StreamingTestLLM, StreamingTestLLM.from_messages(messages))
    llm.stream = True
    return llm.script(chunks, raises)


def test_the_message_carries_the_id_its_stream_minted(tmp_path):
    frames: list = []
    llm = _llm(
        [Message(role="assistant", content=[TextContent(text="Hello there")])],
        ["Hello ", "there"],
    )
    convo = _conversation(tmp_path, llm, frames)

    events: list = []
    convo.agent.step(convo, on_event=events.append)

    started = next(f for f in frames if isinstance(f, StreamStarted))
    message = next(e for e in events if isinstance(e, MessageEvent))
    assert message.id == started.item_id
    assert [f.content for f in frames if isinstance(f, StreamDelta)] == [
        "Hello ",
        "there",
    ]
    # Retired by the durable event, so no abort.
    assert not any(isinstance(f, StreamAborted) for f in frames)


@pytest.mark.asyncio
async def test_astep_mints_the_same_way(tmp_path):
    frames: list = []
    llm = _llm(
        [Message(role="assistant", content=[TextContent(text="Hello there")])],
        ["Hello ", "there"],
    )
    convo = _conversation(tmp_path, llm, frames)

    events: list = []
    await convo.agent.astep(convo, on_event=events.append)

    started = next(f for f in frames if isinstance(f, StreamStarted))
    message = next(e for e in events if isinstance(e, MessageEvent))
    assert message.id == started.item_id


def test_a_tool_call_turn_retires_the_slot_on_its_first_action(tmp_path):
    """The streamed text is that action's thought, so the action closes it."""
    frames: list = []
    llm = _llm(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Listing the directory")],
                tool_calls=[
                    MessageToolCall(
                        id="call-1",
                        name="stream_echo",
                        arguments='{"text": "hi"}',
                        origin="completion",
                    )
                ],
            )
        ],
        ["Listing ", "the directory"],
    )
    convo = _conversation(tmp_path, llm, frames, tools=[Tool(name="EchoStreamTool")])

    events: list = []
    convo.agent.step(convo, on_event=events.append)

    started = next(f for f in frames if isinstance(f, StreamStarted))
    action = next(e for e in events if isinstance(e, ActionEvent))
    assert action.id == started.item_id
    assert not any(isinstance(f, StreamAborted) for f in frames)


def test_a_provider_failure_retires_the_slot_with_an_abort(tmp_path):
    frames: list = []
    llm = _llm(
        [Message(role="assistant", content=[TextContent(text="unused")])],
        ["half a "],
        raises=LLMNoResponseError("provider gave up"),
    )
    convo = _conversation(tmp_path, llm, frames)

    with pytest.raises(LLMNoResponseError):
        convo.agent.step(convo, on_event=lambda _: None)

    started = [f for f in frames if isinstance(f, StreamStarted)]
    aborted = [f for f in frames if isinstance(f, StreamAborted)]
    assert len(started) == len(aborted) == 1
    assert aborted[0].item_id == started[0].item_id


def test_no_stream_consumer_leaves_the_llm_free_to_skip_streaming(tmp_path):
    """Without a consumer the agent must not force a streaming completion."""
    llm = _llm([Message(role="assistant", content=[TextContent(text="hi")])], ["hi"])
    agent = Agent(llm=llm, tools=[])
    convo = LocalConversation(
        agent=agent,
        workspace=str(tmp_path / "workspace"),
        persistence_dir=str(tmp_path / "conversations"),
        visualizer=None,
    )
    convo._ensure_agent_ready()

    convo.agent.step(convo, on_event=lambda _: None)

    assert llm._seen_on_token == [None]


def test_a_step_that_never_reaches_the_provider_opens_nothing(tmp_path):
    """No LLM call, no slot — so there is nothing to abort."""
    frames: list = []
    llm = _llm([Message(role="assistant", content=[TextContent(text="hi")])], [])
    convo = _conversation(tmp_path, llm, frames)

    convo.agent.step(convo, on_event=lambda _: None)

    assert frames == []


def test_a_resolved_secret_is_masked_in_the_deltas(tmp_path):
    """The LLM stream path masked nothing before this; it does now.

    Only already-resolved values are masked. A secret that can reach the
    model's output was exported for a command first, which resolves it.
    """
    frames: list = []
    llm = _llm(
        [Message(role="assistant", content=[TextContent(text="done")])],
        ["the token is ", "hunter2"],
    )
    convo = _conversation(tmp_path, llm, frames)
    convo.state.secret_registry.update_secrets({"TOKEN": "hunter2"})
    # Resolution happens when a command references the name.
    convo.state.secret_registry.get_secrets_as_env_vars("echo $TOKEN")

    convo.agent.step(convo, on_event=lambda _: None)

    streamed = "".join(f.content for f in frames if isinstance(f, StreamDelta))
    assert streamed == "the token is <secret-hidden>"
