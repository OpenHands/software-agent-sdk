"""Tests for extensions fetch utilities."""

from pathlib import Path
from unittest.mock import create_autospec

import pytest

from openhands.sdk.extensions.fetch import (
    ExtensionFetchError,
    SourceType,
    fetch,
    fetch_with_resolution,
    get_cache_path,
    parse_extension_source,
)
from openhands.sdk.git.cached_repo import GitHelper
from openhands.sdk.git.exceptions import GitCommandError


# -- parse_extension_source ---------------------------------------------------


def test_parse_github_shorthand():
    source_type, url = parse_extension_source("github:owner/repo")
    assert source_type == SourceType.GITHUB
    assert url == "https://github.com/owner/repo.git"


def test_parse_github_shorthand_with_whitespace():
    source_type, url = parse_extension_source("  github:owner/repo  ")
    assert source_type == SourceType.GITHUB
    assert url == "https://github.com/owner/repo.git"


def test_parse_github_shorthand_invalid_format():
    with pytest.raises(ExtensionFetchError, match="Invalid GitHub shorthand"):
        parse_extension_source("github:invalid")

    with pytest.raises(ExtensionFetchError, match="Invalid GitHub shorthand"):
        parse_extension_source("github:too/many/parts")


def test_parse_https_git_url():
    source_type, url = parse_extension_source("https://github.com/owner/repo.git")
    assert source_type == SourceType.GIT
    assert url == "https://github.com/owner/repo.git"


def test_parse_https_github_url_without_git_suffix():
    source_type, url = parse_extension_source("https://github.com/owner/repo")
    assert source_type == SourceType.GIT
    assert url == "https://github.com/owner/repo.git"


def test_parse_https_github_url_with_trailing_slash():
    source_type, url = parse_extension_source("https://github.com/owner/repo/")
    assert source_type == SourceType.GIT
    assert url == "https://github.com/owner/repo.git"


def test_parse_https_gitlab_url():
    source_type, url = parse_extension_source("https://gitlab.com/org/repo")
    assert source_type == SourceType.GIT
    assert url == "https://gitlab.com/org/repo.git"


def test_parse_https_bitbucket_url():
    source_type, url = parse_extension_source("https://bitbucket.org/org/repo")
    assert source_type == SourceType.GIT
    assert url == "https://bitbucket.org/org/repo.git"


def test_parse_ssh_git_url():
    source_type, url = parse_extension_source("git@github.com:owner/repo.git")
    assert source_type == SourceType.GIT
    assert url == "git@github.com:owner/repo.git"


def test_parse_git_protocol_url():
    source_type, url = parse_extension_source("git://github.com/owner/repo.git")
    assert source_type == SourceType.GIT
    assert url == "git://github.com/owner/repo.git"


def test_parse_absolute_local_path():
    source_type, url = parse_extension_source("/path/to/extension")
    assert source_type == SourceType.LOCAL
    assert url == "/path/to/extension"


def test_parse_home_relative_path():
    source_type, url = parse_extension_source("~/extensions/my-ext")
    assert source_type == SourceType.LOCAL
    assert url == "~/extensions/my-ext"


def test_parse_dot_relative_path():
    source_type, url = parse_extension_source("./extensions/my-ext")
    assert source_type == SourceType.LOCAL
    assert url == "./extensions/my-ext"


def test_parse_invalid_source():
    with pytest.raises(ExtensionFetchError, match="Unable to parse extension source"):
        parse_extension_source("invalid-source-format")


def test_parse_self_hosted_git_urls():
    source_type, url = parse_extension_source("https://codeberg.org/user/repo")
    assert source_type == SourceType.GIT
    assert url == "https://codeberg.org/user/repo.git"

    source_type, url = parse_extension_source("https://git.mycompany.com/org/repo")
    assert source_type == SourceType.GIT
    assert url == "https://git.mycompany.com/org/repo.git"


def test_parse_http_url():
    source_type, url = parse_extension_source("http://internal-git.local/repo")
    assert source_type == SourceType.GIT
    assert url == "http://internal-git.local/repo.git"


def test_parse_ssh_with_custom_user():
    ssh_url = "deploy@git.example.com:project/repo.git"
    source_type, url = parse_extension_source(ssh_url)
    assert source_type == SourceType.GIT
    assert url == ssh_url


def test_parse_relative_path_with_slash():
    source_type, url = parse_extension_source("extensions/my-ext")
    assert source_type == SourceType.LOCAL
    assert url == "extensions/my-ext"


