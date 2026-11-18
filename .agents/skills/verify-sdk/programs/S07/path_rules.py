"""Path-scoped rules: a `paths:` skill and a nested AGENTS.md.

The workspace has a root AGENTS.md (always-on repo context), an AgentSkills
rule for src/**/*.py, and docs/AGENTS.md. A scripted agent views files with
file_editor: a README, two Python files under src/, and a file in docs/.
"""

from recorder import blocks, calls, recording_llm
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, AgentContext, Conversation, Tool
from openhands.sdk.event import ObservationEvent
from openhands.tools.file_editor import FileEditorTool


workspace = run_paths()["workspace"]


def write(rel: str, content: str) -> None:
    path = workspace / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


write("AGENTS.md", "# Root\n\nROOT-RULE: be careful.\n")
write(
    ".agents/skills/py-rules/SKILL.md",
    "---\nname: py-rules\ndescription: Python rules.\npaths:\n  - src/**/*.py\n---\n"
    "PY-RULE: use type hints.\n",
)
write("docs/AGENTS.md", "DOCS-RULE: docs use sentence case.\n")
write("README.md", "readme\n")
write("src/app/main.py", "print('main')\n")
write("src/app/other.py", "print('other')\n")
write("docs/guide.md", "guide\n")

script = [
    {"tool": "file_editor", "args": {"command": "view", "path": str(workspace / p)}}
    for p in ("README.md", "src/app/main.py", "src/app/other.py", "docs/guide.md")
] + [{"finish": "done"}]
agent = Agent(
    llm=recording_llm(script),
    tools=[Tool(name=FileEditorTool.name)],
    agent_context=AgentContext(load_project_skills=True),
)
conversation = Conversation(agent=agent, workspace=str(workspace), visualizer=None)
conversation.send_message("Look around the project.")
conversation.run()
system = "\n".join(blocks(calls("agent")[0]["messages"][0]))
observations = [
    e.observation.text
    for e in conversation.state.events
    if isinstance(e, ObservationEvent) and e.tool_name == "file_editor"
]
# What the LLM saw for each tool result (the rule is appended there).
last = calls("agent")[-1]["messages"]
tool_texts = [
    "".join(getattr(c, "text", "") for c in m.content) for m in last if m.role == "tool"
]
rules = list(conversation.state.activated_path_rules)
conversation.close()

emit(
    {
        "ok": True,
        "tool_results": len(tool_texts),
        "readme_has_rule": any(r in tool_texts[0] for r in ("PY-RULE", "DOCS-RULE")),
        "main_has_py_rule": "PY-RULE" in tool_texts[1],
        "other_has_py_rule": "PY-RULE" in tool_texts[2],
        "guide_has_docs_rule": "DOCS-RULE" in tool_texts[3],
        "rules_in_system": [
            r for r in ("PY-RULE", "DOCS-RULE", "py-rules") if r in system
        ],
        "root_rule_in_system": "ROOT-RULE" in system,
        "activated_path_rules": rules,
    }
)
