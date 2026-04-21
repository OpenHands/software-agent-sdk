from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_COUNTER = itertools.count()


def load_module(script_name: str):
    path = ROOT / "scripts" / script_name
    module_name = f"test_{path.stem}_{next(MODULE_COUNTER)}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_list_open_issues_filters_by_duplicate_candidate_label(monkeypatch):
    module = load_module("auto_close_duplicate_issues.py")
    requested_paths: list[str] = []

    def fake_request_json(path: str, *, method: str = "GET", body=None):
        requested_paths.append(path)
        return []

    monkeypatch.setattr(module, "request_json", fake_request_json)

    assert module.list_open_issues("OpenHands/agent-sdk") == []
    assert requested_paths == [
        "/repos/OpenHands/agent-sdk/issues?state=open&labels=duplicate-candidate&per_page=100&page=1"
    ]


def test_has_reaction_from_user_ignores_missing_user_ids():
    module = load_module("auto_close_duplicate_issues.py")
    reactions = [
        {"user": None, "content": "-1"},
        {"user": {"id": 42}, "content": "-1"},
    ]

    assert module.user_id_from_item({"user": None}) is None
    assert module.has_reaction_from_user(reactions, None, "-1") is False
    assert module.has_reaction_from_user(reactions, 42, "-1") is True
    assert module.has_reaction_from_user(reactions, 42, "+1") is False


def test_find_latest_auto_close_comment_returns_latest_candidate():
    module = load_module("auto_close_duplicate_issues.py")
    comments = [
        {"body": "plain comment"},
        {
            "body": "<!-- openhands-duplicate-check canonical=10 auto-close=false -->",
            "id": 1,
        },
        {
            "body": "<!-- openhands-duplicate-check canonical=11 auto-close=true -->",
            "id": 2,
        },
        {
            "body": "<!-- openhands-duplicate-check canonical=12 auto-close=true -->",
            "id": 3,
        },
    ]

    latest_comment, canonical_issue = module.find_latest_auto_close_comment(comments)

    assert latest_comment == comments[-1]
    assert canonical_issue == 12


def test_close_issue_as_duplicate_leaves_label_until_requests_succeed(monkeypatch):
    module = load_module("auto_close_duplicate_issues.py")
    calls: list[tuple[str, str]] = []

    def fake_request_json(path: str, *, method: str = "GET", body=None):
        calls.append((method, path))
        if method == "POST" and path.endswith("/comments"):
            raise RuntimeError("comment failed")
        return {}

    def fake_remove_candidate_label(
        repository: str, issue_number: int, *, dry_run: bool
    ):
        calls.append(("REMOVE_LABEL", f"{repository}#{issue_number}:{dry_run}"))
        return True

    monkeypatch.setattr(module, "request_json", fake_request_json)
    monkeypatch.setattr(module, "remove_candidate_label", fake_remove_candidate_label)

    try:
        module.close_issue_as_duplicate(
            "OpenHands/agent-sdk",
            123,
            45,
            dry_run=False,
        )
    except RuntimeError as exc:
        assert str(exc) == "comment failed"
    else:
        raise AssertionError(
            "Expected close_issue_as_duplicate to propagate the failure"
        )

    assert calls == [
        ("PATCH", "/repos/OpenHands/agent-sdk/issues/123"),
        ("POST", "/repos/OpenHands/agent-sdk/issues/123/comments"),
    ]


def test_keep_open_due_to_newer_comments_removes_candidate_label(monkeypatch):
    module = load_module("auto_close_duplicate_issues.py")
    calls: list[tuple[str, int, bool]] = []

    def fake_remove_candidate_label(
        repository: str, issue_number: int, *, dry_run: bool
    ):
        calls.append((repository, issue_number, dry_run))
        return True

    monkeypatch.setattr(module, "remove_candidate_label", fake_remove_candidate_label)

    result = module.keep_open_due_to_newer_comments(
        "OpenHands/agent-sdk",
        {"labels": [{"name": "duplicate-candidate"}]},
        123,
        dry_run=False,
    )

    assert result == {
        "issue_number": 123,
        "action": "kept-open",
        "reason": "newer-comment-after-duplicate-notice",
        "label_removed": True,
    }
    assert calls == [("OpenHands/agent-sdk", 123, False)]


