from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


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


def make_agent_message(text: str) -> dict:
    return {
        "kind": "MessageEvent",
        "source": "agent",
        "llm_message": {"content": [{"type": "text", "text": text}]},
    }


def iso_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_list_open_issues_filters_by_duplicate_candidate_label(monkeypatch):
    module = load_module("auto_close_duplicate_issues.py")
    requested_paths: list[str] = []
    responses = [
        [
            {"number": 1},
            {"number": 2, "pull_request": {"url": "https://example.test/pr/2"}},
        ],
        [{"number": 3}],
        [],
    ]

    def fake_request_json(path: str, *, method: str = "GET", body=None):
        requested_paths.append(path)
        return responses.pop(0)

    monkeypatch.setattr(module, "request_json", fake_request_json)

    assert module.list_open_issues("OpenHands/agent-sdk") == [
        {"number": 1},
        {"number": 3},
    ]
    assert requested_paths == [
        "/repos/OpenHands/agent-sdk/issues?state=open&labels=duplicate-candidate&per_page=100&page=1",
        "/repos/OpenHands/agent-sdk/issues?state=open&labels=duplicate-candidate&per_page=100&page=2",
        "/repos/OpenHands/agent-sdk/issues?state=open&labels=duplicate-candidate&per_page=100&page=3",
    ]


def test_list_issue_comments_paginates(monkeypatch):
    module = load_module("auto_close_duplicate_issues.py")
    requested_paths: list[str] = []
    responses = [[{"id": 1}], [{"id": 2}], []]

    def fake_request_json(path: str, *, method: str = "GET", body=None):
        requested_paths.append(path)
        return responses.pop(0)

    monkeypatch.setattr(module, "request_json", fake_request_json)

    assert module.list_issue_comments("OpenHands/agent-sdk", 7) == [
        {"id": 1},
        {"id": 2},
    ]
    assert requested_paths == [
        "/repos/OpenHands/agent-sdk/issues/7/comments?per_page=100&page=1",
        "/repos/OpenHands/agent-sdk/issues/7/comments?per_page=100&page=2",
        "/repos/OpenHands/agent-sdk/issues/7/comments?per_page=100&page=3",
    ]


def test_list_comment_reactions_paginates(monkeypatch):
    module = load_module("auto_close_duplicate_issues.py")
    requested_paths: list[str] = []
    responses = [[{"id": 1}], [{"id": 2}], []]

    def fake_request_json(path: str, *, method: str = "GET", body=None):
        requested_paths.append(path)
        return responses.pop(0)

    monkeypatch.setattr(module, "request_json", fake_request_json)

    assert module.list_comment_reactions("OpenHands/agent-sdk", 99) == [
        {"id": 1},
        {"id": 2},
    ]
    assert requested_paths == [
        "/repos/OpenHands/agent-sdk/issues/comments/99/reactions?per_page=100&page=1",
        "/repos/OpenHands/agent-sdk/issues/comments/99/reactions?per_page=100&page=2",
        "/repos/OpenHands/agent-sdk/issues/comments/99/reactions?per_page=100&page=3",
    ]


def test_ensure_page_limit_raises():
    module = load_module("auto_close_duplicate_issues.py")

    with pytest.raises(RuntimeError, match="Exceeded pagination limit"):
        module.ensure_page_limit(module.MAX_PAGES + 1, "open issues")


def test_parse_timestamp_reports_invalid_values():
    module = load_module("auto_close_duplicate_issues.py")

    with pytest.raises(ValueError, match="Failed to parse timestamp"):
        module.parse_timestamp("invalid")


def test_auto_close_request_json_reports_urlerror(monkeypatch):
    module = load_module("auto_close_duplicate_issues.py")

    monkeypatch.setattr(module, "github_headers", lambda: {})
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            module.urllib.error.URLError("boom")
        ),
    )

    with pytest.raises(RuntimeError, match="GET /test failed"):
        module.request_json("/test")


def test_auto_close_request_json_reports_invalid_json(monkeypatch):
    module = load_module("auto_close_duplicate_issues.py")
    monkeypatch.setattr(module, "github_headers", lambda: {})

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"not-json"

    monkeypatch.setattr(
        module.urllib.request, "urlopen", lambda *args, **kwargs: DummyResponse()
    )

    with pytest.raises(RuntimeError, match="Failed to parse JSON from /test"):
        module.request_json("/test")


