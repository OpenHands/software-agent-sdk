import json
from unittest.mock import patch

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, LocalConversation, Tool
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.llm_convertible.observation import ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.subagent.registry import _reset_registry_for_tests, register_agent
from openhands.sdk.testing import TestLLM
from openhands.tools.task import TaskToolSet
from openhands.tools.task.definition import TASK_TOOL_EXAMPLES, TaskObservation
from openhands.tools.task.manager import TaskStatus


def _task_tool_call(
    call_id: str,
    prompt: str,
    subagent_type: str = "test_agent",
    description: str | None = None,
    resume: str | None = None,
    llm_profile: str | None = None,
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
    if llm_profile is not None:
        args["llm_profile"] = llm_profile

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

    def test_two_sequential_tasks_with_llm_profiles(self, tmp_path):
        """Two sequential tasks can each run on a different saved LLM profile."""
        # The conftest redirects the default profile dir to tmp_path / "profiles",
        # which is where the parent conversation's profile store resolves.
        store = LLMProfileStore(base_dir=tmp_path / "profiles")
        for name, model in (("fast", "fast-model"), ("slow", "slow-model")):
            store.save(
                name,
                LLM(model=model, api_key=SecretStr(f"{name}-key"), usage_id=name),
                include_secrets=True,
            )

        factory_llms: list[LLM] = []

        def make_factory(sub_llm: TestLLM):
            def factory(llm: LLM) -> Agent:
                factory_llms.append(llm)
                return Agent(llm=sub_llm, tools=[])

            return factory

        register_agent(
            name="agent_a",
            factory_func=make_factory(
                TestLLM.from_messages([_text_message("first result")])
            ),
            description="Test agent: agent_a",
        )
        register_agent(
            name="agent_b",
            factory_func=make_factory(
                TestLLM.from_messages([_text_message("second result")])
            ),
            description="Test agent: agent_b",
        )

        parent_llm = TestLLM.from_messages(
            [
                _task_tool_call(
                    "call_1",
                    prompt="Task A",
                    subagent_type="agent_a",
                    llm_profile="fast",
                ),
                _task_tool_call(
                    "call_2",
                    prompt="Task B",
                    subagent_type="agent_b",
                    llm_profile="slow",
                ),
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
        assert [llm.model for llm in factory_llms] == ["fast-model", "slow-model"]

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

    def test_unknown_llm_profile_returns_error_observation(self, tmp_path):
        """An unknown llm_profile yields an error TaskObservation naming it."""
        sub_llm = TestLLM.from_messages([_text_message("never reached")])
        _register_simple_agent("test_agent", sub_llm)

        parent_llm = TestLLM.from_messages(
            [
                _task_tool_call(
                    "call_1",
                    prompt="Do something",
                    subagent_type="test_agent",
                    llm_profile="nonexistent_profile",
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
        assert "nonexistent_profile" in obs.text

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


class TestTaskToolExamples:
    """Tests that TASK_TOOL_EXAMPLES are included in the tool description
    only when the corresponding agents are registered."""

    def setup_method(self):
        _reset_registry_for_tests()

    def teardown_method(self):
        _reset_registry_for_tests()

    def test_matching_agent_example_included(self, tmp_path):
        """When a registered agent name matches a TASK_TOOL_EXAMPLES key,
        its example appears in the tool description."""
        # Pick one key from the examples dict
        example_name = next(iter(TASK_TOOL_EXAMPLES))
        example_text = TASK_TOOL_EXAMPLES[example_name]

        # Register an agent whose name matches the example key
        register_agent(
            name=example_name,
            factory_func=lambda llm: Agent(llm=llm, tools=[]),
            description=f"Test agent: {example_name}",
        )

        tools = TaskToolSet.create(
            conv_state=None,  # type: ignore[arg-type]
        )
        assert len(tools) == 1
        description = tools[0].description
        assert example_text.strip() in description

    def test_no_matching_agent_example_excluded(self, tmp_path):
        """When no registered agent name matches any TASK_TOOL_EXAMPLES key,
        no example text appears in the tool description."""
        # Register an agent whose name does NOT match any example key
        register_agent(
            name="unrelated_agent",
            factory_func=lambda llm: Agent(llm=llm, tools=[]),
            description="Test agent: unrelated",
        )

        tools = TaskToolSet.create(
            conv_state=None,  # type: ignore[arg-type]
        )
        assert len(tools) == 1
        description = tools[0].description
        for name, example_text in TASK_TOOL_EXAMPLES.items():
            assert example_text.strip() not in description

    def test_description_lists_llm_profiles(self, tmp_path):
        """The tool description lists saved LLM profiles for llm_profile."""
        with patch(
            "openhands.tools.task.definition.get_llm_profile_names",
            return_value=["fast", "slow"],
        ):
            tools = TaskToolSet.create(
                conv_state=None,  # type: ignore[arg-type]
            )
        description = tools[0].description
        assert "llm_profile" in description
        assert "- fast" in description
        assert "- slow" in description

    def test_description_exposes_profile_names_only(self, tmp_path):
        """Profile configuration and secrets are excluded from the description."""
        store = LLMProfileStore()
        store.save(
            "safe-name",
            LLM(
                model="sentinel-private-model",
                base_url="https://sentinel-private.example",
                api_key=SecretStr("sentinel-private-key"),
            ),
            include_secrets=True,
        )

        description = TaskToolSet.create(
            conv_state=None,  # type: ignore[arg-type]
        )[0].description

        assert "- safe-name" in description
        assert "sentinel-private-model" not in description
        assert "sentinel-private.example" not in description
        assert "sentinel-private-key" not in description

    def test_description_without_profiles_omits_section(self, tmp_path):
        """With no saved profiles, the llm_profile section is omitted entirely
        rather than rendering an incoherent "one of these: none" list."""
        with patch(
            "openhands.tools.task.definition.get_llm_profile_names",
            return_value=[],
        ):
            tools = TaskToolSet.create(
                conv_state=None,  # type: ignore[arg-type]
            )
        description = tools[0].description
        assert "llm_profile" not in description
        assert "saved LLM profiles" not in description

    def test_only_registered_examples_included(self, tmp_path):
        """Only examples for registered agents appear; others are excluded."""
        keys = list(TASK_TOOL_EXAMPLES.keys())
        if len(keys) < 2:
            return  # Need at least 2 examples for this test

        included_name = keys[0]
        excluded_name = keys[1]

        register_agent(
            name=included_name,
            factory_func=lambda llm: Agent(llm=llm, tools=[]),
            description=f"Test agent: {included_name}",
        )

        tools = TaskToolSet.create(
            conv_state=None,  # type: ignore[arg-type]
        )
        description = tools[0].description
        assert TASK_TOOL_EXAMPLES[included_name].strip() in description
        assert TASK_TOOL_EXAMPLES[excluded_name].strip() not in description
