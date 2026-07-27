from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import MagicMock

from litellm.types.utils import ModelResponse
from pydantic import PrivateAttr

from openhands.sdk import LLM, Agent, AgentContext, Conversation, LocalConversation
from openhands.sdk.agent.skill_tool_permissions import effective_tools_for_state
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import AgentErrorEvent, ObservationEvent
from openhands.sdk.llm import (
    LLMResponse,
    Message,
    MessageToolCall,
    TextContent,
    TokenCallbackType,
)
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage
from openhands.sdk.skills import Skill
from openhands.sdk.tool import ToolDefinition
from openhands.sdk.tool.builtins import FinishTool, ThinkTool
from openhands.sdk.tool.registry import register_tool
from openhands.sdk.tool.spec import Tool
from openhands.sdk.tool.tool import Action, Observation, ToolExecutor


class _NoopAction(Action):
    pass


class _NoopObservation(Observation):
    pass


class _NoopExecutor(ToolExecutor[_NoopAction, _NoopObservation]):
    def __call__(
        self,
        action: _NoopAction,  # noqa: ARG002
        conversation=None,  # noqa: ARG002
    ) -> _NoopObservation:
        return _NoopObservation.from_text("done")


class _NoopTool(ToolDefinition[_NoopAction, _NoopObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[_NoopTool]:
        if params:
            raise ValueError("Noop tools do not accept parameters")
        return [
            cls(
                description=f"{cls.name} test tool",
                action_type=_NoopAction,
                observation_type=_NoopObservation,
                executor=_NoopExecutor(),
            )
        ]


class _AllowedAlphaTool(_NoopTool):
    name: ClassVar[str] = "allowed_alpha"


class _AllowedBetaTool(_NoopTool):
    name: ClassVar[str] = "allowed_beta"


class _BlockedGammaTool(_NoopTool):
    name: ClassVar[str] = "blocked_gamma"


class _TerminalTool(_NoopTool):
    name: ClassVar[str] = "terminal"


class _FileEditorTool(_NoopTool):
    name: ClassVar[str] = "file_editor"


register_tool(_AllowedAlphaTool.name, _AllowedAlphaTool)
register_tool(_AllowedBetaTool.name, _AllowedBetaTool)
register_tool(_BlockedGammaTool.name, _BlockedGammaTool)


def _metrics() -> MetricsSnapshot:
    return MetricsSnapshot(
        model_name="test-model",
        accumulated_cost=0.0,
        max_budget_per_task=0.0,
        accumulated_token_usage=TokenUsage(model="test-model"),
    )


class _RecordingLLM(LLM):
    _seen_tool_names: list[str] = PrivateAttr(default_factory=list)
    _tool_call_name: str | None = PrivateAttr(default=None)

    def __init__(self, tool_call_name: str | None = None):
        super().__init__(model="test-model", usage_id="test-llm")
        self._tool_call_name = tool_call_name

    @property
    def seen_tool_names(self) -> list[str]:
        return self._seen_tool_names

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        _return_metrics: bool = False,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self._seen_tool_names = [tool.name for tool in tools or []]
        if self._tool_call_name is None:
            message = Message(
                role="assistant",
                content=[TextContent(text="done")],
            )
        else:
            message = Message(
                role="assistant",
                tool_calls=[
                    MessageToolCall(
                        id="call-1",
                        name=self._tool_call_name,
                        arguments="{}",
                        origin="completion",
                    )
                ],
            )
        return LLMResponse(
            message=message,
            metrics=_metrics(),
            raw_response=MagicMock(spec=ModelResponse, id="response-1"),
        )


def _skill(name: str, allowed_tools: list[str] | None) -> Skill:
    return Skill(
        name=name,
        content="Skill content",
        description=f"{name} description",
        source=f"/skills/{name}/SKILL.md",
        is_agentskills_format=True,
        allowed_tools=allowed_tools,
    )


def _conversation_with_invoked_skill(
    llm: LLM,
    skill: Skill,
    tool_names: list[str],
) -> tuple[Agent, LocalConversation]:
    agent = Agent(
        llm=llm,
        tools=[Tool(name=name) for name in tool_names],
        agent_context=AgentContext(skills=[skill]),
    )
    conversation = Conversation(agent=agent, visualizer=None)
    conversation.send_message("use the skill")
    conversation.state.invoked_skills.append(skill.name)
    return agent, conversation


def _tool_map(tool_classes: Sequence[type[_NoopTool]]) -> dict[str, ToolDefinition]:
    tools: list[ToolDefinition] = [
        tool for tool_class in tool_classes for tool in tool_class.create()
    ]
    (finish_tool,) = FinishTool.create()
    (think_tool,) = ThinkTool.create()
    tools.extend([finish_tool, think_tool])
    return {tool.name: tool for tool in tools}


def _state_for_skills(skills: list[Skill], invoked_skills: list[str]) -> Any:
    return SimpleNamespace(
        agent=SimpleNamespace(agent_context=AgentContext(skills=skills)),
        invoked_skills=invoked_skills,
    )


def test_invoked_skill_allowed_tools_filter_tools_sent_to_llm():
    skill = _skill("restricted", allowed_tools=["allowed_alpha"])
    llm = _RecordingLLM()
    agent, conversation = _conversation_with_invoked_skill(
        llm,
        skill,
        [_AllowedAlphaTool.name, _BlockedGammaTool.name],
    )

    agent.step(conversation, on_event=lambda event: None)

    assert set(llm.seen_tool_names) == {"allowed_alpha", "finish", "think"}


def test_invoked_skill_without_allowed_tools_keeps_tools_unrestricted():
    skill = _skill("unrestricted", allowed_tools=None)
    llm = _RecordingLLM()
    agent, conversation = _conversation_with_invoked_skill(
        llm,
        skill,
        [_AllowedAlphaTool.name, _BlockedGammaTool.name],
    )

    agent.step(conversation, on_event=lambda event: None)

    assert _AllowedAlphaTool.name in llm.seen_tool_names
    assert _BlockedGammaTool.name in llm.seen_tool_names
    assert "invoke_skill" in llm.seen_tool_names


def test_disallowed_tool_call_is_rejected_at_runtime():
    skill = _skill("restricted", allowed_tools=["allowed_alpha"])
    llm = _RecordingLLM(tool_call_name=_BlockedGammaTool.name)
    agent, conversation = _conversation_with_invoked_skill(
        llm,
        skill,
        [_AllowedAlphaTool.name, _BlockedGammaTool.name],
    )
    events = []

    agent.step(conversation, on_event=events.append)

    errors = [event for event in events if isinstance(event, AgentErrorEvent)]
    assert len(errors) == 1
    assert "blocked_gamma" in errors[0].error
    assert "not available while skill(s) restricted are active" in errors[0].error


def test_allowed_tool_call_still_executes():
    skill = _skill("restricted", allowed_tools=["allowed_alpha"])
    llm = _RecordingLLM(tool_call_name=_AllowedAlphaTool.name)
    agent, conversation = _conversation_with_invoked_skill(
        llm,
        skill,
        [_AllowedAlphaTool.name, _BlockedGammaTool.name],
    )
    events = []

    agent.step(conversation, on_event=events.append)

    assert not any(isinstance(event, AgentErrorEvent) for event in events)
    assert any(isinstance(event, ObservationEvent) for event in events)


def test_allowed_tools_empty_keeps_only_control_tools():
    skill = _skill("no-tools", allowed_tools=[])
    state = cast(ConversationState, _state_for_skills([skill], [skill.name]))
    tools_map = _tool_map([_AllowedAlphaTool, _BlockedGammaTool])

    effective = effective_tools_for_state(state, tools_map)

    assert set(effective.tools_map) == {"finish", "think"}


def test_allowed_tools_normalizes_aliases_and_scoped_entries():
    skill = _skill("shell-skill", allowed_tools=["Bash(git:*)", "Read"])
    state = cast(ConversationState, _state_for_skills([skill], [skill.name]))
    tools_map = _tool_map([_TerminalTool, _FileEditorTool, _BlockedGammaTool])

    effective = effective_tools_for_state(state, tools_map)

    assert set(effective.tools_map) == {
        "terminal",
        "file_editor",
        "finish",
        "think",
    }


def test_multiple_restricted_skills_intersect_allowed_tools():
    skill_a = _skill("skill-a", allowed_tools=["allowed_alpha", "allowed_beta"])
    skill_b = _skill("skill-b", allowed_tools=["allowed_beta", "blocked_gamma"])
    state = cast(
        ConversationState,
        _state_for_skills([skill_a, skill_b], [skill_a.name, skill_b.name]),
    )
    tools_map = _tool_map([_AllowedAlphaTool, _AllowedBetaTool, _BlockedGammaTool])

    effective = effective_tools_for_state(state, tools_map)

    assert set(effective.tools_map) == {"allowed_beta", "finish", "think"}
