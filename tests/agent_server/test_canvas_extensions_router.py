"""Tests for the installed-canvas-extension management router
(canvas_extensions_router).

Drives the endpoints through a TestClient against a temp install store (the
default install dir is redirected), so nothing touches the real
~/.openhands.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.canvas_extensions_router import canvas_extensions_router

from .canvas_extensions.conftest import write_extension


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """A TestClient whose install store is an isolated temp directory."""
    store = tmp_path / "installed-store"
    monkeypatch.setattr(
        "openhands.agent_server.canvas_extensions.installed."
        "get_installed_canvas_extensions_dir",
        lambda: store,
    )
    app = FastAPI()
    app.include_router(canvas_extensions_router)
    return TestClient(app)


def test_install_lands_disabled_then_get_by_name(client: TestClient, tmp_path: Path):
    src = write_extension(tmp_path / "src" / "demo-extension", name="demo-extension")

    install = client.post("/canvas-extensions/install", json={"source": str(src)})

    assert install.status_code == 200
    assert install.json()["name"] == "demo-extension"
    assert install.json()["enabled"] is False

    got = client.get("/canvas-extensions/installed/demo-extension")
    assert got.status_code == 200
    assert got.json()["name"] == "demo-extension"
    assert got.json()["enabled"] is False


def test_patch_toggles_enabled_state(client: TestClient, tmp_path: Path):
    src = write_extension(tmp_path / "src" / "demo-extension", name="demo-extension")
    client.post("/canvas-extensions/install", json={"source": str(src)})

    enabled = client.patch(
        "/canvas-extensions/installed/demo-extension", json={"enabled": True}
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert (
        client.get("/canvas-extensions/installed/demo-extension").json()["enabled"]
        is True
    )

    disabled = client.patch(
        "/canvas-extensions/installed/demo-extension", json={"enabled": False}
    )
    assert disabled.json()["enabled"] is False


def test_uninstall_removes_from_installed_list(client: TestClient, tmp_path: Path):
    src = write_extension(tmp_path / "src" / "demo-extension", name="demo-extension")
    client.post("/canvas-extensions/install", json={"source": str(src)})

    def installed_names() -> list[str]:
        return [
            e["name"]
            for e in client.get("/canvas-extensions/installed").json()[
                "canvas_extensions"
            ]
        ]

    assert "demo-extension" in installed_names()

    deleted = client.delete("/canvas-extensions/installed/demo-extension")
    assert deleted.status_code == 200
    assert "demo-extension" not in installed_names()


def test_install_existing_without_force_returns_409(client: TestClient, tmp_path: Path):
    src = write_extension(tmp_path / "src" / "demo-extension", name="demo-extension")
    assert (
        client.post("/canvas-extensions/install", json={"source": str(src)}).status_code
        == 200
    )

    conflict = client.post("/canvas-extensions/install", json={"source": str(src)})
    assert conflict.status_code == 409


def test_install_invalid_source_returns_400(client: TestClient, tmp_path: Path):
    missing = tmp_path / "does-not-exist"

    resp = client.post("/canvas-extensions/install", json={"source": str(missing)})

    assert resp.status_code == 400


def test_install_invalid_manifest_returns_422(client: TestClient, tmp_path: Path):
    bad = tmp_path / "src" / "bad-extension"
    bad.mkdir(parents=True)
    (bad / "canvas-extension.json").write_text("{not json")

    resp = client.post("/canvas-extensions/install", json={"source": str(bad)})

    assert resp.status_code == 422


def test_install_missing_manifest_returns_422_not_500(
    client: TestClient, tmp_path: Path
):
    """A source with no manifest file at all raises FileNotFoundError (an
    OSError) out of load_from_dir()'s bare ``read_text()`` -- a very
    plausible real input (e.g. a wrong repo_path into a monorepo), not just
    a theoretical edge case. This must map to the endpoint's own documented
    422, not fall through to an unhandled 500 that leaks a filesystem path.
    """
    no_manifest = tmp_path / "src" / "no-manifest-extension"
    no_manifest.mkdir(parents=True)
    (no_manifest / "README.md").write_text("no manifest here")

    resp = client.post("/canvas-extensions/install", json={"source": str(no_manifest)})

    assert resp.status_code == 422


def test_missing_extension_returns_404(client: TestClient):
    assert client.get("/canvas-extensions/installed/ghost").status_code == 404
    assert (
        client.patch(
            "/canvas-extensions/installed/ghost", json={"enabled": False}
        ).status_code
        == 404
    )
    assert client.delete("/canvas-extensions/installed/ghost").status_code == 404
    assert client.get("/canvas-extensions/installed/ghost/bundle").status_code == 404


def test_bundle_endpoint_busts_cache_across_revisions(
    client: TestClient, tmp_path: Path
):
    """Cache-busting across revisions is the specific guarantee named in
    issue #4352: after a force-reinstall lands new bundle bytes, a client
    must get the new content, and the response's validators (ETag) must
    change too, or an intermediate cache keyed on them would keep serving
    the pre-refresh bundle even after a successful revalidation round trip.
    """
    src = write_extension(
        tmp_path / "src" / "demo-extension",
        name="demo-extension",
        entrypoint="dist/index.js",
    )
    client.post("/canvas-extensions/install", json={"source": str(src)})
    first = client.get("/canvas-extensions/installed/demo-extension/bundle")
    assert first.status_code == 200

    # Different length, not just different bytes -- so the assertion below
    # can't pass by relying on filesystem mtime resolution alone.
    (src / "dist" / "index.js").write_text("console.log('updated-bundle-v2')")
    client.post("/canvas-extensions/install", json={"source": str(src), "force": True})
    second = client.get("/canvas-extensions/installed/demo-extension/bundle")

    assert second.status_code == 200
    assert second.text == "console.log('updated-bundle-v2')"
    assert second.text != first.text
    assert second.headers["etag"] != first.headers["etag"]


def test_bundle_endpoint_serves_entrypoint_with_no_cache_header(
    client: TestClient, tmp_path: Path
):
    src = write_extension(
        tmp_path / "src" / "demo-extension",
        name="demo-extension",
        entrypoint="dist/index.js",
    )
    client.post("/canvas-extensions/install", json={"source": str(src)})

    resp = client.get("/canvas-extensions/installed/demo-extension/bundle")

    assert resp.status_code == 200
    assert resp.text == "console.log('ok')"
    assert resp.headers["cache-control"] == "no-cache"
    assert "javascript" in resp.headers["content-type"]


def test_bundle_endpoint_rejects_escaping_entrypoint_installed_out_of_band(
    client: TestClient, tmp_path: Path
):
    """Containment must be re-checked at serve time, not just install time.

    Simulates a symlink escape introduced after install (e.g. tampering
    with the install directory directly) by writing straight into the
    install store, bypassing install_canvas_extension()'s own validation.
    """
    src = write_extension(tmp_path / "src" / "demo-extension", name="demo-extension")
    install = client.post("/canvas-extensions/install", json={"source": str(src)})
    install_path = Path(install.json()["install_path"])

    outside = tmp_path / "outside.js"
    outside.write_text("payload")
    (install_path / "dist" / "index.js").unlink()
    (install_path / "dist" / "index.js").symlink_to(outside)

    resp = client.get("/canvas-extensions/installed/demo-extension/bundle")

    assert resp.status_code == 404


def test_bundle_endpoint_returns_404_when_entrypoint_file_deleted(
    client: TestClient, tmp_path: Path
):
    """Distinct failure mode from the symlink-escape case: the manifest is
    still valid, but the file it points to is simply gone.
    """
    src = write_extension(tmp_path / "src" / "demo-extension", name="demo-extension")
    install = client.post("/canvas-extensions/install", json={"source": str(src)})
    install_path = Path(install.json()["install_path"])
    (install_path / "dist" / "index.js").unlink()

    resp = client.get("/canvas-extensions/installed/demo-extension/bundle")

    assert resp.status_code == 404


def test_bundle_endpoint_serves_disabled_extensions_too(
    client: TestClient, tmp_path: Path
):
    """The bundle endpoint doesn't gate on ``enabled`` -- same as the GET
    metadata endpoints elsewhere in the codebase (plugins/skills), which
    don't filter disabled entries out either. A fresh install always lands
    disabled, so this is also the common case for a caller that installs
    and immediately previews the bundle before explicitly enabling it.
    """
    src = write_extension(tmp_path / "src" / "demo-extension", name="demo-extension")
    install = client.post("/canvas-extensions/install", json={"source": str(src)})
    assert install.json()["enabled"] is False

    resp = client.get("/canvas-extensions/installed/demo-extension/bundle")

    assert resp.status_code == 200


@pytest.mark.parametrize(
    "bad_name",
    ["Bad_Name", "a b", "-leading-hyphen", "trailing-hyphen-", "%2e%2e"],
)
def test_path_parameter_rejects_invalid_names(client: TestClient, bad_name: str):
    """The {extension_name} pattern is defense-in-depth against path
    traversal / injection through the URL itself -- it must reject bad
    names before any request reaches the service layer, not just rely on
    ``validate_extension_name`` deeper in the stack.
    """
    assert client.get(f"/canvas-extensions/installed/{bad_name}").status_code == 422
    assert (
        client.patch(
            f"/canvas-extensions/installed/{bad_name}", json={"enabled": True}
        ).status_code
        == 422
    )
    assert client.delete(f"/canvas-extensions/installed/{bad_name}").status_code == 422
    assert (
        client.get(f"/canvas-extensions/installed/{bad_name}/bundle").status_code == 422
    )


def test_install_ignores_smuggled_enabled_field(client: TestClient, tmp_path: Path):
    """There's no ``enabled`` field on the request model, so a caller
    attempting to force an install to land enabled is silently ignored
    rather than accepted -- matching the disabled-by-default guarantee at
    the HTTP boundary, not just at the service-function signature.
    """
    src = write_extension(tmp_path / "src" / "demo-extension", name="demo-extension")

    resp = client.post(
        "/canvas-extensions/install",
        json={"source": str(src), "enabled": True},
    )

    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_force_reinstall_preserves_enabled_state(client: TestClient, tmp_path: Path):
    src = write_extension(tmp_path / "src" / "demo-extension", name="demo-extension")
    client.post("/canvas-extensions/install", json={"source": str(src)})
    client.patch("/canvas-extensions/installed/demo-extension", json={"enabled": True})

    reinstalled = client.post(
        "/canvas-extensions/install", json={"source": str(src), "force": True}
    )

    assert reinstalled.status_code == 200
    assert reinstalled.json()["enabled"] is True
    assert (
        client.get("/canvas-extensions/installed/demo-extension").json()["enabled"]
        is True
    )


def test_list_installed_empty_and_multiple(client: TestClient, tmp_path: Path):
    assert client.get("/canvas-extensions/installed").json() == {
        "canvas_extensions": []
    }

    write_extension(tmp_path / "src" / "one", name="ext-one")
    write_extension(tmp_path / "src" / "two", name="ext-two")
    client.post(
        "/canvas-extensions/install", json={"source": str(tmp_path / "src" / "one")}
    )
    client.post(
        "/canvas-extensions/install", json={"source": str(tmp_path / "src" / "two")}
    )

    names = {
        e["name"]
        for e in client.get("/canvas-extensions/installed").json()["canvas_extensions"]
    }
    assert names == {"ext-one", "ext-two"}
