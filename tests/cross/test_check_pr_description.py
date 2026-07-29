from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_prod_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / ".github" / "scripts" / "check_pr_description.py"
    name = "check_pr_description"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_prod = _load_prod_module()
validate_pr_body = _prod.validate_pr_body
body_from_event = _prod.body_from_event


VALID_BODY = """<!-- Keep this PR as draft until it is ready for review. -->

HUMAN:

I reviewed the agent's changes and confirmed they do what the PR says.
The implementation is small and the validation output matches the goal.

AGENT:

---

## Why

The existing workflow did not make the missing human context visible enough.

## Summary

- Add a required PR description readiness check.

## Issue Number

N/A

## How to Test

Ran the new validation script against a passing and failing PR body.

## Video/Screenshots

N/A

## Type

- [ ] Bug fix
- [x] Feature
- [ ] Refactor
- [ ] Breaking change
- [ ] Docs / chore

## Notes

N/A
"""


def test_valid_pr_body_passes():
    assert validate_pr_body(VALID_BODY) == []


def test_human_section_must_be_first_visible_line_and_filled():
    body = VALID_BODY.replace("HUMAN:", "## Summary", 1)

    errors = validate_pr_body(body)

    assert "The first visible line of the PR description must be `HUMAN:`." in errors
    assert "Add a short human-written note between `HUMAN:` and `AGENT:`." in errors


def test_required_template_fields_must_be_present_and_filled():
    how_to_test = (
        "## How to Test\n\n"
        "Ran the new validation script against a passing and failing PR body."
    )
    body = VALID_BODY.replace(how_to_test, "## How to Test\n\n<!-- TODO -->")
    body = body.replace("## Summary", "## Details")

    errors = validate_pr_body(body)

    assert "Fill in the `## How to Test` section of the PR template." in errors
    assert "Keep the `## Summary` section from the PR template." in errors


def test_optional_template_sections_may_be_removed():
    body = VALID_BODY.replace("## Issue Number\n\nN/A\n\n", "")
    body = body.split("## Video/Screenshots", maxsplit=1)[0]

    assert validate_pr_body(body) == []


def test_body_from_event_reads_pull_request_body(tmp_path: Path):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"pull_request": {"body": VALID_BODY}}))

    assert body_from_event(event_path) == VALID_BODY
