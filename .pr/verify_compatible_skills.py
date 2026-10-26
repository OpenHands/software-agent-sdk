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
    write_skill(home / ".codex" / "skills", "codex-user-skill", "FROM_CODEX_HOME")

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
    # is unknown at construction), and the render decides per runtime. A
    # directly-built agent (no finalize) has no runtime yet (sourcing None), so
    # it must NOT advertise any auto-loaded compatible skill: it runs no
    # OpenHands tools, so the <SKILLS> catalog it renders is advisory only and a
    # vendor dir other than the CLI's own is unreachable. Its non-inherited
    # skills still render.
    from unittest.mock import patch

    from openhands.sdk import Conversation
    from openhands.sdk.agent import ACPAgent
    from openhands.sdk.launch.finalize import _apply_acp_skill_sourcing

    def rendered_suffix(agent: ACPAgent) -> str:
        project = ws / "acp-project"
        project.mkdir(exist_ok=True)
        with patch.object(ACPAgent, "_start_acp_server"):
            conversation = Conversation(agent=agent, workspace=str(project))
            conversation._ensure_agent_ready()
            loaded = conversation.agent
            assert isinstance(loaded, ACPAgent)
            return loaded._installed_suffix or ""

    acp = ACPAgent(
        acp_command=["npx", "@zed-industries/claude-agent-acp"],
        agent_context=AgentContext(load_compatible_skills=True),
    )
    acp_ctx = acp.agent_context
    assert acp_ctx is not None
    print("ACP constructed skills:", [s.name for s in acp_ctx.skills])
    assert acp.acp_skill_sourcing is None
    native_prompt = rendered_suffix(acp)
    # The ACP agent exposes no invoke_skill tool and the catalog omits each
    # skill's location, so no auto-loaded compatible skill is reachable — not
    # even from another vendor's directory.
    print("ACP default prompt has claude skill:", "claude-user-skill" in native_prompt)
    print("ACP default prompt has codex skill:", "codex-user-skill" in native_prompt)
    assert "claude-user-skill" not in native_prompt
    assert "codex-user-skill" not in native_prompt

    managed = _apply_acp_skill_sourcing(acp, "openhands_managed")
    assert isinstance(managed, ACPAgent)
    print(
        "ACP managed skills:",
        [s.name for s in managed.agent_context.skills],
    )
    assert "claude-user-skill" in rendered_suffix(managed)

print("OK")
