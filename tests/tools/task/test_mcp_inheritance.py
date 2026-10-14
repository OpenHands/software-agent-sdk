"""Delegated sub-agents run with exactly their parent's MCP servers.

Every test drives a real conversation, a real delegation tool and real stdio
MCP servers; only the model is scripted. ``RecordingLLM`` records the tools
offered in each model request, the way a provider would see them.
"""

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import PrivateAttr, SecretStr

from openhands.sdk import Agent, LocalConversation, Tool
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.llm.call_context import LLMCallContext
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.llm.streaming import TokenCallbackType
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.subagent import AgentDefinition
from openhands.sdk.subagent.registry import _reset_registry_for_tests, register_agent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import ToolDefinition
from openhands.tools.delegate import DelegateExecutor
from openhands.tools.delegate.definition import DelegateAction
from openhands.tools.task import TaskToolSet
from openhands.tools.workflow import WorkflowToolSet
from openhands.tools.workflow.definition import WorkflowTool


SERVER = str(Path(__file__).with_name("mcp_echo_server.py"))
TOKEN = "parent-token"


class RecordingLLM(TestLLM):
    """A TestLLM that records each request; copies share the script and record."""

    _requests: list[dict[str, list[str]]] = PrivateAttr(default_factory=list)

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self._requests.append(
            {
                "tools": sorted(tool.name for tool in tools or []),
                "tool_results": [
                    content.text
                    for message in messages
                    if message.role == "tool"
                    for content in message.content
                    if isinstance(content, TextContent)
                ],
            }
        )
        return super().completion(
            messages,
            tools=tools,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            call_context=call_context,
            **kwargs,
        )

    @property
    def requests(self) -> list[dict[str, list[str]]]:
        return self._requests


def _llm(responses: list[Message | Exception]) -> RecordingLLM:
    llm = RecordingLLM.from_messages(responses)
    assert isinstance(llm, RecordingLLM)
    return llm


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _server(tool: str, marker: Path | None = None, **kwargs: Any) -> MCPServer:
    args = [SERVER, tool, *([str(marker)] if marker else [])]
    return MCPServer(command=sys.executable, args=args, **kwargs)


def _call(tool: str, **arguments: Any) -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[
            MessageToolCall(
                id=f"call_{tool}",
                name=tool,
                arguments=json.dumps(arguments),
                origin="completion",
            )
        ],
    )


def _done() -> Message:
    return Message(role="assistant", content=[TextContent(text="done")])


def _register_declarer(tmp_path: Path, tools: list[str] | None = None) -> None:
    """A sub-agent that declares an extra server and a same-named rogue one."""
    mcp_config = {
        "rogue": _server("rogue_extra", tmp_path / "rogue_extra.started"),
        "parent": _server("rogue_same_name", tmp_path / "rogue_same_name.started"),
    }

    def factory(llm) -> Agent:
        return Agent(
            llm=llm,
            tools=[Tool(name=name) for name in tools or []],
            mcp_config=mcp_config,
        )

    register_agent(
        name="declarer",
        factory_func=factory,
        description=AgentDefinition(
            name="declarer", tools=tools or [], mcp_config=mcp_config
        ),
    )


def _run(
    tmp_path: Path,
    llm: RecordingLLM,
    tools: list[Tool],
    mcp_config: dict[str, MCPServer],
    **conversation_kwargs: Any,
) -> LocalConversation:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    conversation = LocalConversation(
        agent=Agent(llm=llm, tools=tools, mcp_config=mcp_config),
        workspace=str(workspace),
        visualizer=None,
        **conversation_kwargs,
    )
    conversation.send_message("go")
    conversation.run()
    conversation.close()
    return conversation


def _assert_no_rogue_server_started(tmp_path: Path) -> None:
    assert not (tmp_path / "rogue_extra.started").exists()
    assert not (tmp_path / "rogue_same_name.started").exists()


WORKFLOW_SCRIPT = "async def main(wf):\n    return await wf.run_agent('go', 'declarer')"


@pytest.mark.parametrize(
    ("delegation_tool", "delegate"),
    [
        (TaskToolSet.name, _call("task", prompt="go", subagent_type="declarer")),
        (WorkflowToolSet.name, _call("workflow", name="w", script=WORKFLOW_SCRIPT)),
        (WorkflowTool.name, _call("workflow", name="w", script=WORKFLOW_SCRIPT)),
    ],
)
def test_a_sub_agent_uses_exactly_the_parent_mcp_tools(
    tmp_path: Path, delegation_tool: str, delegate: Message
) -> None:
    _register_declarer(tmp_path)
    llm = _llm([delegate, _call("echo", text="hi"), _done(), _done()])
    parent_mcp = {"parent": _server("echo", env={"MCP_TOKEN": SecretStr(TOKEN)})}

    _run(tmp_path, llm, [Tool(name=delegation_tool)], parent_mcp)

    parent, child, child_after_call, parent_after = llm.requests
    assert "echo" in parent["tools"]
    assert "echo" in child["tools"]
    assert not {"rogue_extra", "rogue_same_name"} & set(child["tools"])
    assert f"echo:hi:token={TOKEN}" in child_after_call["tool_results"]
    assert "echo" in parent_after["tools"]
    _assert_no_rogue_server_started(tmp_path)


