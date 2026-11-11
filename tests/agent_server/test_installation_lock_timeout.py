"""A plugin, skill or Canvas request that times out on the installation
metadata lock answers 503, not a 500 or a 422.

Another holder keeps the real lock file locked while each endpoint runs, with
the lock timeout cut to 0.2 s. Goes through ``create_app`` because the mapping
is an app-level exception handler.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from filelock import FileLock

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.sdk.extensions.installation import metadata as metadata_module

from .canvas_extensions.conftest import write_extension


_BUSY_DETAIL = (
    "Another plugin, skill or extension operation is in progress. Please retry."
)


@pytest.fixture
def stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    plugins = tmp_path / "plugins-installed"
    skills = tmp_path / "skills-installed"
    canvas = tmp_path / "canvas-installed"
    for store in (plugins, skills, canvas):
        store.mkdir()
    monkeypatch.setattr(
        "openhands.sdk.plugin.installed.DEFAULT_INSTALLED_PLUGINS_DIR", plugins
    )
    monkeypatch.setattr(
        "openhands.sdk.skills.installed.DEFAULT_INSTALLED_SKILLS_DIR", skills
    )
    monkeypatch.setattr(
        "openhands.agent_server.canvas_extensions.installed."
        "get_installed_canvas_extensions_dir",
        lambda: canvas,
    )
    # The marketplace routes fetch their catalog with git before listing.
    monkeypatch.setattr(
        "openhands.agent_server.plugins_service._fetch_plugin_catalog_entries",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "openhands.agent_server.skills_service._fetch_catalog_entries",
        lambda *args, **kwargs: [],
    )
    return {"plugins": plugins, "skills": skills, "canvas": canvas}


@pytest.fixture
def client(tmp_path: Path, stores: dict[str, Path]) -> TestClient:
    client = TestClient(
        create_app(Config(session_api_keys=[])), raise_server_exceptions=False
    )
    plugin = tmp_path / "src" / "demo-plugin"
    (plugin / ".plugin").mkdir(parents=True)
    (plugin / ".plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo-plugin", "version": "1.0.0", "description": "x"})
    )
    skill = tmp_path / "src" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: A test skill\n---\nBody\n"
    )
    canvas = write_extension(tmp_path / "src" / "demo-canvas", name="demo-canvas")
    for route, source in (
        ("/api/plugins/install", plugin),
        ("/api/skills/install", skill),
        ("/api/canvas-extensions/install", canvas),
    ):
        response = client.post(route, json={"source": str(source)})
        assert response.status_code == 200, response.text
    return client


@pytest.mark.parametrize(
    ("store", "method", "path", "json_body"),
    [
        ("plugins", "POST", "/api/plugins/install", "source"),
        ("plugins", "GET", "/api/plugins/installed", None),
        ("plugins", "PATCH", "/api/plugins/installed/demo-plugin", {"enabled": False}),
        ("plugins", "DELETE", "/api/plugins/installed/demo-plugin", None),
        ("plugins", "POST", "/api/plugins/installed/demo-plugin/refresh", None),
        ("plugins", "GET", "/api/plugins/marketplace", None),
        ("skills", "POST", "/api/skills/install", "source"),
        ("skills", "GET", "/api/skills/installed", None),
        ("skills", "PATCH", "/api/skills/installed/demo-skill", {"enabled": False}),
        ("skills", "DELETE", "/api/skills/installed/demo-skill", None),
        ("skills", "POST", "/api/skills/installed/demo-skill/refresh", None),
        ("skills", "GET", "/api/skills/marketplace", None),
        ("canvas", "POST", "/api/canvas-extensions/install", "source"),
        ("canvas", "GET", "/api/canvas-extensions/installed", None),
        (
            "canvas",
            "PATCH",
            "/api/canvas-extensions/installed/demo-canvas",
            {"enabled": True},
        ),
        ("canvas", "DELETE", "/api/canvas-extensions/installed/demo-canvas", None),
        (
            "canvas",
            "DELETE",
            "/api/canvas-extensions/installed/demo-canvas/backend/secret-grant",
            None,
        ),
    ],
)
def test_lock_timeout_returns_503(
    client: TestClient,
    stores: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    store: str,
    method: str,
    path: str,
    json_body: dict | str | None,
):
    if json_body == "source":
        # A force reinstall from a fresh source, so the request gets past the
        # "already installed" check and fetching, up to the lock.
        name = {"plugins": "demo-plugin", "skills": "demo-skill"}.get(
            store, "demo-canvas"
        )
        json_body = {"source": str(tmp_path / "src" / name), "force": True}
    monkeypatch.setattr(metadata_module, "_LOCK_TIMEOUT_SECONDS", 0.2)

    held = FileLock(stores[store] / ".metadata.lock")
    with held:
        response = client.request(method, path, json=json_body)

    assert response.status_code == 503, response.text
    assert response.json() == {"detail": _BUSY_DETAIL}
