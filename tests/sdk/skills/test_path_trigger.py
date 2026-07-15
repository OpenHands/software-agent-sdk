"""Tests for path-scoped skills ("rules"): PathTrigger, glob matching, loading,
partition exclusion, and the AgentContext tool-use injection matcher."""

import shutil
import subprocess
from pathlib import Path

import pytest

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.skills import (
    KeywordTrigger,
    PathTrigger,
    Skill,
    load_project_skills,
    utils as skills_utils,
)
from openhands.sdk.skills.exceptions import SkillValidationError
from openhands.sdk.skills.skill import path_matches_glob


_HAS_GIT = shutil.which("git") is not None


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


def test_paths_and_triggers_frontmatter_paths_wins(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A file with both `paths:` and `triggers:` becomes a PathTrigger rule, and
    the dropped `triggers:` are surfaced via a warning."""
    path = _write_rule(
        tmp_path, "both.md", 'paths:\n  - "**/*.ts"\ntriggers:\n  - "deploy"', "body"
    )
    with caplog.at_level("WARNING"):
        skill = Skill.load(path)
    assert isinstance(skill.trigger, PathTrigger)
    assert "paths" in caplog.text and "'triggers'" in caplog.text
    assert "deploy" in caplog.text


def test_paths_and_inputs_frontmatter_warns_inputs_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`paths:` alongside `inputs:` wins and warns that `inputs:` are ignored."""
    path = _write_rule(
        tmp_path,
        "both.md",
        'paths:\n  - "**/*.ts"\ninputs:\n  - name: x\n    description: d',
        "body",
    )
    with caplog.at_level("WARNING"):
        skill = Skill.load(path)
    assert isinstance(skill.trigger, PathTrigger)
    assert "'inputs'" in caplog.text


def test_paths_only_frontmatter_does_not_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A path rule with no `triggers:`/`inputs:` loads without a warning."""
    path = _write_rule(tmp_path, "r.md", 'paths:\n  - "**/*.ts"', "body")
    with caplog.at_level("WARNING"):
        skill = Skill.load(path)
    assert isinstance(skill.trigger, PathTrigger)
    assert "will be ignored" not in caplog.text


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


# ---------------------------------------------------------------------------
# Nested AGENTS.md / third-party files -> directory-scoped path rules.
# A root AGENTS.md stays always-on; nested ones inject only when the agent
# touches a file under their directory.
# ---------------------------------------------------------------------------


def _make_agents_workspace(tmp_path: Path) -> Path:
    """Root AGENTS.md plus two nested ones (one shallow, one deep)."""
    (tmp_path / "AGENTS.md").write_text("ROOT guidance.\n")
    server = tmp_path / "server"
    server.mkdir()
    (server / "AGENTS.md").write_text("SERVER rule: validate inputs.\n")
    deep = tmp_path / "pkg" / "sub"
    deep.mkdir(parents=True)
    (deep / "AGENTS.md").write_text("SUB rule.\n")
    return tmp_path


def _force_walk_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make discovery use the filesystem-walk path (no git)."""
    monkeypatch.setattr(skills_utils, "_git_worktree_relpaths", lambda _wd: None)


def _assert_nested_rules(skills_by_name: dict[str, Skill]) -> None:
    # Root stays always-on (full content, no path trigger).
    assert skills_by_name["agents"].trigger is None

    server = skills_by_name["agents:server"]
    assert isinstance(server.trigger, PathTrigger)
    assert server.trigger.paths == ["server/**"]
    assert server.disable_model_invocation is True  # forced for path rules
    assert "SERVER rule" in server.content

    sub = skills_by_name["agents:pkg/sub"]
    assert isinstance(sub.trigger, PathTrigger)
    assert sub.trigger.paths == ["pkg/sub/**"]


def test_nested_agents_md_become_path_rules_walk_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_walk_fallback(monkeypatch)
    _make_agents_workspace(tmp_path)
    skills = {s.name: s for s in load_project_skills(tmp_path)}
    _assert_nested_rules(skills)


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
def test_nested_agents_md_become_path_rules_git(tmp_path: Path) -> None:
    _make_agents_workspace(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    skills = {s.name: s for s in load_project_skills(tmp_path)}
    _assert_nested_rules(skills)


def test_find_nested_excludes_top_level_and_prunes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_walk_fallback(monkeypatch)
    (tmp_path / "AGENTS.md").write_text("root")  # top-level: not nested
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "AGENTS.md").write_text("a")
    vendored = tmp_path / "node_modules" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "AGENTS.md").write_text("vendored")  # node_modules: pruned
    hidden = tmp_path / ".venv" / "pkg"
    hidden.mkdir(parents=True)
    (hidden / "AGENTS.md").write_text("hidden")  # hidden dir: pruned

    found = skills_utils.find_nested_third_party_files(
        tmp_path, Skill.PATH_TO_THIRD_PARTY_SKILL_NAME
    )
    rel_dirs = {rel_dir.as_posix() for _path, rel_dir in found}
    assert rel_dirs == {"a"}


