"""Persist the credentials owned by one conversation container."""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path
from stat import S_ISREG
from uuid import UUID

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, SecretStr, field_serializer, field_validator

from openhands.agent_server.config import Config
from openhands.agent_server.persistence.store import _get_persistence_dir
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import serialize_secret, validate_secret


class RuntimeIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation_id: UUID
    api_key: SecretStr
    encryption_key: SecretStr
    workspace_path: Path
    launched_agent_profile: LaunchedAgentProfile | None = None

    @field_serializer("api_key", "encryption_key")
    def serialize_key(self, value, info):
        return serialize_secret(value, info)

    @field_validator("api_key", "encryption_key")
    @classmethod
    def validate_key(cls, value, info):
        result = validate_secret(value, info)
        if result is None:
            raise ValueError("Runtime identity cannot be decrypted")
        return result

    @property
    def cipher(self) -> Cipher:
        return Cipher(self.encryption_key.get_secret_value())


class RuntimeProvisioningStore:
    def __init__(self, config: Config):
        if config.cipher is None:
            raise ValueError("Docker runtime requires OH_SECRET_KEY")
        self.config = config
        self.cipher = config.cipher
        persistence = _get_persistence_dir(config).resolve()
        self.control_root = persistence / "runtime-control"
        self.data_root = persistence / "runtime-data"
        self._identities: dict[UUID, tuple[tuple[int, int, int], RuntimeIdentity]] = {}
        for root in (self.control_root, self.data_root):
            if root.is_symlink():
                raise ValueError("Runtime storage roots must not be symlinks")
            root.mkdir(parents=True, mode=0o700, exist_ok=True)
            root.chmod(0o700)

    def manifest_path(self, conversation_id: UUID) -> Path:
        return self.control_root / f"{conversation_id.hex}.json"

    def runtime_dir(self, conversation_id: UUID) -> Path:
        return self.direct_child(self.data_root, conversation_id.hex)

    @staticmethod
    def direct_child(root: Path, name: str) -> Path:
        if root.is_symlink():
            raise ValueError("Runtime storage root must not be a symlink")
        child = root / name
        resolved = child.resolve()
        if child.is_symlink() or resolved.parent != root.resolve():
            raise ValueError("Runtime mount must not follow a symlink")
        return resolved

    def load_optional(self, conversation_id: UUID) -> RuntimeIdentity | None:
        """Load an identity, returning ``None`` only when it does not exist."""
        path = self.manifest_path(conversation_id)
        try:
            manifest_stat = path.lstat()
        except FileNotFoundError:
            self._identities.pop(conversation_id, None)
            return None
        except OSError:
            self._identities.pop(conversation_id, None)
            raise ValueError("Conversation runtime identity is unavailable") from None
        if not S_ISREG(manifest_stat.st_mode):
            self._identities.pop(conversation_id, None)
            raise ValueError("Conversation runtime identity is unavailable")
        signature = (
            manifest_stat.st_ino,
            manifest_stat.st_mtime_ns,
            manifest_stat.st_size,
        )
        cached = self._identities.get(conversation_id)
        if cached is not None and cached[0] == signature:
            return cached[1]
        identity = RuntimeIdentity.model_validate_json(
            path.read_text(), context={"cipher": self.cipher}
        )
        if identity.conversation_id != conversation_id:
            raise ValueError("Runtime identity does not match conversation")
        self._identities[conversation_id] = (signature, identity)
        return identity

    def load(self, conversation_id: UUID) -> RuntimeIdentity:
        identity = self.load_optional(conversation_id)
        if identity is None:
            raise ValueError("Conversation runtime identity is unavailable")
        return identity

    def create(
        self, conversation_id: UUID, workspace_path: Path | None = None
    ) -> RuntimeIdentity:
        path = self.manifest_path(conversation_id)
        with FileLock(str(path) + ".lock"):
            if path.exists():
                identity = self.load(conversation_id)
                if (
                    workspace_path is not None
                    and workspace_path.resolve() != identity.workspace_path
                ):
                    raise ValueError("A conversation cannot change workspaces")
                return identity
            conversation_dir = self.direct_child(
                self.config.conversations_path, conversation_id.hex
            )
            if (conversation_dir / "meta.json").exists() or (
                conversation_dir / "base_state.json"
            ).exists():
                raise ValueError(
                    "An existing local conversation cannot become a Docker runtime"
                )
            if workspace_path is None:
                workspace_path = self.direct_child(
                    self.runtime_dir(conversation_id), "workspace"
                )
                workspace_path.mkdir(parents=True, mode=0o700, exist_ok=True)
            else:
                if not workspace_path.is_absolute():
                    raise ValueError("Conversation workspace must be absolute")
                if workspace_path.is_symlink():
                    raise ValueError("Conversation workspace must be a real directory")
                try:
                    workspace_path.mkdir(parents=True, mode=0o700, exist_ok=True)
                except OSError as exc:
                    raise ValueError(
                        "Conversation workspace must be a real directory"
                    ) from exc
                if workspace_path.is_symlink() or not workspace_path.is_dir():
                    raise ValueError("Conversation workspace must be a real directory")
                workspace_path = workspace_path.resolve()
            identity = RuntimeIdentity(
                conversation_id=conversation_id,
                api_key=SecretStr(secrets.token_urlsafe(32)),
                encryption_key=SecretStr(secrets.token_urlsafe(32)),
                workspace_path=workspace_path,
            )
            self._save(identity)
            return identity

    def save(self, identity: RuntimeIdentity) -> None:
        with FileLock(str(self.manifest_path(identity.conversation_id)) + ".lock"):
            self._save(identity)

    def _save(self, identity: RuntimeIdentity) -> None:
        payload = identity.model_dump_json(context={"cipher": self.cipher})
        fd, temporary = tempfile.mkstemp(dir=self.control_root)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(payload)
            os.replace(temporary, self.manifest_path(identity.conversation_id))
            stat = self.manifest_path(identity.conversation_id).stat()
            signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            self._identities[identity.conversation_id] = (signature, identity)
        finally:
            Path(temporary).unlink(missing_ok=True)
