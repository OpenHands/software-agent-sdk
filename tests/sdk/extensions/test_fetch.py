"""Tests for extensions fetch utilities."""

import subprocess
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
        parse_extension_source("github:/repo")

    with pytest.raises(ExtensionFetchError, match="Invalid GitHub shorthand"):
        parse_extension_source("github:owner/")


def test_parse_github_shorthand_with_subpath():
    """The repository URL ignores the subpath, which fetch() uses as repo_path."""
    source_type, url = parse_extension_source(
        "github:OpenHands/extensions/skills/github"
    )
    assert source_type == SourceType.GITHUB
    assert url == "https://github.com/OpenHands/extensions.git"


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


@pytest.mark.parametrize(
    "source",
    [
        "https://dev.azure.com/org/project/_git/repo",
        "https://org@dev.azure.com/org/Web%20Apps/_git/skills",
        "https://org.visualstudio.com/project/_git/repo",
        "https://tfs.example.com/tfs/Collection/project/_git/repo",
    ],
)
def test_parse_azure_devops_url_keeps_path_without_git_suffix(source):
    """Azure DevOps treats ``<repo>.git`` as a different, missing repository."""
    source_type, url = parse_extension_source(source)
    assert source_type == SourceType.GIT
    assert url == source


def test_parse_azure_devops_url_strips_trailing_slash():
    source_type, url = parse_extension_source(
        "https://dev.azure.com/org/project/_git/repo/"
    )
    assert source_type == SourceType.GIT
    assert url == "https://dev.azure.com/org/project/_git/repo"


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


def _commit_version(source: Path, version: str, tag: str | None = None) -> str:
    (source / "version.txt").write_text(version)
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", version], cwd=source, check=True)
    if tag:
        subprocess.run(
            ["git", "-c", "tag.gpgSign=false", "tag", "-a", tag, "-m", version],
            cwd=source,
            check=True,
        )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _create_git_source(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=source,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
    subprocess.run(["git", "config", "commit.gpgSign", "false"], cwd=source, check=True)
    main_head = _commit_version(source, "v1", tag="v1.0.0")
    subprocess.run(["git", "switch", "-q", "-c", "feature"], cwd=source, check=True)
    feature_head = _commit_version(source, "v2")
    subprocess.run(["git", "switch", "-q", "main"], cwd=source, check=True)
    return source, {"main": main_head, "feature": feature_head}


def _clone_full_cache(source_url: str, cache_dir: Path) -> Path:
    cache_dir.mkdir()
    cached_path = get_cache_path(source_url, cache_dir)
    subprocess.run(["git", "clone", "-q", source_url, cached_path], check=True)
    return cached_path


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


def test_fetch_updates_existing_cache(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)
    mock_git.get_requested_branch.return_value = None
    mock_git.get_current_branch.return_value = "main"
    mock_git.needs_explicit_branch_fetch.return_value = False

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
    mock_git.fetch.assert_called_once_with(cache_path)
    mock_git.clone.assert_not_called()


def test_fetch_rejects_unknown_ref_in_existing_cache(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir)

    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(
            source_url,
            cache_dir=cache_dir,
            ref="missing-ref",
        )

    assert (cached_path / "version.txt").read_text() == "v1"


def test_fetch_remote_branch_after_default_shallow_clone(tmp_path: Path):
    source, refs = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir)
    assert (cached_path / "version.txt").read_text() == "v1"

    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref="feature",
    )

    assert (result / "version.txt").read_text() == "v2"
    assert resolved_ref == refs["feature"]


def test_requested_ref_discards_dirty_cached_files(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir, ref="feature")
    (cached_path / "version.txt").write_text("dirty")

    fetch(source_url, cache_dir=cache_dir, ref="feature")

    assert (cached_path / "version.txt").read_text() == "v2"


