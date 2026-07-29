from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.context.condenser.base import (
    CondensationRequirement,
    CondenserBase,
    RollingCondenser,
)
from openhands.sdk.context.view import View
from openhands.sdk.conversation import Conversation
from openhands.sdk.event.condenser import Condensation, CondensationRequest
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.llm import LLM
from openhands.sdk.llm.exceptions import (
    LLMContextWindowExceedError,
    LLMMalformedConversationHistoryError,
)


if TYPE_CHECKING:
    from openhands.sdk.event.condenser import Condensation


class RaisingLLM(LLM):
    _force_responses: bool = PrivateAttr(default=False)

    def __init__(self, *, model: str = "test-model", force_responses: bool = False):
        super().__init__(model=model, usage_id="test-llm")
        self._force_responses = force_responses

    def uses_responses_api(self) -> bool:  # override gating
        return self._force_responses

    def completion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMContextWindowExceedError()

    def responses(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMContextWindowExceedError()


class MalformedHistoryRaisingLLM(LLM):
    _force_responses: bool = PrivateAttr(default=False)

    def __init__(self, *, model: str = "test-model", force_responses: bool = False):
        super().__init__(model=model, usage_id="test-llm")
        self._force_responses = force_responses

    def uses_responses_api(self) -> bool:  # override gating
        return self._force_responses

    def completion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMMalformedConversationHistoryError(
            "messages.134: `tool_use` ids were found without `tool_result` blocks "
            "immediately after"
        )

    async def acompletion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMMalformedConversationHistoryError(
            "messages.134: `tool_use` ids were found without `tool_result` blocks "
            "immediately after"
        )

    def responses(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMMalformedConversationHistoryError(
            "messages.134: `tool_use` ids were found without `tool_result` blocks "
            "immediately after"
        )

    async def aresponses(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMMalformedConversationHistoryError(
            "messages.134: `tool_use` ids were found without `tool_result` blocks "
            "immediately after"
        )


class HandlesRequestsCondenser(CondenserBase):
    def condense(
        self, view: View, agent_llm: "LLM | None" = None
    ) -> "View | Condensation":  # pragma: no cover - trivial passthrough
        return view

    def handles_condensation_requests(self) -> bool:
        return True


class AlwaysCondensingCondenser(RollingCondenser):
    fail: bool = False

    def condensation_requirement(
        self, view: View, agent_llm: LLM | None = None
    ) -> CondensationRequirement | None:
        return CondensationRequirement.SOFT

    def get_condensation(
        self, view: View, agent_llm: LLM | None = None
    ) -> Condensation:
        if self.fail:
            raise RuntimeError("summary failed")
        return Condensation(
            forgotten_event_ids=set(),
            llm_response_id="summary-response",
        )


def _condensation_statuses(events: list) -> list[str]:
    return [
        event.value["status"]
        for event in events
        if isinstance(event, ConversationStateUpdateEvent)
        and event.key == "context_condensation"
    ]


def test_agent_emits_condensation_lifecycle_events(caplog) -> None:
    agent = Agent(
        llm=LLM(model="test-model", usage_id="test-llm"),
        tools=[],
        condenser=AlwaysCondensingCondenser(),
    )
    conversation = Conversation(agent=agent)
    conversation._ensure_agent_ready()
    seen: list = []

    agent.step(conversation, on_event=seen.append)

    assert _condensation_statuses(seen) == ["started", "completed"]
    condensation_index, condensation = next(
        (index, event)
        for index, event in enumerate(seen)
        if isinstance(event, Condensation)
    )
    completed_index = next(
        index
        for index, event in enumerate(seen)
        if isinstance(event, ConversationStateUpdateEvent)
        and event.key == "context_condensation"
        and event.value["status"] == "completed"
    )
    assert condensation_index < completed_index
    assert any(
        f"event_id={condensation.id}" in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_agent_emits_async_condensation_lifecycle_events() -> None:
    agent = Agent(
        llm=LLM(model="test-model", usage_id="test-llm"),
        tools=[],
        condenser=AlwaysCondensingCondenser(),
    )
    conversation = Conversation(agent=agent)
    conversation._ensure_agent_ready()
    seen: list = []

    await agent.astep(conversation, on_event=seen.append)

    assert _condensation_statuses(seen) == ["started", "completed"]


def test_agent_clears_condensation_status_after_failure() -> None:
    agent = Agent(
        llm=LLM(model="test-model", usage_id="test-llm"),
        tools=[],
        condenser=AlwaysCondensingCondenser(fail=True),
    )
    conversation = Conversation(agent=agent)
    conversation._ensure_agent_ready()
    seen: list = []

    with pytest.raises(RuntimeError, match="summary failed"):
        agent.step(conversation, on_event=seen.append)

    assert _condensation_statuses(seen) == ["started", "failed"]


@pytest.mark.parametrize("force_responses", [True, False])
def test_agent_triggers_condensation_request_when_ctx_exceeded_with_condenser(
    force_responses: bool,
):
    llm = RaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[], condenser=HandlesRequestsCondenser())
    convo = Conversation(agent=agent)

    convo._ensure_agent_ready()

    seen = []

    def on_event(e):
        seen.append(e)

    agent.step(convo, on_event=on_event)

    assert any(isinstance(e, CondensationRequest) for e in seen)


@pytest.mark.parametrize("force_responses", [True, False])
def test_agent_triggers_condensation_request_when_history_is_malformed(
    force_responses: bool,
    caplog,
):
    llm = MalformedHistoryRaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[], condenser=HandlesRequestsCondenser())
    convo = Conversation(agent=agent)

    convo._ensure_agent_ready()

    seen = []

    def on_event(e):
        seen.append(e)

    agent.step(convo, on_event=on_event)

    assert any(isinstance(e, CondensationRequest) for e in seen)
    assert any(
        "malformed conversation history error" in record.message
        for record in caplog.records
    )
    assert any(
        "triggering condensation retry with condensed history" in record.message
        for record in caplog.records
    )


@pytest.mark.parametrize("force_responses", [True, False])
def test_agent_raises_ctx_exceeded_when_no_condenser(force_responses: bool):
    llm = RaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[], condenser=None)
    convo = Conversation(agent=agent)

    convo._ensure_agent_ready()

    with pytest.raises(LLMContextWindowExceedError):
        agent.step(convo, on_event=lambda e: None)


@pytest.mark.parametrize("force_responses", [True, False])
def test_agent_raises_malformed_history_error_when_no_condenser(
    force_responses: bool,
    caplog,
):
    llm = MalformedHistoryRaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[], condenser=None)
    convo = Conversation(agent=agent)

    convo._ensure_agent_ready()

    with pytest.raises(LLMMalformedConversationHistoryError):
        agent.step(convo, on_event=lambda e: None)

    assert any(
        "malformed conversation history error but no condenser can handle "
        "condensation requests" in record.message
        for record in caplog.records
    )
    assert any(
        "event-stream or resume bug" in record.message for record in caplog.records
    )


