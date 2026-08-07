"""Tests for the content-materialization seam in LocalConversation.

As each event enters ``_on_event``, content parts that carry non-inline bytes
(images today) are written to the workspace and a path pointer is appended to
the event's ``extended_content`` — mirroring the path-rule injection seam.
The interceptor is source-agnostic (user messages *and* tool observations) and
type-agnostic (dispatches on ``BaseContent.materialize()``).
"""

import base64
from pathlib import Path

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import MessageEvent, ObservationEvent
from openhands.sdk.llm import ImageContent, Message, TextContent
from openhands.sdk.llm.utils.content_materialize import MATERIALIZE_SUBDIR
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins.finish import FinishObservation


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9"
    "awAAAABJRU5ErkJggg=="
)


def _data_url(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _conversation(tmp_path: Path) -> LocalConversation:
    agent = Agent(
        llm=TestLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text="ok")])],
            model="test-model",
        ),
        tools=[],
        include_default_tools=[],
    )
    return LocalConversation(
        agent=agent,
        workspace=tmp_path,
        persistence_dir=tmp_path / "conversation",
        delete_on_close=True,
    )


def _materialize(conv: LocalConversation, event):
    result = conv._maybe_materialize_content(event)
    return result


def test_user_message_image_materialized_and_pointer_injected(tmp_path: Path) -> None:
    conv = _conversation(tmp_path)
    try:
        event = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[
                    TextContent(text="attach this"),
                    ImageContent(image_urls=[_data_url(PNG_BYTES)]),
                ],
            ),
        )
        result = _materialize(conv, event)
        assert isinstance(result, MessageEvent)

        # A pointer was appended to extended_content.
        assert len(result.extended_content) == 1
        pointer = result.extended_content[0].text
        assert MATERIALIZE_SUBDIR in pointer
        assert "image/png" in pointer

        # The file actually landed in the workspace with correct bytes.
        subdir = tmp_path / MATERIALIZE_SUBDIR
        files = list(subdir.iterdir())
        assert len(files) == 1
        assert files[0].read_bytes() == PNG_BYTES
    finally:
        conv.close()


def test_observation_image_materialized(tmp_path: Path) -> None:
    conv = _conversation(tmp_path)
    try:
        obs = ObservationEvent(
            observation=FinishObservation(
                content=[
                    TextContent(text="here is a tool image"),
                    ImageContent(image_urls=[_data_url(PNG_BYTES)]),
                ]
            ),
            action_id="a1",
            tool_name="some_tool",
            tool_call_id="tc1",
        )
        result = _materialize(conv, obs)
        assert isinstance(result, ObservationEvent)
        assert len(result.extended_content) == 1
        assert MATERIALIZE_SUBDIR in result.extended_content[0].text
        assert len(list((tmp_path / MATERIALIZE_SUBDIR).iterdir())) == 1
    finally:
        conv.close()


def test_text_only_message_is_unchanged(tmp_path: Path) -> None:
    conv = _conversation(tmp_path)
    try:
        event = MessageEvent(
            source="user",
            llm_message=Message(
                role="user", content=[TextContent(text="no attachments here")]
            ),
        )
        result = _materialize(conv, event)
        assert result.extended_content == []
        assert not (tmp_path / MATERIALIZE_SUBDIR).exists()
    finally:
        conv.close()


def test_event_without_content_parts_is_unchanged(tmp_path: Path) -> None:
    conv = _conversation(tmp_path)
    try:
        # ObservationEvent whose observation has only text -> no materializable parts.
        obs = ObservationEvent(
            observation=FinishObservation(content=[TextContent(text="just text")]),
            action_id="a1",
            tool_name="some_tool",
            tool_call_id="tc1",
        )
        result = _materialize(conv, obs)
        assert result.extended_content == []
    finally:
        conv.close()


def test_replay_does_not_write_duplicates(tmp_path: Path) -> None:
    conv = _conversation(tmp_path)
    try:
        event = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[ImageContent(image_urls=[_data_url(PNG_BYTES)])],
            ),
        )
        first = _materialize(conv, event)
        assert len(first.extended_content) == 1

        # Re-emitting the same event id must not re-materialize or re-inject.
        second = _materialize(conv, event)
        assert second.extended_content == []
        assert len(list((tmp_path / MATERIALIZE_SUBDIR).iterdir())) == 1
    finally:
        conv.close()
