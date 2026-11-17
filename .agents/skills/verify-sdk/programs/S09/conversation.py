"""MCP servers inside a conversation, driven by a scripted agent.

Usage: conversation.py MODE

  main      one local stdio server (mcp_server.py) with ${VAR} placeholders
            in its env; conversation secrets; the agent calls echo, counter,
            nested, kind_echo, fail, picture, structured, secret references,
            and register_extra/extra/remove_extra.
  filter    Agent(filter_tools_regex="^(echo|counter)$").
  startup   a working server next to one that exits at startup, then the
            failing server alone.
  disabled  only a disabled server (its command would write a marker file).

The recorder keeps every LLM call's tool schemas; the program prints facts.
"""

import os
import sys
from pathlib import Path

from recorder import calls, recording_llm
from sdk_verify import conversation_dir, emit, run_paths

from openhands.sdk import Agent, Conversation
from openhands.sdk.event import ObservationEvent
from openhands.sdk.mcp import MCPServer


mode = sys.argv[1]
paths = run_paths()
SERVER = str(Path(__file__).with_name("mcp_server.py"))
os.environ["HOST_ONLY"] = "host-value"  # in the program's environment only


def server(*extra, **kwargs):
    return MCPServer(command=sys.executable, args=[SERVER, "stdio", *extra], **kwargs)


def call(tool, **args):
    return {"tool": tool, "args": args}


def converse(mcp_config, script, **agent_kwargs):
    agent = Agent(
        llm=recording_llm(script), tools=[], mcp_config=mcp_config, **agent_kwargs
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(paths["workspace"]),
        persistence_dir=str(paths["conversations"]),
        secrets={"API_KEY": "sekret-123", "MY_TOKEN": "tok-456"},
        visualizer=None,
    )
    error = None
    try:
        conversation.send_message("use the tools")
        conversation.run()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:300]
    return conversation, error


def results(conversation):
    rows = []
    for e in conversation.state.events:
        if isinstance(e, ObservationEvent):
            obs = e.observation
            kinds = sorted({type(c).__name__ for c in getattr(obs, "content", [])})
            rows.append(
                {
                    "tool": e.tool_name,
                    "text": obs.text[:300],
                    "is_error": obs.is_error,
                    "content": kinds,
                }
            )
    return rows


out = {"ok": True, "mode": mode}
if mode == "main":
    mcp_config = {
        "verify": server(
            env={"TOKEN": "${MY_TOKEN}", "OTHER": "${UNSET_VAR:-dflt-other}"},
        )
    }
    script = [
        call("echo", text="hello"),
        call("echo", text="with extras", summary="say it", security_risk="LOW"),
        call("counter"),
        call("counter"),
        call("nested", config={"title": "t", "tags": ["a", "b"], "limits": {"max": 3}}),
        call("kind_echo", kind="special"),
        call("fail", reason="on purpose"),
        call("picture"),
        call("structured"),
        call("reverse", text="$API_KEY"),
        call("reverse", text="${API_KEY}"),
        call("echo", text="${MISSING:-dflt} $HOST_ONLY"),
        call("echo", text="$API_KEY"),
        call("getenv", var="TOKEN", reverse=True),
        call("getenv", var="OTHER"),
        call("register_extra"),
        call("slow", seconds=1.5),
        call("extra", value=21),
        call("remove_extra"),
        call("slow", seconds=1.5),
        {"finish": "done"},
    ]
    conversation, error = converse(mcp_config, script)
    rows = results(conversation)
    out["error"] = error
    out["status"] = conversation.state.execution_status.value
    out["results"] = rows
    out["texts"] = [r["text"] for r in rows]
    out["errors"] = [r["tool"] for r in rows if r["is_error"]]
    agent_calls = calls("agent")
    first = agent_calls[0]
    out["first_tools"] = sorted(first["tools"])
    nested = (
        first["schemas"].get("nested", {}).get("function", {}).get("parameters", {})
    )
    out["nested_schema"] = {
        "required": nested.get("required"),
        "config_type": nested.get("properties", {}).get("config", {}).get("type"),
        "config_props": sorted(
            nested.get("properties", {}).get("config", {}).get("properties", {})
        ),
        "tags_type": nested.get("properties", {})
        .get("config", {})
        .get("properties", {})
        .get("tags", {})
        .get("type"),
        "max_type": nested.get("properties", {})
        .get("config", {})
        .get("properties", {})
        .get("limits", {})
        .get("properties", {})
        .get("max", {})
        .get("type"),
        "has_summary": "summary" in nested.get("properties", {}),
        "has_security_risk": "security_risk" in nested.get("properties", {}),
        "description": first["schemas"]
        .get("nested", {})
        .get("function", {})
        .get("description"),
    }
    out["kind_schema_props"] = sorted(
        first["schemas"]
        .get("kind_echo", {})
        .get("function", {})
        .get("parameters", {})
        .get("properties", {})
    )
    # Tool lists the model saw over time: extra appears, then disappears.
    out["calls_with_extra"] = [
        i for i, c in enumerate(agent_calls) if "extra" in c["tools"]
    ]
    out["last_tools_have_extra"] = "extra" in agent_calls[-1]["tools"]
    cdir = conversation_dir(conversation.id.hex)
    persisted = (cdir / "base_state.json").read_text() + "".join(
        p.read_text() for p in (cdir / "events").glob("*.json")
    )
    out["secret_values_persisted"] = [
        v for v in ("tok-456", "sekret-123") if v in persisted
    ]
    out["placeholder_persisted"] = "${MY_TOKEN}" in persisted
    conversation.close()

elif mode == "filter":
    conversation, error = converse(
        {"verify": server()},
        [{"finish": "done"}],
        filter_tools_regex="^(echo|counter)$",
    )
    out["error"] = error
    out["tools"] = sorted(calls("agent")[0]["tools"])
    conversation.close()

elif mode == "startup":
    conversation, error = converse(
        {"good": server("--name", "good"), "broken": server("--fail-start")},
        [call("good_echo", text="still here"), {"finish": "done"}],
    )
    out["error"] = error
    out["status"] = conversation.state.execution_status.value
    out["tools"] = sorted(calls("agent")[0]["tools"])
    out["texts"] = [r["text"] for r in results(conversation)]
    conversation.close()
    before = len(calls("agent"))
    conversation, error = converse(
        {"broken": server("--fail-start")}, [{"finish": "done"}]
    )
    out["only_broken_error"] = error
    out["only_broken_status"] = conversation.state.execution_status.value
    out["only_broken_tools"] = sorted(calls("agent")[before]["tools"])
    conversation.close()

elif mode == "disabled":
    marker = paths["run"] / "disabled-server-started"
    conversation, error = converse(
        {"off": MCPServer(command="sh", args=["-c", f"touch {marker}"], enabled=False)},
        [{"finish": "done"}],
    )
    out["error"] = error
    out["status"] = conversation.state.execution_status.value
    out["tools"] = sorted(calls("agent")[0]["tools"])
    out["marker_written"] = marker.exists()
    conversation.close()

emit(out)