def test_parse_nested_relative_path():
    source_type, url = parse_extension_source("path/to/my/extension")
    assert source_type == SourceType.LOCAL
    assert url == "path/to/my/extension"


# -- SourceType enum ----------------------------------------------------------


def test_source_type_values():
    assert SourceType.LOCAL == "local"
    assert SourceType.GIT == "git"
    assert SourceType.GITHUB == "github"


# -- get_cache_path ------------------------------------------------------------


def test_cache_path_deterministic(tmp_path: Path):
    source = "https://github.com/owner/repo.git"
    path1 = get_cache_path(source, tmp_path)
    path2 = get_cache_path(source, tmp_path)
    assert path1 == path2


def test_cache_path_different_sources(tmp_path: Path):
    path1 = get_cache_path("https://github.com/owner/repo1.git", tmp_path)
    path2 = get_cache_path("https://github.com/owner/repo2.git", tmp_path)
    assert path1 != path2


def test_cache_path_includes_readable_name(tmp_path: Path):
    source = "https://github.com/owner/my-extension.git"
    path = get_cache_path(source, tmp_path)
    assert "my-extension" in path.name


# -- fetch (local sources) ----------------------------------------------------


def test_fetch_local_path(tmp_path: Path):
    ext_dir = tmp_path / "my-ext"
    ext_dir.mkdir()

    result = fetch(str(ext_dir), cache_dir=tmp_path)
    assert result == ext_dir.resolve()


def test_fetch_local_path_nonexistent(tmp_path: Path):
    with pytest.raises(ExtensionFetchError, match="does not exist"):
        fetch(str(tmp_path / "nonexistent"), cache_dir=tmp_path)


# -- fetch (remote sources) ---------------------------------------------------


def test_fetch_github_shorthand_clones(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)

    def clone_side_effect(url, dest, **kwargs):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir()

    mock_git.clone.side_effect = clone_side_effect

    result = fetch(
        "github:owner/repo",
        cache_dir=tmp_path,
        git_helper=mock_git,
    )

    assert result.exists()
    mock_git.clone.assert_called_once()
    call_args = mock_git.clone.call_args
    assert call_args[0][0] == "https://github.com/owner/repo.git"


def test_fetch_with_ref(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)

    def clone_side_effect(url, dest, **kwargs):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir()

    mock_git.clone.side_effect = clone_side_effect

    fetch(
        "github:owner/repo",
        cache_dir=tmp_path,
        ref="v1.0.0",
        git_helper=mock_git,
    )

    mock_git.clone.assert_called_once()
    call_kwargs = mock_git.clone.call_args[1]
    assert call_kwargs["branch"] == "v1.0.0"


def test_fetch_updates_existing_cache(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)
    mock_git.get_current_branch.return_value = "main"

    cache_path = get_cache_path("https://github.com/owner/repo.git", tmp_path)
    cache_path.mkdir(parents=True)
    (cache_path / ".git").mkdir()

    result = fetch(
        "github:owner/repo",
        cache_dir=tmp_path,
        update=True,
        git_helper=mock_git,
    )

    assert result == cache_path
    mock_git.fetch.assert_called()
    mock_git.clone.assert_not_called()


def test_fetch_no_update_uses_cache(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)

    cache_path = get_cache_path("https://github.com/owner/repo.git", tmp_path)
    cache_path.mkdir(parents=True)
    (cache_path / ".git").mkdir()

    result = fetch(
        "github:owner/repo",
        cache_dir=tmp_path,
        update=False,
        git_helper=mock_git,
    )

    assert result == cache_path
    mock_git.clone.assert_not_called()
    mock_git.fetch.assert_not_called()


def test_fetch_no_update_with_ref_checks_out(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)

    cache_path = get_cache_path("https://github.com/owner/repo.git", tmp_path)
    cache_path.mkdir(parents=True)
    (cache_path / ".git").mkdir()

    fetch(
        "github:owner/repo",
        cache_dir=tmp_path,
        update=False,
        ref="v1.0.0",
        git_helper=mock_git,
    )

    mock_git.checkout.assert_called_once_with(cache_path, "v1.0.0")


def test_fetch_git_error_raises_extension_fetch_error(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)
    mock_git.clone.side_effect = GitCommandError(
        "fatal: repository not found",
        command=["git", "clone"],
        exit_code=128,
    )

    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(
            "github:owner/nonexistent",
            cache_dir=tmp_path,
            git_helper=mock_git,
        )


def test_fetch_generic_error_raises_extension_fetch_error(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)
    mock_git.clone.side_effect = RuntimeError("Unexpected error")

    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(
            "github:owner/repo",
            cache_dir=tmp_path,
            git_helper=mock_git,
        )


