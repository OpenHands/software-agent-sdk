"""Prevention pass runs before rollback; rollback stays as the safety net.

The rollback recovery introduced with ``LLMHistoryContentRejectedError`` keeps
a poisoned turn from wedging the conversation, but pays a turn to do it: the
corrupted screenshot is discarded and retaken. The prevention pass in
``LLM._begin_chat_messages`` (and its Responses-API twin) catches the same
payload on the way out and drops just the image, so the turn's other work
survives.

The two are complementary. Prevention is the fast path; rollback is the
safety net for anything prevention misses.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import (
    LLM,
    ImageContent,
    MessageToolCall,
    TextContent,
)
from openhands.sdk.llm.exceptions import LLMHistoryContentRejectedError
from openhands.sdk.llm.utils.image_validation import UNDECODABLE_IMAGE_PLACEHOLDER
from openhands.sdk.tool import Action, Observation


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)
VALID_URL = f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}"
CORRUPTED_URL = VALID_URL.replace("AAAA", "<secret-hidden>", 1)


class PreventionTestAction(Action):
    command: str = "screenshot"


class PreventionTestObservation(Observation):
    """A tool result that carries a corrupted image URL by default."""

    image_url: str = CORRUPTED_URL

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [
            TextContent(text="screenshot captured"),
            ImageContent(image_urls=[self.image_url]),
        ]


class PreventionTestMixedObservation(Observation):
    """Observation carrying both a valid and a corrupted image URL.

    Kept at module scope with a globally-unique name: the polymorphic
    ``Observation`` registry rejects local classes (they may not exist at
    deserialisation time) and a colliding name would poison other tests via
    import order.
    """

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [ImageContent(image_urls=[VALID_URL, CORRUPTED_URL])]


@pytest.fixture
def agent() -> Agent:
    return Agent(llm=LLM(model="gpt-4o-mini", api_key=SecretStr("k"), usage_id="t"))


def _append_poisoned_turn(convo: LocalConversation, url: str = CORRUPTED_URL) -> None:
    """Append an agent turn whose observation carries ``url``."""
    action = ActionEvent(
        thought=[TextContent(text="looking")],
        action=PreventionTestAction(),
        tool_name="browser",
        tool_call_id="call_1",
        tool_call=MessageToolCall(
            id="call_1",
            name="browser",
            arguments="{}",
            origin="completion",
        ),
        llm_response_id="resp_1",
    )
    convo._on_event(action)
    convo._on_event(
        ObservationEvent(
            action_id=action.id,
            observation=PreventionTestObservation(image_url=url),
            tool_name="browser",
            tool_call_id=action.tool_call_id,
        )
    )


def _rejects_when_corrupted_url_reaches_wire(convo: LocalConversation):
    """Return a fake ``Agent.step`` that simulates a strict provider.

    Anthropic (and friends) surface the offending offset as
    ``messages.<N>.content.0.tool_result.content.1.image.source.base64:
    invalid base64 data`` — the shape the rollback classifier keys on. This
    stand-in raises that error whenever the corrupted URL is still present in
    the formatted request.
    """
    finished_status = convo.state.execution_status.__class__.FINISHED
    calls = {"n": 0}

    def fake_step(self, conversation, on_event, on_token=None):
        calls["n"] += 1
        formatted = self.llm.format_messages_for_llm(
            [
                e.to_llm_message()
                for e in conversation.state.view.events
                if hasattr(e, "to_llm_message")
            ]
        )
        if "<secret-hidden>" in str(formatted):
            raise LLMHistoryContentRejectedError(
                "messages.1.content.1.image.source.base64: invalid base64 data"
            )
        conversation.state.execution_status = finished_status

    fake_step.calls = calls  # type: ignore[attr-defined]
    return fake_step


def test_prevention_lets_the_turn_complete_without_rollback(
    agent, tmp_path, monkeypatch
):
    convo = Conversation(agent=agent, workspace=str(tmp_path), persistence_dir=None)
    assert isinstance(convo, LocalConversation)
    convo.send_message("take a screenshot")
    _append_poisoned_turn(convo)
    poisoned_ids = {e.id for e in convo.state.view.events}

    fake_step = _rejects_when_corrupted_url_reaches_wire(convo)
    monkeypatch.setattr(Agent, "step", fake_step)

    convo.run()

    assert convo.state.execution_status.name == "FINISHED"
    # A single step suffices — no rollback-driven retry.
    assert fake_step.calls["n"] == 1  # type: ignore[attr-defined]

    # The poisoned events are still on the active branch: prevention doesn't
    # touch persisted state, it only scrubs the outgoing request.
    view_ids = {e.id for e in convo.state.view.events}
    assert poisoned_ids <= view_ids

    # And no rollback-recovery explanation was injected.
    injected = [
        e
        for e in convo.state.view.events
        if isinstance(e, MessageEvent)
        and e.source == "environment"
        and "invalid base64"
        in "".join(c.text for c in e.llm_message.content if isinstance(c, TextContent))
    ]
    assert injected == []


def test_disabling_prevention_falls_through_to_rollback(agent, tmp_path, monkeypatch):
    """The safety net stays intact when prevention is turned off."""
    agent = Agent(
        llm=LLM(
            model="gpt-4o-mini",
            api_key=SecretStr("k"),
            usage_id="t",
            drop_undecodable_images=False,
        )
    )
    convo = Conversation(agent=agent, workspace=str(tmp_path), persistence_dir=None)
    assert isinstance(convo, LocalConversation)
    convo.send_message("take a screenshot")
    _append_poisoned_turn(convo)

    fake_step = _rejects_when_corrupted_url_reaches_wire(convo)
    monkeypatch.setattr(Agent, "step", fake_step)

    convo.run()

    # First step raises, rollback recovery kicks in, second step succeeds.
    assert fake_step.calls["n"] == 2  # type: ignore[attr-defined]
    assert convo.state.execution_status.name == "FINISHED"

    # Rollback injected the explanation message the recovery path emits.
    injected_texts = [
        "".join(c.text for c in e.llm_message.content if isinstance(c, TextContent))
        for e in convo.state.view.events
        if isinstance(e, MessageEvent) and e.source == "environment"
    ]
    assert any("invalid base64" in t for t in injected_texts)


def test_prevention_leaves_a_placeholder_the_agent_can_see(agent, tmp_path):
    """The dropped image is replaced with a text placeholder, not silently gone.

    A silent drop would leave the agent's model of the world out of sync with
    what the provider actually got; the placeholder tells it something was
    attached and did not make it through.
    """
    convo = Conversation(agent=agent, workspace=str(tmp_path), persistence_dir=None)
    assert isinstance(convo, LocalConversation)
    convo.send_message("take a screenshot")
    _append_poisoned_turn(convo)

    messages = [
        e.to_llm_message()
        for e in convo.state.view.events
        if isinstance(e, (ActionEvent, ObservationEvent, MessageEvent))
    ]
    formatted = agent.llm.format_messages_for_llm(messages)

    assert UNDECODABLE_IMAGE_PLACEHOLDER in str(formatted)
    assert "<secret-hidden>" not in str(formatted)


def test_prevention_preserves_valid_images_alongside_corrupt_ones(agent, tmp_path):
    """A turn with one good screenshot and one poisoned URL keeps the good one."""
    convo = Conversation(agent=agent, workspace=str(tmp_path), persistence_dir=None)
    assert isinstance(convo, LocalConversation)
    convo.send_message("take screenshots")

    action = ActionEvent(
        thought=[TextContent(text="looking")],
        action=PreventionTestAction(),
        tool_name="browser",
        tool_call_id="call_1",
        tool_call=MessageToolCall(
            id="call_1",
            name="browser",
            arguments="{}",
            origin="completion",
        ),
        llm_response_id="resp_1",
    )
    convo._on_event(action)

    convo._on_event(
        ObservationEvent(
            action_id=action.id,
            observation=PreventionTestMixedObservation(),
            tool_name="browser",
            tool_call_id="call_1",
        )
    )

    messages = [
        e.to_llm_message()
        for e in convo.state.view.events
        if isinstance(e, (ActionEvent, ObservationEvent, MessageEvent))
    ]
    formatted = str(agent.llm.format_messages_for_llm(messages))

    assert VALID_URL in formatted
    assert "<secret-hidden>" not in formatted
    assert UNDECODABLE_IMAGE_PLACEHOLDER in formatted
