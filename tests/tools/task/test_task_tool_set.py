import json

from openhands.sdk import Agent, Conversation, LocalConversation, Tool
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.llm_convertible.observation import ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.tools.delegate.registration import (
    _reset_registry_for_tests,
    register_agent,
)
from openhands.tools.task import TaskToolSet
from openhands.tools.task.definition import TaskObservation
from openhands.tools.task.manager import TaskStatus


def _task_tool_call(
    call_id: str,
    prompt: str,
    subagent_type: str = "test_agent",
    description: str | None = None,
    resume: str | None = None,
    max_turns: int | None = None,
) -> Message:
    """Build a Message whose only tool call is the task tool."""
    args: dict = {
        "prompt": prompt,
        "subagent_type": subagent_type,
    }
    if description is not None:
        args["description"] = description
    if resume is not None:
        args["resume"] = resume
    if max_turns is not None:
        args["max_turns"] = max_turns

    return Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[
            MessageToolCall(
                id=call_id,
                name="task",
                arguments=json.dumps(args),
                origin="completion",
            )
        ],
    )


def _text_message(text: str) -> Message:
    """A plain assistant text message (no tool calls)."""
    return Message(role="assistant", content=[TextContent(text=text)])


def _register_simple_agent(name: str, sub_llm: TestLLM) -> None:
    """Register a sub-agent backed by *sub_llm* (ignores the parent-copied LLM)."""

    def factory(llm):
        return Agent(llm=sub_llm, tools=[])

    register_agent(name=name, factory_func=factory, description=f"Test agent: {name}")


def _get_task_observations(conversation: LocalConversation) -> list[TaskObservation]:
    """Extract all TaskObservation objects from conversation events."""
    results = []
    for event in conversation.state.events:
        if isinstance(event, ObservationEvent) and isinstance(
            event.observation, TaskObservation
        ):
            results.append(event.observation)
    return results