def test_parse_agent_json_handles_single_line_fenced_json():
    module = load_module("issue_duplicate_check_openhands.py")

    assert module.parse_agent_json('```json{"key":"value"}```') == {"key": "value"}


def test_normalize_result_promotes_actionable_duplicates():
    module = load_module("issue_duplicate_check_openhands.py")
    normalized = module.normalize_result(
        {
            "classification": "duplicate",
            "confidence": "HIGH",
            "should_comment": False,
            "is_duplicate": True,
            "auto_close_candidate": "1",
            "canonical_issue_number": "",
            "candidate_issues": [
                {"number": "21", "title": "First"},
                {"number": 22, "title": "Second"},
                {"number": 23, "title": "Third"},
                {"number": 24, "title": "Fourth"},
            ],
            "summary": "  duplicate summary  ",
        }
    )

    assert normalized["should_comment"] is True
    assert normalized["auto_close_candidate"] is True
    assert normalized["canonical_issue_number"] == 21
    assert len(normalized["candidate_issues"]) == 3
    assert normalized["summary"] == "duplicate summary"


def test_normalize_result_lowercases_classification():
    module = load_module("issue_duplicate_check_openhands.py")
    normalized = module.normalize_result(
        {
            "classification": "Duplicate",
            "confidence": "HIGH",
            "should_comment": True,
            "is_duplicate": True,
            "auto_close_candidate": True,
            "canonical_issue_number": 21,
            "candidate_issues": [{"number": 21, "title": "Existing issue"}],
        }
    )

    assert normalized["classification"] == "duplicate"
    assert normalized["should_comment"] is True
    assert normalized["is_duplicate"] is True
    assert normalized["auto_close_candidate"] is True


def test_poll_start_task_retries_after_empty_payload(monkeypatch):
    module = load_module("issue_duplicate_check_openhands.py")
    responses = [
        [],
        {"items": [{"status": "READY", "app_conversation_id": "conv-123"}]},
    ]

    monkeypatch.setattr(
        module, "request_json", lambda *args, **kwargs: responses.pop(0)
    )
    monkeypatch.setattr(
        module, "openhands_headers", lambda: {"Authorization": "Bearer test-token"}
    )
    monkeypatch.setattr(module.time, "time", lambda: 0)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    item = module.poll_start_task(
        "task-123", poll_interval_seconds=1, max_wait_seconds=10
    )

    assert item["app_conversation_id"] == "conv-123"


def test_poll_conversation_retries_after_empty_items(monkeypatch):
    module = load_module("issue_duplicate_check_openhands.py")
    responses = [
        {"items": []},
        {
            "items": [
                {
                    "execution_status": "completed",
                    "conversation_url": "https://example.test",
                }
            ]
        },
    ]

    monkeypatch.setattr(
        module, "request_json", lambda *args, **kwargs: responses.pop(0)
    )
    monkeypatch.setattr(
        module, "openhands_headers", lambda: {"Authorization": "Bearer test-token"}
    )
    monkeypatch.setattr(module.time, "time", lambda: 0)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    item = module.poll_conversation(
        "conv-123", poll_interval_seconds=1, max_wait_seconds=10
    )

    assert item["execution_status"] == "completed"


def test_poll_conversation_raises_on_failed_status(monkeypatch):
    module = load_module("issue_duplicate_check_openhands.py")

    monkeypatch.setattr(
        module,
        "request_json",
        lambda *args, **kwargs: {
            "items": [
                {
                    "execution_status": "failed",
                    "conversation_url": "https://example.test",
                    "error_detail": "boom",
                }
            ]
        },
    )
    monkeypatch.setattr(
        module, "openhands_headers", lambda: {"Authorization": "Bearer test-token"}
    )
    monkeypatch.setattr(module.time, "time", lambda: 0)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    try:
        module.poll_conversation(
            "conv-123", poll_interval_seconds=1, max_wait_seconds=10
        )
    except RuntimeError as exc:
        assert "OpenHands conversation ended with failed" in str(exc)
        assert "boom" in str(exc)
    else:
        raise AssertionError("Expected poll_conversation to raise on failed status")
