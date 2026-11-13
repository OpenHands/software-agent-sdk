"""Build an agent from a tool preset, as a program does, and run one scripted
task with the preset's own file tools.

Usage: presets.py default|gpt5|gemini|structured

- default: get_default_agent(llm, cli_mode=True); terminal, file_editor and
  task_tracker do the task; also reports the preset tool lists.
- gpt5: get_gpt5_agent(llm, cli_mode=True); apply_patch writes the file.
- gemini: get_gemini_agent(llm, cli_mode=True); write_file, read_file with
  offset/limit, edit with an expected count, list_directory, and two edits
  of one file in a single response (parallel tool calls).
- structured: get_default_agent(..., finish_tool_response_schema=Model); the
  finish call carries the model's fields.
"""

import sys

from pydantic import BaseModel
from S14.toolkit import expand, tool_results, workspace
from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Conversation
from openhands.sdk.tool import default_tool_specs
from openhands.tools.preset.default import get_default_agent, get_default_tools
from openhands.tools.preset.gemini import get_gemini_agent, get_gemini_tools
from openhands.tools.preset.gpt5 import get_gpt5_agent, get_gpt5_tools


class Outcome(BaseModel):
    success: bool
    outcome_summary: str


mode = sys.argv[1]
ws = workspace()


def names(specs):
    return [t.name for t in specs]


if mode == "default":
    script = [
        {
            "tool": "task_tracker",
            "args": {
                "command": "plan",
                "task_list": [{"title": "write file", "status": "in_progress"}],
            },
        },
        {
            "tool": "file_editor",
            "args": {
                "command": "create",
                "path": "{ws}/hello.txt",
                "file_text": "hello\n",
            },
        },
        {"tool": "terminal", "args": {"command": "cat hello.txt"}},
        {"finish": "done"},
    ]
elif mode == "gpt5":
    script = [
        {
            "tool": "apply_patch",
            "args": {
                "patch": "*** Begin Patch\n*** Add File: GPT5_DEMO.txt\n"
                "+patched in\n*** End Patch"
            },
        },
        {"tool": "terminal", "args": {"command": "cat GPT5_DEMO.txt"}},
        {"finish": "done"},
    ]
elif mode == "gemini":
    lines = "".join(f"line {i}\n" for i in range(1, 21))
    script = [
        {
            "tool": "write_file",
            "args": {"file_path": "FACTS.txt", "content": lines + "alpha beta alpha\n"},
        },
        {
            "tool": "read_file",
            "args": {"file_path": "FACTS.txt", "offset": 5, "limit": 3},
        },
        {
            "tool": "edit",
            "args": {
                "file_path": "FACTS.txt",
                "old_string": "alpha",
                "new_string": "ALPHA",
                "expected_replacements": 2,
            },
        },
        {
            "tool": "edit",
            "args": {
                "file_path": "FACTS.txt",
                "old_string": "line 1\n",
                "new_string": "LINE ONE\n",
                "expected_replacements": 5,
            },
        },
        {
            "tools": [
                {
                    "tool": "edit",
                    "args": {
                        "file_path": "FACTS.txt",
                        "old_string": "line 2\n",
                        "new_string": "LINE TWO\n",
                    },
                },
                {
                    "tool": "edit",
                    "args": {
                        "file_path": "FACTS.txt",
                        "old_string": "line 3\n",
                        "new_string": "LINE THREE\n",
                    },
                },
            ]
        },
        {"tool": "list_directory", "args": {"dir_path": "."}},
        {"finish": "done"},
    ]
elif mode == "structured":
    script = [
        {"tool": "finish", "args": {"message": "missing a field", "success": True}},
        {
            "tool": "finish",
            "args": {
                "message": "all good",
                "success": True,
                "outcome_summary": "wrote nothing",
            },
        },
    ]
else:
    raise SystemExit(f"unknown mode {mode}")

llm = make_llm(expand(script))
if mode == "gpt5":
    agent = get_gpt5_agent(llm, cli_mode=True)
elif mode == "gemini":
    agent = get_gemini_agent(llm, cli_mode=True)
    agent = agent.model_copy(update={"tool_concurrency_limit": 4})
elif mode == "structured":
    agent = get_default_agent(llm, cli_mode=True, finish_tool_response_schema=Outcome)
else:
    agent = get_default_agent(llm, cli_mode=True)

# The structured finish schema is a Python class, so that conversation is
# not persisted (Tool params must be JSON-serializable to persist).
conversation = Conversation(
    agent=agent,
    workspace=str(ws),
    visualizer=None,
    **(
        {}
        if mode == "structured"
        else {"persistence_dir": str(run_paths()["conversations"])}
    ),
)
conversation.send_message("Do the task.")
conversation.run()
rows = tool_results(conversation)
tools_map = sorted(conversation.agent.tools_map)
condenser = conversation.agent.condenser
from openhands.sdk.event import ActionEvent  # noqa: E402


finish_actions = [
    e.action.model_dump(mode="json", exclude_none=True)
    for e in conversation.state.events
    if isinstance(e, ActionEvent) and e.tool_name == "finish" and e.action
]
finish_tool = conversation.agent.tools_map.get("finish")
parsed = None
if mode == "structured":
    parsed = finish_tool.parse_last_response(list(conversation.state.events))
status = conversation.state.execution_status.value
conversation.close()

result = {
    "ok": True,
    "mode": mode,
    "status": status,
    "agent_tool_specs": names(agent.tools),
    "tools_map": tools_map,
    "condenser": type(condenser).__name__ if condenser else None,
    "condenser_max_size": getattr(condenser, "max_size", None),
    "condenser_usage_id": getattr(getattr(condenser, "llm", None), "usage_id", None),
    "results": [{k: r[k] for k in ("tool", "is_error", "text")} for r in rows],
    "errors": [r["text"][:300] for r in rows if r["is_error"]],
    "files": sorted(p.name for p in ws.iterdir()),
}
if mode == "default":
    result["default_tools"] = names(get_default_tools())
    result["default_tools_no_browser_subagents"] = names(
        get_default_tools(enable_browser=False, enable_sub_agents=True)
    )
    result["sdk_default_tool_specs"] = names(default_tool_specs())
    result["gpt5_tools"] = names(get_gpt5_tools(enable_browser=False))
    result["gemini_tools"] = names(get_gemini_tools(enable_browser=False))
    result["hello"] = (ws / "hello.txt").read_text()
if mode == "gpt5":
    result["demo"] = (ws / "GPT5_DEMO.txt").read_text()
if mode == "gemini":
    result["facts"] = (ws / "FACTS.txt").read_text()
if mode == "structured":
    result["finish_action"] = finish_actions[-1] if finish_actions else None
    result["parsed_type"] = type(parsed).__name__
    result["parsed"] = parsed.model_dump() if parsed is not None else None
    schema = getattr(finish_tool, "response_schema", None)
    result["finish_schema"] = schema.__name__ if schema else None
emit(result)
