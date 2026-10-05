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
from pathlib import Path, PurePosixPath
from typing import Final, Literal
from urllib.parse import urljoin, urlsplit

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
_ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS: Final[float] = 120
_MAX_ARTIFACT_BYTES: Final[int] = 1024 * 1024 * 1024
_MAX_ARTIFACT_REDIRECTS: Final[int] = 5


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _req, _fp, _code, _msg, _headers, _newurl):
        return None


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
        # The health wait runs in its own task so the name lock stays free for
        # status()/stop()/logs() while a backend is coming up.
        self.start_task: asyncio.Task[None] | None = None
        self.stop_requested = asyncio.Event()


class CanvasExtensionBackendManager:
    """Owns one optional backend process per installed Canvas App."""

    def __init__(
        self,
        installed_dir: Path | None = None,
        state_dir: Path | None = None,
        *,
        allow_insecure_artifact_urls: bool = False,
        max_artifact_bytes: int = _MAX_ARTIFACT_BYTES,
    ) -> None:
        self.installed_dir = installed_dir or get_installed_canvas_extensions_dir()
        root = (
            state_dir or get_user_persistence_dir() / "canvas-extensions" / "backends"
        )
        self.state_dir = root
        self.artifacts_dir = root / "artifacts"
        self.downloads_dir = root / "downloads"
        self.data_dir = root / "data"
        self.approvals_dir = root / "approvals"
        self.allow_insecure_artifact_urls = allow_insecure_artifact_urls
        self.max_artifact_bytes = max_artifact_bytes
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
    def _validate_download_url(url: str, allow_http: bool) -> None:
        parsed = urlsplit(url)
        schemes = {"https", "http"} if allow_http else {"https"}
        if (
            parsed.scheme not in schemes
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("backend artifact URL is not allowed")

    def _download_archive(self, url: str, expected_sha256: str) -> Path:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        destination = self.downloads_dir / f"{expected_sha256}.tar.gz"
        if destination.is_file() and self._sha256(destination) == expected_sha256:
            return destination
        destination.unlink(missing_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        opener = urllib.request.build_opener(_NoRedirect)
        current_url = url
        try:
            for redirect_count in range(_MAX_ARTIFACT_REDIRECTS + 1):
                self._validate_download_url(
                    current_url, self.allow_insecure_artifact_urls
                )
                request = urllib.request.Request(
                    current_url,
                    headers={
                        "Accept": "application/octet-stream",
                        "Accept-Encoding": "identity",
                        "User-Agent": "OpenHands-Agent-Server",
                    },
                )
                try:
                    response = opener.open(
                        request, timeout=_ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS
                    )
                except urllib.error.HTTPError as exc:
                    if exc.code not in {301, 302, 303, 307, 308}:
                        raise ValueError(
                            f"backend artifact download failed with HTTP {exc.code}"
                        ) from exc
                    location = exc.headers.get("Location")
                    if not location or redirect_count == _MAX_ARTIFACT_REDIRECTS:
                        raise ValueError(
                            "backend artifact redirect limit exceeded"
                        ) from exc
                    current_url = urljoin(current_url, location)
                    continue
                with response:
                    length = response.headers.get("Content-Length")
                    if length is not None and int(length) > self.max_artifact_bytes:
                        raise ValueError("backend artifact exceeds maximum size")
                    digest = hashlib.sha256()
                    received = 0
                    with temporary.open("wb") as stream:
                        while chunk := response.read(1024 * 1024):
                            received += len(chunk)
                            if received > self.max_artifact_bytes:
                                raise ValueError(
                                    "backend artifact exceeds maximum size"
                                )
                            digest.update(chunk)
                            stream.write(chunk)
                    if digest.hexdigest() != expected_sha256:
                        raise ValueError(
                            "backend artifact checksum does not match manifest"
                        )
                    os.replace(temporary, destination)
                    return destination
            raise ValueError("backend artifact redirect limit exceeded")
        except (OSError, urllib.error.URLError) as exc:
            raise ValueError("backend artifact download failed") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _extract_archive(
        archive: Path, destination: Path, strip_components: int = 0
    ) -> None:
        root = destination.resolve()
        seen: set[PurePosixPath] = set()
        extracted = 0
        with tarfile.open(archive, mode="r:gz") as tar:
            for member in tar:
                original = PurePosixPath(member.name)
                if original.is_absolute() or ".." in original.parts:
                    raise ValueError(
                        "backend artifact contains an unsafe archive entry"
                    )
                parts = original.parts[strip_components:]
                if not parts:
                    continue
                relative = PurePosixPath(*parts)
                if relative in seen or not (member.isfile() or member.isdir()):
                    raise ValueError(
                        "backend artifact contains an unsafe archive entry"
                    )
                seen.add(relative)
                target = (root / Path(*relative.parts)).resolve()
                if not target.is_relative_to(root):
                    raise ValueError(
                        "backend artifact contains an unsafe archive entry"
                    )
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    source = tar.extractfile(member)
                    if source is None:
                        raise ValueError(
                            "backend artifact contains an unsafe archive entry"
                        )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(member.mode & 0o555)
                extracted += 1
        if not extracted:
            raise ValueError("backend artifact contains no files after stripping")
        for path in sorted(destination.rglob("*"), reverse=True):
            if path.is_dir():
                path.chmod(0o555)
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
            if artifact.path is not None:
                archive = self._resolve_contained(package_root, artifact.path)
                actual_sha256 = await asyncio.to_thread(self._sha256, archive)
                if actual_sha256 != artifact.sha256:
                    raise ValueError(
                        "backend artifact checksum does not match manifest"
                    )
            else:
                assert artifact.url is not None
                archive = await asyncio.to_thread(
                    self._download_archive, artifact.url, artifact.sha256
                )
                actual_sha256 = artifact.sha256

            artifact_dir = self.artifacts_dir / name / actual_sha256
            if not artifact_dir.exists():
                artifact_dir.parent.mkdir(parents=True, exist_ok=True)
                temporary = Path(
                    tempfile.mkdtemp(
                        prefix=f".{actual_sha256}.", dir=artifact_dir.parent
                    )
                )
                try:
                    await asyncio.to_thread(
                        self._extract_archive,
                        archive,
                        temporary,
                        artifact.strip_components,
                    )
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
                if manifest is None or manifest.backend is None:
                    # The on-disk manifest no longer validates (or stopped
                    # declaring a backend); report that instead of tracing back
                    # an AssertionError once `_probe` dereferences it.
                    return self._status_unlocked(name)
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

    def _environment(
        self, backend: CanvasExtensionBackend, data_dir: Path
    ) -> dict[str, str]:
        disallowed = set(backend.inherit_environment) - _SAFE_INHERITED_ENV
        if disallowed:
            names = ", ".join(sorted(disallowed))
            raise ValueError(
                f"backend requests disallowed environment variables: {names}"
            )
        home = data_dir / "home"
        home.mkdir(parents=True, exist_ok=True)
        environment = {
            name: os.environ[name]
            for name in backend.inherit_environment
            if name in os.environ
        }
        environment["HOME"] = str(home)
        return environment

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

    async def _await_healthy(
        self,
        runtime: _Runtime,
        backend: CanvasExtensionBackend,
        port: int,
    ) -> None:
        """Poll the backend's health endpoint until it is ready.

        Runs as ``runtime.start_task`` rather than inline so the name lock is
        free: ``status()`` can report ``starting`` and ``stop()`` can abandon a
        backend that never comes up.
        """
        try:
            deadline = time.monotonic() + backend.health.timeout_seconds
            probe_url = f"http://127.0.0.1:{port}{backend.health.path}"
            while time.monotonic() < deadline:
                if runtime.stop_requested.is_set():
                    return
                process = runtime.process
                if process is None or process.returncode is not None:
                    code = process.returncode if process is not None else None
                    raise RuntimeError(f"backend exited with code {code}")
                if await asyncio.to_thread(self._probe, probe_url):
                    runtime.state = "ready"
                    runtime.detail = None
                    return
                await asyncio.sleep(backend.health.interval_seconds)
            raise TimeoutError("backend health check timed out")
        except asyncio.CancelledError:
            runtime.state = "stopped"
            runtime.detail = None
            raise
        except Exception as exc:
            if runtime.stop_requested.is_set():
                # `stop()` owns the final state; don't clobber it with an
                # error raised purely because we tore the process down.
                runtime.state = "stopped"
                runtime.detail = None
            else:
                runtime.state = "unhealthy"
                runtime.detail = str(exc)

    async def start(self, name: str, revision: str) -> BackendStatus:
        async with self._lock(name):
            current = self._status_unlocked(name)
            if current.state in {"missing", "unsupported"}:
                return current
            runtime = self._runtime(name)
            task = runtime.start_task
            if runtime.process and runtime.process.returncode is None:
                if runtime.revision != revision:
                    raise RuntimeError(
                        "a different backend revision is already running"
                    )
                if task is None:
                    return current
            else:
                if revision != self.revision(name):
                    raise ValueError(
                        "approval revision does not match installed revision"
                    )
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
                    raise RuntimeError(
                        "prepared backend metadata does not match manifest"
                    )
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
                runtime.stop_requested = asyncio.Event()
                try:
                    runtime.process = await asyncio.create_subprocess_exec(
                        *self._expand_argv(backend, port, data_dir, artifact_dir),
                        cwd=package_root,
                        env=self._environment(backend, data_dir),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        start_new_session=True,
                    )
                except Exception as exc:
                    runtime.state = "unhealthy"
                    runtime.detail = str(exc)
                    await self._terminate_runtime(runtime)
                    return self._status_unlocked(name)
                runtime.drain_tasks = [
                    asyncio.create_task(
                        self._drain(runtime, runtime.process.stdout, b"[stdout] ")
                    ),
                    asyncio.create_task(
                        self._drain(runtime, runtime.process.stderr, b"[stderr] ")
                    ),
                ]
                task = asyncio.create_task(self._await_healthy(runtime, backend, port))
                runtime.start_task = task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        async with self._lock(name):
            # Clear the handle and reclaim a failed start's process group here,
            # where the runtime is known to still be ours.
            if runtime.start_task is task:
                runtime.start_task = None
            if runtime.state == "unhealthy":
                await self._terminate_runtime(runtime)
            return self._status_unlocked(name)

    async def _finish_runtime(self, runtime: _Runtime) -> None:
        if runtime.process is not None:
            await runtime.process.wait()
        if runtime.drain_tasks:
            await asyncio.gather(*runtime.drain_tasks, return_exceptions=True)
        runtime.drain_tasks = []

    @staticmethod
    def _signal_group(pgid: int, sig: int) -> None:
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass

    @staticmethod
    def _group_alive(pgid: int) -> bool:
        try:
            os.killpg(pgid, 0)
            return True
        except ProcessLookupError:
            return False

    async def _wait_for_group_exit(self, pgid: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            if not self._group_alive(pgid):
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)

    async def _terminate_runtime(self, runtime: _Runtime) -> None:
        process = runtime.process
        if process is None:
            return
        pgid = process.pid
        # Signal the whole group and wait for the *group* to drain, not just the
        # leader: a backend may fork descendants, and one that outlives (or is
        # forked just after) the leader keeps the group alive. Escalating only
        # on the leader's exit code would leak those descendants.
        if process.returncode is None or self._group_alive(pgid):
            self._signal_group(pgid, signal.SIGTERM)
            if not await self._wait_for_group_exit(pgid, _STOP_TIMEOUT_SECONDS):
                self._signal_group(pgid, signal.SIGKILL)
                await self._wait_for_group_exit(pgid, _STOP_TIMEOUT_SECONDS)
        if process.returncode is None:
            await process.wait()
        await self._finish_runtime(runtime)
        runtime.process = None
        runtime.port = None

    def has_running_backends(self) -> bool:
        return any(
            runtime.start_task is not None
            or (runtime.process is not None and runtime.process.returncode is None)
            for runtime in self._runtimes.values()
        )

    async def stop(self, name: str) -> BackendStatus:
        task: asyncio.Task[None] | None = None
        async with self._lock(name):
            runtime = self._runtimes.get(name)
            if runtime is not None:
                # Ask an in-flight health wait to give up before cancelling it,
                # so a backend stuck in `starting` is stoppable instead of
                # holding the lock (and the REST handler) for the full timeout.
                runtime.stop_requested.set()
                task = runtime.start_task
                runtime.start_task = None
                if task is not None:
                    task.cancel()
                await self._terminate_runtime(runtime)
                runtime.state = "stopped"
                runtime.detail = None
            status = self._status_unlocked(name)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        return status

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
            if runtime and (
                runtime.start_task is not None
                or (runtime.process and runtime.process.returncode is None)
            ):
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