def test_is_non_bot_comment_filters_github_bots():
    module = load_module("auto_close_duplicate_issues.py")

    assert (
        module.is_non_bot_comment({"user": {"type": "User", "login": "enyst"}}) is True
    )
    assert (
        module.is_non_bot_comment({"user": {"type": "Bot", "login": "renovate[bot]"}})
        is False
    )
    assert (
        module.is_non_bot_comment({"user": {"type": "User", "login": "all-hands-bot"}})
        is True
    )
    assert (
        module.is_non_bot_comment(
            {"user": {"type": "User", "login": "dependabot[bot]"}}
        )
        is False
    )


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


def test_close_issue_propagates_comment_failure(monkeypatch):
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

    with pytest.raises(RuntimeError, match="comment failed"):
        module.close_issue_as_duplicate("OpenHands/agent-sdk", 123, 45, dry_run=False)

    assert calls == [
        ("PATCH", "/repos/OpenHands/agent-sdk/issues/123"),
        ("POST", "/repos/OpenHands/agent-sdk/issues/123/comments"),
    ]


def test_close_issue_as_duplicate_removes_label_on_success(monkeypatch):
    module = load_module("auto_close_duplicate_issues.py")
    calls: list[tuple[str, str]] = []

    def fake_request_json(path: str, *, method: str = "GET", body=None):
        calls.append((method, path))
        return {}

    def fake_remove_candidate_label(
        repository: str, issue_number: int, *, dry_run: bool
    ):
        calls.append(("REMOVE_LABEL", f"{repository}#{issue_number}:{dry_run}"))
        return True

    monkeypatch.setattr(module, "request_json", fake_request_json)
    monkeypatch.setattr(module, "remove_candidate_label", fake_remove_candidate_label)

    module.close_issue_as_duplicate("OpenHands/agent-sdk", 123, 45, dry_run=False)

    assert calls == [
        ("PATCH", "/repos/OpenHands/agent-sdk/issues/123"),
        ("POST", "/repos/OpenHands/agent-sdk/issues/123/comments"),
        ("REMOVE_LABEL", "OpenHands/agent-sdk#123:False"),
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


def test_auto_close_main_honors_author_veto(monkeypatch, capsys):
    module = load_module("auto_close_duplicate_issues.py")
    now = datetime.now(UTC)
    old_timestamp = iso_timestamp(now - timedelta(days=5))
    issue = {
        "number": 123,
        "created_at": old_timestamp,
        "labels": [{"name": module.DUPLICATE_CANDIDATE_LABEL}],
        "user": {"id": 7},
    }
    comments = [
        {
            "id": 11,
            "body": "<!-- openhands-duplicate-check canonical=45 auto-close=true -->",
            "created_at": old_timestamp,
        }
    ]
    reactions = [{"user": {"id": 7}, "content": "-1"}]
    removed: list[tuple[str, int, bool]] = []
    veto_notes: list[tuple[str, int, bool]] = []

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            repository="OpenHands/agent-sdk", close_after_days=3, dry_run=False
        ),
    )
    monkeypatch.setattr(module, "list_open_issues", lambda repository: [issue])
    monkeypatch.setattr(
        module, "list_issue_comments", lambda repository, number: comments
    )
    monkeypatch.setattr(
        module, "list_comment_reactions", lambda repository, comment_id: reactions
    )
    monkeypatch.setattr(
        module,
        "remove_candidate_label",
        lambda repository, issue_number, *, dry_run: removed.append(
            (repository, issue_number, dry_run)
        )
        or True,
    )
    monkeypatch.setattr(
        module,
        "post_veto_note",
        lambda repository, issue_number, *, dry_run: veto_notes.append(
            (repository, issue_number, dry_run)
        )
        or True,
    )
    monkeypatch.setattr(
        module,
        "close_issue_as_duplicate",
        lambda *args, **kwargs: pytest.fail("close_issue_as_duplicate should not run"),
    )

    assert module.main() == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "repository": "OpenHands/agent-sdk",
        "results": [
            {
                "issue_number": 123,
                "action": "kept-open",
                "reason": "author-thumbed-down-duplicate-comment",
                "label_removed": True,
                "veto_note_posted": True,
                "author_thumbs_up": False,
            }
        ],
    }
    assert removed == [("OpenHands/agent-sdk", 123, False)]
    assert veto_notes == [("OpenHands/agent-sdk", 123, False)]


