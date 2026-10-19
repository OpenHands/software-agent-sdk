"""Tests for the installed-plugin management router (plugins_router).

Drives the endpoints through a TestClient against a temp install store (the SDK
default install dir is redirected), so nothing touches the real ~/.openhands.
"""

import json
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.plugins_router import plugins_router
from openhands.sdk.plugin import Plugin


def _make_installable_plugin(plugin_dir: Path, name: str) -> Path:
    """Create a minimal installable plugin directory (manifest only)."""
    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "description": name})
    )
    return plugin_dir


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """A TestClient whose install store is an isolated temp directory."""
    store = tmp_path / "installed-store"
    store.mkdir()
    monkeypatch.setattr(
        "openhands.sdk.plugin.installed.DEFAULT_INSTALLED_PLUGINS_DIR", store
    )
    app = FastAPI()
    app.include_router(plugins_router)
    return TestClient(app)


def test_install_then_get_by_name(client: TestClient, tmp_path: Path):
    src = _make_installable_plugin(tmp_path / "src", "demo-plugin")

    install = client.post("/plugins/install", json={"source": str(src)})

    assert install.status_code == 200
    assert install.json()["name"] == "demo-plugin"
    assert install.json()["enabled"] is True

    got = client.get("/plugins/installed/demo-plugin")
    assert got.status_code == 200
    assert got.json()["name"] == "demo-plugin"


