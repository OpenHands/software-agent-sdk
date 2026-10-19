"""Regression tests for the forbidden dynamic attributes pre-commit hook."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "check_forbidden_dynamic_attributes.py"
)


def _load_module(tmp_path: Path) -> Any:
    """Load the checker script as an importable module with an isolated baseline."""
    spec = importlib.util.spec_from_file_location("checker", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Override after exec so the module-level assignment doesn't clobber it.
    module.BASELINE_FILE = tmp_path / "baseline.json"  # type: ignore[attr-defined]
    return module


@pytest.fixture()
def checker(tmp_path: Path):
    return _load_module(tmp_path)


def _write_py(path: Path, body: str) -> Path:
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


def test_remove_then_reintroduce_is_caught(checker, tmp_path: Path):
    """Reintroducing a removed call must fail.

    Baseline one ``getattr``, replace it with ``x.y`` (check fails on stale
    entry, forcing a baseline refresh), then restore the identical line.
    Because the stale entry forced a refresh, the reintroduced call is no
    longer covered and is correctly rejected.
    """
    f = _write_py(tmp_path / "reintro.py", 'v = getattr(obj, "attr")\n')
    checker.main([str(f), "--update-baseline"])  # baseline count = 1

    # Remove the call → stale entry → must fail.
    _write_py(f, "v = obj.attr\n")
    assert checker.main([str(f)]) == 1
    # The developer is forced to refresh the baseline, which now records 0.
    checker.main([str(f), "--update-baseline"])

    # Reintroduce the identical call → now a *new* violation, not covered.
    _write_py(f, 'v = getattr(obj, "attr")\n')
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


def test_dunder_dict_get_also_detected(checker, tmp_path: Path):
    """Direct instance dictionary lookup is also forbidden."""
    f = _write_py(tmp_path / "dict_get.py", 'value = obj.__dict__.get("attr")\n')

    assert checker.main([str(f)]) == 1


def test_regular_dict_get_is_allowed(checker, tmp_path: Path):
    """Ordinary mapping access is not dynamic attribute access."""
    f = _write_py(tmp_path / "dict_get.py", 'value = data.get("key")\n')

    assert checker.main([str(f)]) == 0


def test_deleted_baselined_file_is_stale(checker, tmp_path: Path):
    """Deleting a baselined file must be caught even when checking other files.

    Pre-commit does not pass deleted files to hooks, so the checker must flag
    stale baseline entries for files that no longer exist — even when the
    current run is checking a different (still-existing) file.
    """
    f = _write_py(tmp_path / "deleted.py", 'v = getattr(obj, "attr")\n')
    checker.main([str(f), "--update-baseline"])

    f.unlink()  # simulate deletion

    other = _write_py(tmp_path / "other.py", "x = 1\n")
    assert checker.main([str(other)]) == 1


def test_multiline_arg_change_is_caught(checker, tmp_path: Path):
    """Editing arguments on a subsequent line of a multiline call must fail.

    Because the baseline hashes the full call source segment (not just the
    first physical line), changing arguments below the first line produces a
    different hash and is correctly rejected as a new violation.
    """
    original = "v = getattr(\n    obj,\n    'attr',\n)\n"
    f = _write_py(tmp_path / "multiline.py", original)
    checker.main([str(f), "--update-baseline"])

    # Change the argument on a subsequent line — same first line, different call.
    _write_py(f, "v = getattr(\n    obj,\n    'other',\n)\n")
    assert checker.main([str(f)]) == 1


def test_baseline_addition_is_rejected(checker):
    """A baseline may never gain a new allowance."""
    reference = checker.Counter({("a.py", "getattr", "old"): 1})
    current = checker.Counter(
        {
            ("a.py", "getattr", "old"): 1,
            ("b.py", "setattr", "new"): 1,
        }
    )

    assert checker._baseline_additions(reference, current) == checker.Counter(
        {("b.py", "setattr", "new"): 1}
    )


def test_baseline_removal_is_allowed(checker):
    """Deleting an existing allowance preserves baseline monotonicity."""
    reference = checker.Counter(
        {
            ("a.py", "getattr", "old"): 1,
            ("b.py", "setattr", "removed"): 1,
        }
    )
    current = checker.Counter({("a.py", "getattr", "old"): 1})

    assert not checker._baseline_additions(reference, current)


def test_duplicate_baseline_addition_is_rejected(checker):
    """Increasing an existing allowance's multiplicity is also an addition."""
    key = ("a.py", "getattr", "same")

    assert checker._baseline_additions(
        checker.Counter({key: 1}), checker.Counter({key: 2})
    ) == checker.Counter({key: 1})


def test_baseline_ref_option_rejects_addition(checker, tmp_path: Path, monkeypatch):
    """The CLI guard fails before accepting an expanded baseline."""
    key = ("added.py", "getattr", "new")
    checker.BASELINE_FILE.write_text(
        json.dumps([{"file": key[0], "name": key[1], "hash": key[2]}])
    )
    monkeypatch.setattr(
        checker, "_load_baseline_from_git", lambda _ref: checker.Counter()
    )
    clean = _write_py(tmp_path / "clean.py", "x = 1\n")

    assert checker.main([str(clean), "--baseline-ref", "base-sha"]) == 1


def test_baseline_ref_option_allows_removal(checker, tmp_path: Path, monkeypatch):
    """The CLI guard permits a baseline that is a strict subset."""
    removed = ("removed.py", "getattr", "old")
    checker.BASELINE_FILE.write_text("[]\n")
    monkeypatch.setattr(
        checker,
        "_load_baseline_from_git",
        lambda _ref: checker.Counter({removed: 1}),
    )
    clean = _write_py(tmp_path / "clean.py", "x = 1\n")

    assert checker.main([str(clean), "--baseline-ref", "base-sha"]) == 0
