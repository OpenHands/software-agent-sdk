import tempfile
from pathlib import Path

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


def test_loader_gates_claude_vendor_file():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        skills_dir = _write_repo_with_vendor_files(root)
        repo_skills, _ = load_skills_from_dir(
            skills_dir, llm_model="litellm_proxy/anthropic/claude-sonnet-4"
        )
        assert "repo" in repo_skills
        assert "claude" in repo_skills
        assert "gemini" not in repo_skills


def test_loader_gates_gemini_vendor_file():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        skills_dir = _write_repo_with_vendor_files(root)
        repo_skills, _ = load_skills_from_dir(skills_dir, llm_model="gemini-2.5-pro")
        assert "repo" in repo_skills
        assert "gemini" in repo_skills
        assert "claude" not in repo_skills


def test_loader_excludes_both_for_other_models():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        skills_dir = _write_repo_with_vendor_files(root)
        repo_skills, _ = load_skills_from_dir(skills_dir, llm_model="openai/gpt-4o")
        assert "repo" in repo_skills
        assert "claude" not in repo_skills
        assert "gemini" not in repo_skills


def test_loader_uses_canonical_name_for_vendor_match():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        skills_dir = _write_repo_with_vendor_files(root)
        # Non-matching "proxy" model, but canonical matches Anthropic/Claude
        repo_skills, _ = load_skills_from_dir(
            skills_dir,
            llm_model="proxy/test-model",
            llm_model_canonical="anthropic/claude-sonnet-4",
        )
        assert "repo" in repo_skills
        assert "claude" in repo_skills
        assert "gemini" not in repo_skills