def test_fetched_remote_branch_remains_refreshable_without_ref(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    fetch(source_url, cache_dir=cache_dir)
    cached_path = fetch(source_url, cache_dir=cache_dir, ref="feature")

    subprocess.run(["git", "switch", "-q", "feature"], cwd=source, check=True)
    feature_head = _commit_version(source, "v3", tag="feature-v3")
    subprocess.run(["git", "switch", "-q", "main"], cwd=source, check=True)

    _, resolved_ref = fetch_with_resolution(source_url, cache_dir=cache_dir)

    assert (cached_path / "version.txt").read_text() == "v3"
    assert resolved_ref == feature_head

    source.rename(tmp_path / "offline-source")
    _, tag_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref="feature-v3",
    )
    assert tag_ref == feature_head


def test_requested_branch_refresh_does_not_update_symbolic_ref_target(
    tmp_path: Path,
):
    source, refs = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir, ref="feature")
    subprocess.run(
        ["git", "update-ref", "-d", "refs/remotes/origin/feature"],
        cwd=cached_path,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "symbolic-ref",
            "refs/remotes/origin/feature",
            "refs/remotes/origin/main",
        ],
        cwd=cached_path,
        check=True,
    )
    subprocess.run(["git", "switch", "-q", "feature"], cwd=source, check=True)
    feature_head = _commit_version(source, "v3")
    subprocess.run(["git", "switch", "-q", "main"], cwd=source, check=True)

    fetch(source_url, cache_dir=cache_dir)

    assert (cached_path / "version.txt").read_text() == "v3"
    assert (
        subprocess.run(
            ["git", "rev-parse", "refs/remotes/origin/main"],
            cwd=cached_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == refs["main"]
    )
    assert (
        subprocess.run(
            ["git", "rev-parse", "refs/remotes/origin/feature"],
            cwd=cached_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == feature_head
    )


def test_offline_ref_does_not_fall_back_through_symbolic_cache_ref(
    tmp_path: Path,
):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir, ref="feature")
    subprocess.run(
        ["git", "update-ref", "-d", "refs/remotes/origin/feature"],
        cwd=cached_path,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "symbolic-ref",
            "refs/remotes/origin/feature",
            "refs/remotes/origin/main",
        ],
        cwd=cached_path,
        check=True,
    )
    source.rename(tmp_path / "offline-source")

    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(source_url, cache_dir=cache_dir, ref="feature")

    assert (cached_path / "version.txt").read_text() == "v2"


def test_pinned_ref_clears_requested_branch_for_later_update(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir, ref="feature")
    fetch(source_url, cache_dir=cache_dir, ref="v1.0.0")
    main_head = _commit_version(source, "main-v2")

    _, resolved_ref = fetch_with_resolution(source_url, cache_dir=cache_dir)

    assert (cached_path / "version.txt").read_text() == "main-v2"
    assert resolved_ref == main_head


def test_detached_recovery_does_not_use_default_branch_as_checkout_option(
    tmp_path: Path,
):
    source, refs = _create_git_source(tmp_path)
    subprocess.run(
        ["git", "update-ref", "refs/heads/--orphan=evil", refs["feature"]],
        cwd=source,
        check=True,
    )
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/--orphan=evil"],
        cwd=source,
        check=True,
    )
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir, ref="v1.0.0")

    _, resolved_ref = fetch_with_resolution(source_url, cache_dir=cache_dir)

    assert (cached_path / "version.txt").read_text() == "v2"
    assert resolved_ref == refs["feature"]
    assert (
        subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cached_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )


def test_no_ref_fetch_updates_legacy_narrow_branch_cache(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached_path = get_cache_path(source_url, cache_dir)
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "--depth",
            "1",
            "--branch",
            "feature",
            source_url,
            cached_path,
        ],
        check=True,
    )
    subprocess.run(["git", "switch", "-q", "feature"], cwd=source, check=True)
    feature_head = _commit_version(source, "v3")
    subprocess.run(["git", "switch", "-q", "main"], cwd=source, check=True)

    _, resolved_ref = fetch_with_resolution(source_url, cache_dir=cache_dir)

    assert (cached_path / "version.txt").read_text() == "v3"
    assert resolved_ref == feature_head