def test_auto_close_main_closes_old_duplicate(monkeypatch, capsys):
    module = load_module("auto_close_duplicate_issues.py")
    now = datetime.now(UTC)
    old_timestamp = iso_timestamp(now - timedelta(days=5))
    issue = {
        "number": 123,
        "created_at": old_timestamp,
        "labels": [{"name": module.DUPLICATE_CANDIDATE_LABEL}],
        "user": {"id": 7},
    }
    comments = [
        {
            "id": 11,
            "body": "<!-- openhands-duplicate-check canonical=45 auto-close=true -->",
            "created_at": old_timestamp,
        }
    ]
    closed: list[tuple[str, int, int, bool]] = []

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            repository="OpenHands/agent-sdk", close_after_days=3, dry_run=False
        ),
    )
    monkeypatch.setattr(module, "list_open_issues", lambda repository: [issue])
    monkeypatch.setattr(
        module, "list_issue_comments", lambda repository, number: comments
    )
    monkeypatch.setattr(
        module, "list_comment_reactions", lambda repository, comment_id: []
    )
    monkeypatch.setattr(
        module,
        "close_issue_as_duplicate",
        lambda repository,
        issue_number,
        canonical_issue_number,
        *,
        dry_run: closed.append(
            (repository, issue_number, canonical_issue_number, dry_run)
        ),
    )

    assert module.main() == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "repository": "OpenHands/agent-sdk",
        "results": [
            {
                "issue_number": 123,
                "action": "closed-as-duplicate",
                "canonical_issue_number": 45,
                "author_thumbs_up": False,
            }
        ],
    }
    assert closed == [("OpenHands/agent-sdk", 123, 45, False)]


def test_auto_close_main_skips_malformed_issue_data(monkeypatch, capsys):
    module = load_module("auto_close_duplicate_issues.py")

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            repository="OpenHands/agent-sdk", close_after_days=3, dry_run=False
        ),
    )
    monkeypatch.setattr(
        module, "list_open_issues", lambda repository: [{"number": 123}]
    )

    assert module.main() == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary == {"repository": "OpenHands/agent-sdk", "results": []}


def test_auto_close_main_skips_malformed_duplicate_comment(monkeypatch, capsys):
    module = load_module("auto_close_duplicate_issues.py")
    now = datetime.now(UTC)
    old_timestamp = iso_timestamp(now - timedelta(days=5))
    issue = {
        "number": 123,
        "created_at": old_timestamp,
        "labels": [{"name": module.DUPLICATE_CANDIDATE_LABEL}],
        "user": {"id": 7},
    }
    comments = [
        {
            "body": "<!-- openhands-duplicate-check canonical=45 auto-close=true -->",
            "created_at": old_timestamp,
        }
    ]

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            repository="OpenHands/agent-sdk", close_after_days=3, dry_run=False
        ),
    )
    monkeypatch.setattr(module, "list_open_issues", lambda repository: [issue])
    monkeypatch.setattr(
        module, "list_issue_comments", lambda repository, number: comments
    )
    monkeypatch.setattr(
        module,
        "close_issue_as_duplicate",
        lambda *args, **kwargs: pytest.fail("close_issue_as_duplicate should not run"),
    )

    assert module.main() == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary == {"repository": "OpenHands/agent-sdk", "results": []}


def test_auto_close_main_skips_non_numeric_issue_number(monkeypatch, capsys):
    module = load_module("auto_close_duplicate_issues.py")
    now = datetime.now(UTC)

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            repository="OpenHands/agent-sdk", close_after_days=3, dry_run=False
        ),
    )
    monkeypatch.setattr(
        module,
        "list_open_issues",
        lambda repository: [
            {"number": "oops", "created_at": iso_timestamp(now - timedelta(days=5))}
        ],
    )

    assert module.main() == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary == {"repository": "OpenHands/agent-sdk", "results": []}


