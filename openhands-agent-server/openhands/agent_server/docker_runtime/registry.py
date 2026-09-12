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
import hashlib
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen
from uuid import UUID, uuid4

from openhands.agent_server.config import V1_SESSION_API_KEY_ENV, Config
from openhands.agent_server.docker_runtime.broker import RuntimeCredentialBroker
from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore
from openhands.agent_server.persistence import FileSecretsStore
from openhands.agent_server.persistence.store import _get_persistence_dir
from openhands.sdk.llm.auth.credentials import CredentialStore
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.command import execute_command, sanitized_env


logger = get_logger(__name__)


# Canonical path inside every sub-container. Doesn't have to match the
# host-side path — the agent-server inside the container is reconfigured
# via ``OH_CONVERSATIONS_PATH`` / ``OH_PERSISTENCE_DIR`` to use these.
_CONTAINER_CONV_DIR = "/var/openhands/conversations"
_CONTAINER_PERSIST_DIR = "/var/openhands/.openhands"
_CONTAINER_WORKSPACE_DIR = "/workspace"
_RUNTIME_OWNER_LABEL = "ai.openhands.runtime-owner"
_CONVERSATION_ID_LABEL = "ai.openhands.conversation-id"


def _execution_scope(config: Config) -> str:
    conversations_path = config.conversations_path.resolve()
    persistence_path = _get_persistence_dir(config).resolve()
    identity = f"{conversations_path}\0{persistence_path}"
    return hashlib.sha256(identity.encode()).hexdigest()[:24]