@pytest.mark.parametrize("abbreviated", [False, True])
def test_fetch_sha_falls_back_to_configured_refspec(
    tmp_path: Path,
    abbreviated: bool,
):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir)
    requested_sha = _commit_version(source, "v2")
    _commit_version(source, "v3")
    subprocess.run(
        ["git", "config", "protocol.version", "0"],
        cwd=cached_path,
        check=True,
    )

    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref=requested_sha[:12] if abbreviated else requested_sha,
    )

    assert (result / "version.txt").read_text() == "v2"
    assert resolved_ref == requested_sha
    assert (
        subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", "refs/openhands"],
            cwd=cached_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )


def test_fetch_sha_from_uncached_source_with_protocol_v0(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, refs = _create_git_source(tmp_path)
    requested_sha = refs["feature"]
    subprocess.run(["git", "switch", "-q", "feature"], cwd=source, check=True)
    _commit_version(source, "feature-v3")
    subprocess.run(["git", "switch", "-q", "main"], cwd=source, check=True)
    source_url = f"file://{source}"
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.version")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "0")

    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=tmp_path / "cache",
        ref=requested_sha,
    )

    assert (result / "version.txt").read_text() == "v2"
    assert resolved_ref == requested_sha


def test_fetch_rejects_deleted_remote_branch(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir, ref="feature")
    subprocess.run(["git", "branch", "-D", "feature"], cwd=source, check=True)

    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(source_url, cache_dir=cache_dir, ref="feature")

    assert (cached_path / "version.txt").read_text() == "v2"

    source.rename(tmp_path / "offline-source")
    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(source_url, cache_dir=cache_dir, ref="feature")


def test_deleted_hex_named_branch_cannot_resolve_as_cached_object_id(
    tmp_path: Path,
):
    source, refs = _create_git_source(tmp_path)
    subprocess.run(["git", "branch", "cafe", "feature"], cwd=source, check=True)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir, ref="cafe")
    subprocess.run(
        ["git", "update-ref", "refs/heads/cafe", refs["feature"]],
        cwd=cached_path,
        check=True,
    )
    subprocess.run(["git", "branch", "-D", "cafe"], cwd=source, check=True)

    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(source_url, cache_dir=cache_dir, ref="cafe")


def test_missing_branch_does_not_delete_symbolic_ref_target(tmp_path: Path):
    source, refs = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir)
    subprocess.run(
        [
            "git",
            "symbolic-ref",
            "refs/remotes/origin/missing",
            "refs/remotes/origin/main",
        ],
        cwd=cached_path,
        check=True,
    )

    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(source_url, cache_dir=cache_dir, ref="refs/heads/missing")

    assert (
        subprocess.run(
            ["git", "rev-parse", "refs/remotes/origin/main"],
            cwd=cached_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == refs["main"]
    )


def test_fetch_uses_cached_branch_after_transient_fetch_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, refs = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir, ref="feature")

    def fail_fetch(*args, **kwargs):
        raise GitCommandError(
            "transient fetch failure",
            command=["git", "fetch"],
            exit_code=128,
        )

    monkeypatch.setattr(GitHelper, "_fetch_selected_ref", fail_fetch)

    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref="feature",
    )

    assert result == cached_path
    assert (result / "version.txt").read_text() == "v2"
    assert resolved_ref == refs["feature"]


def test_fetch_does_not_restore_stale_branch_after_ref_kind_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, _ = _create_git_source(tmp_path)
    subprocess.run(["git", "branch", "shared", "feature"], cwd=source, check=True)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir, ref="shared")
    subprocess.run(["git", "branch", "-D", "shared"], cwd=source, check=True)
    subprocess.run(
        ["git", "-c", "tag.gpgSign=false", "tag", "shared", "main"],
        cwd=source,
        check=True,
    )

    def fail_fetch(*args, **kwargs):
        source.rename(tmp_path / "offline-source")
        raise GitCommandError(
            "transient fetch failure",
            command=["git", "fetch"],
            exit_code=128,
        )

    monkeypatch.setattr(GitHelper, "_fetch_selected_ref", fail_fetch)

    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(source_url, cache_dir=cache_dir, ref="shared")

    assert (cached_path / "version.txt").read_text() == "v2"