def test_auto_close_main_skips_non_numeric_comment_id(monkeypatch, capsys):
    module = load_module("auto_close_duplicate_issues.py")
    now = datetime.now(UTC)
    old_timestamp = iso_timestamp(now - timedelta(days=5))
    issue = {
        "number": 123,
        "created_at": old_timestamp,
        "labels": [{"name": module.DUPLICATE_CANDIDATE_LABEL}],
        "user": {"id": 7},
    }
    comments = [
        {
            "id": "oops",
            "body": "<!-- openhands-duplicate-check canonical=45 auto-close=true -->",
            "created_at": old_timestamp,
        }
    ]

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            repository="OpenHands/agent-sdk", close_after_days=3, dry_run=False
        ),
    )
    monkeypatch.setattr(module, "list_open_issues", lambda repository: [issue])
    monkeypatch.setattr(
        module, "list_issue_comments", lambda repository, number: comments
    )
    monkeypatch.setattr(
        module,
        "close_issue_as_duplicate",
        lambda *args, **kwargs: pytest.fail("close_issue_as_duplicate should not run"),
    )

    assert module.main() == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary == {"repository": "OpenHands/agent-sdk", "results": []}


def test_auto_close_main_removes_label_when_newer_comment_exists(monkeypatch, capsys):
    module = load_module("auto_close_duplicate_issues.py")
    now = datetime.now(UTC)
    old_timestamp = iso_timestamp(now - timedelta(days=5))
    newer_timestamp = iso_timestamp(now - timedelta(days=4))
    issue = {
        "number": 123,
        "created_at": old_timestamp,
        "labels": [{"name": module.DUPLICATE_CANDIDATE_LABEL}],
        "user": {"id": 7},
    }
    comments = [
        {
            "id": 11,
            "body": "<!-- openhands-duplicate-check canonical=45 auto-close=true -->",
            "created_at": old_timestamp,
        },
        {"id": 12, "body": "new info", "created_at": newer_timestamp},
    ]
    keep_open_calls: list[tuple[str, int, bool]] = []

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            repository="OpenHands/agent-sdk", close_after_days=3, dry_run=False
        ),
    )
    monkeypatch.setattr(module, "list_open_issues", lambda repository: [issue])
    monkeypatch.setattr(
        module, "list_issue_comments", lambda repository, number: comments
    )
    monkeypatch.setattr(
        module, "list_comment_reactions", lambda repository, comment_id: []
    )
    monkeypatch.setattr(
        module,
        "keep_open_due_to_newer_comments",
        lambda repository, issue_arg, issue_number, *, dry_run: keep_open_calls.append(
            (repository, issue_number, dry_run)
        )
        or {"issue_number": issue_number, "action": "kept-open"},
    )
    monkeypatch.setattr(
        module,
        "close_issue_as_duplicate",
        lambda *args, **kwargs: pytest.fail("close_issue_as_duplicate should not run"),
    )

    assert module.main() == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "repository": "OpenHands/agent-sdk",
        "results": [{"issue_number": 123, "action": "kept-open"}],
    }
    assert keep_open_calls == [("OpenHands/agent-sdk", 123, False)]


def test_auto_close_main_ignores_newer_bot_comments(monkeypatch, capsys):
    module = load_module("auto_close_duplicate_issues.py")
    now = datetime.now(UTC)
    old_timestamp = iso_timestamp(now - timedelta(days=5))
    newer_timestamp = iso_timestamp(now - timedelta(days=4))
    issue = {
        "number": 123,
        "created_at": old_timestamp,
        "labels": [{"name": module.DUPLICATE_CANDIDATE_LABEL}],
        "user": {"id": 7},
    }
    comments = [
        {
            "id": 11,
            "body": "<!-- openhands-duplicate-check canonical=45 auto-close=true -->",
            "created_at": old_timestamp,
        },
        {
            "id": 12,
            "body": "status update",
            "created_at": newer_timestamp,
            "user": {"type": "Bot", "login": "renovate[bot]"},
        },
    ]
    closed: list[tuple[str, int, int, bool]] = []

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            repository="OpenHands/agent-sdk", close_after_days=3, dry_run=False
        ),
    )
    monkeypatch.setattr(module, "list_open_issues", lambda repository: [issue])
    monkeypatch.setattr(
        module, "list_issue_comments", lambda repository, number: comments
    )
    monkeypatch.setattr(
        module, "list_comment_reactions", lambda repository, comment_id: []
    )
    monkeypatch.setattr(
        module,
        "close_issue_as_duplicate",
        lambda repository,
        issue_number,
        canonical_issue_number,
        *,
        dry_run: closed.append(
            (repository, issue_number, canonical_issue_number, dry_run)
        ),
    )
    monkeypatch.setattr(
        module,
        "keep_open_due_to_newer_comments",
        lambda *args, **kwargs: pytest.fail(
            "keep_open_due_to_newer_comments should not run"
        ),
    )

    assert module.main() == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "repository": "OpenHands/agent-sdk",
        "results": [
            {
                "issue_number": 123,
                "action": "closed-as-duplicate",
                "canonical_issue_number": 45,
                "author_thumbs_up": False,
            }
        ],
    }
    assert closed == [("OpenHands/agent-sdk", 123, 45, False)]