@dataclass(slots=True)
class RunningConversationContainer:
    """Container connection details used by the docker-runtime proxy."""

    host: str
    api_key: str | None
    container_id: str | None
    image: str
    scoped_runtime_verified: bool = False

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

    Connections are in-memory. After an outer-server restart, routes lazily
    recreate containers for persisted conversations using the same state and
    workspace mounts. The inner server restores its conversation on access.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._execution_scope = _execution_scope(config)
        self._containers: dict[UUID, RunningConversationContainer] = {}
        self._starts: dict[UUID, asyncio.Task[RunningConversationContainer]] = {}
        self._lock = asyncio.Lock()
        self._provisioning: RuntimeProvisioningStore | None = None
        self._brokers: dict[UUID, RuntimeCredentialBroker] = {}
        self._mutations: dict[UUID, asyncio.Lock] = {}

    def mutation_lock(self, conversation_id: UUID) -> asyncio.Lock:
        return self._mutations.setdefault(conversation_id, asyncio.Lock())

    @property
    def provisioning(self) -> RuntimeProvisioningStore:
        if self._provisioning is None:
            self._provisioning = RuntimeProvisioningStore(self._config)
        return self._provisioning

    async def prepare(self, conversation_id: UUID) -> None:
        self.provisioning.create(conversation_id)
        async with self._lock:
            if conversation_id in self._brokers:
                return
            root = _get_persistence_dir(self._config)
            broker = RuntimeCredentialBroker(
                self.provisioning,
                conversation_id,
                CredentialStore(root / "auth"),
                FileSecretsStore(root, cipher=self._config.cipher),
            )
            await broker.start()
            self._brokers[conversation_id] = broker

    @property
    def config(self) -> Config:
        return self._config

    @property
    def execution_scope(self) -> str:
        return self._execution_scope

    def cleanup_stale_containers(self) -> None:
        """Remove containers left by an earlier instance of this runtime."""
        result = execute_command(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label={_RUNTIME_OWNER_LABEL}={self._execution_scope}",
            ]
        )
        if result.returncode != 0:
            logger.warning(
                "Failed to list stale conversation containers: %s", result.stderr
            )
            return
        container_ids = result.stdout.split()
        if not container_ids:
            return
        cleanup = execute_command(["docker", "rm", "-f", *container_ids])
        if cleanup.returncode != 0:
            logger.warning(
                "Failed to remove stale conversation containers: %s", cleanup.stderr
            )

    def get(self, conversation_id: UUID) -> RunningConversationContainer | None:
        return self._containers.get(conversation_id)

    def items(self) -> list[tuple[UUID, RunningConversationContainer]]:
        return list(self._containers.items())

    def conversation_dir(self, conversation_id: UUID) -> Path:
        return RuntimeProvisioningStore._direct_child(
            self._config.conversations_path, conversation_id.hex
        )

    def workspace_dir(self, conversation_id: UUID) -> Path:
        return RuntimeProvisioningStore._direct_child(
            self._config.workspace_path, conversation_id.hex
        )

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
            container = await asyncio.shield(task)
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
        broker = self._brokers.pop(conversation_id, None)
        if broker is not None:
            await broker.close()
        return stopped

    async def shutdown(self) -> None:
        """Stop every tracked container.

        Best-effort: a single broken container must not block the rest from
        being cleaned up. In-flight starts are awaited and then stopped so a
        shutdown racing with ``docker run`` does not leak the new container.
        """
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

        brokers = list(self._brokers.values())
        self._brokers.clear()
        await asyncio.gather(*(broker.close() for broker in brokers))

    # -- internals ---------------------------------------------------------

    def _build_container(self, conversation_id: UUID) -> RunningConversationContainer:
        """Spawn one conversation container and wait for its health check."""
        cfg = self._config
        identity = self.provisioning.load(conversation_id)
        runtime_dir = self.provisioning.runtime_dir(conversation_id)
        runtime_dir.mkdir(mode=0o700, exist_ok=True)
        runtime_dir.chmod(0o700)
        host_persist_dir = RuntimeProvisioningStore._direct_child(
            runtime_dir, "persistence"
        )
        host_persist_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        host_persist_dir.chmod(0o700)

        host_cid_dir = self.conversation_dir(conversation_id)
        host_cid_dir.mkdir(parents=True, exist_ok=True)
        container_cid_dir = f"{_CONTAINER_CONV_DIR}/{conversation_id.hex}"
        host_workspace_dir = self.workspace_dir(conversation_id)
        host_workspace_dir.mkdir(parents=True, exist_ok=True)

        volumes = list(cfg.conversation_container_volumes) + [
            f"{host_cid_dir}:{container_cid_dir}",
            f"{host_persist_dir}:{_CONTAINER_PERSIST_DIR}",
            f"{host_workspace_dir}:{_CONTAINER_WORKSPACE_DIR}",
            f"{self._brokers[conversation_id].socket_dir}:/var/openhands/credential-broker:ro",
        ]
        env = self._container_env()
        env.update(
            {
                "OH_SECRET_KEY": identity.encryption_key.get_secret_value(),
                V1_SESSION_API_KEY_ENV: identity.api_key.get_secret_value(),
                "OH_RUNTIME_CREDENTIAL_SOCKET": (
                    "/var/openhands/credential-broker/credentials.sock"
                ),
                "OH_RUNTIME_CREDENTIAL_TOKEN": identity.broker_token.get_secret_value(),
                "OH_RUNTIME_LAUNCHED_PROFILE": (
                    identity.launched_agent_profile.model_dump_json()
                    if identity.launched_agent_profile
                    else ""
                ),
            }
        )

        logger.info(
            "Spawning conversation container: cid=%s image=%s",
            conversation_id,
            cfg.conversation_image,
        )
        container = self._run_container(
            conversation_id=conversation_id,
            image=cfg.conversation_image,
            platform=cfg.conversation_container_platform,
            volumes=volumes,
            env=env,
            network=cfg.conversation_container_network,
            api_key=identity.api_key.get_secret_value(),
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
        conversation_id: UUID,
        image: str,
        platform: str,
        volumes: list[str],
        env: dict[str, str],
        network: str | None,
        api_key: str | None,
    ) -> RunningConversationContainer:
        docker_ver = execute_command(["docker", "version"]).returncode
        if docker_ver != 0:
            raise RuntimeError(
                "Docker is not available. Please install and start Docker."
            )

        docker_env = sanitized_env()
        flags: list[str] = []
        for key, value in env.items():
            docker_env[key] = value
            flags += ["-e", key]
        for volume in volumes:
            flags += ["-v", volume]
            logger.info("Adding conversation container volume mount: %s", volume)
        if network:
            flags += ["--network", network]
        if self._config.conversation_container_memory:
            flags += ["--memory", self._config.conversation_container_memory]
        if self._config.conversation_container_cpus is not None:
            flags += ["--cpus", str(self._config.conversation_container_cpus)]
        if self._config.conversation_container_pids_limit is not None:
            flags += [
                "--pids-limit",
                str(self._config.conversation_container_pids_limit),
            ]

        run_cmd = [
            "docker",
            "run",
            "-d",
            "--platform",
            platform,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--rm",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--label",
            f"{_RUNTIME_OWNER_LABEL}={self._execution_scope}",
            "--label",
            f"{_CONVERSATION_ID_LABEL}={conversation_id}",
            "--ulimit",
            "nofile=65536:65536",
            "--name",
            f"agent-server-conversation-{uuid4()}",
            "-p",
            "127.0.0.1::8000",
            *flags,
            image,
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ]
        # This trusted launcher must pass the explicitly forwarded server credentials;
        # execute_command strips them to protect agent-controlled subprocesses.
        proc = subprocess.run(
            run_cmd, env=docker_env, capture_output=True, text=True, check=False
        )
        if proc.returncode != 0:
            raise RuntimeError("Failed to run conversation container")

        container_id = proc.stdout.strip()
        logger.info("Started conversation container: %s", container_id)
        container = RunningConversationContainer(
            host="", api_key=api_key, container_id=container_id, image=image
        )
        try:
            binding = execute_command(["docker", "port", container_id, "8000/tcp"])
            address, port = binding.stdout.strip().rsplit(":", 1)
            if binding.returncode != 0 or address != "127.0.0.1":
                raise ValueError("Expected one loopback port binding")
            if not 1 <= int(port) <= 65535:
                raise ValueError("Invalid assigned port")
            container.host = f"http://127.0.0.1:{int(port)}"
            return container
        except Exception:
            container.cleanup()
            raise

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
                    msg = "Conversation container stopped unexpectedly"
                    raise RuntimeError(msg)
            time.sleep(1)
        raise RuntimeError("Conversation container failed to become healthy in time")
