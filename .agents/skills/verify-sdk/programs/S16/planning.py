"""Plan, then execute, the way example 24 chains two conversations, with
scripted models.

Usage: planning.py default|legacy|absolute|relative

- default: get_planning_agent(llm) in a workspace with a small project. The
  planner greps and globs, views the fresh PLAN.md, is refused an edit of
  any other file, and fills in PLAN.md. A second conversation with
  get_default_agent(llm, cli_mode=True) in the same workspace reads the plan
  and implements it.
- legacy: the workspace already has a root PLAN.md; the planner's tool uses it.
- absolute: Tool(name="planning_file_editor", params={"plan_path": <abs>}).
- relative: the same with a relative plan_path.
"""

import sys
from pathlib import Path

from S14.toolkit import converse, tool_results, workspace
from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Conversation, Tool
from openhands.sdk.event import SystemPromptEvent
from openhands.tools.planning_file_editor import PlanningFileEditorTool
from openhands.tools.preset.default import get_default_agent
from openhands.tools.preset.planning import (
    get_plan_headers,
    get_planning_agent,
    get_planning_tools,
)


mode = sys.argv[1]
ws = workspace()
(ws / "app").mkdir(exist_ok=True)
(ws / "app" / "main.py").write_text("def handler():\n    return 'TODO'\n")
result: dict = {"ok": True, "mode": mode}

if mode == "default":
    plan = ws / ".agents_tmp" / "PLAN.md"
    planner_script = [
        {"tool": "grep", "args": {"pattern": "TODO"}},
        {"tool": "glob", "args": {"pattern": "**/*.py"}},
        {
            "tool": "planning_file_editor",
            "args": {"command": "view", "path": str(plan)},
        },
        {
            "tool": "planning_file_editor",
            "args": {
                "command": "create",
                "path": str(ws / "app" / "new.py"),
                "file_text": "x = 1\n",
            },
        },
        {
            "tool": "planning_file_editor",
            "args": {
                "command": "str_replace",
                "path": str(ws / "app" / "main.py"),
                "old_str": "TODO",
                "new_str": "done",
            },
        },
        {
            "tool": "planning_file_editor",
            "args": {
                "command": "str_replace",
                "path": str(plan),
                "old_str": "# 1. OBJECTIVE",
                "new_str": "# 1. OBJECTIVE\nMake handler() return 'hello'.",
            },
        },
        {"finish": "Plan written."},
    ]
    planner = get_planning_agent(make_llm(planner_script))
    conversation = Conversation(
        agent=planner,
        workspace=str(ws),
        persistence_dir=str(run_paths()["conversations"]),
        visualizer=None,
    )
    conversation.send_message("Plan how to make handler() return 'hello'.")
    conversation.run()
    rows = [r for r in tool_results(conversation) if r["tool"] != "finish"]
    system = next(
        e for e in conversation.state.events if isinstance(e, SystemPromptEvent)
    )
    tools = conversation.agent.tools_map
    condenser = conversation.agent.condenser
    conversation.close()
    result.update(
        {
            "planner_status": "finished" if rows else "no tools ran",
            "planner_specs": [t.name for t in planner.tools],
            "planner_tools": sorted(tools),
            "planner_results": [
                {k: r[k] for k in ("tool", "is_error")} | {"text": r["text"][:300]}
                for r in rows
            ],
            "plan_headers": [
                line for line in plan.read_text().splitlines() if line.startswith("#")
            ],
            "plan_has_objective_text": "Make handler() return 'hello'."
            in plan.read_text(),
            "main_untouched": "TODO" in (ws / "app" / "main.py").read_text(),
            "new_file_absent": not (ws / "app" / "new.py").exists(),
            "description_names_plan": str(plan)
            in tools["planning_file_editor"].description,
            "prompt_has_plan_structure": "IMPLEMENTATION STEPS"
            in system.system_prompt.text,
            "condenser": type(condenser).__name__,
            "condenser_max_size": condenser.max_size,
            "condenser_keep_first": condenser.keep_first,
            "condenser_usage_id": condenser.llm.usage_id,
            "headers_match_preset": plan.read_text().startswith(
                get_plan_headers().splitlines()[0]
            ),
        }
    )
    executor_script = [
        {"tool": "file_editor", "args": {"command": "view", "path": str(plan)}},
        {
            "tool": "file_editor",
            "args": {
                "command": "str_replace",
                "path": str(ws / "app" / "main.py"),
                "old_str": "'TODO'",
                "new_str": "'hello'",
            },
        },
        {
            "tool": "terminal",
            "args": {
                "command": "python3 -c 'import app.main as m; print(m.handler())'"
            },
        },
        {"finish": "Implemented."},
    ]
    execution = Conversation(
        agent=get_default_agent(make_llm(executor_script), cli_mode=True),
        workspace=str(ws),
        persistence_dir=str(run_paths()["conversations"]),
        visualizer=None,
    )
    execution.send_message(f"Implement the plan in {plan}.")
    execution.run()
    rows = [r for r in tool_results(execution) if r["tool"] != "finish"]
    execution.close()
    result.update(
        {
            "executor_saw_plan": "Make handler() return 'hello'." in rows[0]["text"],
            "executor_output": rows[-1]["text"][:200],
            "main_after": (ws / "app" / "main.py").read_text(),
        }
    )
else:
    params: dict = {}
    if mode == "legacy":
        (ws / "PLAN.md").write_text("# legacy plan\n")
    elif mode == "absolute":
        params = {"plan_path": str(run_paths()["run"] / "plans" / "deep" / "PLAN.md")}
    elif mode == "relative":
        params = {"plan_path": "plans/PLAN.md"}
    result["spec_params"] = get_planning_tools(**params)[-1].params
    try:
        conversation = converse(
            [Tool(name=PlanningFileEditorTool.name, params=params or None)],
            [{"finish": "ok"}],
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:300]
    else:
        description = conversation.agent.tools_map["planning_file_editor"].description
        conversation.close()
        result["plan_location"] = description.split("Your PLAN.md location: ")[
            1
        ].splitlines()[0]
        result["default_plan_created"] = (ws / ".agents_tmp" / "PLAN.md").exists()
        result["legacy_kept"] = (ws / "PLAN.md").exists() and (
            ws / "PLAN.md"
        ).read_text() == "# legacy plan\n"
        if mode == "absolute":
            result["absolute_created"] = Path(params["plan_path"]).is_file()
emit(result)
