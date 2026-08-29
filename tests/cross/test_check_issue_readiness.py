from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_prod_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / ".github" / "scripts" / "check_issue_readiness.py"
    name = "check_issue_readiness"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_prod = _load_prod_module()
evaluate_readiness = _prod.evaluate_readiness
extract_sections = _prod.extract_sections


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


def test_extract_sections_splits_on_h2_headings():
    sections = extract_sections("## Alpha\n\ntext\n\n## Beta\n\nmore\n")
    assert sections["alpha"] == "\ntext\n\n"
    assert sections["beta"] == "\nmore\n"


def test_enhancement_h2_headings_passes():
    body = ENHANCEMENT_READY.replace("### ", "## ")
    result = evaluate_readiness(body, ["enhancement"])
    assert result.ready is True
    assert result.reasons == []


def test_bug_h2_headings_passes():
    body = BUG_READY.replace("### ", "## ")
    result = evaluate_readiness(body, ["bug"])
    assert result.ready is True
    assert result.reasons == []


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