def test_fetch_refreshes_moved_tag(tmp_path: Path):
    source, refs = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    fetch(source_url, cache_dir=cache_dir, ref="v1.0.0")
    subprocess.run(
        ["git", "-c", "tag.gpgSign=false", "tag", "-f", "v1.0.0", "feature"],
        cwd=source,
        check=True,
    )

    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref="v1.0.0",
    )

    assert (result / "version.txt").read_text() == "v2"
    assert resolved_ref == refs["feature"]


def test_fetched_tag_does_not_update_symbolic_ref_target(tmp_path: Path):
    source, refs = _create_git_source(tmp_path)
    subprocess.run(
        [
            "git",
            "-c",
            "tag.gpgSign=false",
            "tag",
            "-a",
            "feature-tag",
            "feature",
            "-m",
            "feature",
        ],
        cwd=source,
        check=True,
    )
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir)
    subprocess.run(
        [
            "git",
            "symbolic-ref",
            "refs/tags/feature-tag",
            "refs/remotes/origin/main",
        ],
        cwd=cached_path,
        check=True,
    )

    fetch(source_url, cache_dir=cache_dir, ref="refs/tags/feature-tag")

    assert (
        subprocess.run(
            ["git", "rev-parse", "refs/remotes/origin/main"],
            cwd=cached_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == refs["main"]
    )
    assert (
        subprocess.run(
            ["git", "rev-parse", "refs/tags/feature-tag^{}"],
            cwd=cached_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == refs["feature"]
    )


def test_fetch_branch_to_tag_transition_removes_stale_branch(tmp_path: Path):
    source, refs = _create_git_source(tmp_path)
    subprocess.run(["git", "branch", "shared", "feature"], cwd=source, check=True)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    fetch(source_url, cache_dir=cache_dir, ref="shared")
    subprocess.run(["git", "branch", "-D", "shared"], cwd=source, check=True)
    subprocess.run(
        ["git", "-c", "tag.gpgSign=false", "tag", "shared", "main"],
        cwd=source,
        check=True,
    )

    fetch(source_url, cache_dir=cache_dir, ref="shared")
    source.rename(tmp_path / "offline-source")
    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref="shared",
    )

    assert (result / "version.txt").read_text() == "v1"
    assert resolved_ref == refs["main"]


def test_fetch_tag_to_branch_transition_removes_stale_tag(tmp_path: Path):
    source, refs = _create_git_source(tmp_path)
    subprocess.run(
        ["git", "-c", "tag.gpgSign=false", "tag", "shared", "main"],
        cwd=source,
        check=True,
    )
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    fetch(source_url, cache_dir=cache_dir, ref="shared")
    subprocess.run(["git", "tag", "-d", "shared"], cwd=source, check=True)
    subprocess.run(["git", "branch", "shared", "feature"], cwd=source, check=True)

    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref="shared",
    )

    assert (result / "version.txt").read_text() == "v2"
    assert resolved_ref == refs["feature"]

    source.rename(tmp_path / "offline-source")
    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(source_url, cache_dir=cache_dir, ref="refs/tags/shared")


def test_fetch_rejects_remote_ref_that_is_not_a_commit(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir, ref="v1.0.0")
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=source,
        input="not a commit",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/tags/v1.0.0", blob], cwd=source, check=True
    )

    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(source_url, cache_dir=cache_dir, ref="v1.0.0")

    assert (cached_path / "version.txt").read_text() == "v1"

    source.rename(tmp_path / "offline-source")
    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(source_url, cache_dir=cache_dir, ref="v1.0.0")


@pytest.mark.parametrize("warm_cache", [False, True])
def test_fetch_ambiguous_name_uses_branch(
    tmp_path: Path,
    warm_cache: bool,
):
    source, refs = _create_git_source(tmp_path)
    subprocess.run(["git", "branch", "shared", "feature"], cwd=source, check=True)
    subprocess.run(
        ["git", "-c", "tag.gpgSign=false", "tag", "shared", "main"],
        cwd=source,
        check=True,
    )
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    if warm_cache:
        fetch(source_url, cache_dir=cache_dir)

    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref="shared",
    )

    assert (result / "version.txt").read_text() == "v2"
    assert resolved_ref == refs["feature"]


