"""SecurityAnalysisEvent: the analyzer's verdict is recorded on every analyzed
batch of actions, independent of what the confirmation policy does with it."""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Self
from unittest.mock import patch

import pytest
from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
    Usage,
)
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, ObservationEvent, SecurityAnalysisEvent
from openhands.sdk.event.base import Event
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.security import SecurityAnalysis, SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import ConfirmRisky, NeverConfirm
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.tool import Action, Observation, Tool, ToolExecutor, register_tool
from openhands.sdk.tool.tool import ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


class NoopAction(Action):
    command: str = ""


class NoopObservation(Observation):
    result: str = ""


class NoopExecutor(ToolExecutor[NoopAction, NoopObservation]):
    def __call__(self, action: NoopAction, conversation=None) -> NoopObservation:
        return NoopObservation(result="ok")


class NoopTool(ToolDefinition[NoopAction, NoopObservation]):
    name = "noop_tool"

    @classmethod
    def create(cls, conv_state: "ConversationState | None" = None) -> Sequence[Self]:
        return [
            cls(
                description="A tool that does nothing",
                action_type=NoopAction,
                observation_type=NoopObservation,
                executor=NoopExecutor(),
            )
        ]


register_tool("SecurityAnalysisNoopTool", NoopTool)


class DetailedAnalyzer(SecurityAnalyzerBase):
    """Rates every action MEDIUM and explains itself."""

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        return SecurityRisk.MEDIUM

    def analyze_action(self, action: ActionEvent) -> SecurityAnalysis:
        return SecurityAnalysis(
            risk=SecurityRisk.MEDIUM,
            details={"confidence": 0.42, "rationale": "test"},
        )


class ExplodingAnalyzer(SecurityAnalyzerBase):
    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        raise RuntimeError("analyzer down")


class LegacyBatchAnalyzer(SecurityAnalyzerBase):
    delegate: bool = False

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        return SecurityRisk.LOW

    def analyze_pending_actions(
        self, pending_actions: list[ActionEvent]
    ) -> list[tuple[ActionEvent, SecurityRisk]]:
        if self.delegate:
            pending_actions = [
                action for action, _ in super().analyze_pending_actions(pending_actions)
            ]
        return [(action, SecurityRisk.HIGH) for action in pending_actions]


_SYNTHETIC_SECRET = "qa-fake-secret-5182-not-real"


class SecretDetailAnalyzer(DetailedAnalyzer):
    fail: bool = False

    def analyze_action(self, action: ActionEvent) -> SecurityAnalysis:
        if self.fail:
            raise RuntimeError(f"upstream rejected credential {_SYNTHETIC_SECRET}")
        return SecurityAnalysis(
            risk=SecurityRisk.MEDIUM,
            details={"nested": [{"rationale": _SYNTHETIC_SECRET}], "confidence": 0.42},
        )


def _llm() -> LLM:
    return LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )


_TOOL_ARGS = '{"command": "x", "security_risk": "LOW"}'


