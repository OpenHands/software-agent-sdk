import pytest

from openhands.sdk import LLM
from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import Message, TextContent, llm_profile_store
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.testing import TestLLM


def _make_llm(model: str, usage_id: str) -> LLM:
    return TestLLM.from_messages([], model=model, usage_id=usage_id)


@pytest.fixture()
def profile_store(tmp_path, monkeypatch):
    """
    Create a temp profile store with 'fast' and
    'slow' profiles saved via _make_llm.
    """

    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    monkeypatch.setattr(llm_profile_store, "_DEFAULT_PROFILE_DIR", profile_dir)

    store = LLMProfileStore(base_dir=profile_dir)
    store.save("fast", _make_llm("fast-model", "tmp"))
    store.save("slow", _make_llm("slow-model", "tmp"))
    return store


def _make_conversation() -> LocalConversation:
    return Conversation(
        agent=Agent(
            llm=_make_llm("default-model", "test-llm"),
            tools=[],
        )
    )


@pytest.mark.parametrize(
    "command, expected",
    [
        ("hello world", None),
        ("/modelfoo", None),
        ("/model", ("", None)),
        ("/model   ", ("", None)),
        ("/model fast", ("fast", None)),
        ("/model fast hello world", ("fast", "hello world")),
        ("  /model   fast   hi  ", ("fast", "hi")),
        ("/model my-profile.json", ("my-profile.json", None)),
    ],
)
def test_parse_model_command(command: str, expected: tuple[str, str | None]):
    assert LocalConversation._parse_model_command(command) == expected


def test_model_info():
    """Bare /model emits an info event with current model."""
    conv = _make_conversation()
    conv.send_message("/model")

    info = conv.state.events[-1]
    assert isinstance(info, MessageEvent)
    assert info.source == "environment"
    msg = info.llm_message.content[0]
    assert isinstance(msg, TextContent)
    assert "default-model" in msg.text


def test_switch_model(profile_store):
    """/model fast switches the agent's LLM."""
    conv = _make_conversation()
    conv.send_message("/model fast")

    assert conv.agent.llm.model == "fast-model"
    confirm = conv.state.events[-1]
    assert isinstance(confirm, MessageEvent)
    assert confirm.source == "environment"
    msg = confirm.llm_message.content[0]
    assert isinstance(msg, TextContent)
    assert "fast" in msg.text


def test_switch_model_with_message(profile_store):
    """/model fast hello switches model AND sends the remaining text."""
    conv = _make_conversation()
    conv.send_message("/model fast hello world")

    assert conv.agent.llm.model == "fast-model"

    confirm = conv.state.events[-2]
    assert confirm.source == "environment"
    llm_msg = getattr(confirm, "llm_message")
    msg = llm_msg.content[0]
    assert isinstance(msg, TextContent)
    assert "Switched to model profile" in msg.text

    user_msg = conv.state.events[-1]
    assert user_msg.source == "user"
    llm_msg = getattr(user_msg, "llm_message")
    msg = llm_msg.content[0]
    assert isinstance(msg, TextContent)
    assert msg.text == "hello world"


def test_switch_between_profiles(profile_store):
    """Switch fast -> slow -> fast, verify model changes each time."""
    conv = _make_conversation()

    conv.send_message("/model fast")
    assert conv.agent.llm.model == "fast-model"

    conv.send_message("/model slow")
    assert conv.agent.llm.model == "slow-model"

    conv.send_message("/model fast")
    assert conv.agent.llm.model == "fast-model"


def test_switch_reuses_registry_entry(profile_store):
    """Switching back to a profile reuses the same registry LLM object."""
    conv = _make_conversation()

    conv.send_message("/model fast")
    llm_first = conv.llm_registry.get("model-profile-fast")

    conv.send_message("/model slow")
    conv.send_message("/model fast")
    llm_second = conv.llm_registry.get("model-profile-fast")

    assert llm_first is llm_second


def test_switch_not_found(profile_store):
    """/model nonexistent emits an error event."""
    conv = _make_conversation()
    conv.send_message("/model nonexistent")

    error = conv.state.events[-1]
    assert isinstance(error, MessageEvent)
    assert error.source == "environment"
    msg = error.llm_message.content[0]
    assert isinstance(msg, TextContent)
    assert "not found" in msg.text

    # Agent LLM unchanged
    assert conv.agent.llm.model == "default-model"


def test_normal_message_not_intercepted():
    """Regular messages pass through unaffected."""
    conv = _make_conversation()
    conv.send_message("hello world")

    user_event = conv.state.events[-1]
    assert isinstance(user_event, MessageEvent)
    assert user_event.source == "user"
    msg = user_event.llm_message.content[0]
    assert isinstance(msg, TextContent)
    assert msg.text == "hello world"


def test_message_object_not_intercepted():
    """Message objects bypass /model parsing."""
    conv = _make_conversation()
    msg = Message(role="user", content=[TextContent(text="/model fast")])
    conv.send_message(msg)

    user_event = conv.state.events[-1]
    assert user_event.source == "user"
    llm_msg = getattr(user_event, "llm_message")
    msg = llm_msg.content[0]
    assert isinstance(msg, TextContent)
    assert msg.text == "/model fast"