@pytest.mark.parametrize(
    "requested_ref",
    [
        "refs/heads/feature",
        "origin/feature",
        "refs/remotes/origin/feature",
        "refs/pull/7/head",
    ],
)
def test_fetch_supported_ref_forms(
    tmp_path: Path,
    requested_ref: str,
):
    source, refs = _create_git_source(tmp_path)
    subprocess.run(
        ["git", "update-ref", "refs/pull/7/head", refs["feature"]],
        cwd=source,
        check=True,
    )
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"

    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref=requested_ref,
    )

    assert (result / "version.txt").read_text() == "v2"
    assert resolved_ref == refs["feature"]


def test_general_ref_requires_remote_for_reuse(tmp_path: Path):
    source, refs = _create_git_source(tmp_path)
    subprocess.run(
        ["git", "update-ref", "refs/pull/7/head", refs["feature"]],
        cwd=source,
        check=True,
    )
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    fetch(source_url, cache_dir=cache_dir, ref="refs/pull/7/head")

    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(
            source_url,
            cache_dir=cache_dir,
            ref="refs/pull/7/head",
            update=False,
        )


def test_requested_branch_fetch_stays_shallow_and_detached(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    subprocess.run(["git", "switch", "-q", "feature"], cwd=source, check=True)
    _commit_version(source, "v3")
    _commit_version(source, "v4")
    subprocess.run(["git", "switch", "-q", "main"], cwd=source, check=True)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir)

    fetch(source_url, cache_dir=cache_dir, ref="feature")

    assert (
        subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=cached_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "true"
    )
    assert (
        subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=cached_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "1"
    )
    assert (
        subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cached_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )


@pytest.mark.parametrize(
    "ref",
    ["HEAD", "refs/heads/HEAD", "origin/HEAD", "refs/remotes/origin/HEAD"],
)
def test_fetch_rejects_symbolic_default_ref(tmp_path: Path, ref: str):
    source, refs = _create_git_source(tmp_path)
    subprocess.run(
        ["git", "update-ref", "refs/heads/HEAD", "refs/heads/feature"],
        cwd=source,
        check=True,
    )
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir)

    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(source_url, cache_dir=cache_dir, ref=ref)

    assert (
        subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=cached_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "refs/remotes/origin/main"
    )
    assert (
        subprocess.run(
            ["git", "rev-parse", "refs/remotes/origin/main"],
            cwd=cached_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == refs["main"]
    )


def test_fetch_without_ref_preserves_wildcard_remote_updates(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    _clone_full_cache(source_url, cache_dir)

    subprocess.run(["git", "switch", "-q", "feature"], cwd=source, check=True)
    feature_head = _commit_version(source, "v3")
    subprocess.run(["git", "switch", "-q", "main"], cwd=source, check=True)

    fetch(source_url, cache_dir=cache_dir)
    source.rename(tmp_path / "offline-source")
    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref="feature",
    )

    assert (result / "version.txt").read_text() == "v3"
    assert resolved_ref == feature_head


def test_fetch_without_ref_respects_negative_fetch_refspec(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = _clone_full_cache(source_url, cache_dir)
    subprocess.run(
        ["git", "config", "--add", "remote.origin.fetch", "^refs/heads/feature"],
        cwd=cached_path,
        check=True,
    )
    subprocess.run(["git", "switch", "-q", "feature"], cwd=cached_path, check=True)

    subprocess.run(["git", "switch", "-q", "feature"], cwd=source, check=True)
    _commit_version(source, "v3")

    fetch(source_url, cache_dir=cache_dir)

    assert (cached_path / "version.txt").read_text() == "v2"


def test_fetch_qualified_tag_after_default_shallow_clone(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    fetch(source_url, cache_dir=cache_dir)
    tagged_head = _commit_version(source, "tagged", tag="v2")

    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref="refs/tags/v2",
    )

    assert (result / "version.txt").read_text() == "tagged"
    assert resolved_ref == tagged_head


def test_fetch_without_ref_caches_new_reachable_tag(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    fetch(source_url, cache_dir=cache_dir)
    tagged_head = _commit_version(source, "tagged", tag="v2")

    fetch(source_url, cache_dir=cache_dir)
    source.rename(tmp_path / "offline-source")
    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref="v2",
    )

    assert (result / "version.txt").read_text() == "tagged"
    assert resolved_ref == tagged_head


@pytest.mark.parametrize("ref_kind", ["tag", "sha"])
def test_fetch_older_pinned_ref_after_default_shallow_clone(
    tmp_path: Path,
    ref_kind: str,
):
    source, refs = _create_git_source(tmp_path)
    _commit_version(source, "latest")
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir)
    assert (cached_path / "version.txt").read_text() == "latest"
    requested_ref = "v1.0.0" if ref_kind == "tag" else refs["main"]

    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref=requested_ref,
    )

    assert (result / "version.txt").read_text() == "v1"
    assert resolved_ref == refs["main"]


