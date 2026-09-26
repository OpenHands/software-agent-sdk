"""Opt-in admission checks for local conversation storage."""

import math
import shutil
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class StorageSafetyConfig(BaseModel):
    """Keep a fraction of each local filesystem free for safe shutdown."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_free_ratio: float = Field(default=0.05, gt=0, lt=1)


class StorageSafetyError(RuntimeError):
    """Storage admission was refused, or a persistence write failed."""

    def __init__(
        self,
        code: str,
        path: str,
        *,
        min_free_ratio: float,
        free_bytes: int | None = None,
        total_bytes: int | None = None,
        required_bytes: int = 0,
        errno: int | None = None,
        detail: str | None = None,
    ) -> None:
        self.code = code
        self.path = path
        self.min_free_ratio = min_free_ratio
        self.free_bytes = free_bytes
        self.total_bytes = total_bytes
        self.required_bytes = required_bytes
        self.errno = errno
        available = (
            f"{free_bytes / total_bytes:.1%}"
            if free_bytes is not None and total_bytes
            else "unknown"
        )
        super().__init__(
            detail
            or (
                f"Storage admission paused: {path} has {available} free space; "
                f"at least {100 * min_free_ratio:g}% must remain "
                f"(free={free_bytes}, total={total_bytes}, "
                f"pending write={required_bytes}). Free space or expand the disk, "
                "then explicitly resume the conversation. "
                "Already committed notes and history are retained."
            )
        )

    def to_dict(self) -> dict[str, str | int | float | None]:
        shortage = None
        if self.free_bytes is not None and self.total_bytes is not None:
            shortage = max(
                0,
                math.ceil(self.total_bytes * self.min_free_ratio)
                + self.required_bytes
                - self.free_bytes,
            )
        return {
            "code": self.code,
            "path": self.path,
            "min_free_ratio": self.min_free_ratio,
            "free_bytes": self.free_bytes,
            "total_bytes": self.total_bytes,
            "required_bytes": self.required_bytes,
            "free_ratio": (
                self.free_bytes / self.total_bytes
                if self.free_bytes is not None and self.total_bytes
                else None
            ),
            "shortage_bytes": shortage,
            "errno": self.errno,
            "detail": str(self),
        }


StorageSafetyCallback = Callable[[StorageSafetyError], None]


def check_storage_safety_paths(
    paths: Sequence[str | Path],
    config: StorageSafetyConfig,
    required_bytes: int = 0,
    *,
    allow_reserve: bool = False,
) -> None:
    """Check actual free space, using an existing parent for a new directory."""
    if required_bytes < 0:
        raise ValueError("required_bytes must be nonnegative")
    for raw_path in paths:
        path = Path(raw_path).expanduser().absolute()
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            usage = shutil.disk_usage(probe)
        except OSError as exc:
            raise StorageSafetyError(
                "StorageCheckFailed",
                str(path),
                min_free_ratio=config.min_free_ratio,
                required_bytes=required_bytes,
                detail=f"Cannot check local storage at {path}: {exc}",
            ) from exc
        if usage.total <= 0:
            raise StorageSafetyError(
                "StorageCheckFailed",
                str(path),
                min_free_ratio=config.min_free_ratio,
                detail=f"Cannot determine filesystem capacity at {path}",
            )
        reserved = (
            0 if allow_reserve else math.ceil(usage.total * config.min_free_ratio)
        )
        if usage.free - required_bytes < reserved:
            raise StorageSafetyError(
                "StorageLowSpace",
                str(path),
                min_free_ratio=config.min_free_ratio,
                free_bytes=usage.free,
                total_bytes=usage.total,
                required_bytes=required_bytes,
            )


class StorageSafetyController:
    """Thread-safe stop latch; notifications never depend on persistence."""

    def __init__(
        self,
        paths: Sequence[str | Path],
        config: StorageSafetyConfig,
        callback: StorageSafetyCallback | None = None,
    ) -> None:
        self.paths = tuple(paths)
        self.config = config
        self._callback = callback
        self._lock = threading.Lock()
        self._error: StorageSafetyError | None = None
        self._reserve = ContextVar("storage_shutdown_reserve", default=False)

    @property
    def error(self) -> StorageSafetyError | None:
        with self._lock:
            return self._error

    def stop(self, error: StorageSafetyError) -> None:
        with self._lock:
            notify = self._error is None or self._error.code != error.code
            if self._error is None or error.code == "StorageWriteFailed":
                self._error = error
        if notify:
            logger.warning("%s", error)
            if self._callback is not None:
                try:
                    self._callback(error)
                except Exception:
                    logger.exception("Storage safety notification failed")

    def check(self) -> None:
        error = self.error
        if error is not None:
            raise error
        try:
            check_storage_safety_paths(self.paths, self.config)
        except StorageSafetyError as exc:
            self.stop(exc)
            raise

    def resume(self) -> None:
        try:
            check_storage_safety_paths(self.paths, self.config)
        except StorageSafetyError as exc:
            self.stop(exc)
            raise
        with self._lock:
            self._error = None

    @contextmanager
    def use_shutdown_reserve(self) -> Iterator[None]:
        """Allow already-started results and stop metadata below the threshold."""
        token = self._reserve.set(True)
        try:
            yield
        finally:
            self._reserve.reset(token)

    def before_write(self, path: str | Path, size: int) -> None:
        error = self.error
        if error is not None and error.code == "StorageWriteFailed":
            raise error
        if self._reserve.get():
            try:
                check_storage_safety_paths(
                    [path], self.config, size, allow_reserve=True
                )
            except StorageSafetyError as exc:
                failure = StorageSafetyError(
                    "StorageWriteFailed",
                    str(path),
                    min_free_ratio=self.config.min_free_ratio,
                    free_bytes=exc.free_bytes,
                    total_bytes=exc.total_bytes,
                    required_bytes=size,
                    detail="Insufficient storage to commit an in-flight result.",
                )
                self.stop(failure)
                raise failure from exc
            return
        self.check()
        try:
            # Atomic replacement temporarily needs the entire new file, even
            # when its final size is smaller than the existing destination.
            check_storage_safety_paths([path], self.config, size)
        except StorageSafetyError as exc:
            self.stop(exc)
            raise

    def write_failed(self, path: str | Path, error: OSError) -> StorageSafetyError:
        failure = StorageSafetyError(
            "StorageWriteFailed",
            str(path),
            min_free_ratio=self.config.min_free_ratio,
            errno=error.errno,
            detail=(
                f"Persistence failed at {path}: {error}. Already committed events "
                "are retained; uncommitted tool outcomes must be checked before "
                "resuming."
            ),
        )
        self.stop(failure)
        return failure
