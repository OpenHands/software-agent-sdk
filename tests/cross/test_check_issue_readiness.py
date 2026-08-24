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


# Import markdown_sections first so check_issue_readiness can resolve its
# `from markdown_sections import ...` against the module we loaded above.
_load("markdown_sections", "markdown_sections.py")
_prod = _load("check_issue_readiness", "check_issue_readiness.py")
evaluate_readiness = _prod.evaluate_readiness
extract_sections = _prod.extract_sections
main = _prod.main


ENHANCEMENT_READY = """### Problem or Use Case

I need to persist agent state between sessions.

### Desired Behavior

`agent.save_state()` writes session state to a configured backend.

### Acceptance Criteria

- [ ] `agent.save_state()` writes state to the backend
- [ ] A new `Agent` restores state from a saved snapshot
"""

BUG_READY = """### Actual Behavior

Running `pip install openhands-sdk` and then `pytest` fails with a TypeError
when registering a custom tool.

### Acceptance Criteria

- [ ] No `TypeError` is raised when registering a custom tool
"""


def test_extract_sections_splits_on_headings():
    sections = extract_sections("### Alpha\n\ntext\n\n### Beta\n\nmore\n")
    assert sections["alpha"] == "\ntext\n\n"
    assert sections["beta"] == "\nmore\n"


def test_enhancement_ready_passes():
    result = evaluate_readiness(ENHANCEMENT_READY, ["enhancement"])
    assert result.ready is True
    assert result.reasons == []


def test_enhancement_missing_acceptance_criteria_fails():
    body = "### Desired Behavior\n\nSome desired change.\n"
    result = evaluate_readiness(body, ["enhancement"])
    assert result.ready is False
    assert any("Acceptance Criteria" in r for r in result.reasons)


def test_enhancement_missing_desired_behavior_fails():
    body = ENHANCEMENT_READY.replace(
        "### Desired Behavior\n\n"
        "`agent.save_state()` writes session state to a configured backend.\n\n",
        "",
    )
    result = evaluate_readiness(body, ["enhancement"])
    assert result.ready is False
    assert any("Desired Behavior" in r for r in result.reasons)


def test_bug_ready_passes():
    result = evaluate_readiness(BUG_READY, ["bug"])
    assert result.ready is True
    assert result.reasons == []


def test_bug_missing_run_method_fails():
    body = BUG_READY.replace(
        "Running `pip install openhands-sdk` and then `pytest` fails",
        "Running the SDK test harness fails",
    )
    result = evaluate_readiness(body, ["bug"])
    assert result.ready is False
    assert any("reproducible SDK command" in r for r in result.reasons)


def test_bug_backticked_python_is_a_valid_run_method():
    body = BUG_READY.replace(
        "Running `pip install openhands-sdk` and then `pytest` fails with a TypeError",
        "Running `python` from a venv fails to start",
    )
    result = evaluate_readiness(body, ["bug"])
    assert result.ready is True
    assert result.reasons == []


def test_bug_acceptance_needs_checklist_item():
    body = BUG_READY.replace("- [ ] No `TypeError`", "Fix the TypeError")
    result = evaluate_readiness(body, ["bug"])
    assert result.ready is False
    assert any("checklist item" in r for r in result.reasons)


def test_no_bug_or_enhancement_label_not_ready():
    result = evaluate_readiness(ENHANCEMENT_READY, [])
    assert result.ready is False
    assert any("bug" in r and "enhancement" in r for r in result.reasons)


def test_extract_sections_ignores_heading_inside_fence():
    body = """### Notes
The template says:

```markdown
### Acceptance Criteria
- [ ] Add criteria here
```
"""
    sections = extract_sections(body)
    assert set(sections) == {"notes"}


def test_fenced_heading_does_not_truncate_actual_behavior():
    body = """### Actual Behavior
I ran `pytest` and saw:

~~~text
### Error detail
something went wrong
~~~

### Acceptance Criteria
- [ ] The bug is fixed
"""
    sections = extract_sections(body)
    assert "error detail" not in sections
    assert "pytest" in sections["actual behavior"]
    assert evaluate_readiness(body, ["bug"]).ready


def test_unclosed_fence_does_not_swallow_later_sections():
    """One stray marker in a log paste must not reject an otherwise-ready report."""
    body = """### Actual Behavior
I ran `python repro.py` and saw the crash below.

### Relevant Logs
```shell
Traceback (most recent call last):
  the paste was cut off before the closing fence

### Acceptance Criteria
- [ ] The bug is fixed
"""
    sections = extract_sections(body)
    assert {"actual behavior", "relevant logs", "acceptance criteria"} <= set(sections)
    assert evaluate_readiness(body, ["bug"]).ready


def _run_main(monkeypatch, argv: list[str]) -> int:
    monkeypatch.setattr("sys.argv", ["check_issue_readiness.py", *argv])
    return main()


def test_main_json_ready(tmp_path, capsys, monkeypatch):
    body_file = tmp_path / "issue.md"
    body_file.write_text(BUG_READY)
    exit_code = _run_main(
        monkeypatch, ["--body-file", str(body_file), "--labels", "bug", "--json"]
    )
    assert exit_code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ready"] is True
    assert data["reasons"] == []


def test_main_json_not_ready_stays_machine_readable(tmp_path, capsys, monkeypatch):
    """Not-ready JSON exits 1 but still prints parseable JSON on stdout.

    The workflow absorbs the exit code with `|| true` so `set -euo pipefail`
    does not abort label/comment handling.
    """
    body_file = tmp_path / "issue.md"
    body_file.write_text("### Actual Behavior\n\nIt broke.\n")
    exit_code = _run_main(
        monkeypatch, ["--body-file", str(body_file), "--labels", "bug", "--json"]
    )
    assert exit_code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ready"] is False
    assert len(data["reasons"]) > 0


def test_main_text_ready(tmp_path, capsys, monkeypatch):
    body_file = tmp_path / "issue.md"
    body_file.write_text(BUG_READY)
    exit_code = _run_main(
        monkeypatch, ["--body-file", str(body_file), "--labels", "bug"]
    )
    assert exit_code == 0
    assert "Issue meets ready-for-dev criteria." in capsys.readouterr().out


def test_main_text_not_ready(tmp_path, capsys, monkeypatch):
    body_file = tmp_path / "issue.md"
    body_file.write_text("### Actual Behavior\n\nIt broke.\n")
    exit_code = _run_main(
        monkeypatch, ["--body-file", str(body_file), "--labels", "bug"]
    )
    assert exit_code == 1
    assert "Issue does not meet ready-for-dev criteria:" in capsys.readouterr().out


def test_main_event_path_json_ready(tmp_path, capsys, monkeypatch):
    event_file = tmp_path / "event.json"
    event_file.write_text(
        json.dumps({"issue": {"body": BUG_READY, "labels": [{"name": "bug"}]}})
    )
    exit_code = _run_main(monkeypatch, ["--event-path", str(event_file), "--json"])
    assert exit_code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ready"] is True