def test_patch_toggles_enabled_state(client: TestClient, tmp_path: Path):
    src = _make_installable_plugin(tmp_path / "src", "demo-plugin")
    client.post("/plugins/install", json={"source": str(src)})

    disabled = client.patch("/plugins/installed/demo-plugin", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert client.get("/plugins/installed/demo-plugin").json()["enabled"] is False

    enabled = client.patch("/plugins/installed/demo-plugin", json={"enabled": True})
    assert enabled.json()["enabled"] is True


def test_uninstall_removes_from_installed_list(client: TestClient, tmp_path: Path):
    src = _make_installable_plugin(tmp_path / "src", "demo-plugin")
    client.post("/plugins/install", json={"source": str(src)})

    def installed_names() -> list[str]:
        return [p["name"] for p in client.get("/plugins/installed").json()["plugins"]]

    assert "demo-plugin" in installed_names()

    deleted = client.delete("/plugins/installed/demo-plugin")
    assert deleted.status_code == 200
    assert "demo-plugin" not in installed_names()


def test_install_existing_without_force_returns_409(client: TestClient, tmp_path: Path):
    src = _make_installable_plugin(tmp_path / "src", "demo-plugin")
    assert client.post("/plugins/install", json={"source": str(src)}).status_code == 200

    conflict = client.post("/plugins/install", json={"source": str(src)})
    assert conflict.status_code == 409


def test_missing_plugin_returns_404(client: TestClient):
    assert client.get("/plugins/installed/ghost").status_code == 404
    assert (
        client.patch("/plugins/installed/ghost", json={"enabled": False}).status_code
        == 404
    )
    assert client.delete("/plugins/installed/ghost").status_code == 404
    assert client.post("/plugins/installed/ghost/refresh").status_code == 404


def test_refresh_returns_updated_plugin(client: TestClient, tmp_path: Path):
    src = _make_installable_plugin(tmp_path / "src", "demo-plugin")
    client.post("/plugins/install", json={"source": str(src)})

    refreshed = client.post("/plugins/installed/demo-plugin/refresh")

    assert refreshed.status_code == 200
    assert refreshed.json()["plugin"]["name"] == "demo-plugin"


def test_installed_plugin_carries_bundled_skills(client: TestClient, tmp_path: Path):
    src = _make_installable_plugin(tmp_path / "src", "demo-plugin")
    commands = src / "commands"
    commands.mkdir()
    (commands / "greet.md").write_text("---\ndescription: Say hello\n---\nHello!")
    client.post("/plugins/install", json={"source": str(src)})

    listed = client.get("/plugins/installed")

    assert listed.status_code == 200
    assert listed.json()["plugins"][0]["skills"] == [
        {"name": "demo-plugin:greet", "description": "Say hello"}
    ]


def test_installed_plugin_lists_files_excluding_git_internals(
    client: TestClient, tmp_path: Path
):
    src = _make_installable_plugin(tmp_path / "src", "demo-plugin")
    (src / "README.md").write_text("# Demo")
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("[core]")
    client.post("/plugins/install", json={"source": str(src)})

    got = client.get("/plugins/installed/demo-plugin")

    assert got.status_code == 200
    assert got.json()["files"] == [".plugin/plugin.json", "README.md"]


def test_corrupt_installed_plugin_degrades_to_metadata_only(
    client: TestClient, tmp_path: Path
):
    src = _make_installable_plugin(tmp_path / "src", "demo-plugin")
    install = client.post("/plugins/install", json={"source": str(src)})
    manifest = Path(install.json()["install_path"]) / ".plugin" / "plugin.json"
    manifest.write_text("{not json")

    got = client.get("/plugins/installed/demo-plugin")

    assert got.status_code == 200
    assert got.json()["name"] == "demo-plugin"
    assert got.json()["skills"] is None
    assert got.json()["files"] is None


def test_list_available_returns_plugin_summaries(
    client: TestClient, tmp_path: Path, monkeypatch
):
    # load_available_plugins ships with a different ticket; inject a stub so the
    # lazily-imported symbol resolves here.
    plugin = Plugin.load(
        _make_installable_plugin(tmp_path / "avail", "available-plugin")
    )
    monkeypatch.setattr(
        "openhands.sdk.plugin.load_available_plugins",
        lambda **kwargs: {plugin.name: plugin},
        raising=False,
    )

    resp = client.post("/plugins", json={"load_user": True, "load_project": False})

    assert resp.status_code == 200
    assert resp.json() == {
        "plugins": [
            {
                "name": "available-plugin",
                "version": "1.0.0",
                "description": "available-plugin",
                "path": plugin.path,
                "skills": [],
                "files": [".plugin/plugin.json"],
            }
        ]
    }


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _make_git_plugin_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "git-plugin"
    plugin = repo / "plugins" / "demo"
    (plugin / ".plugin").mkdir(parents=True)
    (plugin / "commands").mkdir()
    (plugin / ".plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo-plugin",
                "version": "1.0.0",
                "automation": {"runner": "conversation"},
            }
        )
    )
    (plugin / "commands" / "run.md").write_text("---\ndescription: Run\n---\nRun it")
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_fetch_endpoint_pins_without_installing(
    client: TestClient, tmp_path: Path, monkeypatch
):
    repo, sha = _make_git_plugin_repo(tmp_path)
    monkeypatch.setattr(
        "openhands.sdk.plugin.strict_fetch.DEFAULT_CACHE_DIR", tmp_path / "cache"
    )

    before = client.get("/plugins/installed").json()
    resp = client.post(
        "/plugins/fetch",
        json={
            "source": f"file://{repo}",
            "ref": "HEAD",
            "repo_path": "plugins/demo",
        },
    )
    after = client.get("/plugins/installed").json()

    assert resp.status_code == 200
    body = resp.json()
    assert body["resolved_ref"] == sha
    assert sha in body["path"]
    assert body["source"] == f"file://{repo}"
    assert body["repo_path"] == "plugins/demo"
    assert body["manifest"]["automation"] == {"runner": "conversation"}
    assert body["commands"] == ["run"]
    assert before == after


def test_fetch_endpoint_returns_structured_error(client: TestClient):
    resp = client.post(
        "/plugins/fetch",
        json={"source": "https://x-access-token:${TOKEN}@example.invalid/repo.git"},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"] == {
        "code": "missing_secret",
        "message": "Missing secret: TOKEN",
    }


def test_fetch_endpoint_invalid_expanded_source_is_structured_and_redacted(
    client: TestClient,
):
    secret_value = "supersecretvalue"

    resp = client.post(
        "/plugins/fetch",
        json={
            "source": "${TOKEN}",
            "secrets": {
                "TOKEN": {"kind": "StaticSecret", "value": secret_value},
            },
        },
    )

    assert resp.status_code == 422
    assert resp.json()["detail"] == {
        "code": "invalid_source",
        "message": "Invalid plugin source.",
    }
    assert secret_value not in resp.text
