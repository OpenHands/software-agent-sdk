"""Own one hardened agent-server container per conversation."""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import URLError
from urllib.request import urlopen
from uuid import UUID, uuid4

import httpx

from openhands.agent_server.config import V1_SESSION_API_KEY_ENV, Config
from openhands.agent_server.conversation_registry import ConversationRegistry
from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore
from openhands.agent_server.models import (
    ConversationRuntimeInfo,
    ConversationRuntimeStatus,
)
from openhands.agent_server.persistence.store import _get_persistence_dir
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.command import execute_command, sanitized_env


if TYPE_CHECKING:
    from fastapi import APIRouter

    from openhands.agent_server.conversation_service import ConversationService


logger = get_logger(__name__)

_CONVERSATIONS_DIR = "/var/openhands/conversations"
_PERSISTENCE_DIR = "/var/openhands/.openhands"
_WORKSPACE_DIR = "/workspace"
_OWNER_LABEL = "ai.openhands.runtime-owner"


@dataclass(slots=True)
class ConversationContainer:
    host: str
    api_key: str
    container_id: str

    def stop(self) -> None:
        result = execute_command(["docker", "stop", self.container_id])
        if result.returncode != 0 and "No such container" not in result.stderr:
            raise RuntimeError(
                f"Failed to stop conversation container: {result.stderr}"
            )

    def is_running(self) -> bool:
        result = execute_command(
            ["docker", "inspect", "-f", "{{.State.Running}}", self.container_id]
        )
        return result.returncode == 0 and result.stdout.strip() == "true"


@dataclass(slots=True)
class ContainerLease:
    """An active lease holding a container runtime against suspension."""

    conversation_id: UUID
    container: ConversationContainer
    _release_fn: Callable[[], None]
    _released: bool = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._release_fn()

    async def __aenter__(self) -> ConversationContainer:
        return self.container

    async def __aexit__(
        self, exc_type: object, exc_val: object, exc_tb: object
    ) -> None:
        self.release()


