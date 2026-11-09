"""Tests for strict plugin pre-flight and pinned fetch behavior."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from openhands.sdk.plugin import (
    PluginSourceError,
    fetch_plugin_with_resolution,
    preflight_plugin,
)
from openhands.sdk.plugin.strict_fetch import fetch_plugin_strict


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _make_plugin_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    plugin = repo / "plugins" / "demo"
    (plugin / ".plugin").mkdir(parents=True)
    (plugin / "skills" / "hello").mkdir(parents=True)
    (plugin / "commands").mkdir()
    (plugin / ".plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo-plugin",
                "version": "1.0.0",
                "description": "Demo",
                "automation": {"runner": "conversation"},
            }
        )
    )
    (plugin / "skills" / "hello" / "SKILL.md").write_text(
        "---\nname: hello\ndescription: Hello\n---\nHello"
    )
    (plugin / "commands" / "run.md").write_text("---\ndescription: Run\n---\nRun it")
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    sha1 = _commit(repo, "v1")

    (plugin / ".plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo-plugin",
                "version": "2.0.0",
                "description": "Demo v2",
                "automation": {"runner": "script"},
            }
        )
    )
    sha2 = _commit(repo, "v2")
    return repo, sha1, sha2


def test_preflight_rejects_file_source_by_default(tmp_path: Path):
    repo, _sha1, _sha2 = _make_plugin_repo(tmp_path)

    with pytest.raises(PluginSourceError) as exc_info:
        with preflight_plugin(
            source=f"file://{repo}",
            ref="HEAD",
            repo_path="plugins/demo",
        ):
            pass

    assert exc_info.value.code == "source_not_allowed"


def test_preflight_yields_raw_manifest_and_removes_temp_checkout(tmp_path: Path):
    repo, _sha1, sha2 = _make_plugin_repo(tmp_path)

    with preflight_plugin(
        source=f"file://{repo}",
        ref="HEAD",
        repo_path="plugins/demo",
        allow_local=True,
    ) as checked:
        checkout_path = checked.path
        assert checkout_path.exists()
        assert checked.resolved_ref == sha2
        assert checked.source == f"file://{repo}"
        assert checked.repo_path == "plugins/demo"
        assert checked.manifest["automation"] == {"runner": "script"}
        assert checked.skills == ["hello"]
        assert checked.commands == ["run"]

    assert not checkout_path.exists()


def test_preflight_missing_secret_fails_before_git(tmp_path: Path):
    repo, _sha1, _sha2 = _make_plugin_repo(tmp_path)

    with pytest.raises(PluginSourceError) as exc_info:
        with preflight_plugin(
            source="file://${TOKEN}@" + str(repo),
            ref="HEAD",
            repo_path="plugins/demo",
            allow_local=True,
        ):
            pass

    assert exc_info.value.code == "missing_secret"
    assert "TOKEN" in exc_info.value.message


def test_preflight_invalid_expanded_source_hides_secret_value():
    secret_value = "supersecretvalue"

    with pytest.raises(PluginSourceError) as exc_info:
        with preflight_plugin(
            source="${TOKEN}",
            secrets={"TOKEN": secret_value},
        ):
            pass

    assert exc_info.value.code == "invalid_source"
    assert exc_info.value.message == "Invalid plugin source."
    assert secret_value not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_strict_fetch_pins_each_commit_to_an_immutable_cache_path(tmp_path: Path):
    repo, sha1, sha2 = _make_plugin_repo(tmp_path)
    cache_dir = tmp_path / "cache"

    first = fetch_plugin_strict(
        source=f"file://{repo}",
        ref=sha1,
        repo_path="plugins/demo",
        cache_dir=cache_dir,
        allow_local=True,
    )
    second = fetch_plugin_strict(
        source=f"file://{repo}",
        ref=sha2,
        repo_path="plugins/demo",
        cache_dir=cache_dir,
        allow_local=True,
    )

    assert sha1 in str(first.path)
    assert sha2 in str(second.path)
    assert first.path != second.path
    assert first.manifest["version"] == "1.0.0"
    assert second.manifest["version"] == "2.0.0"
    assert (
        json.loads((first.path / ".plugin" / "plugin.json").read_text())["version"]
        == "1.0.0"
    )

    cached_path, resolved = fetch_plugin_with_resolution(
        source=f"file://{repo}",
        ref=sha1,
        repo_path="plugins/demo",
        cache_dir=cache_dir,
    )
    assert cached_path == first.path
    assert resolved == sha1


def test_strict_fetch_does_not_cache_git_remote_credentials(tmp_path: Path):
    repo, sha1, _sha2 = _make_plugin_repo(tmp_path)
    cache_dir = tmp_path / "cache"
    secret_value = "SUPERSECRET"
    source = f"file://x-access-token:${{TOKEN}}@{repo}"

    first = fetch_plugin_strict(
        source=source,
        ref=sha1,
        repo_path="plugins/demo",
        secrets={"TOKEN": secret_value},
        cache_dir=cache_dir,
        allow_local=True,
    )
    second = fetch_plugin_strict(
        source=source,
        ref=sha1,
        repo_path="plugins/demo",
        secrets={"TOKEN": secret_value},
        cache_dir=cache_dir,
        allow_local=True,
    )

    assert second.path == first.path
    pinned_root = first.path.parents[1]
    assert not (pinned_root / ".git").exists()
    assert not list(pinned_root.rglob(".git"))
    assert secret_value not in "\n".join(
        path.read_text(errors="ignore")
        for path in pinned_root.rglob("*")
        if path.is_file()
    )
