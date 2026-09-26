"""Owned lifecycle for optional Canvas App backend subprocesses."""

import asyncio
import hashlib
import os
import platform
import shutil
import signal
import socket
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, Field

from openhands.agent_server.canvas_extensions.installed import (
    get_installed_canvas_extension,
    get_installed_canvas_extension_manifest,
    get_installed_canvas_extensions_dir,
)
from openhands.agent_server.canvas_extensions.manifest import (
    BackendPlatform,
    CanvasExtensionBackend,
    CanvasExtensionBackendArtifact,
    CanvasExtensionManifest,
)
from openhands.sdk.utils.path import get_user_persistence_dir


BackendState = Literal[
    "missing", "stopped", "starting", "ready", "unhealthy", "unsupported"
]
_SAFE_INHERITED_ENV: Final[frozenset[str]] = frozenset(
    {"LANG", "LC_ALL", "LC_CTYPE", "PATH", "TMPDIR", "TZ"}
)
_MAX_LOG_BYTES: Final[int] = 256 * 1024
_STOP_TIMEOUT_SECONDS: Final[float] = 5


class BackendRevisionRequest(BaseModel):
    """Explicit approval for one exact installed revision."""

    revision: str = Field(min_length=1)


class BackendStatus(BaseModel):
    """Current state of an installed Canvas App backend."""

    name: str
    state: BackendState
    revision: str | None = None
    prepared_revision: str | None = None
    pid: int | None = None
    port: int | None = None
    detail: str | None = None


class BackendLogs(BaseModel):
    """Bounded combined stdout and stderr output."""

    name: str
    logs: str
    truncated: bool


class _PreparedState(BaseModel):
    revision: str
    platform: BackendPlatform
    artifact_sha256: str
    artifact_dir: str


class _Runtime:
    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self.port: int | None = None
        self.revision: str | None = None
        self.state: BackendState = "stopped"
        self.detail: str | None = None
        self.logs: deque[bytes] = deque()
        self.log_bytes = 0
        self.logs_truncated = False
        self.drain_tasks: list[asyncio.Task[None]] = []