class DockerConversationRegistry(ConversationRegistry):
    def __init__(self, config: Config) -> None:
        super().__init__(config)
        paths = (
            f"{config.conversations_path.resolve()}\0"
            f"{_get_persistence_dir(config).resolve()}"
        )
        self.owner = hashlib.sha256(paths.encode()).hexdigest()[:24]
        self.provisioning = RuntimeProvisioningStore(config)
        self._containers: dict[UUID, ConversationContainer] = {}
        self._starts: dict[UUID, asyncio.Task[ConversationContainer]] = {}
        self._deleting: set[UUID] = set()
        self._stopping: dict[UUID, asyncio.Event] = {}
        self._leases: dict[UUID, int] = {}
        self._lease_generations: dict[UUID, int] = {}
        self._lock = asyncio.Lock()
        self._service: ConversationService | None = None
        self._last_access: dict[UUID, float] = {}
        self._sessions: dict[UUID, int] = {}
        self._eviction_task: asyncio.Task[None] | None = None

    def configure_service(self, service: ConversationService) -> None:
        self._service = service
        service.runtime_cipher_resolver = self.resolve_persisted_cipher

    def resolve_persisted_cipher(self, conversation_id: UUID) -> Cipher:
        """Resolve persisted state without weakening per-runtime isolation.

        Conversations created before Docker mode have no provisioning identity and
        were encrypted with the host key. A present but invalid identity still
        raises rather than falling back to that key.
        """
        identity = self.provisioning.load_optional(conversation_id)
        return identity.cipher if identity is not None else self.provisioning.cipher

    def runtime_info(self, conversation_id: UUID) -> ConversationRuntimeInfo:
        identity = self.provisioning.load_optional(conversation_id)
        if identity is None:
            return ConversationRuntimeInfo(
                runtime_status=ConversationRuntimeStatus.MISSING,
                can_resume=False,
            )
        return ConversationRuntimeInfo(
            runtime_status=(
                ConversationRuntimeStatus.AVAILABLE
                if self.get(conversation_id)
                else ConversationRuntimeStatus.STARTING
                if self.is_starting(conversation_id)
                else ConversationRuntimeStatus.MISSING
            ),
            can_resume=True,
        )

    @property
    def serves_persisted_event_reads(self) -> bool:
        return True

    async def start(self) -> None:
        await asyncio.to_thread(self.cleanup_stale_containers)
        ttl = self.config.conversation_idle_ttl_seconds
        if ttl and ttl > 0:
            self._eviction_task = asyncio.create_task(self._evict_idle_runtimes_loop())

    def add_execution_routes(self, router: APIRouter) -> None:
        from openhands.agent_server.docker_runtime.routers import (
            docker_conversation_router,
        )
        from openhands.agent_server.event_router import event_read_router

        # Persisted event history is safe to read from the outer catalog for
        # both Docker-backed and historical host-local conversations. Writes
        # continue through the runtime proxy below.
        router.include_router(event_read_router)
        router.include_router(docker_conversation_router)

    @property
    def workspace_router(self) -> APIRouter:
        from openhands.agent_server.docker_runtime.routers import (
            docker_workspace_router,
        )

        return docker_workspace_router

    @property
    def conversation_sockets_router(self) -> APIRouter:
        from openhands.agent_server.docker_runtime.routers import docker_sockets_router

        return docker_sockets_router

    @property
    def session_sockets_router(self) -> APIRouter:
        from openhands.agent_server.docker_runtime.routers import (
            docker_session_sockets_router,
        )

        return docker_session_sockets_router

    def conversation_dir(self, conversation_id: UUID) -> Path:
        return self.provisioning.direct_child(
            self.config.conversations_path, conversation_id.hex
        )

    def workspace_dir(self, conversation_id: UUID) -> Path:
        workspace = self.provisioning.load(conversation_id).workspace_path
        if workspace.is_symlink():
            raise ValueError("Conversation workspace must not be a symlink")
        return workspace.resolve()

    def get(self, conversation_id: UUID) -> ConversationContainer | None:
        return self._containers.get(conversation_id)

    def is_starting(self, conversation_id: UUID) -> bool:
        return conversation_id in self._starts

    def attach_session(self, conversation_id: UUID) -> None:
        """Record an outer proxied session attached to this runtime.

        Non-zero counts suppress idle eviction, mirroring the inner
        ``EventService.has_external_subscribers()`` guard: a client holding a
        live events websocket or a long-lived proxied stream keeps the
        container alive even while the conversation itself looks idle.
        """
        self._sessions[conversation_id] = self._sessions.get(conversation_id, 0) + 1

    def detach_session(self, conversation_id: UUID) -> None:
        """Release a session recorded by :meth:`attach_session`."""
        remaining = self._sessions.get(conversation_id, 0) - 1
        if remaining > 0:
            self._sessions[conversation_id] = remaining
        else:
            self._sessions.pop(conversation_id, None)
        self._last_access[conversation_id] = time.monotonic()

    def has_attached_sessions(self, conversation_id: UUID) -> bool:
        """True if an outer proxied session is currently attached."""
        return self._sessions.get(conversation_id, 0) > 0

    def cleanup_stale_containers(self) -> None:
        result = execute_command(
            ["docker", "ps", "-aq", "--filter", f"label={_OWNER_LABEL}={self.owner}"]
        )
        if result.returncode != 0:
            logger.warning("Failed to list stale conversation containers")
            return
        ids = result.stdout.split()
        if ids:
            execute_command(["docker", "rm", "-f", *ids])

    def has_active_leases(self, conversation_id: UUID) -> bool:
        return self._leases.get(conversation_id, 0) > 0

    def release_lease(self, conversation_id: UUID) -> None:
        """Release a previously acquired lease on a container runtime."""
        count = self._leases.get(conversation_id, 0) - 1
        if count <= 0:
            self._leases.pop(conversation_id, None)
        else:
            self._leases[conversation_id] = count

    def acquire_lease(self, conversation_id: UUID) -> Callable[[], None]:
        """Record an active in-flight request or WebSocket for this conversation.

        Returns a release callback.
        """
        self._leases[conversation_id] = self._leases.get(conversation_id, 0) + 1
        self._lease_generations[conversation_id] = (
            self._lease_generations.get(conversation_id, 0) + 1
        )
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                self.release_lease(conversation_id)

        return release

    async def lease(self, conversation_id: UUID) -> ContainerLease:
        """Acquire a container lease for an active request or WebSocket.

        While a lease is held, the conversation runtime will not be suspended.
        """
        container = await self.get_or_create(conversation_id)
        release = self.acquire_lease(conversation_id)
        return ContainerLease(
            conversation_id=conversation_id,
            container=container,
            _release_fn=release,
        )

    async def get_or_create(self, conversation_id: UUID) -> ConversationContainer:
        while True:
            async with self._lock:
                if conversation_id in self._deleting:
                    raise RuntimeError("Conversation is being deleted")
                stop_event = self._stopping.get(conversation_id)
                if stop_event is None:
                    container = self._containers.get(conversation_id)
                    self._lease_generations[conversation_id] = (
                        self._lease_generations.get(conversation_id, 0) + 1
                    )
                    self._last_access[conversation_id] = time.monotonic()
                    break
            await stop_event.wait()

        if container is not None:
            if await asyncio.to_thread(container.is_running):
                return container
            async with self._lock:
                if self._containers.get(conversation_id) is container:
                    self._containers.pop(conversation_id)

        async with self._lock:
            if conversation_id in self._deleting:
                raise RuntimeError("Conversation is being deleted")
            task = self._starts.get(conversation_id)
            if task is None:
                task = asyncio.create_task(
                    asyncio.to_thread(self._build_container, conversation_id)
                )
                self._starts[conversation_id] = task

        try:
            container = await asyncio.shield(task)
        except BaseException:
            async with self._lock:
                if self._starts.get(conversation_id) is task:
                    self._starts.pop(conversation_id, None)
            raise

        async with self._lock:
            existing = self._containers.get(conversation_id)
            if existing is not None:
                if existing is not container:
                    await asyncio.to_thread(container.stop)
                self._last_access[conversation_id] = time.monotonic()
                return existing
            if self._starts.get(conversation_id) is not task:
                await asyncio.to_thread(container.stop)
                raise RuntimeError("Conversation container start was cancelled")
            self._starts.pop(conversation_id, None)
            self._containers[conversation_id] = container
            self._last_access[conversation_id] = time.monotonic()
            return container

    async def begin_delete(self, conversation_id: UUID) -> bool:
        async with self._lock:
            if conversation_id in self._deleting:
                return False
            self._deleting.add(conversation_id)
            return True

    async def finish_delete(self, conversation_id: UUID) -> None:
        async with self._lock:
            self._deleting.discard(conversation_id)

    async def stop(self, conversation_id: UUID) -> None:
        await self._stop(conversation_id)

    async def stop_if_idle(self, conversation_id: UUID, token: int) -> bool:
        return await self._stop(conversation_id, if_idle_token=token)

    async def _stop(
        self, conversation_id: UUID, *, if_idle_token: int | None = None
    ) -> bool:
        task: asyncio.Task[ConversationContainer] | None = None
        container: ConversationContainer | None = None
        async with self._lock:
            if if_idle_token is not None:
                if (
                    conversation_id in self._deleting
                    or conversation_id in self._stopping
                    or self._leases.get(conversation_id, 0) > 0
                    or self.has_attached_sessions(conversation_id)
                    or self._lease_generations.get(conversation_id, 0) != if_idle_token
                    or self._containers.get(conversation_id) is None
                ):
                    return False

            stop_event = self._stopping.get(conversation_id)
            if stop_event is not None:
                wait_for_stop = True
            else:
                stop_event = asyncio.Event()
                self._stopping[conversation_id] = stop_event
                wait_for_stop = False
                task = self._starts.pop(conversation_id, None)
                container = self._containers.pop(conversation_id, None)
                self._last_access.pop(conversation_id, None)
                self._sessions.pop(conversation_id, None)

        if wait_for_stop:
            await stop_event.wait()
            return True

        try:
            if task is not None:
                try:
                    started = await task
                except Exception:
                    started = None
                container = container or started
            if container is not None:
                await asyncio.to_thread(container.stop)
            return True
        finally:
            async with self._lock:
                self._stopping.pop(conversation_id, None)
                stop_event.set()

    async def shutdown(self) -> None:
        if self._eviction_task is not None:
            self._eviction_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._eviction_task
            self._eviction_task = None
        ids = set(self._containers) | set(self._starts)
        await asyncio.gather(*(self.stop(cid) for cid in ids), return_exceptions=True)

    # ------------------------------------------------------------------
    # Idle container suspension and eviction
    # ------------------------------------------------------------------

    async def _evict_idle_runtimes_loop(self) -> None:
        ttl = self.config.conversation_idle_ttl_seconds
        if not ttl:
            return
        interval = max(1.0, min(60.0, ttl / 2))
        while True:
            await asyncio.sleep(interval)
            try:
                await self._evict_idle_runtimes(ttl)
                await self._suspend_idle_containers(ttl)
            except Exception:
                logger.exception("error_evicting_idle_docker_runtimes")

    async def _evict_idle_runtimes(self, ttl_seconds: float) -> None:
        service = self._service
        if service is None:
            return
        cutoff = time.monotonic() - ttl_seconds
        async with self._lock:
            candidates = [
                (conversation_id, container)
                for conversation_id, container in self._containers.items()
                if self._last_access.get(conversation_id, float("inf")) <= cutoff
                and not self.has_attached_sessions(conversation_id)
                and not self.has_active_leases(conversation_id)
            ]

        for conversation_id, container in candidates:
            info = await service.get_conversation(conversation_id)
            if (
                info is None
                or info.execution_status == ConversationExecutionStatus.RUNNING
            ):
                continue
            async with self._lock:
                if self._containers.get(conversation_id) is not container:
                    continue
                if self._last_access.get(conversation_id, float("inf")) > cutoff:
                    continue
                if self.has_attached_sessions(
                    conversation_id
                ) or self.has_active_leases(conversation_id):
                    continue
                self._containers.pop(conversation_id)
                self._last_access.pop(conversation_id, None)
            try:
                await asyncio.to_thread(container.stop)
            except Exception:
                async with self._lock:
                    if conversation_id not in self._containers:
                        self._containers[conversation_id] = container
                        self._last_access[conversation_id] = time.monotonic()
                logger.warning(
                    "Failed to stop idle conversation runtime %s",
                    conversation_id,
                    exc_info=True,
                )
            else:
                logger.info(
                    "Stopped idle conversation runtime %s (idle >= %.0fs)",
                    conversation_id,
                    ttl_seconds,
                )

    async def _suspend_idle_containers(self, ttl: float | None = None) -> None:
        """Check running containers and stop those that are idle and terminal."""
        cutoff = time.monotonic() - ttl if ttl is not None else None
        async with self._lock:
            candidates = list(self._containers.items())

        for conversation_id, container in candidates:
            async with self._lock:
                if (
                    conversation_id in self._deleting
                    or conversation_id in self._stopping
                    or self._containers.get(conversation_id) is not container
                    or self.has_active_leases(conversation_id)
                    or self.has_attached_sessions(conversation_id)
                ):
                    continue
                if (
                    cutoff is not None
                    and self._last_access.get(conversation_id, float("inf")) > cutoff
                ):
                    continue
                token = self._lease_generations.get(conversation_id, 0)

            try:
                suspendable = await self._is_suspendable(conversation_id, container)
            except Exception:
                logger.debug(
                    "Could not check suspend status for %s",
                    conversation_id,
                    exc_info=True,
                )
                continue

            if not suspendable:
                continue

            async with self._lock:
                if (
                    conversation_id in self._deleting
                    or conversation_id in self._stopping
                    or self._containers.get(conversation_id) is not container
                    or self.has_active_leases(conversation_id)
                    or self.has_attached_sessions(conversation_id)
                    or self._lease_generations.get(conversation_id, 0) != token
                ):
                    logger.info(
                        "Conversation %s was re-engaged or active during "
                        "suspend check, skipping suspension",
                        conversation_id,
                    )
                    continue

            try:
                stopped = await self.stop_if_idle(conversation_id, token)

                if stopped:
                    logger.info(
                        "Suspended idle container for conversation %s",
                        conversation_id,
                    )
                else:
                    logger.info(
                        "Conversation %s was re-engaged or active during "
                        "suspend check, skipping suspension",
                        conversation_id,
                    )
            except Exception:
                logger.warning(
                    "Failed to suspend container for %s",
                    conversation_id,
                    exc_info=True,
                )

    async def _is_suspendable(
        self, conversation_id: UUID, container: ConversationContainer
    ) -> bool:
        """Query the inner agent-server to decide if the container is idle.

        Returns ``True`` when the inner conversation is in a terminal
        execution state (finished / error / stuck) **and** reports no
        external WebSocket subscribers or active runs.
        """
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)
            ) as client:
                resp = await client.get(
                    f"{container.host}/api/conversations/{conversation_id}/suspend-check",
                    headers={"X-Session-API-Key": container.api_key},
                )
                if resp.status_code == 404 or resp.is_error:
                    return False
                data = resp.json()
                return bool(data.get("suspendable", False))
        except (httpx.HTTPError, Exception):
            return False

    def _build_container(self, conversation_id: UUID) -> ConversationContainer:
        identity = self.provisioning.load(conversation_id)
        runtime_dir = self.provisioning.runtime_dir(conversation_id)
        persistence_dir = self.provisioning.direct_child(runtime_dir, "persistence")
        conversation_dir = self.conversation_dir(conversation_id)
        workspace_dir = self.workspace_dir(conversation_id)
        for directory in (persistence_dir, conversation_dir, workspace_dir):
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)

        env = sanitized_env()
        env.update(
            {
                "HOME": _PERSISTENCE_DIR,
                "OH_CONVERSATIONS_PATH": _CONVERSATIONS_DIR,
                "OH_PERSISTENCE_DIR": _PERSISTENCE_DIR,
                "OH_CONVERSATION_RUNTIME": "local",
                "OH_SECRET_KEY": identity.encryption_key.get_secret_value(),
                V1_SESSION_API_KEY_ENV: identity.api_key.get_secret_value(),
                "OH_RUNTIME_LAUNCHED_PROFILE": (
                    identity.launched_agent_profile.model_dump_json()
                    if identity.launched_agent_profile
                    else ""
                ),
            }
        )
        if "DEBUG" in os.environ:
            env["DEBUG"] = os.environ["DEBUG"]

        flags: list[str] = []
        for name in (
            "HOME",
            "OH_CONVERSATIONS_PATH",
            "OH_PERSISTENCE_DIR",
            "OH_CONVERSATION_RUNTIME",
            "OH_SECRET_KEY",
            V1_SESSION_API_KEY_ENV,
            "OH_RUNTIME_LAUNCHED_PROFILE",
            "DEBUG",
        ):
            if name in env:
                flags.extend(("-e", name))
        for host, target in (
            (conversation_dir, f"{_CONVERSATIONS_DIR}/{conversation_id.hex}"),
            (persistence_dir, _PERSISTENCE_DIR),
            (workspace_dir, _WORKSPACE_DIR),
        ):
            flags.extend(("-v", f"{host}:{target}"))
        if self.config.conversation_container_memory:
            flags.extend(("--memory", self.config.conversation_container_memory))
        if self.config.conversation_container_cpus is not None:
            flags.extend(("--cpus", str(self.config.conversation_container_cpus)))
        if self.config.conversation_container_pids_limit is not None:
            flags.extend(
                ("--pids-limit", str(self.config.conversation_container_pids_limit))
            )

        command = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--add-host",
            "host.docker.internal:host-gateway",
            "--label",
            f"{_OWNER_LABEL}={self.owner}",
            "--name",
            f"agent-server-conversation-{uuid4()}",
            "-p",
            "127.0.0.1::8000",
            *flags,
            self.config.conversation_image,
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ]
        result = subprocess.run(
            command, env=env, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "docker run failed")

        container_id = result.stdout.strip()
        try:
            binding = execute_command(["docker", "port", container_id, "8000/tcp"])
            address, port = binding.stdout.strip().rsplit(":", 1)
            if binding.returncode != 0 or address != "127.0.0.1":
                raise RuntimeError("Docker did not create a loopback port binding")
            container = ConversationContainer(
                host=f"http://127.0.0.1:{int(port)}",
                api_key=identity.api_key.get_secret_value(),
                container_id=container_id,
            )
            self._wait_until_ready(container)
            return container
        except BaseException:
            execute_command(["docker", "stop", container_id])
            raise

    def _wait_until_ready(self, container: ConversationContainer) -> None:
        deadline = time.monotonic() + self.config.conversation_container_startup_timeout
        while time.monotonic() < deadline:
            try:
                with urlopen(container.host + "/health", timeout=1) as response:
                    if 200 <= response.status < 300:
                        return
            except (URLError, TimeoutError, ConnectionError):
                pass
            running = execute_command(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.State.Running}}",
                    container.container_id,
                ]
            )
            if running.stdout.strip() != "true":
                raise RuntimeError("Conversation container stopped during startup")
            time.sleep(1)
        raise RuntimeError("Conversation container failed to become healthy in time")
