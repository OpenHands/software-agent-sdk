"""User-attached images are saved as files the agent's tools can use."""

import base64
from pathlib import Path

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import ImageContent, Message, TextContent, content_to_str
from openhands.sdk.testing import TestLLM


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4


def _conversation(tmp_path: Path, **kwargs) -> LocalConversation:
    agent = Agent(
        llm=TestLLM.from_messages([], model="test-model"),
        tools=[],
        include_default_tools=[],
    )
    return LocalConversation(agent=agent, workspace=tmp_path / "workspace", **kwargs)


def _image_message(*urls: str) -> Message:
    return Message(
        role="user",
        content=[
            TextContent(text="Attach this screenshot to PROJ-1"),
            ImageContent(image_urls=list(urls)),
        ],
    )


def _png_data_url(data: bytes = PNG_BYTES) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode()


def _user_event(conversation: LocalConversation) -> MessageEvent:
    events = [
        e
        for e in conversation.state.events
        if isinstance(e, MessageEvent) and e.source == "user"
    ]
    assert len(events) == 1
    return events[0]


def _llm_text(event: MessageEvent) -> str:
    return "".join(content_to_str(event.to_llm_message().content))


def test_pasted_image_is_saved_outside_the_workspace(tmp_path: Path):
    conversation = _conversation(tmp_path, persistence_dir=tmp_path / "conversations")

    conversation.send_message(_image_message(_png_data_url()))

    saved = list((tmp_path / "conversations").rglob("attachments/*.png"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == PNG_BYTES
    assert str(saved[0].resolve()) in _llm_text(_user_event(conversation))
    assert not any((tmp_path / "workspace").iterdir())


def test_saved_image_path_survives_resume(tmp_path: Path):
    persistence = tmp_path / "conversations"
    conversation = _conversation(tmp_path, persistence_dir=persistence)
    conversation.send_message(_image_message(_png_data_url()))
    conversation_id = conversation.id
    expected = _llm_text(_user_event(conversation))

    resumed = _conversation(
        tmp_path, persistence_dir=persistence, conversation_id=conversation_id
    )

    assert _llm_text(_user_event(resumed)) == expected
    assert next(persistence.rglob("attachments/*.png")).read_bytes() == PNG_BYTES


def test_image_that_cannot_be_decoded_is_reported(tmp_path: Path):
    conversation = _conversation(tmp_path, persistence_dir=tmp_path / "conversations")

    conversation.send_message(_image_message("data:image/png;base64,not*base64"))

    assert "could not be saved" in _llm_text(_user_event(conversation))
    assert not list((tmp_path / "conversations").rglob("attachments/*"))


def test_remote_image_urls_and_text_messages_are_unchanged(tmp_path: Path):
    conversation = _conversation(tmp_path, persistence_dir=tmp_path / "conversations")

    conversation.send_message(_image_message("https://example.com/screenshot.png"))

    assert _user_event(conversation).extended_content == []
    assert not list((tmp_path / "conversations").rglob("attachments"))


def test_in_memory_conversation_saves_to_a_temporary_directory(tmp_path: Path):
    conversation = _conversation(tmp_path)

    conversation.send_message(_image_message(_png_data_url()))

    text = _llm_text(_user_event(conversation))
    saved = Path(text.split("\n- ", 1)[1].split(" (", 1)[0])
    assert saved.read_bytes() == PNG_BYTES
    assert conversation.id.hex in str(saved)
    saved.unlink()
