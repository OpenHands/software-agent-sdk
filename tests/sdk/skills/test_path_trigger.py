"""Tests for path-scoped skills ("rules"): PathTrigger, glob matching, loading,
partition exclusion, and the AgentContext tool-use injection matcher."""

from pathlib import Path

import pytest

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.skills import KeywordTrigger, PathTrigger, Skill, load_project_skills
from openhands.sdk.skills.exceptions import SkillValidationError
from openhands.sdk.skills.skill import path_matches_glob


@pytest.mark.parametrize(
    ("file_path", "pattern", "expected"),
    [
        # ** crosses directory separators at any depth (including zero).
        ("src/api/x.ts", "src/api/**/*.ts", True),
        ("src/api/v1/deep/x.ts", "src/api/**/*.ts", True),
        ("x.test.tsx", "**/*.test.tsx", True),
        ("a/b/x.test.tsx", "**/*.test.tsx", True),
        # A slash-less pattern matches the basename at any depth (gitignore).
        ("a/b/x.ts", "*.ts", True),
        ("x.ts", "*.ts", True),
        ("pkg/Makefile", "Makefile", True),
        # * stays within a single path segment.
        ("src/api/x.ts", "src/*/x.ts", True),
        ("src/api/v1/x.ts", "src/*/x.ts", False),
        # Non-matches.
        ("README.md", "*.ts", False),
        ("a/b/x.ts", "src/**", False),
        ("src/a", "src/**", True),
        ("", "**/*.ts", False),
        # `?` matches exactly one non-separator char (never crosses `/`).
        ("ab.ts", "a?.ts", True),
        ("abc.ts", "a?.ts", False),
        ("a/b.ts", "a?.ts", False),
        # `*` stays within one segment even with an explicit prefix.
        ("a/b.ts", "a/*.ts", True),
        ("a/b/c.ts", "a/*.ts", False),
        # Glob metacharacters are literal, not regex: `.` matches only a dot and
        # `+`/parens match themselves (guards against an unescaped-regex bug).
        ("a.b.ts", "*.b.ts", True),
        ("fileXname.ts", "file.name.ts", False),
        ("a+b.ts", "a+b.ts", True),
        ("aaab.ts", "a+b.ts", False),
        ("a(b).ts", "a(b).ts", True),
        # Matching is case-sensitive.
        ("README.md", "readme.md", False),
        # `*` matches leading-dot files (gitignore semantics, unlike shell glob).
        ("src/.env", "src/*", True),
    ],
)
def test_path_matches_glob(file_path: str, pattern: str, expected: bool) -> None:
    assert path_matches_glob(file_path, pattern) is expected


def test_empty_pattern_never_matches() -> None:
    assert path_matches_glob("anything.ts", "") is False


def _write_rule(directory: Path, name: str, frontmatter: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"---\n{frontmatter}\n---\n{body}\n")
    return path


def test_paths_frontmatter_yaml_list_creates_path_trigger(tmp_path: Path) -> None:
    path = _write_rule(
        tmp_path,
        "api.md",
        'paths:\n  - "src/api/**/*.ts"\n  - "**/*.test.tsx"',
        "Use zod for API validation.",
    )
    skill = Skill.load(path)
    assert isinstance(skill.trigger, PathTrigger)
    assert skill.trigger.paths == ["src/api/**/*.ts", "**/*.test.tsx"]
    assert skill.content.strip() == "Use zod for API validation."


def test_paths_frontmatter_comma_string_creates_path_trigger(tmp_path: Path) -> None:
    path = _write_rule(tmp_path, "r.md", "paths: src/**/*.py, tests/**/*.py", "body")
    skill = Skill.load(path)
    assert isinstance(skill.trigger, PathTrigger)
    assert skill.trigger.paths == ["src/**/*.py", "tests/**/*.py"]


def test_match_path_trigger_returns_matched_pattern(tmp_path: Path) -> None:
    skill = Skill(name="r", content="c", trigger=PathTrigger(paths=["src/**/*.ts"]))
    assert skill.match_path_trigger("src/api/x.ts") == "src/**/*.ts"
    assert skill.match_path_trigger("README.md") is None


def test_path_trigger_is_inert_on_text_matching() -> None:
    """A PathTrigger never fires on user-message text (only on file paths)."""
    skill = Skill(name="r", content="c", trigger=PathTrigger(paths=["src/**/*.ts"]))
    assert skill.match_trigger("please edit src/api/x.ts and src/**/*.ts") is None


def test_keyword_skill_does_not_match_paths() -> None:
    from openhands.sdk.skills import KeywordTrigger

    skill = Skill(name="k", content="c", trigger=KeywordTrigger(keywords=["deploy"]))
    assert skill.match_path_trigger("src/api/x.ts") is None


def test_path_rule_loads_from_skills_dir(tmp_path: Path) -> None:
    """A path rule is just a skill with ``paths:`` frontmatter in a skills dir."""
    _write_rule(
        tmp_path / ".openhands" / "skills",
        "api.md",
        'paths:\n  - "src/api/**/*.ts"',
        "API rule",
    )
    skills = load_project_skills(tmp_path)
    by_name = {s.name: s for s in skills}
    assert "api" in by_name
    assert isinstance(by_name["api"].trigger, PathTrigger)


