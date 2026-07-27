from pathlib import Path

import pytest

from openhands.sdk.skills import (
    Skill,
    SkillCatalogError,
    SkillResources,
    SkillRuntime,
)


def make_runtime(tmp_path: Path) -> SkillRuntime:
    root = tmp_path / "example"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("# Example", encoding="utf-8")
    (root / "references" / "guide.md").write_text("guide body", encoding="utf-8")
    skill = Skill(
        name="example",
        content="# Example",
        description="Example workflow helper",
        source=str(root / "SKILL.md"),
        is_agentskills_format=True,
        resources=SkillResources(
            skill_root=str(root),
            references=["guide.md"],
        ),
    )
    return SkillRuntime([skill])


def test_runtime_lists_selects_and_reads(tmp_path):
    runtime = make_runtime(tmp_path)

    assert [entry.name for entry in runtime.list()] == ["example"]
    assert [
        entry.name for entry in runtime.select("workflow helper").candidate_entries
    ] == ["example"]
    result = runtime.read("example", "references/guide.md")
    assert result.handle.relative_path == "references/guide.md"
    assert result.handle.contents == "guide body"


def test_runtime_rejects_escape_unknown_and_missing(tmp_path):
    runtime = make_runtime(tmp_path)

    with pytest.raises(SkillCatalogError, match="unknown skill"):
        runtime.read("missing")
    with pytest.raises(SkillCatalogError, match="escapes skill root"):
        runtime.read("example", "../secret")
    with pytest.raises(SkillCatalogError, match="missing.md"):
        runtime.read("example", "references/missing.md")
