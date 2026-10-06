from contextlib import AbstractAsyncContextManager, suppress
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
            try:
                last_active = runtime_dir.stat().st_mtime
            except OSError:
                # Deleted since the check above; nothing left to reclaim.
                return None
        try:
            home = self.provisioning.direct_child(runtime_dir, "persistence")
        except ValueError:
            home = None
        workspace = self._owned_workspace(conversation_id, runtime_dir)
        return StoredRuntime(
            conversation_id,
            last_active,
            home=home,
            workspaces=(workspace,) if workspace else (),
        )

    def _owned_workspace(self, conversation_id: UUID, runtime_dir: Path) -> Path | None:
        """The runtime's workspace, if the server created it inside runtime-data."""
        try:
            identity = self.provisioning.load_optional(conversation_id)
        except Exception:
            return None
        if identity is None or identity.workspace_path.is_symlink():
            return None
        workspace = identity.workspace_path.resolve()
        # A caller-supplied workspace is someone's checkout, not ours to prune.
        return workspace if workspace.is_relative_to(runtime_dir.resolve()) else None

    def runtimes(self) -> list[StoredRuntime]:
        runtimes = [
            runtime
            for runtime_dir in self.root.iterdir()
            if (conversation_id := _parse_id(runtime_dir.name))
            and (runtime := self.runtime(conversation_id))
        ]
        return sorted(runtimes, key=lambda runtime: runtime.last_active)

    def idle(self, conversation_id: UUID) -> AbstractAsyncContextManager[bool]:
        return self.registry.runtime_idle(conversation_id)


def _parse_id(name: str) -> UUID | None:
    with suppress(ValueError):
        return UUID(hex=name)
    return None
