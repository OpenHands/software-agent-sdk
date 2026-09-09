"""Regression tests for trigger-based skills across conversation condensation."""

from pathlib import Path

from openhands.sdk.agent import Agent
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.skills import KeywordTrigger, PathTrigger, Skill
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins.finish import FinishObservation
from openhands.sdk.tool.schema import Action


class _CondensationFileAction(Action):
    path: str
    command: str = "view"


def _message(text: str) -> Message:
    return Message(role="assistant", content=[TextContent(text=text)])


def _conversation(tmp_path: Path, skills: list[Skill]) -> LocalConversation:
    agent = Agent(
        llm=TestLLM.from_messages([_message("agent response")]),
        condenser=LLMSummarizingCondenser(
            llm=TestLLM.from_messages([_message("condensed history")]),
            max_size=20,
            keep_first=2,
        ),
        tools=[],
        include_default_tools=[],
        agent_context=AgentContext(skills=skills),
    )
    return LocalConversation(
        agent=agent,
        workspace=tmp_path,
        persistence_dir=tmp_path / "conversation",
        delete_on_close=True,
    )


def _append_path_observation(
    conversation: LocalConversation, path: Path, tool_call_id: str
) -> ObservationEvent:
    action = ActionEvent(
        thought=[TextContent(text="inspect file")],
        action=_CondensationFileAction(path=str(path)),
        tool_name="file_editor",
        tool_call_id=tool_call_id,
        tool_call=MessageToolCall(
            id=tool_call_id,
            name="file_editor",
            arguments="{}",
            origin="completion",
        ),
        llm_response_id=f"response-{tool_call_id}",
        source="agent",
    )
    observation = ObservationEvent(
        observation=FinishObservation(content=[TextContent(text="file contents")]),
        action_id=action.id,
        tool_name=action.tool_name,
        tool_call_id=tool_call_id,
    )
    with conversation._state:
        conversation._on_event(action)
        conversation._on_event(observation)

    persisted = [
        event
        for event in conversation.state.events
        if isinstance(event, ObservationEvent) and event.action_id == action.id
    ]
    assert persisted
    return persisted[-1]


def test_keyword_skill_can_reactivate_after_its_event_is_condensed(
    tmp_path: Path,
) -> None:
    skill = Skill(
        name="python_tips",
        content="Prefer small, focused Python functions.",
        trigger=KeywordTrigger(keywords=["python"]),
    )
    conversation = _conversation(tmp_path, [skill])
    try:
        for index in range(5):
            conversation.send_message(f"prefix message {index}")
        conversation.send_message("Show me a python example")
        triggered_event = conversation.state.events[-1]
        assert isinstance(triggered_event, MessageEvent)
        assert triggered_event.activated_skills == ["python_tips"]

        conversation.send_message("Show me another python example")
        deduped_event = conversation.state.events[-1]
        assert isinstance(deduped_event, MessageEvent)
        assert deduped_event.activated_skills == []

        for index in range(10):
            conversation.send_message(f"tail message {index}")

        conversation.condense()

        assert triggered_event.id not in {
            event.id for event in conversation.state.view.events
        }
        assert conversation.state.activated_knowledge_skills == []

        conversation.send_message("Show me another python example")
        retriggered_event = conversation.state.events[-1]
        assert isinstance(retriggered_event, MessageEvent)
        assert retriggered_event.activated_skills == ["python_tips"]
        assert any(
            "Prefer small, focused Python functions." in content.text
            for content in retriggered_event.extended_content
        )
    finally:
        conversation.close()


def test_path_rule_can_reactivate_after_its_event_is_condensed(
    tmp_path: Path,
) -> None:
    rule = Skill(
        name="typescript_rules",
        content="Keep TypeScript modules narrowly scoped.",
        trigger=PathTrigger(paths=["src/**/*.ts"]),
    )
    conversation = _conversation(tmp_path, [rule])
    try:
        for index in range(6):
            conversation.send_message(f"prefix message {index}")
        first_observation = _append_path_observation(
            conversation, tmp_path / "src" / "app.ts", "first-call"
        )
        assert any(
            "Keep TypeScript modules narrowly scoped." in content.text
            for content in first_observation.extended_content
        )

        deduped_observation = _append_path_observation(
            conversation, tmp_path / "src" / "second.ts", "deduped-call"
        )
        assert deduped_observation.extended_content == []

        for index in range(10):
            conversation.send_message(f"tail message {index}")

        conversation.condense()

        assert first_observation.id not in {
            event.id for event in conversation.state.view.events
        }
        assert conversation.state.activated_path_rules == []

        second_observation = _append_path_observation(
            conversation, tmp_path / "src" / "another.ts", "second-call"
        )
        assert any(
            "Keep TypeScript modules narrowly scoped." in content.text
            for content in second_observation.extended_content
        )
        assert conversation.state.activated_path_rules == ["typescript_rules"]
    finally:
        conversation.close()
