"""End-to-end evidence for compatible AgentSkill discovery (Issue #5569)."""

import os
import tempfile
from pathlib import Path

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.skills import (
    load_available_skills,
    load_project_skills,
    skill as skill_mod,
)


def write_skill(root: Path, name: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} desc\n---\n{body}\n"
    )


with tempfile.TemporaryDirectory() as td:
    home = Path(td) / "home"
    home.mkdir()
    os.environ["HOME"] = str(home)
    skill_mod.USER_SKILLS_DIRS = [home / ".agents" / "skills"]
    write_skill(home / ".claude" / "skills", "claude-user-skill", "FROM_CLAUDE_HOME")

    off = load_available_skills(include_user=True)
    print("disabled user skills:", sorted(off))

    on = load_available_skills(include_user=True, include_compatible=True)
    print("enabled user skills:", sorted(on))
    print("source:", on["claude-user-skill"].source)

    ws = Path(td) / "repo"
    write_skill(ws / ".agents" / "skills", "dup", "FROM_AGENTS")
    write_skill(ws / ".cursor" / "skills", "dup", "FROM_CURSOR")
    write_skill(ws / ".codex" / "skills", "codex-only", "FROM_CODEX")
    # Loose Markdown in a vendor dir must not become a permanent-context skill.
    (ws / ".claude" / "skills").mkdir(parents=True)
    (ws / ".claude" / "skills" / "notes.md").write_text("loose note\n")

    proj_off = load_project_skills(ws)
    proj_on = load_project_skills(ws, include_compatible=True)
    print("project (off):", [s.name for s in proj_off])
    print("project (on): ", sorted(s.name for s in proj_on))
    dup = next(s for s in proj_on if s.name == "dup")
    print("collision winner content:", dup.content.strip())

    # Repo-root .agents beats a vendor skill in a subdirectory.
    (ws / ".git").mkdir()
    subdir = ws / "src"
    subdir.mkdir()
    write_skill(subdir / ".claude" / "skills", "dup", "FROM_SUBDIR_VENDOR")
    sub_dup = next(
        s
        for s in load_project_skills(subdir, include_compatible=True)
        if s.name == "dup"
    )
    print("subdir workdir winner content:", sub_dup.content.strip())

    ctx = AgentContext(load_compatible_skills=True)
    print("AgentContext skills:", [s.name for s in ctx.skills])

    # ACP runtime sourcing: the validator keeps vendor user skills (the runtime
    # is unknown at construction), and finalize decides per runtime.
    from openhands.sdk.agent import ACPAgent
    from openhands.sdk.launch.finalize import _apply_acp_skill_sourcing

    acp = ACPAgent(
        acp_command=["claude-code-acp"],
        agent_context=AgentContext(load_compatible_skills=True),
    )
    acp_ctx = acp.agent_context
    assert acp_ctx is not None
    print("ACP constructed skills:", [s.name for s in acp_ctx.skills])

    managed = _apply_acp_skill_sourcing(acp, "openhands_managed")
    assert managed.agent_context is not None
    print(
        "ACP managed skills:",
        [s.name for s in managed.agent_context.skills],
    )

    native = _apply_acp_skill_sourcing(acp, "native")
    assert native.agent_context is not None
    print("ACP native skills:", [s.name for s in native.agent_context.skills])

print("OK")
