from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load(name: str, script_name: str):
    script_path = (
        Path(__file__).resolve().parents[2] / ".github" / "scripts" / script_name
    )
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Import markdown_sections and check_pr_description first so the scripts can
# resolve their imports against the modules we loaded above.
_load("markdown_sections", "markdown_sections.py")
_load("check_pr_description", "check_pr_description.py")
_prod = _load("refresh_linked_pr_checks", "refresh_linked_pr_checks.py")


def _event(action="labeled", label="ready-for-dev", number=12):
    return {
        "action": action,
        "issue": {"number": number},
        "label": {"name": label},
        "repository": {"full_name": "org/repo"},
    }


def _write_event(monkeypatch, payload, tmp_path):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))


class _FakeProc:
    def __init__(self, stdout: str = ""):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def _recording_run(calls):
    def _call(args):
        calls.append(args)
        # Listing runs returns one run; rerun is a no-op success.
        if any("/actions/runs" in a for a in args):
            return _FakeProc("8675309 2026-08-13T00:00:00Z\n")
        return _FakeProc()

    return _call


def _fail_on_call(value):
    def _call(*args, **kwargs):
        raise AssertionError(value)

    return _call


def test_noop_for_unrelated_label(monkeypatch, tmp_path):
    _write_event(monkeypatch, _event(label="bug"), tmp_path)
    monkeypatch.setattr(_prod, "_linked_open_prs", _fail_on_call("unexpected"))
    assert _prod.main() == 0


def test_noop_for_unrelated_action(monkeypatch, tmp_path):
    _write_event(monkeypatch, _event(action="edited"), tmp_path)
    monkeypatch.setattr(_prod, "_linked_open_prs", _fail_on_call("unexpected"))
    assert _prod.main() == 0


def test_reruns_linked_pr_check_when_readiness_label_changes(monkeypatch, tmp_path):
    _write_event(monkeypatch, _event(), tmp_path)
    monkeypatch.setattr(
        _prod,
        "_linked_open_prs",
        lambda repo, num: [{"number": 7, "headRefOid": "abc123"}],
    )
    monkeypatch.setattr(_prod, "_run", lambda args: _FakeProc("Fixes #12"))
    seen = []
    monkeypatch.setattr(
        _prod,
        "_rerun_pr_description_check",
        lambda repo, sha: (seen.append((repo, sha)) or True),
    )
    assert _prod.main() == 0
    assert seen == [("org/repo", "abc123")]


def test_reruns_linked_pr_check_when_readiness_label_removed(monkeypatch, tmp_path):
    """Losing `ready-for-dev` must also refresh linked PR gates (unlabeled)."""
    _write_event(monkeypatch, _event(action="unlabeled"), tmp_path)
    monkeypatch.setattr(
        _prod,
        "_linked_open_prs",
        lambda repo, num: [{"number": 7, "headRefOid": "abc123"}],
    )
    monkeypatch.setattr(_prod, "_run", lambda args: _FakeProc("Fixes #12"))
    seen = []
    monkeypatch.setattr(
        _prod,
        "_rerun_pr_description_check",
        lambda repo, sha: (seen.append((repo, sha)) or True),
    )
    assert _prod.main() == 0
    assert seen == [("org/repo", "abc123")]


def test_skips_cross_referenced_pr_that_does_not_link_issue(monkeypatch, tmp_path):
    _write_event(monkeypatch, _event(), tmp_path)
    monkeypatch.setattr(
        _prod,
        "_linked_open_prs",
        lambda repo, num: [{"number": 7, "headRefOid": "abc123"}],
    )
    # Body mentions #99 (the cross-reference) but not the event's issue #12.
    monkeypatch.setattr(_prod, "_run", lambda args: _FakeProc("Fixes #99"))
    seen = []
    monkeypatch.setattr(
        _prod,
        "_rerun_pr_description_check",
        lambda repo, sha: (seen.append((repo, sha)) or True),
    )
    assert _prod.main() == 0
    assert seen == []


def test_rerun_pr_description_check_lists_runs_with_get(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(_prod, "_run", _recording_run(calls))
    assert _prod._rerun_pr_description_check("org/repo", "abc123") is True
    # Listing runs must be an explicit GET: `-f` args alone switch `gh api`
    # to POST, which 404s on the runs collection endpoint (seen in the wild).
    list_call = next(a for a in calls if "repos/org/repo/actions/runs" in a)
    assert "-X" in list_call
    assert list_call[list_call.index("-X") + 1] == "GET"
    # The selected run is then re-run via an explicit POST.
    rerun_call = next(a for a in calls if any("/rerun" in arg for arg in a))
    assert rerun_call[rerun_call.index("api") + 1 : rerun_call.index("api") + 3] == [
        "-X",
        "POST",
    ]