@pytest.mark.parametrize("force_responses", [True, False])
def test_agent_logs_warning_when_no_condenser_on_ctx_exceeded(
    force_responses: bool, caplog
):
    """Test that warning is logged when context window exceeded without condenser."""
    llm = RaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[], condenser=None)
    convo = Conversation(agent=agent)

    convo._ensure_agent_ready()

    with pytest.raises(LLMContextWindowExceedError):
        agent.step(convo, on_event=lambda e: None)

    assert any(
        "CONTEXT WINDOW EXCEEDED ERROR" in record.message for record in caplog.records
    )
    assert any(
        "no condenser is configured" in record.message for record in caplog.records
    )
    assert any("Condenser: None" in record.message for record in caplog.records)
    assert any("test-model" in record.message for record in caplog.records)


@pytest.mark.parametrize("force_responses", [True, False])
def test_agent_rebuilds_view_on_malformed_history_recovery(
    force_responses: bool,
):
    """rebuild_view is called before CondensationRequest on malformed history."""
    llm = MalformedHistoryRaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[], condenser=HandlesRequestsCondenser())
    convo = Conversation(agent=agent)
    convo._ensure_agent_ready()

    seen: list = []
    with patch.object(
        type(convo._state),
        "rebuild_view",
        wraps=convo._state.rebuild_view,
    ) as mock_rebuild:
        agent.step(convo, on_event=lambda e: seen.append(e))
        assert mock_rebuild.call_count == 1

    assert any(isinstance(e, CondensationRequest) for e in seen)


@pytest.mark.parametrize("force_responses", [True, False])
@pytest.mark.asyncio
async def test_agent_rebuilds_view_on_malformed_history_recovery_async(
    force_responses: bool,
):
    """Async parity: astep calls rebuild_view before condensation retry."""
    llm = MalformedHistoryRaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[], condenser=HandlesRequestsCondenser())
    convo = Conversation(agent=agent)
    convo._ensure_agent_ready()

    seen: list = []
    with patch.object(
        type(convo._state),
        "rebuild_view",
        wraps=convo._state.rebuild_view,
    ) as mock_rebuild:
        await agent.astep(convo, on_event=lambda e: seen.append(e))
        assert mock_rebuild.call_count == 1

    assert any(isinstance(e, CondensationRequest) for e in seen)


class NoHandlesRequestsCondenser(CondenserBase):
    """A condenser that doesn't handle condensation requests."""

    def condense(
        self, view: View, agent_llm: "LLM | None" = None
    ) -> "View | Condensation":  # pragma: no cover - trivial passthrough
        return view

    def handles_condensation_requests(self) -> bool:
        return False


@pytest.mark.parametrize("force_responses", [True, False])
def test_agent_logs_warning_with_non_handling_condenser_on_ctx_exceeded(
    force_responses: bool, caplog
):
    """Test that a helpful warning is logged when condenser doesn't handle requests."""
    llm = RaisingLLM(force_responses=force_responses)
    condenser = NoHandlesRequestsCondenser()
    agent = Agent(llm=llm, tools=[], condenser=condenser)
    convo = Conversation(agent=agent)

    convo._ensure_agent_ready()

    with pytest.raises(LLMContextWindowExceedError):
        agent.step(convo, on_event=lambda e: None)

    assert any(
        "CONTEXT WINDOW EXCEEDED ERROR" in record.message for record in caplog.records
    )
    assert any(
        "does not handle condensation requests" in record.message
        for record in caplog.records
    )
    assert any(
        "NoHandlesRequestsCondenser" in record.message for record in caplog.records
    )
    assert any(
        "Handles Condensation Requests: False" in record.message
        for record in caplog.records
    )
