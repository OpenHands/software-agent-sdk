from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from openhands.agent_server.storage import StoredRuntime


if TYPE_CHECKING:
    from openhands.agent_server.docker_runtime.registry import (
        DockerConversationRegistry,
    )


# Where builds before the storage module set caches aside.
_LEGACY_PRUNED_PREFIX = ".cache-pruned-"


class DockerStorageAdapter:
    """``runtime-data/<id>/``: the sandbox home and, usually, its workspace."""

    def __init__(self, registry: DockerConversationRegistry) -> None:
        self.registry = registry
        self.provisioning = registry.provisioning

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
        return StoredRuntime(
            conversation_id,
            last_active,
            home=home,
            workspaces=self._owned_workspaces(conversation_id, runtime_dir),
        )

    def _owned_workspaces(
        self, conversation_id: UUID, runtime_dir: Path
    ) -> tuple[Path, ...]:
        try:
            identity = self.provisioning.load_optional(conversation_id)
        except Exception:
            return ()
        if identity is None or identity.workspace_path.is_symlink():
            return ()
        workspace = identity.workspace_path.resolve()
        # A caller-supplied workspace is someone's checkout, not ours to prune.
        if not workspace.is_relative_to(runtime_dir.resolve()):
            return ()
        return (workspace,)

    def runtimes(self) -> list[StoredRuntime]:
        runtimes: list[StoredRuntime] = []
        for runtime_dir in self.root.iterdir():
            try:
                conversation_id = UUID(hex=runtime_dir.name)
            except ValueError:
                continue
            if runtime := self.runtime(conversation_id):
                runtimes.append(runtime)
        return sorted(runtimes, key=lambda runtime: runtime.last_active)

    @asynccontextmanager
    async def idle(self, conversation_id: UUID) -> AsyncIterator[bool]:
        registry = self.registry
        async with registry._lock:
            yield not (
                registry.get(conversation_id)
                or registry.is_starting(conversation_id)
                or conversation_id in registry._deleting
            )

    def retire(self, conversation_id: UUID) -> list[Path]:
        # Marker first: if the move never happens, the next pass retries it.
        self.registry.retired_marker(conversation_id).write_text(
            json.dumps({"retired_at": datetime.now(UTC).isoformat()})
        )
        return [self.provisioning.runtime_dir(conversation_id)]

    async def on_retired(self, conversation_id: UUID) -> None:
        if self.registry._service is not None:
            await self.registry._service.refresh_persisted_conversation(conversation_id)

    def leftovers(self) -> list[Path]:
        return [
            *self.root.glob(f"{_LEGACY_PRUNED_PREFIX}*"),
            *self.root.glob(f"*/{_LEGACY_PRUNED_PREFIX}*"),
        ]