def test_parse_agent_json_handles_single_line_fenced_json():
    module = load_module("issue_duplicate_check_openhands.py")

    assert module.parse_agent_json('```json{"key":"value"}```') == {"key": "value"}


def test_parse_agent_json_handles_multiline_fenced_json():
    module = load_module("issue_duplicate_check_openhands.py")

    assert module.parse_agent_json('```json\n{"key":"value"}\n```') == {"key": "value"}


def test_parse_agent_json_handles_plain_json():
    module = load_module("issue_duplicate_check_openhands.py")

    assert module.parse_agent_json('{"key":"value"}') == {"key": "value"}


def test_parse_agent_json_rejects_invalid_json():
    module = load_module("issue_duplicate_check_openhands.py")

    with pytest.raises(ValueError, match="No valid JSON object found"):
        module.parse_agent_json("not json")


def test_parse_agent_json_rejects_trailing_content():
    module = load_module("issue_duplicate_check_openhands.py")

    with pytest.raises(ValueError, match="No valid JSON object found"):
        module.parse_agent_json('prefix {"key":"value"} suffix')


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


def test_issue_duplicate_request_json_reports_urlerror(monkeypatch):
    module = load_module("issue_duplicate_check_openhands.py")

    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            module.urllib.error.URLError("boom")
        ),
    )

    with pytest.raises(RuntimeError, match="GET https://example.test/path failed"):
        module.request_json("https://example.test", "/path")


def test_extract_agent_server_url_returns_runtime_prefix():
    module = load_module("issue_duplicate_check_openhands.py")

    assert (
        module.extract_agent_server_url(
            "https://runtime.example/api/conversations/conv-123"
        )
        == "https://runtime.example"
    )
    assert (
        module.extract_agent_server_url(
            "https://app.all-hands.dev/conversations/conv-123"
        )
        is None
    )


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


def test_request_json_reports_invalid_json(monkeypatch):
    module = load_module("issue_duplicate_check_openhands.py")

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        module.urllib.request, "urlopen", lambda *args, **kwargs: DummyResponse()
    )
    monkeypatch.setattr(
        module.json,
        "load",
        lambda _response: (_ for _ in ()).throw(json.JSONDecodeError("bad", "", 0)),
    )

    with pytest.raises(RuntimeError, match="Failed to parse JSON"):
        module.request_json("https://example.test", "/path")


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


def test_poll_start_task_times_out(monkeypatch):
    module = load_module("issue_duplicate_check_openhands.py")
    current_time = [0]

    monkeypatch.setattr(module, "request_json", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        module, "openhands_headers", lambda: {"Authorization": "Bearer test-token"}
    )

    def fake_time():
        current_time[0] += 6
        return current_time[0]

    monkeypatch.setattr(module.time, "time", fake_time)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    with pytest.raises(TimeoutError, match="Timed out waiting for start task"):
        module.poll_start_task("task-123", poll_interval_seconds=1, max_wait_seconds=5)


def test_poll_start_task_raises_on_failed_status(monkeypatch):
    module = load_module("issue_duplicate_check_openhands.py")

    monkeypatch.setattr(
        module,
        "request_json",
        lambda *args, **kwargs: {"items": [{"status": "FAILED", "error": "boom"}]},
    )
    monkeypatch.setattr(
        module, "openhands_headers", lambda: {"Authorization": "Bearer test-token"}
    )
    monkeypatch.setattr(module.time, "time", lambda: 0)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="OpenHands start task failed"):
        module.poll_start_task("task-123", poll_interval_seconds=1, max_wait_seconds=10)


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


