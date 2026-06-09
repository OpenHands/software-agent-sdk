"""Per-conversation Docker container registry.

A thin async wrapper around :class:`DockerWorkspace`. The workspace owns
the heavy lifting (port allocation, ``docker run``, health checks, ``docker
stop`` on cleanup, optional log streaming, pause/resume); this class just
hands out one workspace per conversation id and serializes concurrent
start/stop calls.

The outer agent-server and every sub-container share the same on-disk
persistence directories (``conversations_path`` and the sibling
``.openhands`` settings dir) via bind-mounts. Each sub-container only
sees its OWN conversation subdirectory under the shared
``conversations_path``, so leases never collide. The outer never claims
a lease — it reads metadata off disk and proxies all mutations.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import cast
from uuid import UUID

from openhands.agent_server.config import V1_SESSION_API_KEY_ENV, Config
from openhands.agent_server.persistence.store import _get_persistence_dir
from openhands.sdk.logger import get_logger
from openhands.sdk.workspace import PlatformType
from openhands.workspace.docker.workspace import DockerWorkspace


logger = get_logger(__name__)


# Canonical path inside every sub-container. Doesn't have to match the
# host-side path — the agent-server inside the container is reconfigured
# via ``OH_CONVERSATIONS_PATH`` / ``OH_PERSISTENCE_DIR`` to use these.
_CONTAINER_CONV_DIR = "/var/openhands/conversations"
_CONTAINER_PERSIST_DIR = "/var/openhands/.openhands"


class DockerConversationRegistry:
    """Hand out one :class:`DockerWorkspace` per conversation id.

    The registry is in-memory: restarting the outer agent-server forgets
    every running container. That's deliberate — sub-containers are
    short-lived agent-server processes whose canonical state lives on the
    shared persistence volume, so any restart can re-claim them by spawning
    fresh containers against the same on-disk state.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._workspaces: dict[UUID, DockerWorkspace] = {}
        self._lock = asyncio.Lock()

    @property
    def config(self) -> Config:
        return self._config

    def get(self, conversation_id: UUID) -> DockerWorkspace | None:
        return self._workspaces.get(conversation_id)

    def items(self) -> list[tuple[UUID, DockerWorkspace]]:
        return list(self._workspaces.items())

    async def get_or_create(
        self, conversation_id: UUID
    ) -> tuple[DockerWorkspace, bool]:
        """Idempotently spawn the container for ``conversation_id``.

        Returns ``(workspace, is_new)``. Callers that need to clean up on a
        failed startup must gate the teardown on ``is_new`` so retried
        requests don't tear down a live conversation.
        """
        async with self._lock:
            existing = self._workspaces.get(conversation_id)
            if existing is not None:
                return existing, False

            ws = await asyncio.to_thread(self._build_workspace, conversation_id)
            self._workspaces[conversation_id] = ws
            return ws, True

    async def stop(self, conversation_id: UUID) -> bool:
        async with self._lock:
            ws = self._workspaces.pop(conversation_id, None)
        if ws is None:
            return False
        await asyncio.to_thread(ws.cleanup)
        return True

    async def shutdown(self) -> None:
        """Stop every tracked container. Best-effort: a single broken
        container must not block the rest from being cleaned up."""
        async with self._lock:
            wss = list(self._workspaces.values())
            self._workspaces.clear()
        for ws in wss:
            try:
                await asyncio.to_thread(ws.cleanup)
            except Exception:
                logger.exception(
                    "Failed to stop conversation container during shutdown"
                )

    # -- internals ---------------------------------------------------------

    def _build_workspace(self, conversation_id: UUID) -> DockerWorkspace:
        """Construct the :class:`DockerWorkspace` for one conversation.

        Blocking: spawns the container and waits for the inner
        agent-server's ``/health`` to come up. Must be called from a worker
        thread.
        """
        cfg = self._config
        host_conv_dir = cfg.conversations_path.resolve()
        host_persist_dir = _get_persistence_dir(cfg).resolve()

        # Per-cid bind-mount: the sub-container can only see ITS OWN
        # conversation subdirectory under the shared dir. The outer server
        # sees every subdirectory and reads metadata off disk for listings.
        host_cid_dir = host_conv_dir / conversation_id.hex
        host_cid_dir.mkdir(parents=True, exist_ok=True)
        container_cid_dir = f"{_CONTAINER_CONV_DIR}/{conversation_id.hex}"

        volumes = list(cfg.conversation_container_volumes) + [
            f"{host_cid_dir}:{container_cid_dir}",
            f"{host_persist_dir}:{_CONTAINER_PERSIST_DIR}",
        ]

        # Point the inner agent-server at the canonical in-container paths.
        # Mirrors how the outer server resolves them via ``Config`` /
        # ``_get_persistence_dir(config)``.
        extra_env = {
            "OH_CONVERSATIONS_PATH": _CONTAINER_CONV_DIR,
            "OH_PERSISTENCE_DIR": _CONTAINER_PERSIST_DIR,
        }

        logger.info(
            "Spawning conversation container: cid=%s image=%s",
            conversation_id,
            cfg.conversation_image,
        )
        # The reverse-proxy needs an ``X-Session-API-Key`` to authenticate
        # with the inner agent-server. Outer and inner share that key by
        # default via ``conversation_container_forward_env`` (see
        # :attr:`Config.conversation_container_forward_env`), so read it
        # straight out of the outer's env. None means "no auth required",
        # which matches the inner's behavior when the env is unset.
        shared_session_key = os.environ.get(V1_SESSION_API_KEY_ENV)

        ws = DockerWorkspace(
            server_image=cfg.conversation_image,
            api_key=shared_session_key,
            # The agent's tool workspace ("where bash/file ops execute")
            # is separate from the conversation persistence directory we
            # bind-mount above. Leave it at the container default.
            working_dir="/workspace",
            # Loopback-only: the outer reaches the inner over 127.0.0.1.
            # Any other binding would let other hosts on the network talk
            # to the inner agent-server, bypassing outer auth.
            bind_host="127.0.0.1",
            # ``Config.conversation_container_platform`` is a plain ``str`` so
            # users can plug in any platform Docker accepts; DockerWorkspace
            # narrows that to a ``Literal``. Trust the user's choice here.
            platform=cast(PlatformType, cfg.conversation_container_platform),
            health_check_timeout=cfg.conversation_container_startup_timeout,
            volumes=volumes,
            network=cfg.conversation_container_network,
            forward_env=list(cfg.conversation_container_forward_env),
            extra_env=extra_env,
            # The outer doesn't manage image lifecycle; whoever produced
            # the image is responsible for its retention.
            cleanup_image=False,
            # Each container hosts only one conversation; no need to expose
            # the auxiliary VSCode/VNC ports outwards. The catch-all proxy
            # forwards conversation-scoped HTTP via ``ws.host`` which is
            # plenty.
            extra_ports=False,
            detach_logs=False,
        )
        logger.info(
            "Conversation container ready: cid=%s host=%s",
            conversation_id,
            ws.host,
        )
        return ws


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
