"""Where sub-agent types come from, through `openhands.sdk.subagent`:
programmatic registration, Markdown files in the project and the user's
home, plugins, and the factories that turn a definition into an Agent.

The program lays out files the way a user would:
  <workspace>/.agents/agents/{reviewer,shared}.md, README.md, nested/deep.md
  <workspace>/.openhands/agents/{reviewer,helper}.md   (reviewer loses)
  ~/.agents/agents/{shared,user-only}.md                (shared loses)
  $OH_PERSISTENCE_DIR/agents/late.md                   (the ~/.openhands base)
"""

from pathlib import Path

from pydantic import SecretStr
from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import LLM, Agent
from openhands.sdk.context.condenser import LLMSummarizingCondenser, NoOpCondenser
from openhands.sdk.hooks import HookConfig
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.subagent import (
    AgentDefinition,
    agent_definition_to_factory,
    discover_agents,
    get_agent_factory,
    get_registered_agent_definitions,
    load_project_agents,
    load_user_agents,
    register_agent,
    register_agent_if_absent,
    register_file_agents,
    register_plugin_agents,
)
from openhands.tools.terminal import TerminalTool  # registers "terminal"


paths = run_paths()
ws = paths["workspace"]
home = paths["home"]


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def raises(fn) -> str | None:
    try:
        fn()
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"[:200]


REVIEWER = """---
name: reviewer
description: >-
  Reviews code. <example>review the diff</example> <example>check style</example>
tools: [terminal, file_editor]
skills: house-style
model: inherit
color: blue
max_iteration_per_run: 7
max_budget_per_run: 0.5
permission_mode: always_confirm
condenser: none
hooks:
  PostToolUse:
    - matcher: terminal
      hooks: [{command: "echo reviewed"}]
mcp_config:
  fetch: {command: uvx, args: [mcp-server-fetch]}
team: platform
---
You are a careful reviewer.
"""
write(ws / ".agents/agents/reviewer.md", REVIEWER)
write(
    ws / ".agents/agents/shared.md",
    "---\nname: shared\ndescription: project copy\n---\nproject",
)
write(ws / ".agents/agents/README.md", "# not an agent\n")
write(
    ws / ".agents/agents/nested/deep.md", "---\nname: deep\ndescription: nested\n---\nx"
)
write(
    ws / ".openhands/agents/reviewer.md",
    "---\nname: reviewer\ndescription: openhands copy\n---\nx",
)
write(
    ws / ".openhands/agents/helper.md", "---\nname: helper\ndescription: helps\n---\nx"
)
write(
    home / ".agents/agents/shared.md",
    "---\nname: shared\ndescription: user copy\n---\nuser",
)
write(
    home / ".agents/agents/user-only.md",
    "---\nname: user-only\ndescription: mine\n---\nx",
)
write(
    paths["openhands"] / "agents/late.md",
    "---\nname: late\ndescription: persistence dir\n---\nx",
)
write(
    ws / ".agents/skills/house-style/SKILL.md",
    "---\nname: house-style\ndescription: style\n---\nUse tabs.",
)
write(ws / "bad.md", "---\nname: bad\npermission_mode: sometimes\n---\nx")

# S11.markdown-schema
reviewer = AgentDefinition.load(ws / ".agents/agents/reviewer.md")
# S11.file-discovery: discovery without registering
project_names = [a.name for a in load_project_agents(ws)]
user_names = [a.name for a in load_user_agents()]
discovered = {a.name: (a.level, a.description) for a in discover_agents(ws)}
registered_before_files = sorted(d.name for d in get_registered_agent_definitions())

# S11.register-programmatic and S11.register-if-absent
parent_llm = make_llm([], usage_id="parent")


def make_alpha(llm: LLM) -> Agent:
    return Agent(llm=llm, tools=[])


register_agent("alpha", make_alpha, "first alpha")
register_agent("shared", make_alpha, "programmatic shared")
duplicate = raises(lambda: register_agent("alpha", make_alpha, "again"))
unknown = raises(lambda: get_agent_factory("nobody"))
kept = register_agent_if_absent(
    "alpha", lambda llm: Agent(llm=llm, tools=[]), "second alpha"
)
alpha = get_agent_factory("alpha")

# S11.file-precedence: project before user, programmatic never overwritten
file_registered = register_file_agents(ws)
shared = get_agent_factory("shared").definition.description
reviewer_source = get_agent_factory("reviewer").definition.source

