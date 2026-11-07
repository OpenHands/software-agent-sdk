"""Durable request claims for the OpenAI-compatible gateway."""

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from uuid import UUID, uuid4

from openhands.agent_server.config import Config
from openhands.agent_server.persistence.store import (
    _ensure_secure_directory,
    _file_lock,
    _get_persistence_dir,
)


_RETENTION_SECONDS = 7 * 24 * 60 * 60
_MAX_CLAIMS = 10_000


@dataclass(frozen=True)
class Claim:
    state: str
    conversation_id: UUID
    claim_id: str | None = None
    response: dict[str, object] | None = None


class GatewayIdempotencyStore:
    def __init__(self, config: Config) -> None:
        directory = _get_persistence_dir(config) / "gateway-idempotency"
        self._database = directory / "claims.sqlite3"
        self._salt = directory / "salt"

    async def claim(
        self,
        *,
        key: str,
        request_digest: str,
        conversation_id: UUID,
    ) -> Claim:
        return await asyncio.to_thread(
            self._claim, key, request_digest, conversation_id
        )

    async def complete(self, *, claim_id: str, response: dict[str, object]) -> None:
        await asyncio.to_thread(self._complete, claim_id, response)

    async def release(self, *, claim_id: str) -> None:
        await asyncio.to_thread(self._release, claim_id)

    def _key_digest(self, key: str) -> str:
        _ensure_secure_directory(self._salt.parent)
        with _file_lock(self._salt.with_suffix(".lock")):
            if self._salt.exists():
                salt = self._salt.read_bytes()
            else:
                salt = secrets.token_bytes(32)
                fd = os.open(self._salt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(salt)
                    stream.flush()
                    os.fsync(stream.fileno())
        if len(salt) != 32:
            raise ValueError("Gateway idempotency salt is invalid")
        return hmac.new(salt, key.encode(), hashlib.sha256).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        _ensure_secure_directory(self._database.parent)
        try:
            fd = os.open(self._database, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        connection = sqlite3.connect(self._database, timeout=5)
        connection.execute(
            """CREATE TABLE IF NOT EXISTS claims (
                key_digest TEXT PRIMARY KEY,
                request_digest TEXT NOT NULL,
                claim_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                response TEXT,
                expires_at REAL NOT NULL
            )"""
        )
        return connection

    def _claim(
        self,
        key: str,
        request_digest: str,
        conversation_id: UUID,
    ) -> Claim:
        key_digest = self._key_digest(key)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM claims WHERE expires_at <= ?", (time.time(),)
            )
            row = connection.execute(
                "SELECT request_digest, claim_id, conversation_id, response "
                "FROM claims WHERE key_digest = ?",
                (key_digest,),
            ).fetchone()
            if row is not None:
                saved_digest, claim_id, saved_id, response = row
                if saved_digest != request_digest:
                    return Claim("mismatch", UUID(saved_id))
                if response is None:
                    return Claim("pending", UUID(saved_id))
                return Claim("complete", UUID(saved_id), response=json.loads(response))
            count = connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
            if count >= _MAX_CLAIMS:
                return Claim("full", conversation_id)
            claim_id = uuid4().hex
            connection.execute(
                "INSERT INTO claims VALUES (?, ?, ?, ?, NULL, ?)",
                (
                    key_digest,
                    request_digest,
                    claim_id,
                    str(conversation_id),
                    time.time() + _RETENTION_SECONDS,
                ),
            )
            return Claim("new", conversation_id, claim_id=claim_id)

    def _complete(self, claim_id: str, response: dict[str, object]) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE claims SET response = ? WHERE claim_id = ?",
                (json.dumps(response, separators=(",", ":")), claim_id),
            )

    def _release(self, claim_id: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM claims WHERE claim_id = ?", (claim_id,))