def test_poll_conversation_times_out(monkeypatch):
    module = load_module("issue_duplicate_check_openhands.py")
    current_time = [0]

    monkeypatch.setattr(module, "request_json", lambda *args, **kwargs: {"items": []})
    monkeypatch.setattr(
        module, "openhands_headers", lambda: {"Authorization": "Bearer test-token"}
    )

    def fake_time():
        current_time[0] += 6
        return current_time[0]

    monkeypatch.setattr(module.time, "time", fake_time)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    with pytest.raises(TimeoutError, match="Timed out waiting for conversation"):
        module.poll_conversation(
            "conv-123", poll_interval_seconds=1, max_wait_seconds=5
        )


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

    with pytest.raises(
        RuntimeError, match="OpenHands conversation ended with failed"
    ) as exc:
        module.poll_conversation(
            "conv-123", poll_interval_seconds=1, max_wait_seconds=10
        )

    assert "boom" in str(exc.value)


def test_issue_duplicate_main_waits_for_start_task_and_writes_output(
    monkeypatch, tmp_path
):
    module = load_module("issue_duplicate_check_openhands.py")
    output_path = tmp_path / "result.json"

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            repository="OpenHands/agent-sdk",
            issue_number=123,
            output=str(output_path),
            poll_interval_seconds=1,
            max_wait_seconds=10,
        ),
    )
    monkeypatch.setattr(
        module,
        "fetch_issue",
        lambda repository, issue_number: {
            "number": issue_number,
            "title": "Issue title",
            "body": "Issue body",
            "html_url": f"https://github.com/{repository}/issues/{issue_number}",
        },
    )
    monkeypatch.setattr(
        module, "start_conversation", lambda *args, **kwargs: {"id": "task-123"}
    )
    monkeypatch.setattr(
        module,
        "poll_start_task",
        lambda task_id, poll_interval_seconds, max_wait_seconds: {
            "app_conversation_id": "conv-123"
        },
    )
    monkeypatch.setattr(
        module,
        "poll_conversation",
        lambda app_conversation_id, poll_interval_seconds, max_wait_seconds: {
            "conversation_url": "https://app.all-hands.dev/conversations/conv-123"
        },
    )
    monkeypatch.setattr(
        module,
        "fetch_app_server_events",
        lambda app_conversation_id: [
            make_agent_message(
                json.dumps(
                    {
                        "classification": "duplicate",
                        "confidence": "high",
                        "should_comment": True,
                        "is_duplicate": True,
                        "auto_close_candidate": True,
                        "canonical_issue_number": 45,
                        "candidate_issues": [{"number": 45, "title": "Existing issue"}],
                        "summary": "duplicate summary",
                    }
                )
            )
        ],
    )

    assert module.main() == 0

    result = json.loads(output_path.read_text())
    assert result["issue_number"] == 123
    assert result["repository"] == "OpenHands/agent-sdk"
    assert result["app_conversation_id"] == "conv-123"
    assert result["canonical_issue_number"] == 45


