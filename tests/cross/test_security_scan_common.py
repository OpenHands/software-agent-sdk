"""Tests for shared security-scan helpers — Link-header pagination.

The approval-drift gate must see *every* review of a PR; if a human APPROVED
sits past the first REST page, truncating would misclassify the PR as
unapproved and block a clean release. These cover the ``Link: rel="next"``
follow and the header parser, with no real network (the per-page GET is
monkeypatched).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_script_module(name: str):
    repo_root = Path(__file__).resolve().parents[2]
    scripts_dir = repo_root / ".github" / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    script_path = scripts_dir / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_common = _load_script_module("security_scan_common")


# --- _next_link -----------------------------------------------------------


def test_next_link_extracts_next_url():
    header = (
        '<https://api.github.com/x?page=2>; rel="next", '
        '<https://api.github.com/x?page=5>; rel="last"'
    )
    assert _common._next_link(header) == "https://api.github.com/x?page=2"


def test_next_link_none_when_absent():
    header = '<https://api.github.com/x?page=1>; rel="prev"'
    assert _common._next_link(header) is None


def test_next_link_none_for_empty_header():
    assert _common._next_link(None) is None
    assert _common._next_link("") is None


# --- github_request_all ---------------------------------------------------


def test_paginates_until_no_next(monkeypatch):
    pages = {
        "https://api.github.com/reviews?per_page=100": (
            [{"id": 1}, {"id": 2}],
            '<https://api.github.com/reviews?page=2>; rel="next"',
        ),
        "https://api.github.com/reviews?page=2": (
            [{"id": 3}],
            None,
        ),
    }

    def fake_get(url, token):
        return pages[url]

    monkeypatch.setattr(_common, "_github_get", fake_get)

    items = _common.github_request_all(
        "https://api.github.com/reviews?per_page=100", token=None
    )
    assert [i["id"] for i in items] == [1, 2, 3]


def test_single_page_no_link(monkeypatch):
    monkeypatch.setattr(_common, "_github_get", lambda url, token: ([{"id": 9}], None))
    items = _common.github_request_all("/reviews", token=None)
    assert items == [{"id": 9}]


def test_non_list_response_wrapped(monkeypatch):
    # A dict (e.g. an error object) must not be iterated element-wise.
    monkeypatch.setattr(
        _common, "_github_get", lambda url, token: ({"message": "x"}, None)
    )
    items = _common.github_request_all("/reviews", token=None)
    assert items == [{"message": "x"}]
