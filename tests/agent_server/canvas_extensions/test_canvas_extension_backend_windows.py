"""Native Windows x64 Canvas App backend lifecycle, using real subprocesses."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.canvas_extensions.backend import (
    CanvasExtensionBackendManager,
)
from openhands.agent_server.canvas_extensions.installed import (
    install_canvas_extension,
)
from openhands.agent_server.canvas_extensions.manifest import MANIFEST_FILENAME
from openhands.agent_server.canvas_extensions.windows_job import (
    pid_alive,
    process_start_time,
)
from openhands.agent_server.canvas_extensions_router import canvas_extensions_router

from .conftest import write_extension


pytestmark = pytest.mark.skipif(
    CanvasExtensionBackendManager.current_platform() != "windows-amd64",
    reason="native Windows x64 backend lifecycle",
)


_SERVER = """
import http.server
import json
import os
import subprocess
import sys

port = int(sys.argv[1])
data_dir = sys.argv[2]
artifact_dir = sys.argv[3]
os.makedirs(data_dir, exist_ok=True)

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        if self.path == "/exit":
            self.wfile.flush()
            os._exit(0)

    def log_message(self, format, *args):
        pass

server = http.server.HTTPServer(("127.0.0.1", port), Handler)
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(300)"],
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
)
with open(os.path.join(data_dir, "runtime.json"), "w") as stream:
    json.dump({
        "artifact_dir": artifact_dir,
        "child_pid": child.pid,
        "cwd": os.getcwd(),
        "environment": dict(os.environ),
        "port": port,
    }, stream)
print("backend-ready", flush=True)
server.serve_forever()
"""


_CRASHING_SERVER = """
import asyncio, sys
from pathlib import Path
from openhands.agent_server.canvas_extensions.backend import (
    CanvasExtensionBackendManager,
)

async def main():
    manager = CanvasExtensionBackendManager(Path(sys.argv[1]), Path(sys.argv[2]))
    revision = manager.revision("my-extension")
    await manager.prepare("my-extension", revision)
    status = await manager.start("my-extension", revision)
    print(status.model_dump_json(), flush=True)
    await asyncio.sleep(300)

asyncio.run(main())
"""


def _creationflags() -> int:
    flags = subprocess.CREATE_NEW_PROCESS_GROUP
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags |= subprocess.CREATE_NO_WINDOW
    return flags


@pytest.fixture(scope="session")
def windows_python_payload(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real CPython PE plus its DLLs, so argv[0] stays in the artifact."""
    src_dir = Path(sys.executable).parent
    dest = tmp_path_factory.mktemp("pyinterp")
    shutil.copy2(sys.executable, dest / "python.exe")
    for pattern in ("python*.dll", "vcruntime*.dll", "zlib*.dll"):
        for src in src_dir.glob(pattern):
            shutil.copy2(src, dest / src.name)
    assert (dest / "python.exe").is_file()
    return dest


def _add_file(
    tar: tarfile.TarFile, source: Path, arcname: str, mode: int = 0o755
) -> None:
    info = tarfile.TarInfo(arcname.replace("\\", "/"))
    info.size = source.stat().st_size
    info.mode = mode
    with source.open("rb") as stream:
        tar.addfile(info, stream)


