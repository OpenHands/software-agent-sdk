"""Date/time and UUID helpers."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from pydantic import PlainSerializer


def utc_now() -> datetime:
    """Return the current time in UTC (``datetime.utcnow`` is deprecated)."""
    return datetime.now(UTC)


def timestamp_instant(value: str | datetime) -> datetime:
    """Return the timezone-aware instant an event timestamp denotes.

    Legacy event timestamps were written as naive server-local ISO strings, so a
    naive value is read as local time; offset-bearing values keep their offset.
    """
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return parsed.astimezone() if parsed.tzinfo is None else parsed


def _uuid_to_hex(uuid_obj: UUID) -> str:
    return uuid_obj.hex


OpenHandsUUID = Annotated[UUID, PlainSerializer(_uuid_to_hex, when_used="json")]
"""UUID type that serialises to a hex string (no hyphens) in JSON."""