# -- fetch_with_resolution ----------------------------------------------------


def test_fetch_with_resolution_local_returns_none_ref(tmp_path: Path):
    ext_dir = tmp_path / "my-ext"
    ext_dir.mkdir()

    path, resolved_ref = fetch_with_resolution(str(ext_dir), cache_dir=tmp_path)
    assert path == ext_dir.resolve()
    assert resolved_ref is None


def test_fetch_with_resolution_remote_returns_sha(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)

    def clone_side_effect(url, dest, **kwargs):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir()

    mock_git.clone.side_effect = clone_side_effect
    mock_git.get_head_commit.return_value = "abc123deadbeef"

    path, resolved_ref = fetch_with_resolution(
        "github:owner/repo",
        cache_dir=tmp_path,
        git_helper=mock_git,
    )

    assert path.exists()
    assert resolved_ref == "abc123deadbeef"


def test_fetch_with_resolution_falls_back_on_sha_error(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)

    def clone_side_effect(url, dest, **kwargs):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir()

    mock_git.clone.side_effect = clone_side_effect
    mock_git.get_head_commit.side_effect = RuntimeError("not a git repo")

    path, resolved_ref = fetch_with_resolution(
        "github:owner/repo",
        cache_dir=tmp_path,
        ref="v2.0",
        git_helper=mock_git,
    )

    assert path.exists()
    assert resolved_ref == "v2.0"


def test_fetch_with_resolution_falls_back_to_head(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)

    def clone_side_effect(url, dest, **kwargs):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir()

    mock_git.clone.side_effect = clone_side_effect
    mock_git.get_head_commit.side_effect = RuntimeError("not a git repo")

    path, resolved_ref = fetch_with_resolution(
        "github:owner/repo",
        cache_dir=tmp_path,
        git_helper=mock_git,
    )

    assert path.exists()
    assert resolved_ref == "HEAD"


# -- repo_path parameter ------------------------------------------------------


def test_fetch_local_with_repo_path_raises_error(tmp_path: Path):
    ext_dir = tmp_path / "monorepo"
    ext_dir.mkdir()
    (ext_dir / "extensions" / "my-ext").mkdir(parents=True)

    with pytest.raises(
        ExtensionFetchError,
        match="repo_path is not supported for local",
    ):
        fetch(
            str(ext_dir),
            cache_dir=tmp_path,
            repo_path="extensions/my-ext",
        )


def test_fetch_github_with_repo_path(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)

    def clone_side_effect(url, dest, **kwargs):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir()
        subdir = dest / "extensions" / "sub-ext"
        subdir.mkdir(parents=True)

    mock_git.clone.side_effect = clone_side_effect

    result = fetch(
        "github:owner/monorepo",
        cache_dir=tmp_path,
        repo_path="extensions/sub-ext",
        git_helper=mock_git,
    )

    assert result.exists()
    assert result.name == "sub-ext"
    assert "extensions" in str(result)


def test_fetch_github_with_nonexistent_repo_path(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)

    def clone_side_effect(url, dest, **kwargs):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir()

    mock_git.clone.side_effect = clone_side_effect

    with pytest.raises(ExtensionFetchError, match="Subdirectory.*not found"):
        fetch(
            "github:owner/repo",
            cache_dir=tmp_path,
            repo_path="nonexistent",
            git_helper=mock_git,
        )


def test_fetch_with_repo_path_and_ref(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)

    def clone_side_effect(url, dest, **kwargs):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir()
        subdir = dest / "extensions" / "my-ext"
        subdir.mkdir(parents=True)

    mock_git.clone.side_effect = clone_side_effect

    result = fetch(
        "github:owner/monorepo",
        cache_dir=tmp_path,
        ref="v1.0.0",
        repo_path="extensions/my-ext",
        git_helper=mock_git,
    )

    assert result.exists()
    mock_git.clone.assert_called_once()
    call_kwargs = mock_git.clone.call_args[1]
    assert call_kwargs["branch"] == "v1.0.0"


def test_fetch_no_repo_path_returns_root(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)

    def clone_side_effect(url, dest, **kwargs):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir()
        (dest / "extensions").mkdir()

    mock_git.clone.side_effect = clone_side_effect

    result = fetch(
        "github:owner/repo",
        cache_dir=tmp_path,
        repo_path=None,
        git_helper=mock_git,
    )

    assert result.exists()
    assert (result / ".git").exists()