def test_issue_duplicate_main_prefers_agent_final_response(monkeypatch, tmp_path):
    module = load_module("issue_duplicate_check_openhands.py")
    output_path = tmp_path / "result.json"

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            repository="OpenHands/agent-sdk",
            issue_number=123,
            output=str(output_path),
            poll_interval_seconds=1,
            max_wait_seconds=10,
        ),
    )
    monkeypatch.setattr(
        module,
        "fetch_issue",
        lambda repository, issue_number: {
            "number": issue_number,
            "title": "Issue title",
            "body": "Issue body",
            "html_url": f"https://github.com/{repository}/issues/{issue_number}",
        },
    )
    monkeypatch.setattr(
        module,
        "start_conversation",
        lambda *args, **kwargs: {"app_conversation_id": "conv-123"},
    )
    monkeypatch.setattr(
        module,
        "poll_conversation",
        lambda app_conversation_id, poll_interval_seconds, max_wait_seconds: {
            "conversation_url": "https://runtime.example/api/conversations/conv-123",
            "session_api_key": "session-key",
        },
    )
    monkeypatch.setattr(
        module,
        "fetch_agent_server_final_response",
        lambda app_conversation_id, agent_server_url, session_api_key: json.dumps(
            {
                "classification": "overlapping-scope",
                "confidence": "medium",
                "should_comment": True,
                "is_duplicate": False,
                "auto_close_candidate": False,
                "canonical_issue_number": 45,
                "candidate_issues": [{"number": 45, "title": "Existing issue"}],
                "summary": "overlap summary",
            }
        )
        if app_conversation_id == "conv-123"
        and agent_server_url == "https://runtime.example"
        and session_api_key == "session-key"
        else pytest.fail("Unexpected final-response parameters"),
    )
    monkeypatch.setattr(
        module,
        "fetch_app_server_events",
        lambda app_conversation_id: pytest.fail(
            "fetch_app_server_events should not run"
        ),
    )
    monkeypatch.setattr(
        module,
        "fetch_agent_server_events",
        lambda *args, **kwargs: pytest.fail("fetch_agent_server_events should not run"),
    )

    assert module.main() == 0

    result = json.loads(output_path.read_text())
    assert result["classification"] == "overlapping-scope"
    assert (
        result["conversation_url"]
        == "https://runtime.example/api/conversations/conv-123"
    )


def test_issue_duplicate_main_falls_back_to_agent_server_events(monkeypatch, tmp_path):
    module = load_module("issue_duplicate_check_openhands.py")
    output_path = tmp_path / "result.json"

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            repository="OpenHands/agent-sdk",
            issue_number=123,
            output=str(output_path),
            poll_interval_seconds=1,
            max_wait_seconds=10,
        ),
    )
    monkeypatch.setattr(
        module,
        "fetch_issue",
        lambda repository, issue_number: {
            "number": issue_number,
            "title": "Issue title",
            "body": "Issue body",
            "html_url": f"https://github.com/{repository}/issues/{issue_number}",
        },
    )
    monkeypatch.setattr(
        module,
        "start_conversation",
        lambda *args, **kwargs: {"app_conversation_id": "conv-123"},
    )
    monkeypatch.setattr(
        module,
        "poll_conversation",
        lambda app_conversation_id, poll_interval_seconds, max_wait_seconds: {
            "conversation_url": "https://runtime.example/api/conversations/conv-123",
            "session_api_key": "session-key",
        },
    )
    monkeypatch.setattr(
        module,
        "fetch_agent_server_final_response",
        lambda app_conversation_id, agent_server_url, session_api_key: "",
    )
    monkeypatch.setattr(
        module, "fetch_app_server_events", lambda app_conversation_id: []
    )
    monkeypatch.setattr(
        module,
        "fetch_agent_server_events",
        lambda app_conversation_id, agent_server_url, session_api_key: [
            make_agent_message(
                json.dumps(
                    {
                        "classification": "overlapping-scope",
                        "confidence": "medium",
                        "should_comment": True,
                        "is_duplicate": False,
                        "auto_close_candidate": False,
                        "canonical_issue_number": 45,
                        "candidate_issues": [{"number": 45, "title": "Existing issue"}],
                        "summary": "overlap summary",
                    }
                )
            )
        ]
        if agent_server_url == "https://runtime.example"
        and session_api_key == "session-key"
        else pytest.fail("Unexpected fallback parameters"),
    )

    assert module.main() == 0

    result = json.loads(output_path.read_text())
    assert result["classification"] == "overlapping-scope"
    assert (
        result["conversation_url"]
        == "https://runtime.example/api/conversations/conv-123"
    )


def test_issue_duplicate_main_reports_missing_start_task_id(monkeypatch, tmp_path):
    module = load_module("issue_duplicate_check_openhands.py")

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            repository="OpenHands/agent-sdk",
            issue_number=123,
            output=str(tmp_path / "result.json"),
            poll_interval_seconds=1,
            max_wait_seconds=10,
        ),
    )
    monkeypatch.setattr(
        module, "fetch_issue", lambda repository, issue_number: {"number": issue_number}
    )
    monkeypatch.setattr(module, "start_conversation", lambda *args, **kwargs: {})

    with pytest.raises(RuntimeError, match="Missing id in start task response"):
        module.main()