@pytest.mark.parametrize("ref", ["--detach", "version.txt"])
def test_fetch_rejects_checkout_argument_or_path_as_ref(tmp_path: Path, ref: str):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    fetch(source_url, cache_dir=cache_dir)

    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(source_url, cache_dir=cache_dir, ref=ref)


def test_fetch_rejects_uncached_ref_when_fetch_fails(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    fetch(source_url, cache_dir=cache_dir)
    source.rename(tmp_path / "offline-source")

    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(
            source_url,
            cache_dir=cache_dir,
            ref="missing-ref",
        )


@pytest.mark.parametrize("ref_kind", ["tag", "sha"])
def test_fetch_uses_cached_pinned_ref_while_offline(tmp_path: Path, ref_kind: str):
    source, refs = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    fetch(source_url, cache_dir=cache_dir)
    source.rename(tmp_path / "offline-source")
    requested_ref = refs["main"] if ref_kind == "sha" else "v1.0.0"

    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref=requested_ref,
    )

    assert (result / "version.txt").read_text() == "v1"
    assert resolved_ref == refs["main"]


def test_fetch_uses_cached_remote_branch_while_offline(tmp_path: Path):
    source, refs = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    fetch(source_url, cache_dir=cache_dir, ref="feature")
    source.rename(tmp_path / "offline-source")

    result, resolved_ref = fetch_with_resolution(
        source_url,
        cache_dir=cache_dir,
        ref="feature",
    )

    assert (result / "version.txt").read_text() == "v2"
    assert resolved_ref == refs["feature"]


def test_fetch_does_not_treat_missing_full_sha_as_remote_branch(tmp_path: Path):
    source, refs = _create_git_source(tmp_path)
    missing_sha = "f" * 40
    subprocess.run(
        ["git", "branch", missing_sha, "feature"],
        cwd=source,
        check=True,
    )
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    fetch(source_url, cache_dir=cache_dir, ref=refs["main"])

    with pytest.raises(ExtensionFetchError, match="Failed to fetch extension"):
        fetch(source_url, cache_dir=cache_dir, ref=missing_sha)


