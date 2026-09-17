from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from pydantic import PrivateAttr

import openhands.sdk.agent.agent as agent_mod
from openhands.sdk.agent import Agent
from openhands.sdk.context.condenser.base import CondenserBase
from openhands.sdk.context.view import View
from openhands.sdk.conversation import Conversation
from openhands.sdk.event.condenser import CondensationRequest
from openhands.sdk.llm import LLM, ModelRuntimeMetadata
from openhands.sdk.llm.exceptions import (
    LLMContextWindowExceedError,
    LLMMalformedConversationHistoryError,
)
from openhands.sdk.llm.utils import runtime_metadata as rm


if TYPE_CHECKING:
    from openhands.sdk.event.condenser import Condensation


class PreflightLLM(LLM):
    """OpenRouter LLM that seeds a route-aware runtime limit on resolution.

    Mirrors what a real provider-aware resolution would publish so tests can
    assert the agent establishes the route before the condenser sees the
    context window (the regression for review re: wiring).
    """

    _route_limit: int = PrivateAttr(default=262144)

    def __init__(self, *, route_limit: int = 262144):
        super().__init__(
            model="openrouter/deepseek/deepseek-v4-flash-0731",
            usage_id="test-llm",
        )
        self._route_limit = route_limit

    def _seed(self) -> ModelRuntimeMetadata:
        self._runtime_metadata = ModelRuntimeMetadata(
            max_input_tokens=self._route_limit, source="test"
        )
        self._runtime_metadata_fetched_at = rm.time.monotonic()
        return self._runtime_metadata

    def resolve_runtime_metadata(self, *, force: bool = False):  # type: ignore[override]
        return self._seed()

    async def aresolve_runtime_metadata(self, *, force: bool = False):  # type: ignore[override]
        return self._seed()

    def completion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMContextWindowExceedError()

    def responses(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMContextWindowExceedError()

    async def acompletion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMContextWindowExceedError()

    async def aresponses(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMContextWindowExceedError()


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


# ---------------------------------------------------------------------------
# Route metadata must be established before the condenser's token decision.
# See: https://github.com/OpenHands/software-agent-sdk/issues/4421
# ---------------------------------------------------------------------------


def test_step_resolves_runtime_metadata_before_condensation(monkeypatch):
    llm = PreflightLLM(route_limit=262144)
    agent = Agent(llm=llm, tools=[], condenser=HandlesRequestsCondenser())
    convo = Conversation(agent=agent)
    convo._ensure_agent_ready()

    observed = {}
    original = agent_mod.prepare_llm_messages

    def spy(view, *, condenser=None, llm=None):
        assert llm is not None
        observed["resolved"] = llm.resolved_runtime_metadata is not None
        observed["limit"] = llm.effective_max_input_tokens
        return original(view, condenser=condenser, llm=llm)

    monkeypatch.setattr(agent_mod, "prepare_llm_messages", spy)

    agent.step(convo, on_event=lambda e: None)

    # Resolution must have run before prepare_llm_messages so the condenser /
    # condensation logic sees the route-aware context window on the first step.
    assert observed.get("resolved") is True
    assert observed.get("limit") == 262144


@pytest.mark.asyncio
async def test_astep_resolves_runtime_metadata_before_condensation(monkeypatch):
    llm = PreflightLLM(route_limit=262144)
    agent = Agent(llm=llm, tools=[], condenser=HandlesRequestsCondenser())
    convo = Conversation(agent=agent)
    convo._ensure_agent_ready()

    observed = {}
    original = agent_mod.aprepare_llm_messages

    async def spy(view, *, condenser=None, llm=None):
        assert llm is not None
        observed["resolved"] = llm.resolved_runtime_metadata is not None
        observed["limit"] = llm.effective_max_input_tokens
        return await original(view, condenser=condenser, llm=llm)

    monkeypatch.setattr(agent_mod, "aprepare_llm_messages", spy)

    await agent.astep(convo, on_event=lambda e: None)

    assert observed.get("resolved") is True
    assert observed.get("limit") == 262144
