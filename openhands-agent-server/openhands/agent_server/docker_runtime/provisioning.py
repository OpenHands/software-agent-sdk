"""Outer-only runtime identities. Neither this store nor its key is mounted."""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path
from typing import Literal
from uuid import UUID

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, SecretStr, field_serializer, field_validator

from openhands.agent_server.config import Config
from openhands.agent_server.persistence.store import _get_persistence_dir
from openhands.sdk.profiles.agent_profile import LaunchedAgentProfile
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import serialize_secret, validate_secret


class RuntimeGrants(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription: Literal["openai"] | None = None
    credential_names: frozenset[str] = frozenset()
    mcp_server_names: frozenset[str] = frozenset()


class RuntimeIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation_id: UUID
    api_key: SecretStr
    encryption_key: SecretStr
    grants: RuntimeGrants = RuntimeGrants()
    launched_agent_profile: LaunchedAgentProfile | None = None
    auxiliary_subscription: bool = False
    broker_token: SecretStr

    @field_serializer("api_key", "encryption_key", "broker_token")
    def serialize_key(self, value, info):
        return serialize_secret(value, info)

    @field_validator("api_key", "encryption_key", "broker_token")
    @classmethod
    def validate_key(cls, value, info):
        result = validate_secret(value, info)
        if result is None:
            raise ValueError("Runtime control identity cannot be decrypted")
        return result

    @property
    def cipher(self) -> Cipher:
        return Cipher(self.encryption_key.get_secret_value())


class RuntimeProvisioningStore:
    def __init__(self, config: Config):
        if config.cipher is None:
            raise ValueError("Docker runtime provisioning requires OH_SECRET_KEY")
        self.config = config
        self.cipher = config.cipher
        persistence = _get_persistence_dir(config).resolve()
        self.control_root = persistence / "runtime-control"
        self.data_root = persistence / "runtime-data"
        if self.control_root.is_symlink() or self.data_root.is_symlink():
            raise ValueError("Runtime storage roots must not be symlinks")
        self.control_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.control_root.chmod(0o700)
        self.data_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.data_root.chmod(0o700)

    def manifest_path(self, conversation_id: UUID) -> Path:
        return self.control_root / f"{conversation_id.hex}.json"

    def runtime_dir(self, conversation_id: UUID) -> Path:
        return self._direct_child(self.data_root, conversation_id.hex)

    @staticmethod
    def _direct_child(root: Path, name: str) -> Path:
        if root.is_symlink():
            raise ValueError("Runtime storage root must not be a symlink")
        child = root / name
        resolved_child = child.resolve()
        if child.is_symlink() or resolved_child.parent != root.resolve():
            raise ValueError("Runtime mount must not follow a symlink")
        return resolved_child

    def conversation_dir(self, conversation_id: UUID) -> Path:
        return self._direct_child(self.config.conversations_path, conversation_id.hex)

    def workspace_dir(self, conversation_id: UUID) -> Path:
        return self._direct_child(self.config.workspace_path, conversation_id.hex)

    def load(self, conversation_id: UUID) -> RuntimeIdentity:
        path = self.manifest_path(conversation_id)
        if not path.is_file() or path.is_symlink():
            raise ValueError(
                "Unsupported legacy conversation: recreate in an isolated runtime"
            )
        identity = RuntimeIdentity.model_validate_json(
            path.read_text(), context={"cipher": self.cipher}
        )
        if identity.conversation_id != conversation_id:
            raise ValueError("Runtime identity does not match conversation")
        return identity

    def create(self, conversation_id: UUID) -> RuntimeIdentity:
        path = self.manifest_path(conversation_id)
        with FileLock(str(path) + ".lock"):
            if path.exists():
                return self.load(conversation_id)
            directory = self.conversation_dir(conversation_id)
            if (directory / "base_state.json").exists() or (
                directory / "meta.json"
            ).exists():
                raise ValueError(
                    "Unsupported legacy conversation: recreate in an isolated runtime"
                )
            identity = RuntimeIdentity(
                conversation_id=conversation_id,
                api_key=SecretStr(secrets.token_urlsafe(32)),
                encryption_key=SecretStr(secrets.token_urlsafe(32)),
                broker_token=SecretStr(secrets.token_urlsafe(32)),
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
        finally:
            Path(temporary).unlink(missing_ok=True)
