import tempfile
from pathlib import Path

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.skills import load_skills_from_dir


def _write_repo_with_vendor_files(root: Path):
    # repo skill under .openhands/skills/repo.md
    skills_dir = root / ".openhands" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    repo_text = (
        "---\n# type: repo\nversion: 1.0.0\nagent: CodeActAgent\n---\n\nRepo baseline\n"
    )
    (skills_dir / "repo.md").write_text(repo_text)

    # vendor files in repo root
    (root / "claude.md").write_text("Claude-Specific Instructions")
    (root / "gemini.md").write_text("Gemini-Specific Instructions")

    return skills_dir


def test_context_gates_claude_vendor_file():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        skills_dir = _write_repo_with_vendor_files(root)
        repo_skills, _ = load_skills_from_dir(skills_dir)
        ac = AgentContext(skills=list(repo_skills.values()))
        suffix = ac.get_system_message_suffix(
            llm_model="litellm_proxy/anthropic/claude-sonnet-4"
        )
        assert suffix is not None
        assert "Repo baseline" in suffix
        assert "Claude-Specific Instructions" in suffix
        assert "Gemini-Specific Instructions" not in suffix


def test_context_gates_gemini_vendor_file():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        skills_dir = _write_repo_with_vendor_files(root)
        repo_skills, _ = load_skills_from_dir(skills_dir)
        ac = AgentContext(skills=list(repo_skills.values()))
        suffix = ac.get_system_message_suffix(llm_model="gemini-2.5-pro")
        assert suffix is not None
        assert "Repo baseline" in suffix
        assert "Gemini-Specific Instructions" in suffix
        assert "Claude-Specific Instructions" not in suffix


def test_context_excludes_both_for_other_models():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        skills_dir = _write_repo_with_vendor_files(root)
        repo_skills, _ = load_skills_from_dir(skills_dir)
        ac = AgentContext(skills=list(repo_skills.values()))
        suffix = ac.get_system_message_suffix(llm_model="openai/gpt-4o")
        assert suffix is not None
        assert "Repo baseline" in suffix
        assert "Claude-Specific Instructions" not in suffix
        assert "Gemini-Specific Instructions" not in suffix


def test_context_uses_canonical_name_for_vendor_match():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        skills_dir = _write_repo_with_vendor_files(root)
        repo_skills, _ = load_skills_from_dir(skills_dir)
        ac = AgentContext(skills=list(repo_skills.values()))
        # Non-matching "proxy" model, but canonical matches Anthropic/Claude
        suffix = ac.get_system_message_suffix(
            llm_model="proxy/test-model",
            llm_model_canonical="anthropic/claude-sonnet-4",
        )
        assert suffix is not None
        assert "Repo baseline" in suffix
        assert "Claude-Specific Instructions" in suffix
        assert "Gemini-Specific Instructions" not in suffix


def test_context_includes_all_when_model_unknown():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        skills_dir = _write_repo_with_vendor_files(root)
        repo_skills, _ = load_skills_from_dir(skills_dir)
        ac = AgentContext(skills=list(repo_skills.values()))
        # No model info provided -> backward-compatible include-all behavior
        suffix = ac.get_system_message_suffix()
        assert suffix is not None
        assert "Repo baseline" in suffix
        assert "Claude-Specific Instructions" in suffix
        assert "Gemini-Specific Instructions" in suffix