def test_a_delegate_tool_sub_agent_uses_exactly_the_parent_mcp_tools(
    tmp_path: Path,
) -> None:
    _register_declarer(tmp_path)
    llm = _llm([_call("echo", text="hi"), _done()])
    parent_mcp = {"parent": _server("echo", env={"MCP_TOKEN": SecretStr(TOKEN)})}
    parent = LocalConversation(
        agent=Agent(llm=llm, mcp_config=parent_mcp),
        workspace=str(tmp_path),
        visualizer=None,
    )
    executor = DelegateExecutor()

    executor(
        DelegateAction(command="spawn", ids=["d"], agent_types=["declarer"]), parent
    )
    executor(DelegateAction(command="delegate", tasks={"d": "go"}), parent)
    executor.close()
    parent.close()

    child, child_after_call = llm.requests
    assert "echo" in child["tools"]
    assert not {"rogue_extra", "rogue_same_name"} & set(child["tools"])
    assert f"echo:hi:token={TOKEN}" in child_after_call["tool_results"]
    _assert_no_rogue_server_started(tmp_path)


def test_a_grandchild_inherits_the_same_mcp_tools(tmp_path: Path) -> None:
    _register_declarer(tmp_path, tools=[TaskToolSet.name])
    llm = _llm(
        [
            _call("task", prompt="delegate", subagent_type="declarer"),
            _call("task", prompt="call echo", subagent_type="declarer"),
            _call("echo", text="deep"),
            _done(),
            _done(),
            _done(),
        ]
    )
    parent_mcp = {"parent": _server("echo", env={"MCP_TOKEN": SecretStr(TOKEN)})}

    _run(tmp_path, llm, [Tool(name=TaskToolSet.name)], parent_mcp)

    child, grandchild = llm.requests[1], llm.requests[2]
    assert "echo" in child["tools"] and "task" in child["tools"]
    assert "echo" in grandchild["tools"]
    assert f"echo:deep:token={TOKEN}" in llm.requests[3]["tool_results"]
    _assert_no_rogue_server_started(tmp_path)


def test_a_parent_without_mcp_servers_gives_its_sub_agents_none(
    tmp_path: Path,
) -> None:
    _register_declarer(tmp_path)
    llm = _llm([_call("task", prompt="go", subagent_type="declarer"), _done(), _done()])

    _run(tmp_path, llm, [Tool(name=TaskToolSet.name)], {})

    parent, child = llm.requests[0], llm.requests[1]
    assert set(child["tools"]) == set(parent["tools"]) - {"task"}
    _assert_no_rogue_server_started(tmp_path)


def test_a_resumed_sub_agent_keeps_the_parent_mcp_tools(tmp_path: Path) -> None:
    _register_declarer(tmp_path)
    llm = _llm(
        [
            _call("task", prompt="first", subagent_type="declarer"),
            _done(),
            _call(
                "task", prompt="again", subagent_type="declarer", resume="task_00000001"
            ),
            _call("echo", text="resumed"),
            _done(),
            _done(),
        ]
    )
    parent_mcp = {"parent": _server("echo", env={"MCP_TOKEN": SecretStr(TOKEN)})}

    _run(
        tmp_path,
        llm,
        [Tool(name=TaskToolSet.name)],
        parent_mcp,
        persistence_dir=str(tmp_path / "persist"),
    )

    resumed = llm.requests[3]
    assert "echo" in resumed["tools"]
    assert f"echo:resumed:token={TOKEN}" in llm.requests[4]["tool_results"]
    _assert_no_rogue_server_started(tmp_path)
    states = list((tmp_path / "persist").rglob("base_state.json"))
    assert len(states) == 2
    assert all(TOKEN not in state.read_text() for state in states)


def test_a_sub_agent_inherits_plugin_mcp_servers_with_their_secrets(
    tmp_path: Path,
) -> None:
    """Plugin servers merged into the parent reach the sub-agent as resolved.

    The workspace's own plugin is also discovered by the sub-agent's
    conversation; were it merged again, its ``${MCP_TOKEN}`` would reach the
    server unexpanded, since only the parent holds the secret.
    """
    _register_declarer(tmp_path)
    plugin = tmp_path / "workspace" / ".agents" / "plugins" / "qa"
    (plugin / ".plugin").mkdir(parents=True)
    (plugin / ".plugin" / "plugin.json").write_text(
        json.dumps({"name": "qa", "version": "1.0.0", "description": "QA"})
    )
    plugin_server = _server("plugged", env={"MCP_TOKEN": SecretStr("${MCP_TOKEN}")})
    (plugin / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "plugin": plugin_server.model_dump(
                        mode="json", context={"expose_secrets": "plaintext"}
                    )
                }
            }
        )
    )
    llm = _llm(
        [
            _call("task", prompt="go", subagent_type="declarer"),
            _call("plugin_plugged", text="hi"),
            _done(),
            _done(),
        ]
    )
    parent_mcp = {"parent": _server("echo")}

    _run(
        tmp_path,
        llm,
        [Tool(name=TaskToolSet.name)],
        parent_mcp,
        secrets={"MCP_TOKEN": TOKEN},
    )

    parent, child = llm.requests[0], llm.requests[1]
    mcp_tools = {"parent_echo", "plugin_plugged"}
    assert mcp_tools <= set(parent["tools"])
    assert mcp_tools <= set(child["tools"])
    assert f"plugged:hi:token={TOKEN}" in llm.requests[2]["tool_results"]
    _assert_no_rogue_server_started(tmp_path)
