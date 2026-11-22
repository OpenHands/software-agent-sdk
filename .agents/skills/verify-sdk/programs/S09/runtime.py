"""MCP servers that join a LocalConversation after it starts.

Usage: runtime.py MODE

  add     LocalConversation.add_mcp_servers(): on an initialized agent (tools
          named `<server>_<tool>`), before the first message (configured like
          any server), and the refusals (duplicate name, SSE, a server that
          cannot start, a disabled server).
  resume  a server added at runtime, then the conversation reopened from its
          persistence directory and asked to call the same tool again.
  budget  LocalConversation(mcp_startup_budget=1.0) with a server whose
          handshake is 4 s late and one that fails after 3 s, then the
          default (no budget) with the slow server.

The recorder keeps every scripted LLM call's tool names; the program prints
facts.
"""

import sys
import time
import uuid
from pathlib import Path

from recorder import calls, recording_llm
from sdk_verify import emit, run_paths

from openhands.sdk import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import AgentErrorEvent, ObservationEvent
from openhands.sdk.mcp import MCPError, MCPServer, MCPServerStartError


mode = sys.argv[1]
paths = run_paths()
SERVER = str(Path(__file__).with_name("mcp_server.py"))


def server(*extra, **kwargs):
    return MCPServer(command=sys.executable, args=[SERVER, "stdio", *extra], **kwargs)


def call(tool, **args):
    return {"tool": tool, "args": args}


def conversation(script, mcp_config=None, usage_id="agent", **kwargs):
    agent = Agent(
        llm=recording_llm(script, usage_id=usage_id),
        tools=[],
        mcp_config=mcp_config or {},
    )
    return LocalConversation(
        agent=agent,
        workspace=str(paths["workspace"]),
        persistence_dir=str(paths["conversations"]),
        visualizer=None,
        **kwargs,
    )


def texts(conv):
    return [
        e.observation.text[:200]
        for e in conv.state.events
        if isinstance(e, ObservationEvent)
    ]


def errors(conv):
    return [e.error[:200] for e in conv.state.events if isinstance(e, AgentErrorEvent)]


def refusal(conv, servers):
    try:
        conv.add_mcp_servers(servers)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"[:200]
    return None


def mcp_tools(conv):
    return sorted(n for n in conv.agent.tools_map if n not in ("finish", "think"))


out = {"ok": True, "mode": mode}
if mode == "add":
    # 1. On an initialized agent (send_message initializes it).
    conv = conversation(
        [call("verify_echo", text="added"), {"finish": "done"}], usage_id="ready"
    )
    conv.send_message("use the added tool")
    out["ready_tools_before"] = mcp_tools(conv)
    conv.add_mcp_servers({"verify": server()})
    out["ready_tools_after_add"] = mcp_tools(conv)
    out["ready_mcp_config"] = sorted(conv.agent.mcp_config)
    out["ready_state_mcp_config"] = sorted(conv.state.agent.mcp_config)
    out["duplicate_error"] = refusal(conv, {"verify": server()})
    out["sse_error"] = refusal(
        conv, {"remote": MCPServer(url="http://127.0.0.1:9/sse", transport="sse")}
    )
    try:
        conv.add_mcp_servers({"broken": server("--fail-start")})
        out["start_failure_error"] = None
    except Exception as exc:
        out["start_failure_error"] = f"{type(exc).__name__}: {exc}"[:200]
        out["start_failure_is_mcp_error"] = isinstance(exc, MCPError)
        if isinstance(exc, MCPServerStartError):
            out["start_failure_server_name"] = exc.server_name
            out["start_failure_cause"] = type(exc.__cause__).__name__
    out["disabled_error"] = refusal(
        conv, {"off": MCPServer(command="sh", args=["-c", "exit 1"], enabled=False)}
    )
    out["mcp_config_after_refusals"] = sorted(conv.agent.mcp_config)
    conv.run()
    out["ready_status"] = conv.state.execution_status.value
    out["ready_first_call_tools"] = sorted(calls("ready")[0]["tools"])
    out["ready_texts"] = texts(conv)
    conv.close()

    # 2. Before the first message: recorded, connected when the agent starts.
    conv = conversation(
        [call("echo", text="early"), {"finish": "done"}], usage_id="early"
    )
    conv.add_mcp_servers({"solo": server()})
    out["early_mcp_config"] = sorted(conv.agent.mcp_config)
    conv.send_message("use the tool")
    conv.run()
    out["early_status"] = conv.state.execution_status.value
    out["early_first_call_tools"] = sorted(calls("early")[0]["tools"])
    out["early_texts"] = texts(conv)
    conv.close()

elif mode == "resume":
    cid = uuid.uuid4()
    conv = conversation(
        [call("verify_echo", text="one"), {"finish": "done"}],
        usage_id="first",
        conversation_id=cid,
        delete_on_close=False,
    )
    conv.send_message("first")
    conv.add_mcp_servers({"verify": server()})
    conv.run()
    out["first_texts"] = texts(conv)
    persisted = sorted(conv.state.agent.mcp_config)
    conv.close()
    out["persisted_mcp_config"] = persisted
    # Reopen it the way a program resumes: the same agent configuration.
    agent = Agent(
        llm=recording_llm(
            [call("verify_echo", text="two"), {"finish": "done"}], usage_id="second"
        ),
        tools=[],
        mcp_config={"verify": server()},
    )
    conv = LocalConversation(
        agent=agent,
        workspace=str(paths["workspace"]),
        persistence_dir=str(paths["conversations"]),
        conversation_id=cid,
        visualizer=None,
    )
    conv.send_message("again")
    conv.run()
    out["resumed_first_call_tools"] = sorted(calls("second")[0]["tools"])
    out["resumed_texts"] = texts(conv)
    out["resumed_second_call_ok"] = any("echo: two" in t for t in texts(conv))
    out["resumed_errors"] = errors(conv)
    conv.close()

elif mode == "budget":
    late_fail = MCPServer(command="sh", args=["-c", "sleep 3; exit 3"])
    conv = conversation(
        [call("slow_echo", text="late"), {"finish": "done"}],
        {"slow": server("--name", "slow", "--slow-start", "4"), "dies": late_fail},
        usage_id="budget",
        mcp_startup_budget=1.0,
    )
    t0 = time.monotonic()
    conv.send_message("use the late tool")
    out["budget_send_seconds"] = round(time.monotonic() - t0, 2)
    out["budget_tools_after_send"] = mcp_tools(conv)
    t1 = time.monotonic()
    conv.run()
    out["budget_run_seconds"] = round(time.monotonic() - t1, 2)
    out["budget_status"] = conv.state.execution_status.value
    out["budget_first_call_tools"] = sorted(calls("budget")[0]["tools"])
    out["budget_texts"] = texts(conv)
    conv.close()

    conv = conversation(
        [{"finish": "done"}],
        {"slow": server("--name", "slow", "--slow-start", "4")},
        usage_id="default",
    )
    t0 = time.monotonic()
    conv.send_message("hi")
    out["default_send_seconds"] = round(time.monotonic() - t0, 2)
    out["default_tools_after_send"] = mcp_tools(conv)
    conv.close()

emit(out)
