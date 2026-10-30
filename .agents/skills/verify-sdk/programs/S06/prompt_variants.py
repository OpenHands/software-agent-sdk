"""Which system prompt does a conversation send, for each way a program can
choose it?

Each variant builds an Agent the way a user would (model name, inline prompt,
template file, persona, planning preset, project vendor files), runs one
scripted turn, and reads the SystemPromptEvent the conversation saved.
"""

import re
from pathlib import Path

from sdk_verify import emit, run_paths, script_messages

from openhands.sdk import Agent, AgentContext, Conversation
from openhands.sdk.event import SystemPromptEvent
from openhands.sdk.skills import load_project_skills
from openhands.sdk.testing import TestLLM


workspace = run_paths()["workspace"]
GPT5_LINE = "Stream your thinking and responses while staying concise"
PREAMBLE_LINE = "ALWAYS send a brief preamble to the user"


def system_prompt(model="test-model", llm_kwargs=None, **agent_kwargs):
    llm = TestLLM.from_messages(
        script_messages([{"text": "ok"}]),
        model=model,
        usage_id="agent",
        **(llm_kwargs or {}),
    )
    agent = Agent(llm=llm, tools=[], **agent_kwargs)
    conversation = Conversation(agent=agent, workspace=str(workspace), visualizer=None)
    conversation.send_message("hi")
    conversation.run()
    event = next(
        e for e in conversation.state.events if isinstance(e, SystemPromptEvent)
    )
    conversation.close()
    dynamic = event.dynamic_context.text if event.dynamic_context else ""
    return event.system_prompt.text, dynamic


def tags(text):
    return sorted(set(re.findall(r"^<([A-Z_]+)>", text, re.M)))


def error_of(fn):
    try:
        fn()
        return None
    except Exception as exc:
        return type(exc).__name__


out = {"ok": True}

# The default preset, twice with different dynamic context: the static block
# must not change.
ctx_a = AgentContext(
    current_datetime="2026-01-01T00:00:00", system_message_suffix="AAA"
)
ctx_b = AgentContext(
    current_datetime="2027-02-02T00:00:00", system_message_suffix="BBB"
)
static_a, dynamic_a = system_prompt(agent_context=ctx_a)
static_b, dynamic_b = system_prompt(agent_context=ctx_b)
out["default_tags"] = tags(static_a)
out["static_same_across_contexts"] = static_a == static_b
out["static_has_dynamic_values"] = any(s in static_a for s in ("2026-01-01", "AAA"))
out["dynamic_has_values"] = "2026-01-01" in dynamic_a and "AAA" in dynamic_a
out["soul_default"] = "You are OpenHands agent" in static_a
soul_file = Path(run_paths()["openhands"]) / "SOUL.md"
out["soul_custom"] = soul_file.is_file() and soul_file.read_text().strip() in static_a

# Planning preset.
planning, _ = system_prompt(system_prompt_filename="system_prompt_planning.j2")
out["planning_differs"] = planning != static_a
out["planning_tags"] = tags(planning)

# Model-specific sections.
for name, model, kwargs, agent_kwargs in [
    ("plain", "custom-made-model", {}, {}),
    ("gpt5", "gpt-5", {}, {}),
    ("codex", "gpt-5-codex", {}, {}),
    ("canonical", "proxy/custom", {"model_canonical_name": "gpt-5-mini"}, {}),
    (
        "override",
        "gpt-5-codex",
        {},
        {"system_prompt_kwargs": {"model_variant": "gpt-5"}},
    ),
]:
    static, _ = system_prompt(model=model, llm_kwargs=kwargs, **agent_kwargs)
    out[f"{name}_gpt5_section"] = GPT5_LINE in static
    out[f"{name}_preamble"] = PREAMBLE_LINE in static

# Vendor files in the project: only for their own models.
(workspace / "AGENTS.md").write_text("# Project\n\nRepo baseline text\n")
(workspace / "claude.md").write_text("Claude-Specific Instructions")
(workspace / "gemini.md").write_text("Gemini-Specific Instructions")
skills = load_project_skills(workspace)
for name, model in [
    ("claude", "anthropic/claude-sonnet-4"),
    ("gemini", "gemini/gemini-2.5-pro"),
    ("gpt4o", "openai/gpt-4o"),
]:
    _, dynamic = system_prompt(model=model, agent_context=AgentContext(skills=skills))
    out[f"{name}_vendor"] = [
        v for v in ("Claude-Specific", "Gemini-Specific") if v in dynamic
    ]
    out[f"{name}_baseline"] = "Repo baseline text" in dynamic

# Inline prompt, custom template with kwargs, missing template.
inline, _ = system_prompt(system_prompt="You are a haiku bot.")
out["inline_starts"] = inline.startswith("You are a haiku bot.")
out["inline_has_default_tags"] = bool(set(tags(inline)) & {"ROLE", "SOUL"})
template = run_paths()["run"] / "my_prompt.j2"
template.write_text("Team {{ team }} prompt for {{ model_name }}.")
custom, _ = system_prompt(
    system_prompt_filename=str(template), system_prompt_kwargs={"team": "blue"}
)
out["template_rendered"] = custom
out["missing_template_error"] = error_of(
    lambda: system_prompt(system_prompt_filename=str(template) + ".missing")
)
out["missing_relative_error"] = error_of(
    lambda: system_prompt(system_prompt_filename="no_such_prompt.j2")
)
out["inline_and_file_error"] = error_of(
    lambda: Agent(
        llm=TestLLM.from_messages([]),
        system_prompt="x",
        system_prompt_filename=str(template),
    )
)

# Persona.
persona, _ = system_prompt(persona="You are a ramen chef.")
out["persona_starts"] = persona.startswith("You are a ramen chef.")
out["persona_tags"] = tags(persona)

emit(out)
