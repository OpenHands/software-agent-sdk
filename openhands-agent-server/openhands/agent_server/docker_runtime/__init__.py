"""Docker-runtime mode for the agent-server.

When ``Config.conversation_runtime == "docker"`` the outer agent-server stops
running conversations in-process and instead spawns one Docker container
per conversation. Each container hosts its own (``local`` mode) agent-server
that actually runs the agent loop, and this outer server:

* reverse-proxies every conversation-scoped HTTP / WebSocket request to the
  matching sub-container, and
* answers list / count / search / get queries directly from the shared
  on-disk persistence directory (no fan-out across containers needed).

The outer and the sub-containers share the same ``conversations_path`` and
``.openhands`` (settings/secrets/workspaces) directories via bind-mounts.
The outer NEVER acquires a conversation lease — sub-containers own the
work, the outer only reads metadata and proxies mutations.

Submodules:

* :mod:`.registry` — per-conversation :class:`DockerWorkspace` registry.
* :mod:`.proxy` — low-level HTTP and WebSocket forwarding helpers.
* :mod:`.routers` — FastAPI routes that intercept conversation-mutation
  paths in docker mode (POST create, per-cid catch-all proxy, WS bridge).
"""

from openhands.agent_server.docker_runtime.registry import (
    DockerConversationRegistry,
    container_conv_dir,
    container_persist_dir,
    host_conv_subdir,
)


__all__ = [
    "DockerConversationRegistry",
    "container_conv_dir",
    "container_persist_dir",
    "host_conv_subdir",
]
