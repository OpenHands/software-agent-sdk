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


# Import check_pr_description first so refresh_linked_pr_checks can resolve its
# `from check_pr_description import ...` against the module we loaded above.
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