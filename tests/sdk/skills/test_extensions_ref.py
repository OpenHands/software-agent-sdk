"""Tests for EXTENSIONS_REF environment variable support.

Subprocess-based tests isolate module-level state (PUBLIC_SKILLS_REF is read
at import time). In-process tests cover runtime cache behaviour.
"""

import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

from openhands.sdk.skills.utils import is_skills_repo_pinned


def _run_in_subprocess(test_code: str, env_extra: dict | None = None) -> None:
    """Run test code in a subprocess with the given environment variables."""
    import os

    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)

    result = subprocess.run(
        [sys.executable, "-c", test_code],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Subprocess test failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


def test_extensions_ref_default():
    """PUBLIC_SKILLS_REF should default to 'main' when EXTENSIONS_REF is not set."""
    code = """
import os
if "EXTENSIONS_REF" in os.environ:
    del os.environ["EXTENSIONS_REF"]
from openhands.sdk.skills.skill import PUBLIC_SKILLS_REF
assert PUBLIC_SKILLS_REF == "main", (
    f"Expected 'main' but got '{PUBLIC_SKILLS_REF}'"
)
"""
    _run_in_subprocess(code)


def test_extensions_ref_custom_branch():
    """PUBLIC_SKILLS_REF should use EXTENSIONS_REF when set to a branch name."""
    code = """
from openhands.sdk.skills.skill import PUBLIC_SKILLS_REF
assert PUBLIC_SKILLS_REF == "feature-branch", (
    f"Expected 'feature-branch' but got '{PUBLIC_SKILLS_REF}'"
)
"""
    _run_in_subprocess(code, {"EXTENSIONS_REF": "feature-branch"})


def test_extensions_ref_with_load_public_skills():
    """load_public_skills should respect EXTENSIONS_REF environment variable."""
    code = """
from unittest import mock
from openhands.sdk.skills.skill import (
    PUBLIC_SKILLS_REF,
    load_public_skills,
)
assert PUBLIC_SKILLS_REF == "test-branch", (
    f"Expected 'test-branch' but got '{PUBLIC_SKILLS_REF}'"
)
with mock.patch(
    "openhands.sdk.skills.skill.update_skills_repository"
) as mock_update:
    mock_update.return_value = None
    load_public_skills()
    mock_update.assert_called_once()
    call_args = mock_update.call_args
    # ref is 2nd positional arg: (repo_url, ref, cache_dir)
    assert call_args[0][1] == "test-branch", (
        f"Expected ref='test-branch' but got {call_args[0][1]}"
    )
"""
    _run_in_subprocess(code, {"EXTENSIONS_REF": "test-branch"})


def test_extensions_ref_with_tag():
    """PUBLIC_SKILLS_REF should accept a tag value via EXTENSIONS_REF."""
    code = """
from openhands.sdk.skills.skill import PUBLIC_SKILLS_REF
assert PUBLIC_SKILLS_REF == "v1.2.3", (
    f"Expected 'v1.2.3' but got '{PUBLIC_SKILLS_REF}'"
)
"""
    _run_in_subprocess(code, {"EXTENSIONS_REF": "v1.2.3"})


def test_extensions_ref_with_commit_sha():
    """PUBLIC_SKILLS_REF should accept a full commit SHA via EXTENSIONS_REF."""
    sha = "a" * 40
    code = f"""
from openhands.sdk.skills.skill import PUBLIC_SKILLS_REF
assert PUBLIC_SKILLS_REF == "{sha}", (
    f"Expected '{sha}' but got '{{PUBLIC_SKILLS_REF}}'"
)
"""
    _run_in_subprocess(code, {"EXTENSIONS_REF": sha})


def test_extensions_ref_empty_string():
    """Empty EXTENSIONS_REF falls back to an empty string (os.environ.get behaviour)."""
    code = """
from openhands.sdk.skills.skill import PUBLIC_SKILLS_REF
assert PUBLIC_SKILLS_REF == "", (
    f"Expected '' but got '{PUBLIC_SKILLS_REF}'"
)
"""
    _run_in_subprocess(code, {"EXTENSIONS_REF": ""})


# ---------------------------------------------------------------------------
# In-process tests for pinned-ref cache behaviour
# ---------------------------------------------------------------------------


def _create_ref_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgSign", "false"], cwd=repo, check=True)
    skill_dir = repo / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill\n---\n\nBody\n"
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    for ref in (
        "refs/remotes/origin/main",
        "refs/remotes/origin/dual",
        "refs/tags/v1",
        "refs/tags/dual",
    ):
        subprocess.run(["git", "update-ref", ref, commit], cwd=repo, check=True)
    return repo, commit


def test_skills_repo_pin_classification_uses_requested_ref(tmp_path: Path):
    repo, commit = _create_ref_repo(tmp_path)

    assert not is_skills_repo_pinned(repo)
    assert is_skills_repo_pinned(repo, "v1")
    assert is_skills_repo_pinned(repo, "refs/tags/v1")
    assert is_skills_repo_pinned(repo, commit)
    assert is_skills_repo_pinned(repo, commit[:12])
    assert not is_skills_repo_pinned(repo, "main")
    assert not is_skills_repo_pinned(repo, "origin/main")
    assert not is_skills_repo_pinned(repo, "refs/heads/main")
    assert not is_skills_repo_pinned(repo, "dual")
    assert not is_skills_repo_pinned(repo, "--detach")
    subprocess.run(["git", "checkout", "-q", "--detach", commit], cwd=repo, check=True)
    assert is_skills_repo_pinned(repo)


def test_tag_ref_keeps_public_skills_cache_without_detached_head(tmp_path: Path):
    from openhands.sdk.skills.skill import (
        _PUBLIC_SKILLS_CACHE_TTL_SECONDS,
        _invalidate_public_skills_cache,
        load_public_skills,
    )

    repo, _ = _create_ref_repo(tmp_path)
    _invalidate_public_skills_cache()

    with (
        patch(
            "openhands.sdk.skills.skill.update_skills_repository",
            return_value=repo,
        ) as mock_update,
        patch(
            "openhands.sdk.skills.skill.time.monotonic",
            side_effect=[0.0, _PUBLIC_SKILLS_CACHE_TTL_SECONDS + 1],
        ),
    ):
        first = load_public_skills(
            repo_url="https://example.invalid/extensions",
            ref="v1",
            marketplace_path=None,
        )
        second = load_public_skills(
            repo_url="https://example.invalid/extensions",
            ref="v1",
            marketplace_path=None,
        )

    assert [skill.name for skill in first] == ["demo"]
    assert [skill.name for skill in second] == ["demo"]
    mock_update.assert_called_once()


def _seed_cache(
    cache: dict,
    lock: threading.Lock,
    cache_key: tuple,
    skills: list,
    *,
    timestamp: float,
) -> None:
    """Seed the public-skills cache with a synthetic entry."""
    with lock:
        cache[cache_key] = (timestamp, skills)


def test_pinned_cache_entry_never_expires():
    """A pinned entry (timestamp=inf) is returned even after the TTL would have elapsed.

    This verifies that pinned tags and commit SHAs do not trigger remote
    polling after the first load.
    """
    from openhands.sdk.skills.skill import (
        _PUBLIC_SKILLS_CACHE,
        _PUBLIC_SKILLS_CACHE_LOCK,
        Skill,
        _invalidate_public_skills_cache,
        load_public_skills,
    )

    fake_skill = Skill(name="pinned-skill", content="pinned content")
    cache_key = ("https://github.com/OpenHands/extensions", "v1.0.0", None)

    _invalidate_public_skills_cache()
    _seed_cache(
        _PUBLIC_SKILLS_CACHE,
        _PUBLIC_SKILLS_CACHE_LOCK,
        cache_key,
        [fake_skill],
        timestamp=float("inf"),
    )

    with patch("openhands.sdk.skills.skill.update_skills_repository") as mock_update:
        result = load_public_skills(ref="v1.0.0", marketplace_path=None)
        mock_update.assert_not_called()

    assert len(result) == 1
    assert result[0].name == "pinned-skill"


def test_mutable_cache_entry_expires_after_ttl():
    """A branch entry (finite timestamp) IS re-fetched once the TTL has passed."""
    from openhands.sdk.skills.skill import (
        _PUBLIC_SKILLS_CACHE,
        _PUBLIC_SKILLS_CACHE_LOCK,
        _PUBLIC_SKILLS_CACHE_TTL_SECONDS,
        Skill,
        _invalidate_public_skills_cache,
        load_public_skills,
    )

    fake_skill = Skill(name="stale-skill", content="stale content")
    cache_key = ("https://github.com/OpenHands/extensions", "main", None)

    _invalidate_public_skills_cache()
    _seed_cache(
        _PUBLIC_SKILLS_CACHE,
        _PUBLIC_SKILLS_CACHE_LOCK,
        cache_key,
        [fake_skill],
        timestamp=time.monotonic() - _PUBLIC_SKILLS_CACHE_TTL_SECONDS - 1,
    )

    with patch("openhands.sdk.skills.skill.update_skills_repository") as mock_update:
        mock_update.return_value = None  # simulates transient failure
        load_public_skills(ref="main", marketplace_path=None)
        mock_update.assert_called_once()
