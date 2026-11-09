"""Client-defined tools the way example 53 uses them: JSON specs passed as
`Conversation(..., client_tools=SPECS)`, a callback that intercepts the
agent's calls, and no Python executor.

Usage: client_tools.py conversation|resume|registration

- conversation: a scripted agent (terminal plus three client tools) calls
  `show_notification`, `navigate_to` and `set_filter` (which has an argument
  named `kind`), and once breaks the `level` enum. The conversation id is
  written to `client_conversation_id` in the run directory.
- resume: a new process reopens that conversation with an agent that has
  only the terminal and no `client_tools`, and the scripted agent calls
  `navigate_to` again.
- registration: `ClientToolSpec` validation, `register_client_tools`
  reuse, conflicts and collisions, and names shared with built-ins.
"""

import json
import sys

from S14.toolkit import converse, tool_results
from sdk_verify import emit, run_paths

from openhands.sdk import Event, Tool
from openhands.sdk.event import ActionEvent
from openhands.sdk.tool import ClientToolSpec, ToolAnnotations, register_client_tools
from openhands.tools.terminal import TerminalTool


SPECS = [
    ClientToolSpec(
        name="show_notification",
        description="Display a notification banner in the UI.",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Text to display"},
                "level": {
                    "type": "string",
                    "enum": ["info", "warning", "error"],
                    "description": "Severity",
                },
            },
            "required": ["message", "level"],
        },
    ),
    ClientToolSpec(
        name="navigate_to",
        description="Navigate the UI to a route.",
        parameters={
            "type": "object",
            "properties": {
                "route": {"type": "string", "description": "Route, e.g. '/settings'"}
            },
            "required": ["route"],
        },
        annotations=ToolAnnotations(readOnlyHint=True),
    ),
    ClientToolSpec(
        name="set_filter",
        description="Filter the file list in the UI.",
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "File kind, e.g. 'python'"},
                "value": {"type": "string", "description": "Filter text"},
            },
            "required": ["kind"],
        },
    ),
]
NAMES = {spec.name for spec in SPECS}
id_file = run_paths()["run"] / "client_conversation_id"
mode = sys.argv[1]
result: dict = {"ok": True, "mode": mode}
intercepted: list[dict] = []


def on_event(event: Event) -> None:
    if isinstance(event, ActionEvent) and event.tool_name in NAMES:
        args = event.action.model_dump(mode="json") if event.action else {}
        intercepted.append(
            {
                "tool": event.tool_name,
                "args": args,
                "raw_arguments": json.loads(event.tool_call.arguments),
                "action_type": type(event.action).__name__,
            }
        )


def error_of(fn) -> str:
    try:
        fn()
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"[:300]
    return "no error"


if mode == "conversation":
    script = [
        {"tool": "terminal", "args": {"command": "echo 3 files"}},
        {"tool": "show_notification", "args": {"message": "3 files", "level": "info"}},
        {"tool": "navigate_to", "args": {"route": "/files"}},
        {"tool": "set_filter", "args": {"kind": "python", "value": "test_"}},
        {"tool": "show_notification", "args": {"message": "x", "level": "critical"}},
        {"finish": "notified"},
    ]
    conversation = converse(
        [Tool(name=TerminalTool.name)],
        script,
        conversation_kwargs={"client_tools": SPECS, "callbacks": [on_event]},
    )
    id_file.write_text(str(conversation.id))
    rows = [r for r in tool_results(conversation) if r["tool"] != "finish"]
    tools = conversation.agent.tools_map
    events = [
        e
        for e in conversation.state.events
        if isinstance(e, ActionEvent) and e.tool_name == "set_filter"
    ]
    round_trip = (
        Event.model_validate_json(events[0].model_dump_json()) if events else None
    )
    notify = tools["show_notification"].to_openai_tool(
        add_security_risk_prediction=True
    )["function"]["parameters"]
    navigate = tools["navigate_to"].to_openai_tool(add_security_risk_prediction=True)[
        "function"
    ]["parameters"]
    result.update(
        {
            "status": conversation.state.execution_status.value,
            "tools_map": sorted(tools),
            "agent_spec_names": [t.name for t in conversation.agent.tools],
            "results": [
                {k: r[k] for k in ("tool", "kind", "is_error", "text")} for r in rows
            ],
            "intercepted": intercepted,
            "kind_arg_round_trip": round_trip is not None
            and round_trip.action.model_dump(mode="json")
            == events[0].action.model_dump(mode="json"),
            "kind_arg_value": round_trip.action.model_dump(mode="json").get("kind")
            if round_trip
            else None,
            "notify_properties": list(notify["properties"]),
            "notify_required": notify.get("required"),
            "notify_level_enum": notify["properties"]["level"].get("enum"),
            "navigate_properties": list(navigate["properties"]),
            "mcp_schema_is_original": tools["show_notification"].to_mcp_tool()[
                "inputSchema"
            ]
            == SPECS[0].parameters,
        }
    )
    for row in result["results"]:
        row["text"] = row["text"][:300]
    conversation.close()

