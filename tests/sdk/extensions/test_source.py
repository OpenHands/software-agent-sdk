"""Tests for extension source path handling."""

from pathlib import Path

import pytest

from openhands.sdk.extensions.source import (
    is_local_path,
    parse_github_url,
    resolve_source_path,
    validate_source_path,
)


# -- parse_github_url ---------------------------------------------------------


def test_parse_github_blob_url():
    result = parse_github_url(
        "https://github.com/OpenHands/extensions/blob/main/skills/github"
    )
    assert result is not None
    assert result.owner == "OpenHands"
    assert result.repo == "extensions"
    assert result.branch == "main"
    assert result.path == "skills/github"


def test_parse_github_tree_url():
    result = parse_github_url(
        "https://github.com/OpenHands/extensions/tree/main/skills/github"
    )
    assert result is not None
    assert result.path == "skills/github"


@pytest.mark.parametrize(
    "url",
    ["./skills/my-skill", "https://gitlab.com/o/r/blob/main/p"],
)
def test_parse_github_url_returns_none_for_non_github(url: str):
    assert parse_github_url(url) is None


# -- is_local_path ------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "./skills/my-skill",
        "../parent/skill",
        "/absolute/path",
        "~/home/path",
        "file:///path/to/file",
    ],
)
def test_is_local_path_true(source: str):
    assert is_local_path(source)


@pytest.mark.parametrize(
    "source",
    ["https://github.com/o/r/blob/main/p", "just-a-name"],
)
def test_is_local_path_false(source: str):
    assert not is_local_path(source)


# -- validate_source_path -----------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "./skills/my-skill",
        "/absolute/path",
        "https://github.com/owner/repo/blob/main/path",
    ],
)
def test_validate_source_path_accepts_valid(source: str):
    assert validate_source_path(source) == source


def test_validate_source_path_rejects_invalid():
    with pytest.raises(ValueError, match="Invalid source path"):
        validate_source_path("just-a-name")


# -- resolve_source_path ------------------------------------------------------


def test_resolve_source_path_file_url():
    assert resolve_source_path("file:///tmp/skill") == Path("/tmp/skill")


def test_resolve_source_path_absolute():
    assert resolve_source_path("/absolute/path") == Path("/absolute/path")


def test_resolve_source_path_relative_with_base():
    result = resolve_source_path("./skill", base_path=Path("/project"))
    assert result == Path("/project/skill")


def test_resolve_source_path_home():
    result = resolve_source_path("~/documents/skill")
    assert result == Path.home() / "documents" / "skill"
