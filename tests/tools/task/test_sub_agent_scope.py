import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentBase, Conversation, LocalConversation, Tool
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.event.llm_convertible.observation import ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.profiles import OpenHandsAgentProfile, resolve_agent_profile
from openhands.sdk.subagent import AgentDefinition, SubAgentScope
from openhands.sdk.subagent.registry import _reset_registry_for_tests, register_agent
from openhands.sdk.testing import TestLLM
from openhands.tools.glob import GlobTool
from openhands.tools.grep import GrepTool
from openhands.tools.preset import register_builtins_agents
from openhands.tools.task import TaskToolSet
from openhands.tools.task.definition import TaskObservation
from openhands.tools.task.manager import TaskManager
from openhands.tools.workflow import WorkflowToolSet
from openhands.tools.workflow.definition import WorkflowObservation, WorkflowTool


TOOLS_ONLY = {"tools": True, "mcp_servers": False}
TOOLS_AND_MCP = {"tools": True, "mcp_servers": True}


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _register(
    name: str,
    tools: list[str],
    *,
    mcp: tuple[str, ...] = (),
    built_tools: list[str] | None = None,
    llm: LLM | None = None,
) -> None:
    mcp_config = {server: MCPServer(command="uvx") for server in mcp}

    def factory(parent_llm: LLM) -> Agent:
        names = tools if built_tools is None else built_tools
        return Agent(
            llm=llm or parent_llm,
            tools=[Tool(name=tool) for tool in names],
            mcp_config=mcp_config,
        )

    register_agent(
        name=name,
        factory_func=factory,
        description=AgentDefinition(
            name=name,
            description=f"{name} agent",
            tools=tools,
            mcp_config=mcp_config or None,
        ),
    )


def _tool_call(tool: str, **arguments) -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[
            MessageToolCall(
                id="call_1",
                name=tool,
                arguments=json.dumps(arguments),
                origin="completion",
            )
        ],
    )


def _done() -> Message:
    return Message(role="assistant", content=[TextContent(text="done")])


def _observations(conversation: LocalConversation, kind: type) -> list:
    return [
        event.observation
        for event in conversation.state.events
        if isinstance(event, ObservationEvent) and isinstance(event.observation, kind)
    ]


def _offered(parent: AgentBase, scope: dict[str, bool] | None) -> str:
    (tool,) = TaskToolSet.create(
        conv_state=SimpleNamespace(agent=parent),  # type: ignore[arg-type]
        sub_agent_scope=scope,
    )
    return tool.description


def _parent(*tools: Tool) -> Agent:
    return Agent(
        llm=TestLLM.from_messages([]),
        tools=list(tools),
    )


def test_a_scoped_task_tool_offers_only_sub_agents_within_the_parent() -> None:
    _register("shell", ["terminal"])
    _register("surfer", ["browser_tool_set"])
    _register("fetcher", ["terminal"], mcp=("fetch",))
    parent = _parent(Tool(name="terminal"))

    tools_only = _offered(parent, TOOLS_ONLY)
    tools_and_mcp = _offered(parent, TOOLS_AND_MCP)
    unscoped = _offered(parent, None)

    assert "**shell**" in tools_only and "**fetcher**" in tools_only
    assert "**surfer**" not in tools_only
    assert "**shell**" in tools_and_mcp
    assert "**fetcher**" not in tools_and_mcp
    assert all(f"**{n}**" in unscoped for n in ("shell", "surfer", "fetcher"))


def test_a_scoped_task_tool_refuses_a_sub_agent_beyond_the_parent(
    tmp_path: Path,
) -> None:
    _register("surfer", ["browser_tool_set"], mcp=("fetch",))
    parent_llm = TestLLM.from_messages(
        [_tool_call("task", prompt="look it up", subagent_type="surfer"), _done()]
    )
    agent = Agent(
        llm=parent_llm,
        tools=[Tool(name="task_tool_set", params={"sub_agent_scope": TOOLS_AND_MCP})],
    )
    conversation = Conversation(agent=agent, workspace=str(tmp_path), visualizer=None)

    conversation.send_message("look it up")
    conversation.run()

    (observation,) = _observations(conversation, TaskObservation)
    assert observation.is_error
    assert (
        "Agent 'surfer' uses browser_tool_set, MCP server 'fetch', "
        "which this agent does not have." in observation.text
    )


def test_a_sub_agent_is_checked_as_built_not_as_described(tmp_path: Path) -> None:
    _register("understated", [], built_tools=["terminal"])
    parent = LocalConversation(
        agent=_parent(Tool(name="grep")), workspace=str(tmp_path), visualizer=None
    )
    manager = TaskManager(sub_agent_scope=SubAgentScope(tools=True))
    manager.attach_parent(parent)

    assert "**understated**" in _offered(parent.agent, TOOLS_ONLY)
    with pytest.raises(ValueError, match="'understated' uses terminal"):
        manager._get_sub_agent("understated")


