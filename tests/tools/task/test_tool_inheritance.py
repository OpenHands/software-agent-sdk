from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest

from openhands.sdk import LLM, Agent, LocalConversation, Tool
from openhands.sdk.subagent import AgentDefinition
from openhands.sdk.subagent.registry import (
    _reset_registry_for_tests,
    agent_definition_to_factory,
    register_agent,
    register_file_agents,
)
from openhands.tools.delegate import DelegateExecutor
from openhands.tools.delegate.definition import DelegateAction
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task.manager import TaskManager
from openhands.tools.task_tracker import TaskTrackerTool


@pytest.fixture(params=["task", "delegate"])
def spawn_child(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[Callable[[Agent], tuple[LocalConversation, LocalConversation]]]:
    _reset_registry_for_tests()
    with ExitStack() as cleanup:

        def spawn(agent: Agent) -> tuple[LocalConversation, LocalConversation]:
            parent = LocalConversation(
                agent=agent, workspace=str(tmp_path), visualizer=None
            )
            cleanup.callback(parent.close)
            parent.send_message("Initialize the tools.")
            if request.param == "task":
                manager = TaskManager()
                manager.attach_parent(parent)
                cleanup.callback(manager.close)
                child = LocalConversation(
                    agent=manager._get_sub_agent("child"),
                    workspace=str(tmp_path),
                    visualizer=None,
                )
                cleanup.callback(child.close)
            else:
                executor = DelegateExecutor()
                cleanup.callback(executor.close)
                observation = executor(
                    DelegateAction(
                        command="spawn", ids=["child"], agent_types=["child"]
                    ),
                    parent,
                )
                assert not observation.is_error
                child = executor._sub_agents["child"]
            child.send_message("Initialize the tools.")
            return parent, child

        yield spawn
    _reset_registry_for_tests()


def _llm() -> LLM:
    return LLM(model="gpt-4o", api_key="test-key", usage_id="tool-inheritance")


def test_markdown_inheritance_preserves_the_parents_available_tools(
    tmp_path: Path,
    spawn_child: Callable[[Agent], tuple[LocalConversation, LocalConversation]],
) -> None:
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "child.md").write_text("---\nname: child\n---\nRead the workspace.")
    register_file_agents(tmp_path)
    parent, child = spawn_child(
        Agent(
            llm=_llm(),
            tools=[Tool(name=FileEditorTool.name), Tool(name=TaskTrackerTool.name)],
            filter_tools_regex=r"^file_editor$",
            include_default_tools=[],
        )
    )

    assert set(parent.agent.tools_map) == {"file_editor"}
    assert set(child.agent.tools_map) == {"file_editor"}


def test_handwritten_factory_keeps_its_own_tools(
    spawn_child: Callable[[Agent], tuple[LocalConversation, LocalConversation]],
) -> None:
    def factory(llm: LLM) -> Agent:
        return Agent(
            llm=llm,
            tools=[Tool(name=FileEditorTool.name)],
            include_default_tools=[],
        )

    register_agent(
        name="child", factory_func=factory, description=AgentDefinition(name="child")
    )
    _, child = spawn_child(
        Agent(
            llm=_llm(),
            tools=[Tool(name=FileEditorTool.name), Tool(name=TaskTrackerTool.name)],
        )
    )

    assert set(child.agent.tools_map) == {"file_editor"}


@pytest.mark.parametrize(
    ("initial_tools", "updated_tools", "expected_tools"),
    [
        (None, [], {"finish"}),
        ([], None, {"file_editor", "finish"}),
    ],
)
def test_inheritance_uses_the_definitions_current_tools(
    spawn_child: Callable[[Agent], tuple[LocalConversation, LocalConversation]],
    initial_tools: list[str] | None,
    updated_tools: list[str] | None,
    expected_tools: set[str],
) -> None:
    definition = AgentDefinition(name="child", tools=initial_tools)
    register_agent(
        name="child",
        factory_func=agent_definition_to_factory(definition),
        description=definition,
    )
    definition.tools = updated_tools
    _, child = spawn_child(Agent(llm=_llm(), tools=[Tool(name=FileEditorTool.name)]))

    assert set(child.agent.tools_map) == expected_tools
