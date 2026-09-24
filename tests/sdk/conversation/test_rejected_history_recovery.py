"""A provider rejection of persisted content must not wedge the run (#5225).

Before this recovery existed, the offending events stayed on the active branch,
so every retry rebuilt the same doomed request and the conversation was stuck
in ``ConversationExecutionStatus.ERROR`` for good.
"""

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
from openhands.sdk.llm.exceptions import (
    LLMBadRequestError,
    LLMHistoryContentRejectedError,
)
from openhands.sdk.tool import Action, Observation


REJECTION = (
    "litellm.BadRequestError: AnthropicException - messages.148.content.0."
    "tool_result.content.1.image.source.base64: invalid base64 data"
)


class RollbackTestAction(Action):
    command: str = "noop"


class RollbackTestObservation(Observation):
    result: str = "ok"

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.result)]


@pytest.fixture
def agent() -> Agent:
    return Agent(llm=LLM(model="gpt-4o-mini", api_key=SecretStr("k"), usage_id="t"))


def _poisoned_turn(convo: LocalConversation, response_id: str, count: int = 1) -> None:
    """Append ``count`` parallel tool calls plus their results."""
    for i in range(count):
        action = ActionEvent(
            thought=[TextContent(text="looking")],
            action=RollbackTestAction(),
            tool_name="browser",
            tool_call_id=f"call_{response_id}_{i}",
            tool_call=MessageToolCall(
                id=f"call_{response_id}_{i}",
                name="browser",
                arguments="{}",
                origin="completion",
            ),
            llm_response_id=response_id,
        )
        convo._on_event(action)
        convo._on_event(
            ObservationEvent(
                action_id=action.id,
                observation=RollbackTestObservation(),
                tool_name="browser",
                tool_call_id=action.tool_call_id,
            )
        )


def test_run_recovers_instead_of_wedging(agent, tmp_path, monkeypatch):
    convo = Conversation(agent=agent, workspace=str(tmp_path), persistence_dir=None)
    assert isinstance(convo, LocalConversation)
    convo.send_message("take a screenshot")
    _poisoned_turn(convo, "resp1")
    poisoned_ids = {e.id for e in convo.state.view.events}

    calls = {"n": 0}

    def fake_step(self, conversation, on_event, on_token=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMHistoryContentRejectedError(REJECTION)
        conversation.state.execution_status = (
            conversation.state.execution_status.__class__.FINISHED
        )

    monkeypatch.setattr(Agent, "step", fake_step)
    convo.run()

    # The run completed rather than ending in ERROR.
    assert convo.state.execution_status.name == "FINISHED"
    assert calls["n"] == 2

    view_ids = {e.id for e in convo.state.view.events}
    # The poisoned action/observation pair left the active branch...
    assert not (poisoned_ids & view_ids) or all(
        not isinstance(e, (ActionEvent, ObservationEvent))
        for e in convo.state.view.events
    )
    # ...but nothing was deleted from the persisted log.
    all_ids = {e.id for e in convo.state.events}
    assert poisoned_ids <= all_ids


def test_agent_is_told_what_failed_and_what_already_ran(agent, tmp_path, monkeypatch):
    convo = Conversation(agent=agent, workspace=str(tmp_path), persistence_dir=None)
    assert isinstance(convo, LocalConversation)
    convo.send_message("go")
    _poisoned_turn(convo, "resp1", count=3)

    calls = {"n": 0}

    def fake_step(self, conversation, on_event, on_token=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMHistoryContentRejectedError(REJECTION)
        conversation.state.execution_status = (
            conversation.state.execution_status.__class__.FINISHED
        )

    monkeypatch.setattr(Agent, "step", fake_step)
    convo.run()

    injected = [
        e
        for e in convo.state.view.events
        if isinstance(e, MessageEvent) and e.source == "environment"
    ]
    assert injected, "expected an injected explanation on the active branch"
    text = "".join(
        c.text for c in injected[-1].llm_message.content if isinstance(c, TextContent)
    )
    assert "invalid base64" in text
    # All three parallel calls are accounted for.
    assert text.count("browser (succeeded)") == 3
    assert "still exist" in text


def test_parallel_turn_rolls_back_as_a_unit(agent, tmp_path, monkeypatch):
    """No ActionEvent may survive without its paired result, or the next
    request trips LLMMalformedConversationHistoryError instead."""
    convo = Conversation(agent=agent, workspace=str(tmp_path), persistence_dir=None)
    assert isinstance(convo, LocalConversation)
    convo.send_message("go")
    _poisoned_turn(convo, "resp1", count=3)

    calls = {"n": 0}

    def fake_step(self, conversation, on_event, on_token=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMHistoryContentRejectedError(REJECTION)
        conversation.state.execution_status = (
            conversation.state.execution_status.__class__.FINISHED
        )

    monkeypatch.setattr(Agent, "step", fake_step)
    convo.run()

    survivors = [e for e in convo.state.view.events if isinstance(e, ActionEvent)]
    assert survivors == [], "the whole parallel turn should have been rolled back"


def test_repeat_failure_surfaces_instead_of_unwinding_further(
    agent, tmp_path, monkeypatch
):
    convo = Conversation(agent=agent, workspace=str(tmp_path), persistence_dir=None)
    assert isinstance(convo, LocalConversation)
    convo.send_message("go")
    _poisoned_turn(convo, "resp1")
    _poisoned_turn(convo, "resp2")

    def always_rejects(self, conversation, on_event, on_token=None):
        raise LLMHistoryContentRejectedError(REJECTION)

    monkeypatch.setattr(Agent, "step", always_rejects)

    with pytest.raises(Exception):
        convo.run()

    assert convo.state.execution_status.name == "ERROR"


def test_plain_config_bad_request_is_not_rolled_back(agent, tmp_path, monkeypatch):
    """A configuration error is not fixed by discarding history; it must keep
    its existing terminal behaviour rather than silently eating a turn."""
    convo = Conversation(agent=agent, workspace=str(tmp_path), persistence_dir=None)
    assert isinstance(convo, LocalConversation)
    convo.send_message("go")
    _poisoned_turn(convo, "resp1")
    before = [e.id for e in convo.state.view.events]

    def bad_config(self, conversation, on_event, on_token=None):
        raise LLMBadRequestError("Unsupported parameter: 'temperature'")

    monkeypatch.setattr(Agent, "step", bad_config)

    with pytest.raises(Exception):
        convo.run()

    assert convo.state.execution_status.name == "ERROR"
    # History untouched apart from the error event the run loop records.
    assert before == [e.id for e in convo.state.view.events if e.id in set(before)]
    assert not any(
        isinstance(e, MessageEvent) and e.source == "environment"
        for e in convo.state.view.events
    )