def test_partition_excludes_path_rules_from_catalog_and_repo_context() -> None:
    """Path rules go in neither <available_skills> nor <REPO_CONTEXT>."""
    ctx = AgentContext(
        skills=[
            Skill(name="rule", content="c", trigger=PathTrigger(paths=["**/*.ts"])),
            Skill(name="repo", content="always"),  # trigger=None => repo context
        ]
    )
    repo_skills, available_skills = ctx._partition_skills()
    assert [s.name for s in repo_skills] == ["repo"]
    assert [s.name for s in available_skills] == []


def test_get_tool_use_suffix_match_nomatch_and_dedup() -> None:
    ctx = AgentContext(
        skills=[
            Skill(
                name="api",
                content="Use zod.",
                trigger=PathTrigger(paths=["src/api/**/*.ts"]),
            )
        ]
    )
    # Matching path injects content and reports the activated rule.
    result = ctx.get_tool_use_suffix("src/api/users.ts", skip_skill_names=[])
    assert result is not None
    content, activated = result
    assert activated == ["api"]
    assert "Use zod." in content.text

    # Non-matching path injects nothing.
    assert ctx.get_tool_use_suffix("README.md", skip_skill_names=[]) is None

    # Already-activated rule is skipped (dedup).
    assert ctx.get_tool_use_suffix("src/api/users.ts", skip_skill_names=["api"]) is None


def test_get_tool_use_suffix_empty_path_returns_none() -> None:
    ctx = AgentContext(
        skills=[Skill(name="r", content="c", trigger=PathTrigger(paths=["**/*.ts"]))]
    )
    assert ctx.get_tool_use_suffix("", skip_skill_names=[]) is None


def test_multiple_rules_match_one_path_all_injected() -> None:
    ctx = AgentContext(
        skills=[
            Skill(name="ts", content="TS rule", trigger=PathTrigger(paths=["**/*.ts"])),
            Skill(
                name="api",
                content="API rule",
                trigger=PathTrigger(paths=["src/api/**"]),
            ),
            Skill(name="py", content="PY rule", trigger=PathTrigger(paths=["**/*.py"])),
        ]
    )
    result = ctx.get_tool_use_suffix("src/api/users.ts", skip_skill_names=[])
    assert result is not None
    content, activated = result
    assert activated == ["ts", "api"]  # both match, py excluded
    assert "TS rule" in content.text and "API rule" in content.text
    assert "PY rule" not in content.text


def test_path_rule_forces_disable_model_invocation() -> None:
    """Path rules must not be advertised or invocable; the flag is forced
    regardless of construction path (direct or frontmatter)."""
    direct = Skill(name="r", content="c", trigger=PathTrigger(paths=["**/*.ts"]))
    assert direct.disable_model_invocation is True


def test_path_rule_serialization_round_trip() -> None:
    skill = Skill(
        name="api",
        content="Use zod.",
        source="/repo/.openhands/skills/api.md",
        trigger=PathTrigger(paths=["src/api/**/*.ts", "**/*.test.ts"]),
    )
    back = Skill.model_validate_json(skill.model_dump_json())
    assert isinstance(back.trigger, PathTrigger)
    assert back.trigger.paths == ["src/api/**/*.ts", "**/*.test.ts"]
    assert back.disable_model_invocation is True


def test_paths_and_triggers_frontmatter_paths_wins(tmp_path: Path) -> None:
    """A file with both `paths:` and `triggers:` becomes a PathTrigger rule."""
    path = _write_rule(
        tmp_path, "both.md", 'paths:\n  - "**/*.ts"\ntriggers:\n  - "deploy"', "body"
    )
    skill = Skill.load(path)
    assert isinstance(skill.trigger, PathTrigger)


@pytest.mark.parametrize("value", ["", "[]"])
def test_empty_paths_is_not_a_path_trigger(tmp_path: Path, value: str) -> None:
    """Empty `paths:` frontmatter falls through to trigger=None (not a rule)."""
    path = _write_rule(tmp_path, "r.md", f"paths: {value}", "body")
    skill = Skill.load(path)
    assert not isinstance(skill.trigger, PathTrigger)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a/**, **/*.ts", ["a/**", "**/*.ts"]),  # comma string, whitespace trimmed
        (["a/**", " b ", ""], ["a/**", "b"]),  # list, trimmed + empties dropped
        ("  ,  ", None),  # only separators/whitespace -> None
        ([], None),
        (None, None),
    ],
)
def test_parse_paths(value, expected) -> None:
    assert Skill._parse_paths(value) == expected


def test_invalid_paths_type_raises() -> None:
    with pytest.raises(SkillValidationError, match="paths must be a string or list"):
        Skill._parse_paths(5)  # type: ignore[arg-type]


def test_match_path_trigger_none_for_non_path_triggers() -> None:
    kw = Skill(name="k", content="c", trigger=KeywordTrigger(keywords=["deploy"]))
    repo = Skill(name="r", content="c")  # trigger=None
    assert kw.match_path_trigger("src/api/x.ts") is None
    assert repo.match_path_trigger("src/api/x.ts") is None
