import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock

from openhands.sdk import get_logger


logger = get_logger(__name__)

LEASE_FILE_NAME = "owner_lease.json"
LEASE_LOCK_FILE_NAME = ".owner_lease.lock"
DEFAULT_LEASE_TTL_SECONDS = 45.0


@dataclass(frozen=True)
class LeaseClaim:
    generation: int
    takeover: bool


class ConversationLeaseHeldError(RuntimeError):
    def __init__(
        self,
        *,
        conversation_dir: Path,
        owner_instance_id: str,
        expires_at: float,
    ) -> None:
        self.conversation_dir = conversation_dir
        self.owner_instance_id = owner_instance_id
        self.expires_at = expires_at
        super().__init__(
            f"conversation lease is held by {owner_instance_id} until {expires_at}"
        )


class ConversationOwnershipLostError(RuntimeError):
    def __init__(
        self,
        *,
        conversation_dir: Path,
        owner_instance_id: str,
        generation: int,
    ) -> None:
        self.conversation_dir = conversation_dir
        self.owner_instance_id = owner_instance_id
        self.generation = generation
        super().__init__(
            "conversation ownership was lost before the write completed"
        )


class ConversationLease:
    def __init__(
        self,
        *,
        conversation_dir: Path,
        owner_instance_id: str,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> None:
        self._conversation_dir = conversation_dir
        self._owner_instance_id = owner_instance_id
        self._ttl_seconds = ttl_seconds
        self._lease_path = conversation_dir / LEASE_FILE_NAME
        self._lock_path = conversation_dir / LEASE_LOCK_FILE_NAME

    def claim(self) -> LeaseClaim:
        self._conversation_dir.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self._lock_path)):
            now = time.time()
            payload = self._read_payload()
            if payload is not None:
                current_owner = str(payload["owner_instance_id"])
                current_generation = int(payload["generation"])
                expires_at = float(payload["expires_at"])
                if (
                    current_owner != self._owner_instance_id
                    and expires_at > now
                ):
                    raise ConversationLeaseHeldError(
                        conversation_dir=self._conversation_dir,
                        owner_instance_id=current_owner,
                        expires_at=expires_at,
                    )
                same_owner = current_owner == self._owner_instance_id
                generation = (
                    current_generation if same_owner else current_generation + 1
                )
                takeover = not same_owner
            else:
                generation = 1
                takeover = False
            self._write_payload(
                generation=generation,
                expires_at=now + self._ttl_seconds,
            )
            return LeaseClaim(generation=generation, takeover=takeover)

    def renew(self, generation: int) -> None:
        with FileLock(str(self._lock_path)):
            self._assert_owner_locked(generation)
            self._write_payload(
                generation=generation,
                expires_at=time.time() + self._ttl_seconds,
            )

    @contextmanager
    def guarded_write(self, generation: int) -> Iterator[None]:
        with FileLock(str(self._lock_path)):
            self._assert_owner_locked(generation)
            yield



    def release(self, generation: int) -> None:
        with FileLock(str(self._lock_path)):
            payload = self._read_payload()
            if payload is None:
                return
            if (
                str(payload["owner_instance_id"]) != self._owner_instance_id
                or int(payload["generation"]) != generation
            ):
                return
            self._lease_path.unlink(missing_ok=True)

    def _assert_owner_locked(self, generation: int) -> None:
        payload = self._read_payload()
        if payload is None:
            raise ConversationOwnershipLostError(
                conversation_dir=self._conversation_dir,
                owner_instance_id=self._owner_instance_id,
                generation=generation,
            )
        if (
            str(payload["owner_instance_id"]) != self._owner_instance_id
            or int(payload["generation"]) != generation
        ):
            raise ConversationOwnershipLostError(
                conversation_dir=self._conversation_dir,
                owner_instance_id=self._owner_instance_id,
                generation=generation,
            )

    def _read_payload(self) -> dict[str, object] | None:
        if not self._lease_path.exists():
            return None
        try:
            return json.loads(self._lease_path.read_text())
        except Exception:
            logger.warning(
                "Failed to parse conversation lease file; treating as stale: %s",
                self._lease_path,
            )
            return None

    def _write_payload(self, *, generation: int, expires_at: float) -> None:
        payload = {
            "owner_instance_id": self._owner_instance_id,
            "generation": generation,
            "expires_at": expires_at,
        }
        tmp_path = self._lease_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload))
        tmp_path.replace(self._lease_path)
