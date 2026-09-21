"""Own one hardened agent-server container per conversation."""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import URLError
from urllib.request import urlopen
from uuid import UUID, uuid4

from openhands.agent_server.config import V1_SESSION_API_KEY_ENV, Config
from openhands.agent_server.conversation_registry import ConversationRegistry
from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore
from openhands.agent_server.models import (
    ConversationRuntimeInfo,
    ConversationRuntimeStatus,
)
from openhands.agent_server.persistence.store import _get_persistence_dir
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
        self._lock = asyncio.Lock()

    def configure_service(self, service: ConversationService) -> None:
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

    async def get_or_create(self, conversation_id: UUID) -> ConversationContainer:
        async with self._lock:
            if conversation_id in self._deleting:
                raise RuntimeError("Conversation is being deleted")
            container = self._containers.get(conversation_id)

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
                return existing
            if self._starts.get(conversation_id) is not task:
                await asyncio.to_thread(container.stop)
                raise RuntimeError("Conversation container start was cancelled")
            self._starts.pop(conversation_id, None)
            self._containers[conversation_id] = container
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
        async with self._lock:
            task = self._starts.pop(conversation_id, None)
            container = self._containers.pop(conversation_id, None)
        if task is not None:
            try:
                started = await task
            except Exception:
                started = None
            container = container or started
        if container is not None:
            await asyncio.to_thread(container.stop)

    async def shutdown(self) -> None:
        ids = set(self._containers) | set(self._starts)
        await asyncio.gather(*(self.stop(cid) for cid in ids), return_exceptions=True)

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
