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
        next_response = self._scripted_responses[0]
        if (
            isinstance(next_response, Message)
            and next_response.tool_calls
            and next_response.tool_calls[0].name == "call_mcp_tool"
        ):
            call = next_response.tool_calls[0]
            arguments = json.loads(call.arguments)
            arguments["revision"] = _catalog(self._requests[-1])["revision"]
            self._scripted_responses[0] = next_response.model_copy(
                update={
                    "tool_calls": [
                        call.model_copy(update={"arguments": json.dumps(arguments)})
                    ]
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


def _mcp_call(name: str, **arguments: Any) -> Message:
    return _call("call_mcp_tool", name=name, arguments=arguments, revision="")


def _catalog(request: dict[str, list[str]]) -> dict[str, Any]:
    for result in reversed(request["tool_results"]):
        if result.startswith('{"revision":'):
            return json.loads(result)
    raise AssertionError("The agent must list MCP tools before calling one")


def _assert_catalog(request: dict[str, list[str]], names: set[str]) -> None:
    assert {"list_mcp_tools", "call_mcp_tool"} <= set(request["tools"])
    assert {tool["name"] for tool in _catalog(request)["tools"]} == names


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
    llm = _llm(
        [
            delegate,
            _call("list_mcp_tools"),
            _mcp_call("parent.echo", text="hi"),
            _done(),
            _call("list_mcp_tools"),
            _done(),
        ]
    )
    parent_mcp = {"parent": _server("echo", env={"MCP_TOKEN": SecretStr(TOKEN)})}

    _run(tmp_path, llm, [Tool(name=delegation_tool)], parent_mcp)

    parent, child, child_listing, child_after_call, parent_after, parent_listing = (
        llm.requests
    )
    assert {"list_mcp_tools", "call_mcp_tool"} <= set(parent["tools"])
    assert {"list_mcp_tools", "call_mcp_tool"} <= set(child["tools"])
    _assert_catalog(child_listing, {"parent.echo"})
    assert f"echo:hi:token={TOKEN}" in child_after_call["tool_results"]
    assert {"list_mcp_tools", "call_mcp_tool"} <= set(parent_after["tools"])
    _assert_catalog(parent_listing, {"parent.echo"})
    _assert_no_rogue_server_started(tmp_path)


def test_a_delegate_tool_sub_agent_uses_exactly_the_parent_mcp_tools(
    tmp_path: Path,
) -> None:
    _register_declarer(tmp_path)
    llm = _llm([_call("list_mcp_tools"), _mcp_call("parent.echo", text="hi"), _done()])
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

    child, child_listing, child_after_call = llm.requests
    assert {"list_mcp_tools", "call_mcp_tool"} <= set(child["tools"])
    _assert_catalog(child_listing, {"parent.echo"})
    assert f"echo:hi:token={TOKEN}" in child_after_call["tool_results"]
    _assert_no_rogue_server_started(tmp_path)


def test_a_grandchild_inherits_the_same_mcp_tools(tmp_path: Path) -> None:
    _register_declarer(tmp_path, tools=[TaskToolSet.name])
    llm = _llm(
        [
            _call("task", prompt="delegate", subagent_type="declarer"),
            _call("list_mcp_tools"),
            _call("task", prompt="call echo", subagent_type="declarer"),
            _call("list_mcp_tools"),
            _mcp_call("parent.echo", text="deep"),
            _done(),
            _done(),
            _done(),
        ]
    )
    parent_mcp = {"parent": _server("echo", env={"MCP_TOKEN": SecretStr(TOKEN)})}

    _run(tmp_path, llm, [Tool(name=TaskToolSet.name)], parent_mcp)

    child, grandchild = llm.requests[1], llm.requests[3]
    assert "task" in child["tools"]
    _assert_catalog(llm.requests[2], {"parent.echo"})
    assert {"list_mcp_tools", "call_mcp_tool"} <= set(grandchild["tools"])
    _assert_catalog(llm.requests[4], {"parent.echo"})
    assert f"echo:deep:token={TOKEN}" in llm.requests[5]["tool_results"]
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
            _call("list_mcp_tools"),
            _mcp_call("parent.echo", text="resumed"),
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
    assert {"list_mcp_tools", "call_mcp_tool"} <= set(resumed["tools"])
    _assert_catalog(llm.requests[4], {"parent.echo"})
    assert f"echo:resumed:token={TOKEN}" in llm.requests[5]["tool_results"]
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
    server unexpanded, without the inherited secret registry.
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
            _call("list_mcp_tools"),
            _call("task", prompt="go", subagent_type="declarer"),
            _call("list_mcp_tools"),
            _mcp_call("plugin.plugged", text="hi"),
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

    mcp_tools = {"parent.echo", "plugin.plugged"}
    _assert_catalog(llm.requests[1], mcp_tools)
    _assert_catalog(llm.requests[3], mcp_tools)
    assert "plugged:hi:token=<secret-hidden>" in llm.requests[4]["tool_results"]
    _assert_no_rogue_server_started(tmp_path)
