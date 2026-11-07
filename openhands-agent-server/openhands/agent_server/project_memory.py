"""File store for a project's memory.

Project memory is what a later conversation of the project would otherwise
have to ask the person again: their standing preferences, decisions, where
things are. It is a few short Markdown files in
``<persistence_dir>/projects/<project id hex>/memory/``:

- ``MEMORY.md`` is the index: one-line rules and preferences, and a line per
  topic file saying what it holds.
- Topic files (``deploy-notes.md``) hold one subject each.

Every write replaces a whole file and names the revision it was based on, so
two conversations editing the same file cannot silently lose each other's
change: a stale write is refused with the current text to merge. A revision
is a hash of the file's bytes, so it needs no extra state.

All methods are blocking; callers run them in a worker thread.
"""

import hashlib
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field

from openhands.agent_server.project_artifacts import project_files_dir


MEMORY_INDEX = "MEMORY.md"
"""The index file every project's memory starts from."""

MAX_MEMORY_BYTES = 64 * 1024
"""Largest memory file; memory is meant to stay short."""

_TOPIC = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,78}[a-z0-9_-])?\.md")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class MemoryFile(BaseModel):
    name: str
    size: int = Field(description="Size in bytes.")
    revision: str = Field(description="Pass it back to replace or delete the file.")
    updated_at: datetime


class MemoryFileContent(MemoryFile):
    content: str


class MemoryConflict(Exception):
    """The file changed since the revision the write was based on."""

    def __init__(self, current: MemoryFileContent | None):
        super().__init__("Memory file changed since it was read")
        self.current = current


class MemoryTooLarge(ValueError):
    pass


def valid_memory_name(name: str) -> bool:
    """``MEMORY.md``, or a lowercase ``.md`` name of letters, digits and ``._-``."""
    return name == MEMORY_INDEX or bool(_TOPIC.fullmatch(name))


def _revision(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


class ProjectMemoryStore:
    def __init__(self, persistence_dir: Path):
        self.persistence_dir = Path(persistence_dir)

    def _dir(self, project_id: UUID) -> Path:
        return project_files_dir(self.persistence_dir, project_id) / "memory"

    def _path(self, project_id: UUID, name: str) -> Path:
        if not valid_memory_name(name):
            raise ValueError(f"Invalid memory file name: {name!r}")
        return self._dir(project_id) / name

    def _lock(self, project_id: UUID) -> threading.Lock:
        with _locks_guard:
            return _locks.setdefault(str(self._dir(project_id)), threading.Lock())

    def list(self, project_id: UUID) -> list[MemoryFile]:
        """The index first, then the topic files by name."""
        directory = self._dir(project_id)
        if not directory.is_dir():
            return []
        files = [
            entry
            for entry in (
                self._read(directory / name)
                for name in sorted(os.listdir(directory))
                if valid_memory_name(name)
            )
            if entry is not None
        ]
        files.sort(key=lambda f: (f.name != MEMORY_INDEX, f.name))
        return [MemoryFile(**f.model_dump(exclude={"content"})) for f in files]

    def get(self, project_id: UUID, name: str) -> MemoryFileContent | None:
        return self._read(self._path(project_id, name))

    def write(
        self,
        project_id: UUID,
        name: str,
        content: str,
        base_revision: str | None,
    ) -> MemoryFileContent:
        """Replace ``name`` with ``content``.

        ``base_revision`` is the revision the change was based on, ``None`` for
        a new file. Raises :class:`MemoryConflict` when the file's current
        revision differs, including when it was created or deleted meanwhile.
        """
        path = self._path(project_id, name)
        data = content.encode("utf-8")
        if len(data) > MAX_MEMORY_BYTES:
            raise MemoryTooLarge(
                f"Memory files are at most {MAX_MEMORY_BYTES // 1024} KiB"
            )
        with self._lock(project_id):
            current = self._read(path)
            if (current.revision if current else None) != base_revision:
                raise MemoryConflict(current)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f".{name}.{uuid.uuid4().hex}.tmp")
            try:
                temp.write_bytes(data)
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)
            written = self._read(path)
        assert written is not None
        return written

    def delete(self, project_id: UUID, name: str, base_revision: str | None) -> bool:
        """Delete ``name``; ``False`` when it does not exist.

        With ``base_revision``, raises :class:`MemoryConflict` when the file
        changed since that revision.
        """
        path = self._path(project_id, name)
        with self._lock(project_id):
            current = self._read(path)
            if current is None:
                return False
            if base_revision is not None and current.revision != base_revision:
                raise MemoryConflict(current)
            path.unlink(missing_ok=True)
        return True

    @staticmethod
    def _read(path: Path) -> MemoryFileContent | None:
        try:
            if not path.is_file() or path.is_symlink():
                return None
            data = path.read_bytes()
            updated = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            return None
        return MemoryFileContent(
            name=path.name,
            size=len(data),
            revision=_revision(data),
            updated_at=updated,
            content=data.decode("utf-8", errors="replace"),
        )