class CanvasExtensionBackendManager:
    """Owns one optional backend process per installed Canvas App."""

    def __init__(
        self,
        installed_dir: Path | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self.installed_dir = installed_dir or get_installed_canvas_extensions_dir()
        root = (
            state_dir or get_user_persistence_dir() / "canvas-extensions" / "backends"
        )
        self.state_dir = root
        self.artifacts_dir = root / "artifacts"
        self.data_dir = root / "data"
        self.approvals_dir = root / "approvals"
        self._runtimes: dict[str, _Runtime] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, name: str) -> asyncio.Lock:
        return self._locks.setdefault(name, asyncio.Lock())

    def _runtime(self, name: str) -> _Runtime:
        return self._runtimes.setdefault(name, _Runtime())

    def _manifest(self, name: str) -> CanvasExtensionManifest | None:
        return get_installed_canvas_extension_manifest(name, self.installed_dir)

    def revision(self, name: str) -> str | None:
        info = get_installed_canvas_extension(name, self.installed_dir)
        manifest = self._manifest(name)
        if info is None or manifest is None:
            return None
        if info.resolved_ref:
            return info.resolved_ref
        payload = manifest.model_dump_json(exclude_none=True).encode()
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"

    @staticmethod
    def current_platform() -> BackendPlatform | None:
        if platform.system() != "Linux":
            return None
        machine = platform.machine().lower()
        if machine in {"x86_64", "amd64"}:
            return "linux-amd64"
        if machine in {"aarch64", "arm64"}:
            return "linux-arm64"
        return None

    def _backend_parts(
        self, name: str
    ) -> (
        tuple[
            CanvasExtensionBackend,
            CanvasExtensionBackendArtifact,
            BackendPlatform,
        ]
        | None
    ):
        manifest = self._manifest(name)
        current_platform = self.current_platform()
        if manifest is None or manifest.backend is None or current_platform is None:
            return None
        artifact = manifest.backend.artifacts.get(current_platform)
        if artifact is None:
            return None
        return manifest.backend, artifact, current_platform

    def _approval_path(self, name: str) -> Path:
        return self.approvals_dir / f"{name}.json"

    def _read_prepared(self, name: str) -> _PreparedState | None:
        try:
            return _PreparedState.model_validate_json(
                self._approval_path(name).read_text()
            )
        except (OSError, ValueError):
            return None

    def _write_prepared(self, name: str, state: _PreparedState) -> None:
        self.approvals_dir.mkdir(parents=True, exist_ok=True)
        destination = self._approval_path(name)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(state.model_dump_json())
        os.replace(temporary, destination)

    def invalidate_approval(self, name: str) -> None:
        self._approval_path(name).unlink(missing_ok=True)

    @staticmethod
    def _resolve_contained(root: Path, relative: str) -> Path:
        resolved_root = root.resolve()
        candidate = (resolved_root / relative).resolve()
        if not candidate.is_relative_to(resolved_root) or not candidate.is_file():
            raise ValueError("backend artifact is not a contained regular file")
        return candidate

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _extract_archive(archive: Path, destination: Path) -> None:
        root = destination.resolve()
        with tarfile.open(archive, mode="r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                target = (root / member.name).resolve()
                if (
                    member.name.startswith("/")
                    or not target.is_relative_to(root)
                    or not (member.isfile() or member.isdir())
                ):
                    raise ValueError(
                        "backend artifact contains an unsafe archive entry"
                    )
            tar.extractall(destination, members=members, filter="data")
        for path in destination.rglob("*"):
            mode = path.stat().st_mode
            path.chmod(mode & (0o555 if path.is_file() else 0o755))
        destination.chmod(0o555)

    async def prepare(self, name: str, revision: str) -> BackendStatus:
        async with self._lock(name):
            status = self._status_unlocked(name)
            if status.state in {"missing", "unsupported"}:
                return status
            current_revision = self.revision(name)
            if revision != current_revision:
                raise ValueError("approval revision does not match installed revision")
            prepared = self._read_prepared(name)
            if (
                prepared is not None
                and prepared.revision == revision
                and Path(prepared.artifact_dir).is_dir()
            ):
                return self._status_unlocked(name)

            parts = self._backend_parts(name)
            assert parts is not None
            _, artifact, current_platform = parts
            package_root = self.installed_dir / name
            archive = self._resolve_contained(package_root, artifact.path)
            actual_sha256 = await asyncio.to_thread(self._sha256, archive)
            if actual_sha256 != artifact.sha256:
                raise ValueError("backend artifact checksum does not match manifest")

            artifact_dir = self.artifacts_dir / name / actual_sha256
            if not artifact_dir.exists():
                artifact_dir.parent.mkdir(parents=True, exist_ok=True)
                temporary = Path(
                    tempfile.mkdtemp(
                        prefix=f".{actual_sha256}.", dir=artifact_dir.parent
                    )
                )
                try:
                    await asyncio.to_thread(self._extract_archive, archive, temporary)
                    try:
                        os.replace(temporary, artifact_dir)
                    except OSError:
                        if not artifact_dir.is_dir():
                            raise
                finally:
                    shutil.rmtree(temporary, ignore_errors=True)
            self._write_prepared(
                name,
                _PreparedState(
                    revision=revision,
                    platform=current_platform,
                    artifact_sha256=actual_sha256,
                    artifact_dir=str(artifact_dir),
                ),
            )
            return self._status_unlocked(name)

    def _status_unlocked(self, name: str) -> BackendStatus:
        revision = self.revision(name)
        manifest = self._manifest(name)
        runtime = self._runtimes.get(name)
        prepared = self._read_prepared(name)
        prepared_revision = prepared.revision if prepared else None
        if manifest is None:
            state: BackendState = "missing"
            detail = "Canvas App is not installed or its manifest is invalid"
        elif manifest.backend is None:
            state = "unsupported"
            detail = "Canvas App does not declare a backend"
        elif self._backend_parts(name) is None:
            state = "unsupported"
            detail = "Canvas App backend does not support this platform"
        elif runtime is not None and runtime.state in {
            "starting",
            "ready",
            "unhealthy",
        }:
            state = runtime.state
            detail = runtime.detail
        else:
            state = "stopped"
            detail = None
        return BackendStatus(
            name=name,
            state=state,
            revision=revision,
            prepared_revision=prepared_revision,
            pid=runtime.process.pid if runtime and runtime.process else None,
            port=runtime.port if runtime else None,
            detail=detail,
        )

    async def status(self, name: str) -> BackendStatus:
        async with self._lock(name):
            runtime = self._runtimes.get(name)
            if runtime and runtime.process and runtime.process.returncode is not None:
                returncode = runtime.process.returncode
                await self._finish_runtime(runtime)
                runtime.process = None
                runtime.port = None
                runtime.state = "unhealthy"
                runtime.detail = f"Backend exited with code {returncode}"
            elif runtime and runtime.state == "ready" and runtime.port is not None:
                manifest = self._manifest(name)
                assert manifest is not None and manifest.backend is not None
                probe_url = (
                    f"http://127.0.0.1:{runtime.port}{manifest.backend.health.path}"
                )
                if not await asyncio.to_thread(self._probe, probe_url):
                    runtime.state = "unhealthy"
                    runtime.detail = "Backend health probe failed"
            return self._status_unlocked(name)

    @staticmethod
    def _allocate_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _environment(self, backend: CanvasExtensionBackend) -> dict[str, str]:
        disallowed = set(backend.inherit_environment) - _SAFE_INHERITED_ENV
        if disallowed:
            names = ", ".join(sorted(disallowed))
            raise ValueError(
                f"backend requests disallowed environment variables: {names}"
            )
        return {
            name: os.environ[name]
            for name in backend.inherit_environment
            if name in os.environ
        }

    @staticmethod
    def _expand_argv(
        backend: CanvasExtensionBackend,
        port: int,
        data_dir: Path,
        artifact_dir: Path,
    ) -> list[str]:
        replacements = {
            "{port}": str(port),
            "{data_dir}": str(data_dir),
            "{artifact_dir}": str(artifact_dir),
        }
        argv: list[str] = []
        for argument in backend.argv:
            for placeholder, value in replacements.items():
                argument = argument.replace(placeholder, value)
            argv.append(argument)

        artifact_root = artifact_dir.resolve()
        executable = Path(argv[0]).resolve()
        if (
            not executable.is_relative_to(artifact_root)
            or not executable.is_file()
            or not os.access(executable, os.X_OK)
        ):
            raise ValueError(
                "backend executable must be an executable regular file inside "
                "the prepared artifact"
            )
        argv[0] = str(executable)
        return argv

    async def _drain(
        self, runtime: _Runtime, stream: asyncio.StreamReader | None, label: bytes
    ) -> None:
        if stream is None:
            return
        while chunk := await stream.read(8192):
            entry = label + chunk
            runtime.logs.append(entry)
            runtime.log_bytes += len(entry)
            while runtime.log_bytes > _MAX_LOG_BYTES and runtime.logs:
                runtime.log_bytes -= len(runtime.logs.popleft())
                runtime.logs_truncated = True

    @staticmethod
    def _probe(url: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return 200 <= response.status < 400
        except (OSError, urllib.error.URLError):
            return False

    async def start(self, name: str, revision: str) -> BackendStatus:
        async with self._lock(name):
            current = self._status_unlocked(name)
            if current.state in {"missing", "unsupported"}:
                return current
            runtime = self._runtime(name)
            if runtime.process and runtime.process.returncode is None:
                if runtime.revision != revision:
                    raise RuntimeError(
                        "a different backend revision is already running"
                    )
                return current
            if revision != self.revision(name):
                raise ValueError("approval revision does not match installed revision")
            prepared = self._read_prepared(name)
            if prepared is None or prepared.revision != revision:
                raise RuntimeError("backend revision must be prepared before start")
            artifact_dir = Path(prepared.artifact_dir)
            if not artifact_dir.is_dir():
                raise RuntimeError("prepared backend artifact is missing")

            parts = self._backend_parts(name)
            assert parts is not None
            backend, artifact, current_platform = parts
            expected_artifact_dir = (
                self.artifacts_dir / name / prepared.artifact_sha256
            ).resolve()
            if (
                prepared.platform != current_platform
                or prepared.artifact_sha256 != artifact.sha256
                or artifact_dir.resolve() != expected_artifact_dir
            ):
                raise RuntimeError("prepared backend metadata does not match manifest")
            package_root = (self.installed_dir / name).resolve()
            data_dir = self.data_dir / name
            data_dir.mkdir(parents=True, exist_ok=True)
            port = self._allocate_port()
            runtime.state = "starting"
            runtime.detail = None
            runtime.port = port
            runtime.revision = revision
            runtime.logs.clear()
            runtime.log_bytes = 0
            runtime.logs_truncated = False
            try:
                runtime.process = await asyncio.create_subprocess_exec(
                    *self._expand_argv(backend, port, data_dir, artifact_dir),
                    cwd=package_root,
                    env=self._environment(backend),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
                runtime.drain_tasks = [
                    asyncio.create_task(
                        self._drain(runtime, runtime.process.stdout, b"[stdout] ")
                    ),
                    asyncio.create_task(
                        self._drain(runtime, runtime.process.stderr, b"[stderr] ")
                    ),
                ]
                deadline = time.monotonic() + backend.health.timeout_seconds
                probe_url = f"http://127.0.0.1:{port}{backend.health.path}"
                while time.monotonic() < deadline:
                    if runtime.process.returncode is not None:
                        raise RuntimeError(
                            f"backend exited with code {runtime.process.returncode}"
                        )
                    if await asyncio.to_thread(self._probe, probe_url):
                        runtime.state = "ready"
                        return self._status_unlocked(name)
                    await asyncio.sleep(backend.health.interval_seconds)
                raise TimeoutError("backend health check timed out")
            except asyncio.CancelledError:
                runtime.state = "stopped"
                runtime.detail = None
                await asyncio.shield(self._terminate_runtime(runtime))
                raise
            except Exception as exc:
                runtime.state = "unhealthy"
                runtime.detail = str(exc)
                await self._terminate_runtime(runtime)
                return self._status_unlocked(name)

    async def _finish_runtime(self, runtime: _Runtime) -> None:
        if runtime.process is not None:
            await runtime.process.wait()
        if runtime.drain_tasks:
            await asyncio.gather(*runtime.drain_tasks, return_exceptions=True)
        runtime.drain_tasks = []

    async def _terminate_runtime(self, runtime: _Runtime) -> None:
        process = runtime.process
        if process is None:
            return
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), _STOP_TIMEOUT_SECONDS)
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        await self._finish_runtime(runtime)
        runtime.process = None
        runtime.port = None

    def has_running_backends(self) -> bool:
        return any(
            runtime.process is not None and runtime.process.returncode is None
            for runtime in self._runtimes.values()
        )

    async def stop(self, name: str) -> BackendStatus:
        async with self._lock(name):
            runtime = self._runtimes.get(name)
            if runtime is not None:
                await self._terminate_runtime(runtime)
                runtime.state = "stopped"
                runtime.detail = None
            return self._status_unlocked(name)

    async def logs(self, name: str, limit_bytes: int) -> BackendLogs:
        async with self._lock(name):
            runtime = self._runtimes.get(name)
            if runtime is None:
                return BackendLogs(name=name, logs="", truncated=False)
            content = b"".join(runtime.logs)
            truncated = runtime.logs_truncated or len(content) > limit_bytes
            return BackendLogs(
                name=name,
                logs=content[-limit_bytes:].decode(errors="replace"),
                truncated=truncated,
            )

    async def delete_data(self, name: str) -> BackendStatus:
        async with self._lock(name):
            runtime = self._runtimes.get(name)
            if runtime and runtime.process and runtime.process.returncode is None:
                raise RuntimeError("stop the backend before deleting its data")
            shutil.rmtree(self.data_dir / name, ignore_errors=True)
            return self._status_unlocked(name)

    def ready_endpoint(self, name: str) -> tuple[str, int] | None:
        """Internal registry boundary for the future authenticated router."""
        runtime = self._runtimes.get(name)
        if runtime is None or runtime.state != "ready" or runtime.port is None:
            return None
        return "127.0.0.1", runtime.port

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(self.stop(name) for name in list(self._runtimes)),
            return_exceptions=True,
        )
