from pathlib import Path

import pytest

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.skills import load_project_skills


_REPO_BASELINE_TEXT = (
    "---\n# type: repo\nversion: 1.0.0\nagent: CodeActAgent\n---\n\nRepo baseline\n"
)
_REPO_BASELINE_TEXT = (
    "---\n# type: repo\nversion: 1.0.0\nagent: CodeActAgent\n---\n\nRepo baseline\n"
)
# Different baseline formats for testing backward compatibility:
# - _REPO_BASELINE_TEXT: legacy format with frontmatter (used in
#   .openhands/skills/repo.md)
# - _AGENTS_BASELINE_TEXT: simple markdown format (used in AGENTS.md)
_AGENTS_BASELINE_TEXT = "# Project Guidelines\n\nRepo baseline\n"


def _write_repo_with_vendor_files(root: Path, baseline_source: str) -> None:
    """Create test repository with baseline and vendor-specific skill files.

    Args:
        root: Root directory for the test repository
        baseline_source: Either "repo_md" (legacy .openhands/skills/repo.md)
                        or "agents_md" (AGENTS.md in repo root)
    """
    if baseline_source == "repo_md":
        skills_dir = root / ".openhands" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "repo.md").write_text(_REPO_BASELINE_TEXT)
    elif baseline_source == "agents_md":
        (root / "AGENTS.md").write_text(_AGENTS_BASELINE_TEXT)
    else:
        raise ValueError(f"Unknown baseline_source: {baseline_source}")

    (root / "claude.md").write_text("Claude-Specific Instructions")
    (root / "gemini.md").write_text("Gemini-Specific Instructions")


# Test both loading mechanisms for backward compatibility:
# - "repo_md": Legacy .openhands/skills/repo.md (still supported for existing repos)
# - "agents_md": New approach using AGENTS.md in repo root (recommended)
@pytest.mark.parametrize("baseline_source", ["repo_md", "agents_md"])
def test_context_gates_claude_vendor_file(tmp_path: Path, baseline_source: str):
    _write_repo_with_vendor_files(tmp_path, baseline_source)
    skills = load_project_skills(tmp_path)
    ac = AgentContext(skills=skills)
    suffix = ac.get_system_message_suffix(
        llm_model="litellm_proxy/anthropic/claude-sonnet-4"
    )
    assert suffix is not None
    assert "Repo baseline" in suffix
    assert "Claude-Specific Instructions" in suffix
    assert "Gemini-Specific Instructions" not in suffix


@pytest.mark.parametrize("baseline_source", ["repo_md", "agents_md"])
def test_context_gates_gemini_vendor_file(tmp_path: Path, baseline_source: str):
    _write_repo_with_vendor_files(tmp_path, baseline_source)
    skills = load_project_skills(tmp_path)
    ac = AgentContext(skills=skills)
    suffix = ac.get_system_message_suffix(llm_model="gemini-2.5-pro")
    assert suffix is not None
    assert "Repo baseline" in suffix
    assert "Gemini-Specific Instructions" in suffix
    assert "Claude-Specific Instructions" not in suffix


@pytest.mark.parametrize("baseline_source", ["repo_md", "agents_md"])
def test_context_excludes_both_for_other_models(tmp_path: Path, baseline_source: str):
    _write_repo_with_vendor_files(tmp_path, baseline_source)
    skills = load_project_skills(tmp_path)
    ac = AgentContext(skills=skills)
    suffix = ac.get_system_message_suffix(llm_model="openai/gpt-4o")
    assert suffix is not None
    assert "Repo baseline" in suffix
    assert "Claude-Specific Instructions" not in suffix
    assert "Gemini-Specific Instructions" not in suffix


@pytest.mark.parametrize("baseline_source", ["repo_md", "agents_md"])
def test_context_uses_canonical_name_for_vendor_match(
    tmp_path: Path, baseline_source: str
):
    _write_repo_with_vendor_files(tmp_path, baseline_source)
    skills = load_project_skills(tmp_path)
    ac = AgentContext(skills=skills)
    suffix = ac.get_system_message_suffix(
        llm_model="proxy/test-model",
        llm_model_canonical="anthropic/claude-sonnet-4",
    )
    assert suffix is not None
    assert "Repo baseline" in suffix
    assert "Claude-Specific Instructions" in suffix
    assert "Gemini-Specific Instructions" not in suffix


@pytest.mark.parametrize("baseline_source", ["repo_md", "agents_md"])
def test_context_includes_all_when_model_unknown(tmp_path: Path, baseline_source: str):
    _write_repo_with_vendor_files(tmp_path, baseline_source)
    skills = load_project_skills(tmp_path)
    ac = AgentContext(skills=skills)
    # No model info provided -> backward-compatible include-all behavior
    suffix = ac.get_system_message_suffix()
    assert suffix is not None
    assert "Repo baseline" in suffix
    assert "Claude-Specific Instructions" in suffix
    assert "Gemini-Specific Instructions" in suffix