def test_a_scope_refuses_a_sub_agent_whose_tools_it_cannot_see(
    tmp_path: Path,
) -> None:
    register_agent(
        name="cli",
        factory_func=lambda llm: ACPAgent(acp_command=["true"]),  # type: ignore[arg-type,return-value]
        description="ACP CLI agent",
    )
    parent = LocalConversation(
        agent=_parent(Tool(name="grep")), workspace=str(tmp_path), visualizer=None
    )
    scoped = TaskManager(sub_agent_scope=SubAgentScope(tools=True))
    scoped.attach_parent(parent)
    unscoped = TaskManager()
    unscoped.attach_parent(parent)

    with pytest.raises(ValueError, match="'cli' runs as ACPAgent, whose tools cannot"):
        scoped._get_sub_agent("cli")
    assert isinstance(unscoped._get_sub_agent("cli"), ACPAgent)


def test_a_sub_agent_delegates_within_the_same_scope(tmp_path: Path) -> None:
    _register("orchestrator", ["task_tool_set", "workflow_tool_set"])
    parent = LocalConversation(
        agent=_parent(Tool(name="task_tool_set"), Tool(name="workflow_tool_set")),
        workspace=str(tmp_path),
        visualizer=None,
    )
    manager = TaskManager(sub_agent_scope=SubAgentScope(tools=True))
    manager.attach_parent(parent)

    sub_agent = manager._get_sub_agent("orchestrator")

    assert sub_agent.tools == [
        Tool(name="task_tool_set", params={"sub_agent_scope": TOOLS_ONLY}),
        Tool(name="workflow_tool_set", params={"sub_agent_scope": TOOLS_ONLY}),
    ]


def test_an_unscoped_task_manager_starts_any_sub_agent(tmp_path: Path) -> None:
    _register("orchestrator", ["terminal", "task_tool_set"])
    parent = LocalConversation(
        agent=_parent(), workspace=str(tmp_path), visualizer=None
    )
    manager = TaskManager()
    manager.attach_parent(parent)

    sub_agent = manager._get_sub_agent("orchestrator")

    assert sub_agent.tools == [Tool(name="terminal"), Tool(name="task_tool_set")]


@pytest.mark.parametrize("workflow_tool", [WorkflowToolSet.name, WorkflowTool.name])
def test_a_scoped_workflow_refuses_a_sub_agent_beyond_the_parent(
    tmp_path: Path, workflow_tool: str
) -> None:
    _register("shell", ["terminal"])
    script = "async def main(wf):\n    return await wf.run_agent('ls', 'shell')"
    parent_llm = TestLLM.from_messages(
        [_tool_call("workflow", name="probe", script=script), _done()]
    )
    agent = Agent(
        llm=parent_llm,
        tools=[Tool(name=workflow_tool, params={"sub_agent_scope": TOOLS_ONLY})],
    )
    conversation = Conversation(agent=agent, workspace=str(tmp_path), visualizer=None)

    conversation.send_message("probe")
    conversation.run()

    (observation,) = _observations(conversation, WorkflowObservation)
    assert observation.is_error
    assert "Agent 'shell' uses terminal" in observation.text


@pytest.mark.parametrize(
    ("subagent", "missing"),
    [
        ("general-purpose", "file_editor, task_tracker, terminal"),
        ("bash-runner", "terminal"),
        ("code-explorer", "terminal"),
        ("web-researcher", "browser_tool_set"),
    ],
)
def test_a_read_only_profile_cannot_delegate_beyond_its_tools(
    tmp_path: Path, subagent: str, missing: str
) -> None:
    register_builtins_agents(enable_browser=True)
    _register("grepper", ["grep"])
    store = LLMProfileStore(base_dir=tmp_path / "llm")
    store.save(
        "default",
        LLM(model="gpt-4o", api_key=SecretStr("k"), usage_id="x"),
        include_secrets=True,
    )
    profile = OpenHandsAgentProfile(
        name="read-only",
        llm_profile_ref="default",
        tools=[
            Tool(name=GlobTool.name),
            Tool(name=GrepTool.name),
            Tool(name=TaskToolSet.name),
        ],
    )
    settings = resolve_agent_profile(
        profile, llm_store=store, mcp_config={}, available_skills=None
    )
    parent_llm = TestLLM.from_messages(
        [_tool_call("task", prompt="go", subagent_type=subagent), _done()]
    )
    agent = settings.create_agent().model_copy(update={"llm": parent_llm})
    conversation = Conversation(agent=agent, workspace=str(tmp_path), visualizer=None)

    conversation.send_message("go")
    conversation.run()

    description = conversation.agent.tools_map["task"].description
    assert "**grepper**" in description
    assert f"**{subagent}**" not in description
    (observation,) = _observations(conversation, TaskObservation)
    assert observation.is_error
    assert (
        f"Agent '{subagent}' uses {missing}, which this agent does not have."
        in observation.text
    )


def test_a_scoped_task_tool_says_when_no_sub_agent_fits() -> None:
    _register("shell", ["terminal"])

    description = _offered(_parent(Tool(name="grep")), TOOLS_ONLY)

    assert "**shell**" not in description
    assert "register_agent" not in description
    assert "- None: every registered agent uses tools" in description