# S11.plugin-agents
plugin_registered = register_plugin_agents(
    [
        AgentDefinition(name="alpha", description="plugin alpha"),
        AgentDefinition(
            name="plug",
            description="from a plugin",
            hooks=HookConfig.from_dict({"Stop": [{"hooks": [{"command": "true"}]}]}),
        ),
    ],
    work_dir=ws,
)
plug = get_agent_factory("plug").definition

# S11.definition-factory
built = agent_definition_to_factory(
    reviewer.model_copy(update={"tools": ["terminal"]}), work_dir=ws
)(parent_llm)
default_condenser = agent_definition_to_factory(
    AgentDefinition(name="d", tools=["terminal"]), work_dir=ws
)(parent_llm)
unknown_tool = raises(
    lambda: agent_definition_to_factory(
        AgentDefinition(name="t", tools=["no_such_tool"])
    )(parent_llm)
)
unknown_skill = raises(
    lambda: agent_definition_to_factory(
        AgentDefinition(name="s", skills=["no-such-skill"]), work_dir=ws
    )
)

# S11.definition-model: a named LLM profile, inherit, and a missing profile
store_dir = ws / "profiles"
LLMProfileStore(store_dir).save(
    "fast", LLM(model="openai/gpt-4o-mini", usage_id="fast", api_key=SecretStr("x"))
)
profiled = agent_definition_to_factory(
    AgentDefinition(name="p", model="fast", profile_store_dir=str(store_dir))
)(parent_llm)
inherited = agent_definition_to_factory(AgentDefinition(name="i", model="inherit"))(
    parent_llm
)
missing_profile = raises(
    lambda: agent_definition_to_factory(
        AgentDefinition(name="m", model="nope", profile_store_dir=str(store_dir))
    )(parent_llm)
)
emit(
    {
        "ok": True,
        "schema": {
            "name": reviewer.name,
            "tools": reviewer.tools,
            "skills": reviewer.skills,
            "model": reviewer.model,
            "color": reviewer.color,
            "max_iteration_per_run": reviewer.max_iteration_per_run,
            "max_budget_per_run": reviewer.max_budget_per_run,
            "permission_mode": reviewer.permission_mode,
            "condenser": type(reviewer.condenser).__name__,
            "hooks_post_tool_use": len(reviewer.hooks.post_tool_use)
            if reviewer.hooks
            else 0,
            "mcp_servers": sorted(reviewer.mcp_config or {}),
            "system_prompt": reviewer.system_prompt,
            "examples": reviewer.when_to_use_examples,
            "metadata": reviewer.metadata,
        },
        "invalid_permission_mode": raises(lambda: AgentDefinition.load(ws / "bad.md")),
        "project_names": project_names,
        "user_names": user_names,
        "discovered": discovered,
        "registered_before_files": registered_before_files,
        "duplicate_error": duplicate,
        "unknown_error": unknown,
        "if_absent_returned": kept,
        "alpha_description": alpha.definition.description,
        "file_registered": sorted(file_registered),
        "shared_description": shared,
        "reviewer_from": "/.agents/agents/"
        if "/.agents/agents/" in (reviewer_source or "")
        else reviewer_source,
        "plugin_registered": plugin_registered,
        "plug_hooks_stop": len(plug.hooks.stop) if plug.hooks else 0,
        "factory": {
            "tools": [t.name for t in built.tools],
            "suffix": built.agent_context.system_message_suffix
            if built.agent_context
            else None,
            "skills": [s.name for s in built.agent_context.skills]
            if built.agent_context
            else [],
            "mcp_servers": sorted(built.mcp_config or {}),
            "condenser": type(built.condenser).__name__,
            "default_condenser": type(default_condenser.condenser).__name__,
            "default_condenser_usage_id": default_condenser.condenser.llm.usage_id
            if isinstance(default_condenser.condenser, LLMSummarizingCondenser)
            else None,
        },
        "noop_is_noop": isinstance(built.condenser, NoOpCondenser),
        "unknown_tool_error": unknown_tool,
        "unknown_skill_error": unknown_skill,
        "profile_model": profiled.llm.model,
        "inherit_same_llm": inherited.llm is parent_llm,
        "missing_profile_error": missing_profile,
        "terminal_tool": TerminalTool.name,
    }
)
