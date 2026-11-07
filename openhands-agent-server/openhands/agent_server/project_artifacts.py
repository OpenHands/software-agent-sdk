"""File store for a project's artifacts.

Artifacts are the files a project produces or is given that do not belong in
its source repositories: reports, images, exports, uploads. Each project keeps
them flat under ``<persistence_dir>/project-artifacts/<project id hex>/``, with
a small JSON record per file in ``.meta/`` saying who added it.

Writes go to a temporary file first and are renamed into place, so a reader
never sees half a file. All methods are blocking; callers run them in a worker
thread.
"""

import json
import os
import re
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from openhands.agent_server.utils import utc_now
from openhands.sdk.utils.datetime import OpenHandsUUID


MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
"""Largest artifact accepted, from an upload or a conversation's workspace."""

ArtifactSource = Literal["user", "agent"]

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()+-]{0,199}$")
_META_DIR = ".meta"
_CHUNK = 1024 * 1024


class ArtifactTooLarge(ValueError):
    pass


class ProjectArtifact(BaseModel):
    name: str
    size: int = Field(description="Size in bytes.")
    source: ArtifactSource = Field(
        description="`user` for an upload, `agent` for a file a conversation saved."
    )
    conversation_id: OpenHandsUUID | None = Field(
        default=None, description="The conversation that saved it, for `agent`."
    )
    created_at: datetime
    updated_at: datetime


def valid_artifact_name(name: str) -> bool:
    """A flat file name: letters, digits, spaces and ``._()+-``, no leading dot."""
    return bool(_NAME.match(name)) and name not in {".", ".."}


class ProjectArtifactStore:
    def __init__(self, persistence_dir: Path):
        self.root = Path(persistence_dir) / "project-artifacts"

    def _dir(self, project_id: UUID) -> Path:
        return self.root / project_id.hex

    def _paths(self, project_id: UUID, name: str) -> tuple[Path, Path]:
        if not valid_artifact_name(name):
            raise ValueError(f"Invalid artifact name: {name!r}")
        base = self._dir(project_id)
        return base / name, base / _META_DIR / f"{name}.json"

    def list(self, project_id: UUID) -> list[ProjectArtifact]:
        meta_dir = self._dir(project_id) / _META_DIR
        if not meta_dir.is_dir():
            return []
        artifacts = []
        for meta in meta_dir.glob("*.json"):
            artifact = self._read_meta(meta)
            if (
                artifact is not None
                and (self._dir(project_id) / artifact.name).is_file()
            ):
                artifacts.append(artifact)
        artifacts.sort(key=lambda a: a.updated_at, reverse=True)
        return artifacts

    def get(self, project_id: UUID, name: str) -> tuple[ProjectArtifact, Path] | None:
        path, meta = self._paths(project_id, name)
        artifact = self._read_meta(meta)
        if artifact is None or not path.is_file():
            return None
        return artifact, path

    def save(
        self,
        project_id: UUID,
        name: str,
        stream: BinaryIO,
        *,
        source: ArtifactSource,
        conversation_id: UUID | None = None,
    ) -> ProjectArtifact:
        """Store ``stream`` as ``name``, replacing an artifact of that name.

        Raises:
            ArtifactTooLarge: The stream is over ``MAX_ARTIFACT_BYTES``;
                nothing is stored.
        """
        path, meta = self._paths(project_id, name)
        meta.parent.mkdir(parents=True, exist_ok=True)
        previous = self._read_meta(meta)
        size = 0
        with self._temp(path) as tmp:
            with open(tmp, "wb") as out:
                while chunk := stream.read(_CHUNK):
                    size += len(chunk)
                    if size > MAX_ARTIFACT_BYTES:
                        raise ArtifactTooLarge(name)
                    out.write(chunk)
            os.replace(tmp, path)
        now = utc_now()
        artifact = ProjectArtifact(
            name=name,
            size=size,
            source=source,
            conversation_id=conversation_id,
            created_at=previous.created_at if previous else now,
            updated_at=now,
        )
        with self._temp(meta) as tmp:
            tmp.write_text(artifact.model_dump_json())
            os.replace(tmp, meta)
        return artifact

    def delete(self, project_id: UUID, name: str) -> bool:
        path, meta = self._paths(project_id, name)
        existed = meta.exists() or path.exists()
        meta.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        return existed

    def delete_project(self, project_id: UUID) -> None:
        shutil.rmtree(self._dir(project_id), ignore_errors=True)

    @staticmethod
    def _read_meta(meta: Path) -> ProjectArtifact | None:
        try:
            return ProjectArtifact.model_validate(json.loads(meta.read_text()))
        except (OSError, ValueError):
            return None

    @staticmethod
    @contextmanager
    def _temp(target: Path) -> Iterator[Path]:
        tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            yield tmp
        finally:
            tmp.unlink(missing_ok=True)


def resolve_workspace_file(working_dir: Path, path: str) -> Path:
    """Resolve ``path`` inside ``working_dir``, following symlinks.

    Raises:
        ValueError: The path leaves the working directory or is not a file.
    """
    root = working_dir.resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Path is outside the conversation's workspace")
    if not resolved.is_file():
        raise ValueError("Path is not a file")
    return resolved
