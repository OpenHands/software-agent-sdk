import asyncio
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.canvas_extensions.backend import (
    CanvasExtensionBackendManager,
)
from openhands.agent_server.canvas_extensions.installed import (
    disable_canvas_extension,
    install_canvas_extension,
    uninstall_canvas_extension,
)
from openhands.agent_server.canvas_extensions.manifest import MANIFEST_FILENAME
from openhands.agent_server.canvas_extensions_router import canvas_extensions_router

from .conftest import write_extension


_SERVER = """#!/usr/bin/python3
import http.server
import json
import os
import subprocess
import sys

port = int(sys.argv[1])
data_dir = sys.argv[2]
artifact_dir = sys.argv[3]
os.makedirs(data_dir, exist_ok=True)
child = subprocess.Popen([\"/bin/sleep\", \"300\"])
with open(os.path.join(data_dir, \"runtime.json\"), \"w\") as stream:
    json.dump({
        \"artifact_dir\": artifact_dir,
        \"child_pid\": child.pid,
        \"cwd\": os.getcwd(),
        \"environment\": dict(os.environ),
        \"port\": port,
    }, stream)
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass

print(\"backend-ready\", flush=True)
http.server.HTTPServer((\"127.0.0.1\", port), Handler).serve_forever()
"""


def _write_backend_extension(directory: Path, *, timeout: float = 3) -> Path:
    write_extension(directory)
    archive = directory / "backend-linux-amd64.tar.gz"
    script = _SERVER.encode()
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("server.py")
        info.mode = 0o755
        info.size = len(script)
        tar.addfile(info, io.BytesIO(script))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest_path = directory / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text())
    manifest["backend"] = {
        "schema_version": 1,
        "artifacts": {
            "linux-amd64": {
                "path": archive.name,
                "sha256": checksum,
            },
            "linux-arm64": {
                "path": archive.name,
                "sha256": checksum,
            },
        },
        "argv": [
            "{artifact_dir}/server.py",
            "{port}",
            "{data_dir}",
            "{artifact_dir}",
        ],
        "health": {
            "path": "/health",
            "timeout_seconds": timeout,
            "interval_seconds": 0.05,
        },
    }
    manifest_path.write_text(json.dumps(manifest))
    return directory


@pytest.mark.asyncio
async def test_prepare_start_logs_stop_and_preserve_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _write_backend_extension(tmp_path / "source" / "my-extension")
    installed_dir = tmp_path / "installed"
    state_dir = tmp_path / "state"
    install_canvas_extension(str(source), installed_dir=installed_dir)
    manager = CanvasExtensionBackendManager(installed_dir, state_dir)
    revision = manager.revision("my-extension")
    assert revision is not None

    assert (await manager.status("my-extension")).state == "stopped"
    prepared = await manager.prepare("my-extension", revision)
    assert prepared.prepared_revision == revision
    [artifact_dir] = list((manager.artifacts_dir / "my-extension").iterdir())
    assert artifact_dir.is_dir()
    assert not bool(artifact_dir.stat().st_mode & 0o222)

    monkeypatch.setenv("OH_SECRET_KEY", "must-not-leak")
    first, second = await asyncio.gather(
        manager.start("my-extension", revision),
        manager.start("my-extension", revision),
    )
    assert first.state == second.state == "ready"
    assert first.pid == second.pid
    assert manager.ready_endpoint("my-extension") == ("127.0.0.1", first.port)

    runtime_file = manager.data_dir / "my-extension" / "runtime.json"
    runtime = json.loads(runtime_file.read_text())
    assert runtime["cwd"] == str((installed_dir / "my-extension").resolve())
    assert runtime["artifact_dir"] == str(artifact_dir)
    assert "OH_SECRET_KEY" not in runtime["environment"]
    child_pid = runtime["child_pid"]

    logs = await manager.logs("my-extension", 4096)
    assert "backend-ready" in logs.logs
    stopped = await manager.stop("my-extension")
    assert stopped.state == "stopped"
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
    assert runtime_file.is_file()

    restarted = await manager.start("my-extension", revision)
    assert restarted.state == "ready"
    await manager.shutdown()
    assert (await manager.status("my-extension")).state == "stopped"

    assert disable_canvas_extension("my-extension", installed_dir=installed_dir)
    assert uninstall_canvas_extension("my-extension", installed_dir=installed_dir)
    assert runtime_file.is_file()


@pytest.mark.asyncio
async def test_prepare_rejects_revision_checksum_and_unsafe_archive(tmp_path: Path):
    source = _write_backend_extension(tmp_path / "source" / "my-extension")
    installed_dir = tmp_path / "installed"
    install_canvas_extension(str(source), installed_dir=installed_dir)
    manager = CanvasExtensionBackendManager(installed_dir, tmp_path / "state")
    revision = manager.revision("my-extension")
    assert revision is not None

    with pytest.raises(ValueError, match="revision"):
        await manager.prepare("my-extension", "wrong-revision")

    archive = installed_dir / "my-extension" / "backend-linux-amd64.tar.gz"
    archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        await manager.prepare("my-extension", revision)

    source = _write_backend_extension(tmp_path / "source-unsafe" / "my-extension")
    archive = source / "backend-linux-amd64.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"escape"
        info = tarfile.TarInfo("../escape")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    manifest_path = source / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text())
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    for artifact in manifest["backend"]["artifacts"].values():
        artifact["sha256"] = checksum
    manifest_path.write_text(json.dumps(manifest))
    unsafe_installed = tmp_path / "unsafe-installed"
    install_canvas_extension(str(source), installed_dir=unsafe_installed)
    unsafe_manager = CanvasExtensionBackendManager(
        unsafe_installed, tmp_path / "unsafe-state"
    )
    unsafe_revision = unsafe_manager.revision("my-extension")
    assert unsafe_revision is not None
    with pytest.raises(ValueError, match="unsafe archive"):
        await unsafe_manager.prepare("my-extension", unsafe_revision)
    assert not (tmp_path / "escape").exists()