def _write_backend_extension(
    directory: Path,
    python_payload: Path,
    *,
    timeout: float = 8,
    script: str = _SERVER,
) -> Path:
    write_extension(directory)
    archive = directory / "backend-windows-amd64.tar.gz"
    payload = script.encode()
    with tarfile.open(archive, "w:gz") as tar:
        for source in python_payload.iterdir():
            if source.is_file():
                _add_file(tar, source, source.name)
        info = tarfile.TarInfo("server.py")
        info.mode = 0o644
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest_path = directory / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text())
    manifest["backend"] = {
        "schema_version": 1,
        "artifacts": {
            "windows-amd64": {"path": archive.name, "sha256": checksum},
        },
        "argv": [
            "{artifact_dir}/python.exe",
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


async def _wait_dead(pid: int, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while pid_alive(pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert not pid_alive(pid), f"pid {pid} still alive"


@pytest.mark.asyncio
async def test_linux_only_artifact_is_unsupported_on_windows(tmp_path: Path):
    source = write_extension(tmp_path / "source" / "my-extension")
    archive = source / "backend-linux-amd64.tar.gz"
    archive.write_bytes(b"placeholder")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest_path = source / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text())
    manifest["backend"] = {
        "schema_version": 1,
        "artifacts": {
            "linux-amd64": {"path": archive.name, "sha256": checksum},
            "linux-arm64": {"path": archive.name, "sha256": checksum},
        },
        "argv": ["{artifact_dir}/server.py", "{port}"],
    }
    manifest_path.write_text(json.dumps(manifest))
    installed_dir = tmp_path / "installed"
    install_canvas_extension(str(source), installed_dir=installed_dir)
    manager = CanvasExtensionBackendManager(installed_dir, tmp_path / "state")
    revision = manager.revision("my-extension")
    assert revision is not None
    status = await manager.status("my-extension")
    assert status.state == "unsupported"
    assert "platform" in (status.detail or "")
    assert (await manager.prepare("my-extension", revision)).state == "unsupported"
    assert (await manager.start("my-extension", revision)).state == "unsupported"


@pytest.mark.asyncio
async def test_prepare_start_health_logs_stop_and_survives_without_ui(
    tmp_path: Path,
    windows_python_payload: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = _write_backend_extension(
        tmp_path / "source" / "my-extension", windows_python_payload
    )
    installed_dir = tmp_path / "installed"
    state_dir = tmp_path / "state"
    install_canvas_extension(str(source), installed_dir=installed_dir)
    manager = CanvasExtensionBackendManager(installed_dir, state_dir)
    revision = manager.revision("my-extension")
    assert revision is not None

    assert (await manager.status("my-extension")).state == "stopped"
    prepared = await manager.prepare("my-extension", revision)
    assert prepared.prepared_revision == revision

    monkeypatch.setenv("OH_SECRET_KEY", "must-not-leak")
    started = await manager.start("my-extension", revision)
    assert started.state == "ready"
    assert started.pid is not None
    assert manager.ready_endpoint("my-extension") == ("127.0.0.1", started.port)

    # Navigating away from the App does not unmount this lifecycle: the
    # backend keeps running until an explicit stop / disable / uninstall /
    # shutdown.
    await asyncio.sleep(0.2)
    still = await manager.status("my-extension")
    assert still.state == "ready"
    assert still.pid == started.pid

    runtime = json.loads(
        (manager.data_dir / "my-extension" / "runtime.json").read_text()
    )
    assert runtime["cwd"] == str((installed_dir / "my-extension").resolve())
    assert "OH_SECRET_KEY" not in runtime["environment"]
    assert runtime["environment"]["USERPROFILE"] == str(
        manager.data_dir / "my-extension" / "home"
    )
    child_pid = int(runtime["child_pid"])
    assert pid_alive(child_pid)

    logs = await manager.logs("my-extension", 4096)
    assert "backend-ready" in logs.logs

    stopped = await manager.stop("my-extension")
    assert stopped.state == "stopped"
    await _wait_dead(started.pid)
    await _wait_dead(child_pid)
    assert (manager.data_dir / "my-extension" / "runtime.json").is_file()

    restarted = await manager.start("my-extension", revision)
    assert restarted.state == "ready"
    await manager.shutdown()
    assert (await manager.status("my-extension")).state == "stopped"


@pytest.mark.asyncio
async def test_stop_kills_descendants(
    tmp_path: Path,
    windows_python_payload: Path,
):
    source = _write_backend_extension(
        tmp_path / "source" / "my-extension", windows_python_payload
    )
    installed_dir = tmp_path / "installed"
    install_canvas_extension(str(source), installed_dir=installed_dir)
    manager = CanvasExtensionBackendManager(installed_dir, tmp_path / "state")
    revision = manager.revision("my-extension")
    assert revision is not None
    await manager.prepare("my-extension", revision)
    started = await manager.start("my-extension", revision)
    assert started.state == "ready"
    assert started.pid is not None
    child_pid = int(
        json.loads((manager.data_dir / "my-extension" / "runtime.json").read_text())[
            "child_pid"
        ]
    )
    assert pid_alive(child_pid)
    stopped = await manager.stop("my-extension")
    assert stopped.state == "stopped"
    await _wait_dead(started.pid)
    await _wait_dead(child_pid)


def test_http_disable_stops_backend_tree(
    tmp_path: Path,
    windows_python_payload: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = _write_backend_extension(
        tmp_path / "source" / "my-extension", windows_python_payload
    )
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
        assert (
            client.post(
                "/canvas-extensions/installed/my-extension/backend/prepare",
                json={"revision": revision},
            ).status_code
            == 200
        )
        started = client.post(
            "/canvas-extensions/installed/my-extension/backend/start",
            json={"revision": revision},
        )
        assert started.json()["state"] == "ready"
        pid = started.json()["pid"]
        child_pid = int(
            json.loads(
                (manager.data_dir / "my-extension" / "runtime.json").read_text()
            )["child_pid"]
        )
        disabled = client.patch(
            "/canvas-extensions/installed/my-extension", json={"enabled": False}
        )
        assert disabled.status_code == 200
    assert not pid_alive(pid)
    assert not pid_alive(child_pid)


@pytest.mark.asyncio
async def test_ready_endpoint_closes_when_backend_exits(
    tmp_path: Path, windows_python_payload: Path
):
    source = _write_backend_extension(
        tmp_path / "source" / "my-extension", windows_python_payload
    )
    installed_dir = tmp_path / "installed"
    install_canvas_extension(str(source), installed_dir=installed_dir)
    manager = CanvasExtensionBackendManager(installed_dir, tmp_path / "state")
    revision = manager.revision("my-extension")
    assert revision is not None
    await manager.prepare("my-extension", revision)
    try:
        ready = await manager.start("my-extension", revision)
        assert ready.state == "ready"
        await asyncio.to_thread(
            urllib.request.urlopen, f"http://127.0.0.1:{ready.port}/exit", timeout=5
        )
        process = manager._runtimes["my-extension"].process
        assert process is not None
        deadline = time.monotonic() + 5
        while process.returncode is None and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert process.returncode is not None
        assert manager.ready_endpoint("my-extension") is None
        crashed = await manager.status("my-extension")
        assert crashed.state == "unhealthy"
        restarted = await manager.start("my-extension", revision)
        assert restarted.state == "ready"
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_server_crash_cleans_tree_and_reap_skips_unrelated_pid(
    tmp_path: Path, windows_python_payload: Path
):
    """Abnormal Agent Server exit: job close and/or reap_orphans.

    Preferred path is automatic cleanup via ``KILL_ON_JOB_CLOSE``. If the
    server could not nest a job, ``reap_orphans`` still kills a leftover
    whose start time and image/command line identify it as this backend,
    and never terminates a bystander just because a forged record reused
    its PID.
    """
    source = _write_backend_extension(
        tmp_path / "source" / "my-extension", windows_python_payload
    )
    installed_dir = tmp_path / "installed"
    state_dir = tmp_path / "state"
    install_canvas_extension(str(source), installed_dir=installed_dir)
    crashed = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CRASHING_SERVER,
        str(installed_dir),
        str(state_dir),
        stdout=asyncio.subprocess.PIPE,
        creationflags=_creationflags(),
    )
    assert crashed.stdout is not None
    line = await asyncio.wait_for(crashed.stdout.readline(), timeout=60)
    started = json.loads(line)
    assert started["state"] == "ready"
    backend_pid = started["pid"]
    child_pid = json.loads(
        (state_dir / "data" / "my-extension" / "runtime.json").read_text()
    )["child_pid"]
    crashed.kill()
    await crashed.wait()

    bystander = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(300)",
        creationflags=_creationflags(),
    )
    [record_path] = list((state_dir / "run").glob("*.json"))
    record = json.loads(record_path.read_text())
    forged = state_dir / "run" / f"{bystander.pid}.json"
    record.update(pgid=bystander.pid, start_time=process_start_time(bystander.pid) or 0)
    forged.write_text(json.dumps(record))

    manager = CanvasExtensionBackendManager(installed_dir, state_dir)
    try:
        await manager.reap_orphans()
        await _wait_dead(backend_pid)
        await _wait_dead(child_pid)
        assert bystander.returncode is None and pid_alive(bystander.pid)
        assert list((state_dir / "run").iterdir()) == []
        assert (await manager.status("my-extension")).state == "stopped"
    finally:
        bystander.kill()
        await bystander.wait()
        await manager.shutdown()


@pytest.mark.asyncio
async def test_reap_orphans_kills_verified_orphan_not_pid_reuse(
    tmp_path: Path, windows_python_payload: Path
):
    """Recovery verifies identity: matching PID with the wrong start time lives."""
    source = _write_backend_extension(
        tmp_path / "source" / "my-extension", windows_python_payload
    )
    installed_dir = tmp_path / "installed"
    state_dir = tmp_path / "state"
    install_canvas_extension(str(source), installed_dir=installed_dir)
    manager = CanvasExtensionBackendManager(installed_dir, state_dir)
    revision = manager.revision("my-extension")
    assert revision is not None
    await manager.prepare("my-extension", revision)
    [artifact_dir] = list((manager.artifacts_dir / "my-extension").iterdir())
    python_exe = artifact_dir / "python.exe"
    orphan = await asyncio.create_subprocess_exec(
        str(python_exe),
        "-c",
        "import time; time.sleep(300)",
        creationflags=_creationflags(),
    )
    bystander = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(300)",
        creationflags=_creationflags(),
    )
    run_dir = manager.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    orphan_start = process_start_time(orphan.pid)
    bystander_start = process_start_time(bystander.pid)
    assert orphan_start is not None and bystander_start is not None
    (run_dir / f"{orphan.pid}.json").write_text(
        json.dumps(
            {
                "name": "my-extension",
                "pgid": orphan.pid,
                "start_time": orphan_start,
                "server_pid": 1,
                "server_start_time": 1,
            }
        )
    )
    (run_dir / f"{bystander.pid}.json").write_text(
        json.dumps(
            {
                "name": "my-extension",
                "pgid": bystander.pid,
                "start_time": bystander_start - 1,
                "server_pid": 1,
                "server_start_time": 1,
            }
        )
    )
    try:
        await manager.reap_orphans()
        await _wait_dead(orphan.pid)
        assert pid_alive(bystander.pid)
    finally:
        if pid_alive(bystander.pid):
            bystander.kill()
        await bystander.wait()
        if pid_alive(orphan.pid):
            orphan.kill()
            await orphan.wait()
        await manager.shutdown()