class TestTaskToolSetIntegration:
    """Tests for the TaskToolSet."""

    def setup_method(self):
        _reset_registry_for_tests()

    def teardown_method(self):
        _reset_registry_for_tests()

    def test_basic_task_delegation_and_result(self, tmp_path):
        """Parent delegates to sub-agent; sub-agent text is returned as task result."""
        parent_llm = TestLLM.from_messages(
            [
                _task_tool_call("call_1", prompt="What is the capital of France?"),
                _text_message("The answer is Paris."),
            ]
        )
        sub_llm = TestLLM.from_messages(
            [
                _text_message("The capital of France is Paris."),
            ]
        )
        _register_simple_agent("test_agent", sub_llm)

        agent = Agent(llm=parent_llm, tools=[Tool(name=TaskToolSet.name)])
        conversation = Conversation(
            agent=agent, workspace=str(tmp_path), visualizer=None
        )

        conversation.send_message("What is the capital of France?")
        conversation.run()

        # Conversation finished
        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )

        # Both LLMs fully consumed
        assert parent_llm.remaining_responses == 0
        assert sub_llm.remaining_responses == 0

        # Task observation present and successful
        observations = _get_task_observations(conversation)
        assert len(observations) == 1
        obs = observations[0]
        assert obs.status == TaskStatus.COMPLETED
        assert obs.task_id.startswith("task_")
        assert obs.subagent == "test_agent"
        assert "Paris" in obs.text

    # ── Multiple sequential tasks ───────────────────────────────────

    def test_two_sequential_tasks(self, tmp_path):
        """Parent can launch two tasks one after another in a single turn."""
        sub_llm_1 = TestLLM.from_messages([_text_message("first result")])
        sub_llm_2 = TestLLM.from_messages([_text_message("second result")])
        _register_simple_agent("agent_a", sub_llm_1)
        _register_simple_agent("agent_b", sub_llm_2)

        parent_llm = TestLLM.from_messages(
            [
                _task_tool_call("call_1", prompt="Task A", subagent_type="agent_a"),
                _task_tool_call("call_2", prompt="Task B", subagent_type="agent_b"),
                _text_message("Both tasks done."),
            ]
        )

        agent = Agent(llm=parent_llm, tools=[Tool(name=TaskToolSet.name)])
        conversation = Conversation(
            agent=agent, workspace=str(tmp_path), visualizer=None
        )

        conversation.send_message("Run two tasks")
        conversation.run()

        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        observations = _get_task_observations(conversation)
        assert len(observations) == 2
        assert observations[0].text == "first result"
        assert observations[1].text == "second result"
        assert observations[0].subagent == "agent_a"
        assert observations[1].subagent == "agent_b"

    def test_task_resume_across_turns(self, tmp_path):
        """A task can be launched, then resumed by passing the task_id."""
        # Sub-agent for the first call
        sub_llm_1 = TestLLM.from_messages(
            [
                _text_message("Here is a quiz: What color is the sky?"),
                _text_message("Correct! Blue is right."),
            ]
        )
        _register_simple_agent("quiz_agent", sub_llm_1)

        # First turn: parent delegates to quiz_agent
        parent_llm = TestLLM.from_messages(
            [
                _task_tool_call(
                    "call_1",
                    prompt="Generate a quiz",
                    subagent_type="quiz_agent",
                ),
                _text_message("It is Blue!"),
                _task_tool_call(
                    "call_2",
                    prompt="Generate a quiz",
                    subagent_type="quiz_agent",
                    resume="task_00000001",
                ),
                _text_message("Thank you."),
            ]
        )

        agent = Agent(llm=parent_llm, tools=[Tool(name=TaskToolSet.name)])
        conversation = Conversation(
            agent=agent, workspace=str(tmp_path), visualizer=None
        )

        conversation.send_message("Give me a quiz")
        conversation.run()

        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        observations = _get_task_observations(conversation)
        assert len(observations) == 1
        task_id = observations[0].task_id

        conversation.send_message("My answer is blue")
        conversation.run()

        all_observations = _get_task_observations(conversation)
        # Should now have 2 total observations
        assert len(all_observations) == 2
        resumed_obs = all_observations[1]
        assert resumed_obs.task_id == task_id
        assert "Correct" in resumed_obs.text

    # ── Error handling ──────────────────────────────────────────────

    def test_unknown_agent_type_returns_error_observation(self, tmp_path):
        """Using an unregistered subagent_type yields an error TaskObservation."""
        parent_llm = TestLLM.from_messages(
            [
                _task_tool_call(
                    "call_1",
                    prompt="Do something",
                    subagent_type="nonexistent_agent",
                ),
                _text_message("Oops."),
            ]
        )

        agent = Agent(llm=parent_llm, tools=[Tool(name=TaskToolSet.name)])
        conversation = Conversation(
            agent=agent, workspace=str(tmp_path), visualizer=None
        )

        conversation.send_message("Do something")
        conversation.run()

        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        observations = _get_task_observations(conversation)
        assert len(observations) == 1
        obs = observations[0]
        assert obs.is_error is True
        assert "nonexistent_agent" in obs.text or "Unknown agent" in obs.text

    def test_sub_agent_exception_returns_error_observation(self, tmp_path):
        """When the sub-agent's LLM raises, the task reports an error."""
        sub_llm = TestLLM.from_messages(
            [
                RuntimeError("LLM went boom"),
            ]
        )
        _register_simple_agent("failing_agent", sub_llm)

        parent_llm = TestLLM.from_messages(
            [
                _task_tool_call(
                    "call_1", prompt="Run this", subagent_type="failing_agent"
                ),
                _text_message("The task failed."),
            ]
        )

        agent = Agent(llm=parent_llm, tools=[Tool(name=TaskToolSet.name)])
        conversation = Conversation(
            agent=agent, workspace=str(tmp_path), visualizer=None
        )

        conversation.send_message("Run this")
        conversation.run()

        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        observations = _get_task_observations(conversation)
        assert len(observations) == 1
        obs = observations[0]
        assert obs.is_error is True
        assert obs.status == TaskStatus.ERROR

    def test_task_ids_are_unique_and_sequential(self, tmp_path):
        """Each task gets a unique, incrementing ID."""
        sub_llm_1 = TestLLM.from_messages([_text_message("r1")])
        sub_llm_2 = TestLLM.from_messages([_text_message("r2")])
        _register_simple_agent("agent_x", sub_llm_1)
        _register_simple_agent("agent_y", sub_llm_2)

        parent_llm = TestLLM.from_messages(
            [
                _task_tool_call("c1", prompt="T1", subagent_type="agent_x"),
                _task_tool_call("c2", prompt="T2", subagent_type="agent_y"),
                _text_message("All done."),
            ]
        )

        agent = Agent(llm=parent_llm, tools=[Tool(name=TaskToolSet.name)])
        conversation = Conversation(
            agent=agent, workspace=str(tmp_path), visualizer=None
        )

        conversation.send_message("Do both")
        conversation.run()

        observations = _get_task_observations(conversation)
        assert len(observations) == 2
        id1 = observations[0].task_id
        id2 = observations[1].task_id
        assert id1 != id2
        # Sequential: task_00000001 < task_00000002
        assert id1 < id2

    def test_resume_nonexistent_task_returns_error(self, tmp_path):
        """Resuming a task ID that doesn't exist yields an error observation."""
        sub_llm = TestLLM.from_messages([_text_message("never reached")])
        _register_simple_agent("test_agent", sub_llm)

        parent_llm = TestLLM.from_messages(
            [
                _task_tool_call(
                    "call_1",
                    prompt="Continue",
                    subagent_type="test_agent",
                    resume="task_99999999",
                ),
                _text_message("Failed."),
            ]
        )

        agent = Agent(llm=parent_llm, tools=[Tool(name=TaskToolSet.name)])
        conversation = Conversation(
            agent=agent, workspace=str(tmp_path), visualizer=None
        )

        conversation.send_message("Resume a non-existent task")
        conversation.run()

        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        observations = _get_task_observations(conversation)
        assert len(observations) == 1
        assert observations[0].is_error is True
