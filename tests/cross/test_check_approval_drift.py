"""Tests for the approval-drift (TOCTOU) release check.

Focus on the deterministic, network-free surface: how merged-PR subjects are
mapped to PR numbers (and, crucially, which ones are *not* mapped and must be
surfaced as a blind spot), and bot detection. The GitHub REST calls in
``audit_pr``/``main`` are not exercised here.

``merged_pr_numbers`` shells out to ``git log``; we drive it deterministically
by monkeypatching the module's ``run_git`` to return a canned log, so no real
repository history is required.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_script_module(name: str):
    repo_root = Path(__file__).resolve().parents[2]
    scripts_dir = repo_root / ".github" / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    script_path = scripts_dir / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_drift = _load_script_module("check_approval_drift")

_US = "\x1f"  # the unit-separator used in the git pretty-format


def _fake_log(*subjects: str) -> str:
    """Build a ``%H\\x1f%s`` git log body from subjects (sha is irrelevant)."""
    return "\n".join(f"deadbeef{i}{_US}{s}" for i, s in enumerate(subjects))


def test_matched_and_unmapped_are_split(monkeypatch):
    log = _fake_log(
        "feat: add a thing (#4001)",
        "fix: a merge commit with no PR suffix",
        "chore: bump deps (#4002)",
        "Merge branch 'main' into feature",
    )
    monkeypatch.setattr(_drift, "run_git", lambda *a, **k: log)

    matched, unmapped = _drift.merged_pr_numbers("v1.0.0")

    assert matched == [
        (4001, "feat: add a thing (#4001)"),
        (4002, "chore: bump deps (#4002)"),
    ]
    assert unmapped == [
        "fix: a merge commit with no PR suffix",
        "Merge branch 'main' into feature",
    ]


def test_all_mapped_leaves_no_blind_spot(monkeypatch):
    log = _fake_log("a (#1)", "b (#2)", "c (#3)")
    monkeypatch.setattr(_drift, "run_git", lambda *a, **k: log)

    matched, unmapped = _drift.merged_pr_numbers("v1.0.0")

    assert [n for n, _ in matched] == [1, 2, 3]
    assert unmapped == []


def test_trailing_whitespace_after_pr_number_still_matches(monkeypatch):
    monkeypatch.setattr(_drift, "run_git", lambda *a, **k: _fake_log("x (#42)  "))
    matched, unmapped = _drift.merged_pr_numbers("v1.0.0")
    assert matched == [(42, "x (#42)  ")]
    assert unmapped == []


def test_pr_ref_not_at_end_is_unmapped(monkeypatch):
    # A "(#N)" that is not the trailing token is not the squash-merge PR id.
    monkeypatch.setattr(
        _drift, "run_git", lambda *a, **k: _fake_log("revert of (#7) change")
    )
    matched, unmapped = _drift.merged_pr_numbers("v1.0.0")
    assert matched == []
    assert unmapped == ["revert of (#7) change"]


def test_is_bot_detects_type_and_suffix():
    assert _drift._is_bot({"type": "Bot"}) is True
    assert _drift._is_bot({"login": "dependabot[bot]"}) is True
    assert _drift._is_bot({"login": "octocat", "type": "User"}) is False
