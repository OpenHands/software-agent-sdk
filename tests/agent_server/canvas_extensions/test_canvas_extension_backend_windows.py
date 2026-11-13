"""Real native Windows lifecycle tests; no WSL or mocked process APIs."""

import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

import psutil
import pytest

from openhands.agent_server.canvas_extensions.backend import (
    CanvasExtensionBackendManager,
)
from openhands.agent_server.canvas_extensions.installed import install_canvas_extension
from openhands.agent_server.canvas_extensions.manifest import MANIFEST_FILENAME

from .conftest import write_extension


pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="native Windows lifecycle"
)

_SERVER = """
import http.server, json, os, socket, subprocess, sys
socket.getfqdn = lambda name='': name
port, data = int(sys.argv[1]), sys.argv[2]
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])
with open(os.path.join(data, 'runtime.json'), 'w') as f:
    json.dump({'pid': os.getpid(), 'child': child.pid, 'env': dict(os.environ)}, f)
print('backend-ready', flush=True)
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        if self.path == '/exit':
            self.wfile.flush()
            os._exit(0)
    def log_message(self, *args): pass
http.server.HTTPServer(('127.0.0.1', port), Handler).serve_forever()
"""


@pytest.fixture(scope="session")
def python_archive(tmp_path_factory):
    """Package the runner's locked interpreter as a self-contained native artifact."""
    root = tmp_path_factory.mktemp("windows-runtime")
    base = Path(sys.base_prefix)
    version = f"python{sys.version_info.major}{sys.version_info.minor}"
    library = root / f"{version}.zip"
    with zipfile.ZipFile(library, "w", zipfile.ZIP_DEFLATED) as archive:
        for source in (base / "Lib").rglob("*.py"):
            relative = source.relative_to(base / "Lib")
            if not {"site-packages", "__pycache__"}.intersection(relative.parts):
                archive.write(source, relative.as_posix())
    target = root / "runtime.tar.gz"
    with tarfile.open(target, "w:gz") as archive:
        archive.add(base / "python.exe", arcname="python.exe")
        for source in base.glob("*.dll"):
            archive.add(source, arcname=source.name)
        archive.add(base / "DLLs", arcname="DLLs", recursive=True)
        archive.add(library, arcname=library.name)
        payload = f"{version}.zip\n.\nDLLs\n".encode()
        info = tarfile.TarInfo(f"{version}._pth")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return target


@pytest.fixture
def installed_backend(tmp_path, python_archive):
    source = write_extension(tmp_path / "source" / "my-extension")
    archive = source / "backend.tar.gz"
    with (
        tarfile.open(python_archive, "r:gz") as original,
        tarfile.open(archive, "w:gz") as target,
    ):
        for member in original:
            target.addfile(
                member, original.extractfile(member) if member.isfile() else None
            )
        payload = _SERVER.encode()
        member = tarfile.TarInfo("server.py")
        member.size = len(payload)
        target.addfile(member, io.BytesIO(payload))
    path = source / MANIFEST_FILENAME
    manifest = json.loads(path.read_text())
    manifest["backend"] = {
        "schema_version": 1,
        # The schema-1 canonical map remains Linux-only for wire compatibility.
        # This unused URL must never be selected on Windows.
        "artifacts": {
            "linux-amd64": {
                "url": "https://example.invalid/linux.tar.gz",
                "sha256": "0" * 64,
            }
        },
        "additional_artifacts": {
            "windows-amd64": {
                "path": archive.name,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            }
        },
        "argv": [
            "{artifact_dir}/python.exe",
            "{artifact_dir}/server.py",
            "{port}",
            "{data_dir}",
        ],
        "health": {"path": "/health", "timeout_seconds": 10, "interval_seconds": 0.05},
    }
    path.write_text(json.dumps(manifest))
    installed = tmp_path / "installed"
    install_canvas_extension(str(source), installed_dir=installed)
    return installed, tmp_path / "state"


async def _dead(*pids):
    deadline = time.monotonic() + 10
    while any(psutil.pid_exists(pid) for pid in pids):
        assert time.monotonic() < deadline, f"processes survived: {pids}"
        await asyncio.sleep(0.05)


async def test_windows_backend_lifecycle(installed_backend, monkeypatch):
    manager = CanvasExtensionBackendManager(*installed_backend)
    revision = manager.revision("my-extension")
    assert revision is not None
    monkeypatch.setenv("OH_UNGRANTED_SECRET", "do-not-inherit")
    try:
        prepared = await manager.prepare("my-extension", revision)
        assert prepared.prepared_revision == revision
        started = await manager.start("my-extension", revision)
        assert started.state == "ready", started.detail
        runtime_file = manager.data_dir / "my-extension" / "runtime.json"
        runtime = json.loads(runtime_file.read_text())
        assert "OH_UNGRANTED_SECRET" not in runtime["env"]
        assert Path(runtime["env"]["USERPROFILE"]).is_relative_to(manager.data_dir)
        assert runtime["env"]["SYSTEMROOT"] == os.environ["SYSTEMROOT"]
        assert "backend-ready" in (await manager.logs("my-extension", 4096)).logs
        assert (await manager.status("my-extension")).state == "ready"
        assert (await manager.stop("my-extension")).state == "stopped"
        await _dead(runtime["pid"], runtime["child"])
        assert runtime_file.exists()
        restarted = await manager.start("my-extension", revision)
        assert restarted.state == "ready", restarted.detail
        runtime = json.loads(runtime_file.read_text())
        await manager.shutdown()
        await _dead(runtime["pid"], runtime["child"])
    finally:
        await manager.shutdown()


async def test_windows_server_crash_kills_descendants(installed_backend):
    installed, state = installed_backend
    script = """
import asyncio, sys
from pathlib import Path
from openhands.agent_server.canvas_extensions.backend import (
    CanvasExtensionBackendManager,
)
async def main():
    manager = CanvasExtensionBackendManager(Path(sys.argv[1]), Path(sys.argv[2]))
    revision = manager.revision('my-extension')
    await manager.prepare('my-extension', revision)
    result = await manager.start('my-extension', revision)
    print(result.model_dump_json(), flush=True)
    await asyncio.sleep(300)
asyncio.run(main())
"""
    server = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script, str(installed), str(state), stdout=subprocess.PIPE
    )
    try:
        assert server.stdout is not None
        result = json.loads(await asyncio.wait_for(server.stdout.readline(), 90))
        assert result["state"] == "ready", result
        runtime = json.loads(
            (state / "data" / "my-extension" / "runtime.json").read_text()
        )
        server.kill()
        await server.wait()
        # Cleanup must happen without starting another server or calling reaping.
        await _dead(runtime["pid"], runtime["child"])
        manager = CanvasExtensionBackendManager(installed, state)
        await manager.reap_orphans()
        assert not list(manager.run_dir.glob("*.json"))
    finally:
        if server.returncode is None:
            server.kill()
        await server.wait()
