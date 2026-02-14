"""Persistent storage for browser recording events."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

from openhands.sdk import get_logger


logger = get_logger(__name__)


@dataclass
class EventStorage:
    """Handles persistent storage of recording events to disk."""

    output_dir: str | None = None
    _session_dir: str | None = field(default=None, repr=False)
    _files_written: int = 0
    _total_events: int = 0

    @property
    def session_dir(self) -> str | None:
        return self._session_dir

    @property
    def file_count(self) -> int:
        return self._files_written

    @property
    def total_events(self) -> int:
        return self._total_events

    def create_session_subfolder(self) -> str | None:
        """Create a timestamped subfolder for this recording session."""
        if not self.output_dir:
            return None
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        subfolder = os.path.join(self.output_dir, f"recording-{timestamp}")
        os.makedirs(subfolder, exist_ok=True)
        self._session_dir = subfolder
        return subfolder

    def save_events(self, events: list[dict]) -> str | None:
        """Save events to a timestamped JSON file."""
        if not self._session_dir or not events:
            return None

        os.makedirs(self._session_dir, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        filepath = os.path.join(self._session_dir, f"{timestamp}.json")

        with open(filepath, "w") as f:
            json.dump(events, f)

        self._files_written += 1
        self._total_events += len(events)
        logger.debug(f"Saved {len(events)} events to {filepath}")
        return filepath

    def reset(self) -> None:
        """Reset storage state for a new session."""
        self._session_dir = None
        self._files_written = 0
        self._total_events = 0
