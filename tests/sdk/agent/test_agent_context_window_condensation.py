import pytest
from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.context.condenser.base import CondenserBase
from openhands.sdk.context.view import View
from openhands.sdk.conversation import Conversation
from openhands.sdk.event.condenser import CondensationRequest
from openhands.sdk.llm import LLM
from openhands.sdk.llm.exceptions import LLMContextWindowExceedError


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


class HandlesRequestsCondenser(CondenserBase):
    def condense(self, view: View):  # pragma: no cover - trivial passthrough
        return view

    def handles_condensation_requests(self) -> bool:
        return True


@pytest.mark.parametrize("force_responses", [True, False])
def test_agent_triggers_condensation_request_when_ctx_exceeded_with_condenser(
    force_responses: bool,
):
    llm = RaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[], condenser=HandlesRequestsCondenser())
    convo = Conversation(agent=agent)

    seen = []

    def on_event(e):
        seen.append(e)

    # Expect Agent to emit CondensationRequest and not raise
    agent.step(convo, on_event=on_event)

    assert any(isinstance(e, CondensationRequest) for e in seen)


@pytest.mark.parametrize("force_responses", [True, False])
def test_agent_raises_ctx_exceeded_when_no_condenser(force_responses: bool):
    llm = RaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[], condenser=None)
    convo = Conversation(agent=agent)

    with pytest.raises(LLMContextWindowExceedError):
        agent.step(convo, on_event=lambda e: None)


@pytest.mark.parametrize("force_responses", [True, False])
def test_agent_logs_warning_when_no_condenser_on_ctx_exceeded(
    force_responses: bool, caplog
):
    """Test that warning is logged when context window exceeded without condenser."""  # noqa: E501
    llm = RaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[], condenser=None)
    convo = Conversation(agent=agent)

    with pytest.raises(LLMContextWindowExceedError):
        agent.step(convo, on_event=lambda e: None)

    # Check that warning was logged
    assert any(
        "CONTEXT WINDOW EXCEEDED ERROR" in record.message for record in caplog.records
    )
    assert any(
        "no condenser is configured" in record.message for record in caplog.records
    )
    assert any("Condenser: None" in record.message for record in caplog.records)
    assert any("test-model" in record.message for record in caplog.records)


class NoHandlesRequestsCondenser(CondenserBase):
    """A condenser that doesn't handle condensation requests."""

    def condense(self, view: View):  # pragma: no cover - trivial passthrough
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

    with pytest.raises(LLMContextWindowExceedError):
        agent.step(convo, on_event=lambda e: None)

    # Check that warning was logged with condenser info
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