@pytest.mark.asyncio
async def test_start_rejects_executable_that_resolves_outside_artifact(tmp_path: Path):
    source = _write_backend_extension(tmp_path / "source" / "my-extension")
    manifest_path = source / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text())
    manifest["backend"]["argv"][0] = "{artifact_dir}/../outside"
    manifest_path.write_text(json.dumps(manifest))
    installed_dir = tmp_path / "installed-escape"
    manager = CanvasExtensionBackendManager(installed_dir, tmp_path / "state-escape")
    install_canvas_extension(str(source), installed_dir=installed_dir)
    revision = manager.revision("my-extension")
    assert revision is not None
    await manager.prepare("my-extension", revision)
    outside = manager.artifacts_dir / "my-extension" / "outside"
    outside.write_text("#!/bin/sh\nexit 0\n")
    outside.chmod(0o755)

    status = await manager.start("my-extension", revision)

    assert status.state == "unhealthy"
    assert status.pid is None
    assert "inside the prepared artifact" in (status.detail or "")


@pytest.mark.asyncio
async def test_failed_start_cleanup_and_unsupported_states(tmp_path: Path):
    source = _write_backend_extension(tmp_path / "source" / "my-extension", timeout=0.2)
    installed_dir = tmp_path / "installed"
    install_canvas_extension(str(source), installed_dir=installed_dir)
    installed_script_archive = (
        installed_dir / "my-extension" / "backend-linux-amd64.tar.gz"
    )
    payload = b"#!/usr/bin/python3\nimport time\ntime.sleep(300)\n"
    with tarfile.open(installed_script_archive, "w:gz") as tar:
        info = tarfile.TarInfo("server.py")
        info.mode = 0o755
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    manifest_path = installed_dir / "my-extension" / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text())
    checksum = hashlib.sha256(installed_script_archive.read_bytes()).hexdigest()
    for artifact in manifest["backend"]["artifacts"].values():
        artifact["sha256"] = checksum
    manifest_path.write_text(json.dumps(manifest))

    manager = CanvasExtensionBackendManager(installed_dir, tmp_path / "state")
    revision = manager.revision("my-extension")
    assert revision is not None
    await manager.prepare("my-extension", revision)
    failed = await manager.start("my-extension", revision)
    assert failed.state == "unhealthy"
    assert failed.pid is None
    assert failed.port is None
    assert "timed out" in (failed.detail or "")

    browser_source = write_extension(
        tmp_path / "browser" / "browser-only", name="browser-only"
    )
    install_canvas_extension(str(browser_source), installed_dir=installed_dir)
    assert (await manager.status("my-extension-missing")).state == "missing"
    assert (await manager.status("my-extension")).state == "unhealthy"
    assert (await manager.status("browser-only")).state == "unsupported"
    await manager.shutdown()


def test_backend_http_lifecycle_and_data_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _write_backend_extension(tmp_path / "source" / "my-extension")
    installed_dir = tmp_path / "installed"
    monkeypatch.setattr(
        "openhands.agent_server.canvas_extensions.installed."
        "get_installed_canvas_extensions_dir",
        lambda: installed_dir,
    )
    manager = CanvasExtensionBackendManager(installed_dir, tmp_path / "state")
    app = FastAPI()
    app.state.canvas_extension_backend_manager = manager
    app.include_router(canvas_extensions_router)

    with TestClient(app) as client:
        installed = client.post(
            "/canvas-extensions/install", json={"source": str(source)}
        )
        assert installed.status_code == 200
        status = client.get("/canvas-extensions/installed/my-extension/backend").json()
        revision = status["revision"]
        assert status["state"] == "stopped"

        unprepared = client.post(
            "/canvas-extensions/installed/my-extension/backend/start",
            json={"revision": revision},
        )
        assert unprepared.status_code == 409
        prepared = client.post(
            "/canvas-extensions/installed/my-extension/backend/prepare",
            json={"revision": revision},
        )
        assert prepared.status_code == 200
        started = client.post(
            "/canvas-extensions/installed/my-extension/backend/start",
            json={"revision": revision},
        )
        assert started.json()["state"] == "ready"
        assert (
            client.get(
                "/canvas-extensions/installed/my-extension/backend/logs",
                params={"limit_bytes": 4096},
            ).status_code
            == 200
        )
        running_delete = client.delete(
            "/canvas-extensions/installed/my-extension/backend/data"
        )
        assert running_delete.status_code == 409
        assert (
            client.post(
                "/canvas-extensions/installed/my-extension/backend/stop"
            ).json()["state"]
            == "stopped"
        )
        deleted = client.delete(
            "/canvas-extensions/installed/my-extension/backend/data"
        )
        assert deleted.status_code == 200
        assert not (manager.data_dir / "my-extension").exists()
