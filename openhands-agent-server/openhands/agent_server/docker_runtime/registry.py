"""Per-conversation Docker container registry.

The outer agent-server and every sub-container share the same on-disk
persistence directories (``conversations_path`` and the sibling
``.openhands`` settings dir) via bind-mounts. Each sub-container only
sees its own conversation subdirectory under the shared
``conversations_path``, so leases never collide. The outer never claims
a lease — it reads metadata off disk and proxies all mutations.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from openhands.agent_server.config import V1_SESSION_API_KEY_ENV, Config
from openhands.agent_server.persistence.store import _get_persistence_dir
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.command import execute_command


logger = get_logger(__name__)


# Canonical path inside every sub-container. Doesn't have to match the
# host-side path — the agent-server inside the container is reconfigured
# via ``OH_CONVERSATIONS_PATH`` / ``OH_PERSISTENCE_DIR`` to use these.
_CONTAINER_CONV_DIR = "/var/openhands/conversations"
_CONTAINER_PERSIST_DIR = "/var/openhands/.openhands"


@dataclass(slots=True)
class RunningConversationContainer:
    """Container connection details used by the docker-runtime proxy."""

    host: str
    api_key: str | None
    container_id: str | None
    image: str
    last_activity: float = field(default_factory=time.monotonic)
    active_connections: int = 0

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def cleanup(self) -> None:
        if self.container_id is None:
            return
        container_id = self.container_id
        self.container_id = None
        logger.info("Stopping conversation container: %s", container_id)
        result = execute_command(["docker", "stop", container_id])
        if result.returncode != 0:
            logger.warning(
                "Failed to stop conversation container %s: %s",
                container_id,
                result.stderr,
            )


class DockerConversationRegistry:
    """Hand out one Docker container per conversation id.

    The registry is in-memory: restarting the outer agent-server forgets
    every running container. That's deliberate for this first docker-runtime
    mode; the persisted conversation state remains on disk, while container
    re-attachment/reclaiming can be added later.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._containers: dict[UUID, RunningConversationContainer] = {}
        self._starts: dict[UUID, asyncio.Task[RunningConversationContainer]] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._config.conversation_container_idle_timeout is not None:
            self._cleanup_task = asyncio.create_task(self._cleanup_idle_loop())

    @property
    def config(self) -> Config:
        return self._config

    def get(self, conversation_id: UUID) -> RunningConversationContainer | None:
        return self._containers.get(conversation_id)

    def items(self) -> list[tuple[UUID, RunningConversationContainer]]:
        return list(self._containers.items())

    async def acquire(self, conversation_id: UUID) -> RunningConversationContainer:
        async with self._lock:
            container = self._containers.get(conversation_id)
            if container is None:
                raise KeyError(conversation_id)
            container.active_connections += 1
            container.touch()
            return container

    async def release(self, conversation_id: UUID) -> None:
        async with self._lock:
            container = self._containers.get(conversation_id)
            if container is None:
                return
            container.active_connections = max(0, container.active_connections - 1)
            container.touch()

    async def get_or_create(
        self, conversation_id: UUID
    ) -> tuple[RunningConversationContainer, bool]:
        """Idempotently spawn the container for ``conversation_id``.

        Starts for different conversations are allowed to proceed concurrently,
        while concurrent starts for the same id share the same task. Returns
        ``(container, is_new)`` so callers can clean up only containers they
        just created when the initial proxied request fails.
        """
        async with self._lock:
            existing = self._containers.get(conversation_id)
            if existing is not None:
                return existing, False

            task = self._starts.get(conversation_id)
            is_new = task is None
            if task is None:
                task = asyncio.create_task(
                    asyncio.to_thread(self._build_container, conversation_id)
                )
                self._starts[conversation_id] = task

        try:
            container = await task
        except Exception:
            async with self._lock:
                if self._starts.get(conversation_id) is task:
                    self._starts.pop(conversation_id, None)
            raise

        async with self._lock:
            existing = self._containers.get(conversation_id)
            if existing is not None:
                return existing, False
            if self._starts.get(conversation_id) is not task:
                should_cleanup = True
            else:
                should_cleanup = False
                self._starts.pop(conversation_id, None)
                container.touch()
                self._containers[conversation_id] = container

        if should_cleanup:
            await asyncio.to_thread(container.cleanup)
            raise RuntimeError(
                f"Conversation container startup was cancelled: {conversation_id}"
            )
        return container, is_new

    async def stop(self, conversation_id: UUID) -> bool:
        async with self._lock:
            container = self._containers.pop(conversation_id, None)
            start_task = self._starts.pop(conversation_id, None)

        stopped = False
        if container is not None:
            await asyncio.to_thread(container.cleanup)
            stopped = True
        if start_task is not None:
            try:
                started = await start_task
            except Exception:
                return stopped
            await asyncio.to_thread(started.cleanup)
            stopped = True
        return stopped

    async def shutdown(self) -> None:
        """Stop every tracked container and the idle cleanup task."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        async with self._lock:
            containers = list(self._containers.values())
            start_tasks = list(self._starts.values())
            self._containers.clear()
            self._starts.clear()

        for task in start_tasks:
            try:
                containers.append(await task)
            except Exception:
                logger.exception(
                    "Conversation container startup failed during shutdown"
                )

        for container in containers:
            try:
                await asyncio.to_thread(container.cleanup)
            except Exception:
                logger.exception(
                    "Failed to stop conversation container during shutdown"
                )

    # -- internals ---------------------------------------------------------

    async def _cleanup_idle_loop(self) -> None:
        timeout = self._config.conversation_container_idle_timeout
        assert timeout is not None
        interval = min(max(timeout / 4, 0.25), 30.0)
        while True:
            await asyncio.sleep(interval)
            await self._cleanup_idle_once(timeout)

    async def _cleanup_idle_once(self, timeout: float) -> None:
        now = time.monotonic()
        async with self._lock:
            candidates = [
                (conversation_id, container)
                for conversation_id, container in self._containers.items()
                if container.active_connections == 0
                and now - container.last_activity >= timeout
            ]

        for conversation_id, container in candidates:
            if await asyncio.to_thread(self._is_running, conversation_id, container):
                container.touch()
                continue
            async with self._lock:
                current = self._containers.get(conversation_id)
                if current is not container:
                    continue
                if container.active_connections != 0:
                    continue
                if time.monotonic() - container.last_activity < timeout:
                    continue
                self._containers.pop(conversation_id, None)
            logger.info(
                "Stopping idle conversation container: cid=%s timeout=%ss",
                conversation_id,
                timeout,
            )
            await asyncio.to_thread(container.cleanup)

    def _is_running(
        self,
        conversation_id: UUID,
        container: RunningConversationContainer,
    ) -> bool:
        headers = {}
        if container.api_key:
            headers["X-Session-API-Key"] = container.api_key
        request = Request(
            f"{container.host}/api/conversations/{conversation_id}",
            headers=headers,
        )
        try:
            with urlopen(request, timeout=2.0) as response:
                payload = json.load(response)
        except Exception:
            logger.debug(
                "Could not inspect conversation %s before idle cleanup",
                conversation_id,
                exc_info=True,
            )
            return True
        return payload.get("execution_status") == "running"

    def _build_container(self, conversation_id: UUID) -> RunningConversationContainer:
        """Spawn one conversation container and wait for its health check."""
        cfg = self._config
        host_conv_dir = cfg.conversations_path.resolve()
        host_persist_dir = _get_persistence_dir(cfg).resolve()

        host_cid_dir = host_conv_dir / conversation_id.hex
        host_cid_dir.mkdir(parents=True, exist_ok=True)
        host_runtime_dir = host_persist_dir / "docker-runtime" / conversation_id.hex
        host_runtime_dir.mkdir(parents=True, exist_ok=True)
        container_cid_dir = f"{_CONTAINER_CONV_DIR}/{conversation_id.hex}"

        volumes = list(cfg.conversation_container_volumes) + [
            f"{host_cid_dir}:{container_cid_dir}",
            f"{host_runtime_dir}:{_CONTAINER_PERSIST_DIR}",
        ]
        env = self._container_env()

        logger.info(
            "Spawning conversation container: cid=%s image=%s",
            conversation_id,
            cfg.conversation_image,
        )
        shared_session_key = os.environ.get(V1_SESSION_API_KEY_ENV)
        container = self._run_container(
            image=cfg.conversation_image,
            platform=cfg.conversation_container_platform,
            volumes=volumes,
            env=env,
            network=cfg.conversation_container_network,
            api_key=shared_session_key,
        )
        try:
            self._wait_for_health(
                container,
                timeout=cfg.conversation_container_startup_timeout,
            )
        except Exception:
            container.cleanup()
            raise
        logger.info(
            "Conversation container ready: cid=%s host=%s",
            conversation_id,
            container.host,
        )
        return container

    def _container_env(self) -> dict[str, str]:
        cfg = self._config
        env = {
            key: os.environ[key]
            for key in cfg.conversation_container_forward_env
            if key in os.environ
        }
        env.update(
            {
                "OH_CONVERSATIONS_PATH": _CONTAINER_CONV_DIR,
                "OH_PERSISTENCE_DIR": _CONTAINER_PERSIST_DIR,
                "OH_CONVERSATION_RUNTIME": "local",
            }
        )
        return env

    def _run_container(
        self,
        *,
        image: str,
        platform: str,
        volumes: list[str],
        env: dict[str, str],
        network: str | None,
        api_key: str | None,
    ) -> RunningConversationContainer:
        port = find_available_tcp_port()
        if port < 0:
            raise RuntimeError("No available TCP port found for conversation container")

        docker_ver = execute_command(["docker", "version"]).returncode
        if docker_ver != 0:
            raise RuntimeError(
                "Docker is not available. Please install and start Docker."
            )

        docker_env = dict(os.environ)
        flags: list[str] = []
        for key, value in env.items():
            docker_env[key] = value
            flags += ["-e", key]
        for volume in volumes:
            flags += ["-v", volume]
            logger.info("Adding conversation container volume mount: %s", volume)
        if network:
            flags += ["--network", network]

        host = f"http://127.0.0.1:{port}"
        run_cmd = [
            "docker",
            "run",
            "-d",
            "--platform",
            platform,
            "--rm",
            "--ulimit",
            "nofile=65536:65536",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--name",
            f"agent-server-conversation-{uuid4()}",
            "-p",
            f"127.0.0.1:{port}:8000",
            *flags,
            image,
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ]
        proc = execute_command(run_cmd, env=docker_env)
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to run docker container: {proc.stderr}")

        container_id = proc.stdout.strip()
        logger.info("Started conversation container: %s", container_id)
        return RunningConversationContainer(
            host=host,
            api_key=api_key,
            container_id=container_id,
            image=image,
        )

    def _wait_for_health(
        self, container: RunningConversationContainer, *, timeout: float
    ) -> None:
        start = time.time()
        health_url = f"{container.host}/health"

        while time.time() - start < timeout:
            try:
                with urlopen(health_url, timeout=1.0) as resp:
                    if 200 <= getattr(resp, "status", 200) < 300:
                        return
            except Exception:
                pass

            if container.container_id is not None:
                ps = execute_command(
                    [
                        "docker",
                        "inspect",
                        "-f",
                        "{{.State.Running}}",
                        container.container_id,
                    ]
                )
                if ps.stdout.strip() != "true":
                    logs = execute_command(["docker", "logs", container.container_id])
                    msg = (
                        "Conversation container stopped unexpectedly. Logs:\n"
                        f"{logs.stdout}\n{logs.stderr}"
                    )
                    raise RuntimeError(msg)
            time.sleep(1)
        raise RuntimeError("Conversation container failed to become healthy in time")


_INTERFACE_HOST = "0.0.0.0"
_MIN_PORT = 30000
_MAX_PORT = 39999
_MAX_PORT_ATTEMPTS = 50


def check_port_available(port: int) -> bool:
    """Check if a port is available for binding."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((_INTERFACE_HOST, port))
        return True
    except OSError:
        time.sleep(0.1)
        return False
    finally:
        sock.close()


def find_available_tcp_port(
    min_port: int = _MIN_PORT,
    max_port: int = _MAX_PORT,
    max_attempts: int = _MAX_PORT_ATTEMPTS,
) -> int:
    """Find an available TCP port in the docker workspace range."""
    ports = list(range(min_port, max_port + 1))
    random.SystemRandom().shuffle(ports)

    for port in ports[:max_attempts]:
        if check_port_available(port):
            return port
    return -1


def container_persist_dir() -> str:
    """Canonical settings/secrets path inside every sub-container."""
    return _CONTAINER_PERSIST_DIR


def container_conv_dir() -> str:
    """Canonical conversations-root path inside every sub-container."""
    return _CONTAINER_CONV_DIR


def host_conv_subdir(config: Config, conversation_id: UUID) -> Path:
    """Return the host-side per-conversation directory under
    ``config.conversations_path``. Used by tests and by the outer's
    read-only metadata loader."""
    return config.conversations_path.resolve() / conversation_id.hex