def test_fetch_without_ref_uses_existing_cache_when_fetch_fails(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cache_path = fetch(source_url, cache_dir=cache_dir)
    source.rename(tmp_path / "offline-source")

    result = fetch(source_url, cache_dir=cache_dir)

    assert result == cache_path


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


def test_fetch_no_update_uses_cached_requested_ref(tmp_path: Path):
    source, _ = _create_git_source(tmp_path)
    source_url = f"file://{source}"
    cache_dir = tmp_path / "cache"
    cached_path = fetch(source_url, cache_dir=cache_dir, ref="feature")
    source.rename(tmp_path / "offline-source")

    result = fetch(
        source_url,
        cache_dir=cache_dir,
        update=False,
        ref="v1.0.0",
    )

    assert result == cached_path
    assert (result / "version.txt").read_text() == "v1"


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


def test_fetch_local_with_repo_path_resolves_subdirectory(tmp_path: Path):
    """A local source plus repo_path composes into the subdirectory (OSS-10405)."""
    ext_dir = tmp_path / "monorepo"
    ext_dir.mkdir()
    (ext_dir / "extensions" / "my-ext").mkdir(parents=True)

    path, resolved_ref = fetch_with_resolution(
        str(ext_dir),
        cache_dir=tmp_path,
        repo_path="extensions/my-ext",
    )

    assert path == (ext_dir / "extensions" / "my-ext").resolve()
    assert resolved_ref is None


@pytest.mark.parametrize(
    "source_suffix,repo_path",
    [
        ("", "extensions/my-ext"),
        ("", "/extensions/my-ext"),
        ("/", "extensions/my-ext"),
        ("/", "/extensions/my-ext"),
    ],
)
def test_fetch_local_repo_path_tolerates_slashes(
    tmp_path: Path, source_suffix: str, repo_path: str
):
    """Leading/trailing slashes on either field resolve the same way."""
    ext_dir = tmp_path / "monorepo"
    ext_dir.mkdir()
    (ext_dir / "extensions" / "my-ext").mkdir(parents=True)

    path = fetch(
        str(ext_dir) + source_suffix,
        cache_dir=tmp_path,
        repo_path=repo_path,
    )

    assert path == (ext_dir / "extensions" / "my-ext").resolve()


def test_fetch_local_with_missing_repo_path_raises_error(tmp_path: Path):
    ext_dir = tmp_path / "monorepo"
    ext_dir.mkdir()

    with pytest.raises(ExtensionFetchError, match="not found in local source"):
        fetch(str(ext_dir), cache_dir=tmp_path, repo_path="extensions/my-ext")


def test_fetch_local_repo_path_cannot_escape_source(tmp_path: Path):
    """A traversing repo_path is rejected rather than silently escaping."""
    ext_dir = tmp_path / "monorepo"
    ext_dir.mkdir()
    (tmp_path / "outside").mkdir()

    with pytest.raises(ExtensionFetchError, match="escapes local source"):
        fetch(str(ext_dir), cache_dir=tmp_path, repo_path="../outside")


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


def _clone_monorepo(url, dest, **kwargs):
    dest.mkdir(parents=True, exist_ok=True)
    (dest / ".git").mkdir()
    (dest / "plugins" / "city-weather").mkdir(parents=True)


def test_fetch_github_shorthand_subpath_is_repo_path(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)
    mock_git.clone.side_effect = _clone_monorepo

    result = fetch(
        "github:OpenHands/extensions/plugins/city-weather/",
        cache_dir=tmp_path,
        git_helper=mock_git,
    )

    assert (
        mock_git.clone.call_args[0][0] == "https://github.com/OpenHands/extensions.git"
    )
    assert result.name == "city-weather"
    assert result.parent.name == "plugins"


def test_fetch_github_shorthand_subpath_and_repo_path_conflict(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)

    with pytest.raises(ExtensionFetchError, match="do not also pass repo_path"):
        fetch(
            "github:OpenHands/extensions/plugins/city-weather",
            cache_dir=tmp_path,
            repo_path="plugins/city-weather",
            git_helper=mock_git,
        )
    mock_git.clone.assert_not_called()


def test_fetch_github_shorthand_subpath_cannot_escape(tmp_path: Path):
    mock_git = create_autospec(GitHelper, instance=True)
    mock_git.clone.side_effect = _clone_monorepo

    with pytest.raises(ExtensionFetchError, match="escapes"):
        fetch("github:owner/repo/../..", cache_dir=tmp_path, git_helper=mock_git)


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
    source, _ = _create_git_source(tmp_path)
    subprocess.run(["git", "switch", "-q", "feature"], cwd=source, check=True)
    subdir = source / "extensions" / "my-ext"
    subdir.mkdir(parents=True)
    (subdir / "content.txt").write_text("feature")
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add extension"], cwd=source, check=True
    )
    subprocess.run(["git", "switch", "-q", "main"], cwd=source, check=True)

    result = fetch(
        f"file://{source}",
        cache_dir=tmp_path,
        ref="feature",
        repo_path="extensions/my-ext",
    )

    assert (result / "content.txt").read_text() == "feature"


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
