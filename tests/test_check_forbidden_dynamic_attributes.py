"""Regression tests for the forbidden dynamic attributes pre-commit hook."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "check_forbidden_dynamic_attributes.py"
)


def _load_module(tmp_path: Path):
    """Load the checker script as an importable module with an isolated baseline."""
    spec = importlib.util.spec_from_file_location("checker", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Point BASELINE_FILE at a temp location so tests don't clobber the real one.
    module.__dict__["BASELINE_FILE"] = tmp_path / "baseline.json"
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def checker(tmp_path: Path):
    return _load_module(tmp_path)


def _write_py(path: Path, body: str) -> None:
    path.write_text(body)
    return path


def test_clean_file_passes(checker, tmp_path: Path):
    """A file with no forbidden calls produces no violations."""
    f = _write_py(tmp_path / "clean.py", "x = 1\n")
    assert checker.main([str(f)]) == 0


def test_new_violation_fails(checker, tmp_path: Path):
    """A getattr call not in the baseline is rejected."""
    f = _write_py(tmp_path / "bad.py", 'v = getattr(obj, "attr")\n')
    assert checker.main([str(f)]) == 1


def test_baselined_violation_passes(checker, tmp_path: Path):
    """A getattr call present in the baseline is accepted."""
    f = _write_py(tmp_path / "ok.py", 'v = getattr(obj, "attr")\n')
    checker.main([str(f), "--update-baseline"])
    assert checker.main([str(f)]) == 0


def test_duplicate_baselined_line_is_rejected(checker, tmp_path: Path):
    """Duplicating an already-baselined source line must still fail.

    This is the key regression: a *set*-based comparison would accept the
    second identical line, but a multiset (Counter) comparison correctly
    rejects it because the current count exceeds the baseline count.
    """
    f = _write_py(tmp_path / "dup.py", 'v = getattr(obj, "attr")\n')
    checker.main([str(f), "--update-baseline"])  # baseline now has count 1

    # Add a second identical line on a different line number.
    _write_py(f, 'v = getattr(obj, "attr")\nw = getattr(obj, "attr")\n')
    assert checker.main([str(f)]) == 1


def test_stale_baseline_entry_fails(checker, tmp_path: Path):
    """Removing a baselined call makes its baseline entry stale, which fails.

    Stale entries must fail (not merely print a note) so removing a call
    permanently shrinks the allowance instead of leaving a reusable slot.
    """
    f = _write_py(tmp_path / "stale.py", 'v = getattr(obj, "attr")\n')
    checker.main([str(f), "--update-baseline"])

    # Remove the call; the baseline still records it.
    _write_py(f, "v = 1\n")
    assert checker.main([str(f)]) == 1


def test_update_baseline_idempotent(checker, tmp_path: Path):
    """Running --update-baseline twice produces the same file."""
    f = _write_py(tmp_path / "idem.py", 'v = getattr(obj, "attr")\n')
    checker.main([str(f), "--update-baseline"])
    first = json.loads(checker.BASELINE_FILE.read_text())
    checker.main([str(f), "--update-baseline"])
    second = json.loads(checker.BASELINE_FILE.read_text())
    assert first == second


def test_setattr_also_detected(checker, tmp_path: Path):
    """setattr is also forbidden."""
    f = _write_py(tmp_path / "set.py", 'setattr(obj, "attr", 1)\n')
    assert checker.main([str(f)]) == 1
