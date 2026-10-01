from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore
from openhands.agent_server.storage import StoredRuntime


if TYPE_CHECKING:
    from openhands.agent_server.docker_runtime.registry import (
        DockerConversationRegistry,
    )


@dataclass(frozen=True, slots=True)
class DockerRuntimeStorage:
    """``runtime-data/<id>/``: the sandbox home and, usually, its workspace."""

    registry: "DockerConversationRegistry"

    @property
    def provisioning(self) -> RuntimeProvisioningStore:
        return self.registry.provisioning

    @property
    def root(self) -> Path:
        return self.provisioning.data_root

    def runtime(self, conversation_id: UUID) -> StoredRuntime | None:
        runtime_dir = self.provisioning.runtime_dir(conversation_id)
        if runtime_dir.is_symlink() or not runtime_dir.is_dir():
            return None
        state = self.registry.conversation_dir(conversation_id) / "base_state.json"
        try:
            last_active = state.stat().st_mtime
        except OSError:
            last_active = runtime_dir.stat().st_mtime
        try:
            home = self.provisioning.direct_child(runtime_dir, "persistence")
        except ValueError:
            home = None
        return StoredRuntime(conversation_id, last_active, home=home)

    def runtimes(self) -> list[StoredRuntime]:
        runtimes = [
            runtime
            for runtime_dir in self.root.iterdir()
            if (conversation_id := _parse_id(runtime_dir.name))
            and (runtime := self.runtime(conversation_id))
        ]
        return sorted(runtimes, key=lambda runtime: runtime.last_active)

    @asynccontextmanager
    async def idle(self, conversation_id: UUID) -> AsyncIterator[bool]:
        """Hold the registry lock; True if no container runs or starts for the
        runtime and it is not being deleted.

        Nothing can start a container until the block exits, so files moved
        inside it are not in use.
        """
        registry = self.registry
        async with registry._lock:
            yield not (
                registry.get(conversation_id)
                or registry.is_starting(conversation_id)
                or conversation_id in registry._deleting
            )


def _parse_id(name: str) -> UUID | None:
    with suppress(ValueError):
        return UUID(hex=name)
    return None