def _tool_call_response(n_calls: int = 1):
    def mock(messages, **kwargs):
        return ModelResponse(
            id="mock-1",
            choices=[
                Choices(
                    index=0,
                    message=LiteLLMMessage(
                        role="assistant",
                        content="Running the tool.",
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id=f"call_{i}",
                                type="function",
                                function=Function(
                                    name="noop_tool",
                                    arguments=_TOOL_ARGS,
                                ),
                            )
                            for i in range(n_calls)
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            created=0,
            model="test-model",
            object="chat.completion",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    return mock


def _run_step(
    analyzer,
    policy,
    n_calls: int = 1,
    persistence_dir: str | None = None,
    secrets: dict[str, str] | None = None,
):
    agent = Agent(llm=_llm(), tools=[Tool(name="SecurityAnalysisNoopTool")])
    events: list[Event] = []
    conversation = Conversation(
        agent=agent,
        callbacks=[events.append],
        persistence_dir=persistence_dir,
        visualizer=None,
    )
    if secrets is not None:
        conversation.update_secrets(secrets)
    if analyzer is not None:
        conversation.set_security_analyzer(analyzer)
    conversation.set_confirmation_policy(policy)
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        side_effect=_tool_call_response(n_calls),
    ):
        conversation.send_message(
            Message(role="user", content=[TextContent(text="go")])
        )
        with conversation.state:
            agent.step(conversation, on_event=conversation._on_event)
    return conversation, events


def test_no_analyzer_emits_no_security_analysis_event():
    _, events = _run_step(analyzer=None, policy=NeverConfirm())
    assert [e for e in events if isinstance(e, SecurityAnalysisEvent)] == []


def test_analyzer_verdict_is_recorded_even_when_policy_never_confirms():
    conversation, events = _run_step(LLMSecurityAnalyzer(), NeverConfirm())

    analyses = [e for e in events if isinstance(e, SecurityAnalysisEvent)]
    actions = [e for e in events if isinstance(e, ActionEvent)]
    assert len(analyses) == 1
    assert len(actions) == 1

    event = analyses[0]
    assert event.source == "environment"
    assert event.analyzer == "LLMSecurityAnalyzer"
    assert event.policy == "NeverConfirm"
    # LLMSecurityAnalyzer relays the LLM's own label, which the mock set to LOW.
    assert event.risks == {actions[0].id: SecurityRisk.LOW}
    assert event.details == {}

    # The policy still ran the action: the verdict was recorded, not enforced.
    assert any(isinstance(e, ObservationEvent) for e in events)
    assert (
        conversation.state.execution_status
        != ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )


def test_event_precedes_confirmation_wait_and_carries_analyzer_details():
    conversation, events = _run_step(
        DetailedAnalyzer(), ConfirmRisky(threshold=SecurityRisk.MEDIUM)
    )

    analyses = [e for e in events if isinstance(e, SecurityAnalysisEvent)]
    actions = [e for e in events if isinstance(e, ActionEvent)]
    assert len(analyses) == 1
    event = analyses[0]
    assert event.analyzer == "DetailedAnalyzer"
    assert event.policy == "ConfirmRisky"
    assert event.risks == {actions[0].id: SecurityRisk.MEDIUM}
    assert event.details == {actions[0].id: {"confidence": 0.42, "rationale": "test"}}

    # This time the policy consulted the risk and paused.
    assert (
        conversation.state.execution_status
        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )
    assert not any(isinstance(e, ObservationEvent) for e in events)


def test_one_event_per_batch_keyed_by_action_id():
    _, events = _run_step(DetailedAnalyzer(), NeverConfirm(), n_calls=3)

    analyses = [e for e in events if isinstance(e, SecurityAnalysisEvent)]
    actions = [e for e in events if isinstance(e, ActionEvent)]
    assert len(actions) == 3
    assert len(analyses) == 1
    assert set(analyses[0].risks) == {a.id for a in actions}
    assert set(analyses[0].details) == {a.id for a in actions}


def test_analyzer_error_defaults_to_high_with_error_detail():
    _, events = _run_step(ExplodingAnalyzer(), NeverConfirm())

    (event,) = [e for e in events if isinstance(e, SecurityAnalysisEvent)]
    (action,) = [e for e in events if isinstance(e, ActionEvent)]
    assert event.risks == {action.id: SecurityRisk.HIGH}
    assert event.details == {action.id: {"error": "analyzer down"}}


def test_event_round_trips_through_the_event_union():
    event = SecurityAnalysisEvent(
        analyzer="X",
        policy="NeverConfirm",
        risks={"a": SecurityRisk.HIGH},
        details={"a": {"p": [0.1, 0.9]}},
    )
    restored = Event.model_validate_json(event.model_dump_json())
    assert isinstance(restored, SecurityAnalysisEvent)
    assert restored == event
    assert "HIGH" in restored.visualize.plain


def test_analyze_pending_actions_keeps_its_risk_only_shape():
    """The pre-existing method is a compatibility wrapper over analyze_actions."""
    analyzer = DetailedAnalyzer()
    _, events = _run_step(analyzer, NeverConfirm())
    (action,) = [e for e in events if isinstance(e, ActionEvent)]
    assert analyzer.analyze_pending_actions([action]) == [(action, SecurityRisk.MEDIUM)]


@pytest.mark.parametrize("delegate", [False, True])
def test_legacy_batch_verdict_controls_confirmation_and_audit(delegate: bool):
    conversation, events = _run_step(
        LegacyBatchAnalyzer(delegate=delegate), ConfirmRisky()
    )
    (event,) = [e for e in events if isinstance(e, SecurityAnalysisEvent)]
    (action,) = [e for e in events if isinstance(e, ActionEvent)]
    assert event.risks == {action.id: SecurityRisk.HIGH}
    assert (
        conversation.state.execution_status
        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )
    assert not any(isinstance(e, ObservationEvent) for e in events)
    conversation.close()


@pytest.mark.parametrize("fail", [False, True])
def test_analysis_secrets_masked_in_callbacks_and_persistence(
    tmp_path: Path, fail: bool
):
    conversation, events = _run_step(
        SecretDetailAnalyzer(fail=fail),
        ConfirmRisky(threshold=SecurityRisk.MEDIUM),
        persistence_dir=str(tmp_path),
        secrets={"QA_SYNTHETIC": _SYNTHETIC_SECRET},
    )
    (event,) = [e for e in events if isinstance(e, SecurityAnalysisEvent)]
    (action,) = [e for e in events if isinstance(e, ActionEvent)]
    expected_risk = SecurityRisk.HIGH if fail else SecurityRisk.MEDIUM
    assert event.risks == {action.id: expected_risk}
    expected_details = (
        {"error": "upstream rejected credential <secret-hidden>"}
        if fail
        else {"nested": [{"rationale": "<secret-hidden>"}], "confidence": 0.42}
    )
    assert event.details == {action.id: expected_details}
    assert (
        conversation.state.execution_status
        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )
    conversation.close()

    reopened = Conversation(
        agent=conversation.agent,
        conversation_id=conversation.id,
        persistence_dir=str(tmp_path),
        visualizer=None,
    )
    try:
        with reopened.state:
            restored = [
                e for e in reopened.state.events if isinstance(e, SecurityAnalysisEvent)
            ]
        assert restored == [event]
    finally:
        reopened.close()
    event_files = list(tmp_path.rglob("events/*.json"))
    assert event_files
    assert all(_SYNTHETIC_SECRET not in path.read_text() for path in event_files)