def test_nested_rule_injection_and_dedup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_walk_fallback(monkeypatch)
    _make_agents_workspace(tmp_path)
    rules = [
        s for s in load_project_skills(tmp_path) if isinstance(s.trigger, PathTrigger)
    ]
    ctx = AgentContext(skills=rules)

    # Touching a file under server/ injects the server rule once.
    result = ctx.get_tool_use_suffix(file_path="server/app.py", skip_skill_names=[])
    assert result is not None
    _content, activated = result
    assert activated == ["agents:server"]

    # Already-activated rule is not re-injected.
    assert (
        ctx.get_tool_use_suffix(file_path="server/other.py", skip_skill_names=activated)
        is None
    )

    # A file matching no nested rule injects nothing.
    assert ctx.get_tool_use_suffix(file_path="README.md", skip_skill_names=[]) is None


def test_nested_overlapping_rules_both_inject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_walk_fallback(monkeypatch)
    # A child dir's glob (pkg/sub/**) is a subset of its parent's (pkg/**), so
    # a file under the child matches BOTH nested rules ("inherit up the tree").
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "AGENTS.md").write_text("PARENT rule.\n")
    (tmp_path / "pkg" / "sub").mkdir()
    (tmp_path / "pkg" / "sub" / "AGENTS.md").write_text("CHILD rule.\n")
    rules = [
        s for s in load_project_skills(tmp_path) if isinstance(s.trigger, PathTrigger)
    ]
    ctx = AgentContext(skills=rules)

    result = ctx.get_tool_use_suffix(file_path="pkg/sub/foo.py", skip_skill_names=[])
    assert result is not None
    content, activated = result
    assert sorted(activated) == ["agents:pkg", "agents:pkg/sub"]  # both, once each
    assert "PARENT rule." in content.text
    assert "CHILD rule." in content.text


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
def test_nested_discovery_includes_untracked_excludes_gitignored(
    tmp_path: Path,
) -> None:
    """Uncommitted AGENTS.md still count; .gitignore'd ones do not."""
    (tmp_path / "kept").mkdir()
    (tmp_path / "kept" / "AGENTS.md").write_text("kept")  # untracked, not ignored
    (tmp_path / ".gitignore").write_text("skipped/\n")
    (tmp_path / "skipped").mkdir()
    (tmp_path / "skipped" / "AGENTS.md").write_text("nope")  # gitignored
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    # AGENTS.md files are deliberately left uncommitted.

    found = skills_utils.find_nested_third_party_files(
        tmp_path, Skill.PATH_TO_THIRD_PARTY_SKILL_NAME
    )
    rel_dirs = {rel_dir.as_posix() for _path, rel_dir in found}
    assert rel_dirs == {"kept"}


def test_nested_non_agents_third_party_name_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nested CLAUDE.md becomes a directory-scoped ``claude:<dir>`` rule."""
    _force_walk_fallback(monkeypatch)
    svc = tmp_path / "svc"
    svc.mkdir()
    (svc / "CLAUDE.md").write_text("claude guidance")
    skills = {s.name: s for s in load_project_skills(tmp_path)}
    rule = skills["claude:svc"]
    assert isinstance(rule.trigger, PathTrigger)
    assert rule.trigger.paths == ["svc/**"]


def test_nested_unreadable_file_skipped_without_aborting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-UTF-8 nested file is skipped; other rules still load."""
    _force_walk_fallback(monkeypatch)
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "AGENTS.md").write_text("good rule")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "AGENTS.md").write_bytes(b"\xff\xfe not utf-8 \x80")
    skills = {s.name: s for s in load_project_skills(tmp_path)}
    assert "agents:good" in skills  # unaffected by the bad sibling
    assert "agents:bad" not in skills  # skipped, no exception


def test_find_nested_nonexistent_workdir_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    assert (
        skills_utils.find_nested_third_party_files(
            missing, Skill.PATH_TO_THIRD_PARTY_SKILL_NAME
        )
        == []
    )


def test_git_worktree_relpaths_none_when_git_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git absent -> None, so the caller falls back to a filesystem walk."""

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("git not installed")

    monkeypatch.setattr(skills_utils.subprocess, "run", _raise)
    assert skills_utils._git_worktree_relpaths(tmp_path) is None


def test_nested_symlink_deduped_by_real_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AGENTS.md and a CLAUDE.md symlink to it resolve to one file -> one entry."""
    _force_walk_fallback(monkeypatch)
    d = tmp_path / "dir"
    d.mkdir()
    (d / "AGENTS.md").write_text("real")
    try:
        (d / "CLAUDE.md").symlink_to(d / "AGENTS.md")
    except OSError:
        pytest.skip("symlinks not supported on this platform")

    found = skills_utils.find_nested_third_party_files(
        tmp_path, Skill.PATH_TO_THIRD_PARTY_SKILL_NAME
    )
    assert len(found) == 1
