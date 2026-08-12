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
extract_linked_issue_numbers = _prod.extract_linked_issue_numbers
validate_linked_issue_ready = _prod.validate_linked_issue_ready
fetch_issue_labels = _prod.fetch_issue_labels


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


def test_extract_linked_issue_numbers_keyword_and_bare_ref():
    body = (
        "Fixes #12\n"
        "Closes #12 again\n"
        "resolves #34\n"
        "## Issue Number\n"
        "Issue: #56, see also #12\n"
    )
    assert extract_linked_issue_numbers(body) == [12, 34, 56]


def test_extract_linked_issue_numbers_only_bare_ref_in_issue_section():
    body = "## Summary\n\nSome work.\n\n## Issue Number\n\n#7\n"
    assert extract_linked_issue_numbers(body) == [7]


def test_extract_linked_issue_numbers_no_bare_ref_outside_issue_section():
    # A bare `#42` in the Summary must not be treated as a linked issue.
    body = "## Summary\n\nSee #42 for background.\n\n## Issue Number\n\nN/A\n"
    assert extract_linked_issue_numbers(body) == []


def test_extract_linked_issue_numbers_keyword_inside_word_is_ignored():
    # "fix"/"clos"/"resolv" must not match inside larger words (e.g. "crucifixes").
    body = (
        "## Summary\n\n"
        "crucifixes #12, encloses #34, transfixes #56.\n\n"
        "## Issue Number\n\nN/A\n"
    )
    assert extract_linked_issue_numbers(body) == []


def test_validate_linked_issue_ready_requires_a_number():
    errors = validate_linked_issue_ready(
        "## Issue Number\n\nN/A\n", "org/repo", "token"
    )
    assert errors and "Link an issue" in errors[0]


def test_validate_linked_issue_ready_no_token_skips_network(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("should not call the network without a token")

    monkeypatch.setattr(_prod, "fetch_issue_labels", _fail)
    assert validate_linked_issue_ready("Fixes #12\n", None, None) == []


def test_validate_linked_issue_ready_passes_with_ready_label(monkeypatch):
    monkeypatch.setattr(
        _prod, "fetch_issue_labels", lambda repo, num, token: ["ready-for-dev"]
    )
    assert validate_linked_issue_ready("Fixes #12\n", "org/repo", "token") == []


def test_validate_linked_issue_ready_fails_when_not_ready(monkeypatch):
    monkeypatch.setattr(_prod, "fetch_issue_labels", lambda repo, num, token: ["bug"])
    errors = validate_linked_issue_ready("Fixes #12\n", "org/repo", "token")
    assert errors and "ready-for-dev" in errors[0]


def test_validate_linked_issue_ready_skips_missing_issue(monkeypatch):
    import urllib.error
    from http.client import HTTPMessage

    def _missing(repo, num, token):
        raise urllib.error.HTTPError(
            "https://api.github.com", 404, "Not Found", HTTPMessage(), None
        )

    monkeypatch.setattr(_prod, "fetch_issue_labels", _missing)
    errors = validate_linked_issue_ready("Fixes #12\n", "org/repo", "token")
    assert errors and "could not be found" in errors[0]
