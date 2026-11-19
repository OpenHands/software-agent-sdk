import json
import os
from pathlib import Path
from threading import Lock
from uuid import UUID

from openhands.sdk.agent.stream_context import (
    StreamAborted,
    StreamDelta,
    StreamProgress,
    StreamStarted,
)
from openhands.sdk.event import Event
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class StreamCapture:
    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._active: dict[str, int] = {}
        self._lock = Lock()

    def _append(self, response_id: str, attempt: int, **fields: object) -> None:
        self._directory.mkdir(mode=0o700, exist_ok=True)
        name = f"{UUID(response_id).hex}-{attempt}.jsonl"
        descriptor = os.open(
            self._directory / name, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600
        )
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {"response_id": response_id, "attempt": attempt, **fields},
                    ensure_ascii=False,
                )
                + "\n"
            )
            if fields["type"] == "end":
                stream.flush()
                os.fsync(stream.fileno())

    def _end(self, response_id: str, status: str) -> None:
        attempt = self._active.pop(response_id, None)
        if attempt is not None:
            self._append(response_id, attempt, type="end", status=status)

    def progress(self, frame: StreamProgress) -> None:
        with self._lock:
            try:
                active = self._active.get(frame.item_id)
                if isinstance(frame, StreamStarted):
                    if active is not None and frame.attempt <= active:
                        return
                    self._end(frame.item_id, "superseded")
                    self._append(
                        frame.item_id,
                        frame.attempt,
                        type="start",
                        anchor_seq=frame.anchor_seq,
                    )
                    self._active[frame.item_id] = frame.attempt
                elif active == frame.attempt:
                    if isinstance(frame, StreamDelta):
                        self._append(
                            frame.item_id,
                            frame.attempt,
                            type="delta",
                            sequence=frame.order,
                            channel=frame.kind,
                            choice_index=frame.choice_index,
                            content=frame.content,
                        )
                    elif isinstance(frame, StreamAborted):
                        self._end(frame.item_id, "aborted")
            except (OSError, ValueError):
                self._active.pop(frame.item_id, None)
                logger.warning("Could not record stream progress", exc_info=True)

    def committed(self, event: Event) -> None:
        with self._lock:
            try:
                self._end(event.id, "committed")
            except (OSError, ValueError):
                logger.warning("Could not record stream completion", exc_info=True)