elif mode == "resume":
    import uuid

    from sdk_verify import make_llm

    from openhands.sdk import Agent, Conversation

    cid = uuid.UUID(id_file.read_text().strip())
    agent = Agent(
        llm=make_llm(
            [
                {"tool": "navigate_to", "args": {"route": "/home"}},
                {"finish": "back home"},
            ]
        ),
        tools=[Tool(name=TerminalTool.name)],
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(run_paths()["workspace"]),
        persistence_dir=str(run_paths()["conversations"]),
        conversation_id=cid,
        callbacks=[on_event],
        visualizer=None,
    )
    result["resumed_same_id"] = conversation.id == cid
    result["tools_before_run"] = sorted(t.name for t in conversation.agent.tools)
    conversation.send_message("Go home.")
    conversation.run()
    rows = [r for r in tool_results(conversation) if r["tool"] != "finish"]
    result["status"] = conversation.state.execution_status.value
    result["tools_map"] = sorted(conversation.agent.tools_map)
    result["last_result"] = {
        k: rows[-1][k] for k in ("tool", "kind", "is_error", "text")
    }
    result["intercepted"] = intercepted
    conversation.close()

elif mode == "registration":
    import openhands.tools.terminal  # noqa: F401  (registers "terminal")
    from openhands.sdk.tool import (
        ClientToolRegistrationError,
        ClientToolSchemaConflictError,
        resolve_tool,
    )

    result["array_parameters"] = error_of(
        lambda: ClientToolSpec(
            name="bad", description="d", parameters={"type": "array"}
        )
    )
    spec = SPECS[0]
    result["spec_round_trip"] = (
        ClientToolSpec.model_validate_json(spec.model_dump_json()) == spec
    )
    result["default_parameters"] = ClientToolSpec(
        name="ping", description="d"
    ).parameters
    first = register_client_tools([spec])
    second = register_client_tools([spec])
    result["returned_specs"] = [t.model_dump(mode="json") for t in first]
    conversation = converse([], [], run=False)
    conversation._ensure_agent_ready()
    one = resolve_tool(first[0], conversation.state)[0]
    two = resolve_tool(second[0], conversation.state)[0]
    conversation.close()
    result["same_action_type"] = one.action_type is two.action_type
    result["action_type_name"] = one.action_type.__name__
    result["executor_reply"] = one(one.action_type(message="m", level="info")).text
    changed = ClientToolSpec(
        name="show_notification",
        description="Same name, other schema.",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
    )
    result["schema_conflict"] = error_of(lambda: register_client_tools([changed]))
    result["schema_conflict_type"] = ClientToolSchemaConflictError.__name__
    ping = ClientToolSpec(name="ping", description="d")
    result["duplicate_in_call"] = error_of(lambda: register_client_tools([ping, ping]))
    result["registered_tool_name"] = error_of(
        lambda: register_client_tools(
            [ClientToolSpec(name="terminal", description="d")]
        )
    )
    result["registration_error_type"] = ClientToolRegistrationError.__name__

    # Names shared with built-ins: attached finish vs. unattached think.
    finish_spec = ClientToolSpec(name="finish", description="Client-side finish.")
    think_spec = ClientToolSpec(name="think", description="Client-side think.")
    result["finish_attached"] = error_of(
        lambda: converse(
            [], [{"finish": "x"}], conversation_kwargs={"client_tools": [finish_spec]}
        ).close()
    )
    think = converse(
        [],
        [{"tool": "think", "args": {}}, {"finish": "done"}],
        conversation_kwargs={"client_tools": [think_spec]},
        agent_kwargs={"include_default_tools": ["FinishTool"]},
    )
    result["think_client_status"] = think.state.execution_status.value
    result["think_client_results"] = [
        {k: r[k] for k in ("tool", "kind", "text")}
        for r in tool_results(think)
        if r["tool"] == "think"
    ]
    think.close()

else:
    raise SystemExit(f"unknown mode {mode}")

emit(json.loads(json.dumps(result, default=str)))
