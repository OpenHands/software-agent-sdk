"""Where skills come from: project, user home, install store, and how the
sources merge.

Builds a git repository in the workspace with skills at the repo root and in
a package directory (the same name in .agents/skills, .openhands/skills and
.openhands/microagents, plus a broken skill), user skills under HOME and
$OH_PERSISTENCE_DIR, and two installed skills (one disabled). Then loads
them with the loader functions and through conversations with
AgentContext(load_project_skills=True) / (load_user_skills=True).
"""

import os
import subprocess
from pathlib import Path

from recorder import blocks, calls, recording_llm
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, AgentContext, Conversation
from openhands.sdk.skills import (
    Skill,
    disable_skill,
    install_skill,
    load_available_skills,
    load_project_skills,
    load_user_skills,
    merge_skills_by_name,
)


paths = run_paths()
repo = paths["workspace"] / "repo"
pkg = repo / "pkg"
persist = Path(os.environ["OH_PERSISTENCE_DIR"])


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def skill_md(name: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {name} skill.\n---\n{body}\n"


write(repo / ".agents/skills/shared/SKILL.md", skill_md("shared", "ROOT-SHARED"))
write(repo / "AGENTS.md", "ROOT-AGENTS-MD\n")
write(pkg / "CLAUDE.md", "PKG-CLAUDE-MD\n")
write(pkg / ".agents/skills/dup/SKILL.md", skill_md("dup", "DUP-FROM-AGENTS"))
write(pkg / ".openhands/skills/dup.md", "DUP-FROM-OPENHANDS\n")
write(pkg / ".openhands/microagents/dup.md", "DUP-FROM-MICROAGENTS\n")
write(pkg / ".openhands/skills/only-oh.md", "ONLY-OPENHANDS\n")
write(pkg / ".openhands/microagents/only-micro.md", "ONLY-MICRO\n")
write(pkg / ".agents/skills/broken/SKILL.md", skill_md("Not A Valid Name", "BROKEN"))
write(pkg / ".agents/skills/layered/SKILL.md", skill_md("layered", "PROJECT-LAYER"))
subprocess.run(["git", "init", "-q", str(repo)], check=True)

home = paths["home"]
write(home / ".agents/skills/u-agents/SKILL.md", skill_md("u-agents", "USER-AGENTS"))
write(home / ".agents/skills/layered/SKILL.md", skill_md("layered", "USER-LAYER"))
write(persist / "skills/u-oh.md", "USER-OPENHANDS\n")
write(persist / "microagents/u-micro.md", "USER-MICRO\n")
src = paths["run"] / "skill-sources"
write(src / "u-installed/SKILL.md", skill_md("u-installed", "USER-INSTALLED"))
write(src / "u-off/SKILL.md", skill_md("u-off", "USER-OFF"))
install_skill(str(src / "u-installed"))
install_skill(str(src / "u-off"))
disable_skill("u-off")

out = {"ok": True}

# Project loader: order of directories, git root, a broken skill.
project = {s.name: s for s in load_project_skills(pkg)}
out["project_names"] = sorted(project)
out["dup_content"] = project["dup"].content.strip() if "dup" in project else None

# User loader: home, persistence dir, installed (enabled only).
user = {s.name: s for s in load_user_skills()}
out["user_names"] = sorted(user)

# Layered sources: project beats user.
available = load_available_skills(pkg, include_user=True, include_project=True)
out["layered_content"] = available["layered"].content.strip()
merged = merge_skills_by_name(
    [Skill(name="b", content="primary-b"), Skill(name="a", content="primary-a")],
    [Skill(name="a", content="secondary-a"), Skill(name="c", content="secondary-c")],
)
out["merged"] = [f"{s.name}:{s.content}" for s in merged]


def conversation_skills(context, workspace):
    agent = Agent(llm=recording_llm([{"text": "ok"}]), tools=[], agent_context=context)
    conversation = Conversation(agent=agent, workspace=str(workspace), visualizer=None)
    before = sorted(s.name for s in conversation.agent.agent_context.skills)
    conversation.send_message("hi")
    conversation.run()
    after = {s.name: s.content.strip() for s in conversation.agent.agent_context.skills}
    status = conversation.state.execution_status.value
    conversation.close()
    system = "\n".join(blocks(calls("agent")[-1]["messages"][0]))
    return before, after, status, system


before, after, status, system = conversation_skills(
    AgentContext(
        load_project_skills=True,
        skills=[
            Skill(name="dup", content="EXPLICIT-DUP"),
            Skill(name="keep", content="EXPLICIT-KEEP"),
        ],
        disabled_skills=["only-oh", "never-loaded"],
    ),
    pkg,
)
out["flag_before_first_message"] = before
out["flag_after"] = sorted(after)
out["flag_dup"] = after.get("dup")
out["flag_status"] = status
out["flag_system_has_project"] = (
    "ROOT-AGENTS-MD" in system and "<name>shared</name>" in system
)
out["flag_system_has_explicit_dup"] = "EXPLICIT-DUP" in system

_, after, _, _ = conversation_skills(
    AgentContext(
        load_user_skills=True,
        skills=[Skill(name="u-oh", content="EXPLICIT-U-OH")],
        disabled_skills=["u-micro"],
    ),
    paths["workspace"],
)
out["user_flag_names"] = sorted(after)
out["user_flag_u_oh"] = after.get("u-oh")

try:
    AgentContext(skills=[Skill(name="x", content="1"), Skill(name="x", content="2")])
    out["duplicate_error"] = None
except Exception as exc:
    out["duplicate_error"] = type(exc).__name__

_, after, _, _ = conversation_skills(AgentContext(), paths["workspace"])
out["no_flags_names"] = sorted(after)

emit(out)
