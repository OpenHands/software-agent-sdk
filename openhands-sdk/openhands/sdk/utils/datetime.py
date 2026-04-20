"""Date/time and UUID helpers."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from pydantic import PlainSerializer


def utc_now() -> datetime:
    """Return the current time in UTC (``datetime.utcnow`` is deprecated)."""
    return datetime.now(UTC)


def _uuid_to_hex(uuid_obj: UUID) -> str:
    return uuid_obj.hex


OpenHandsUUID = Annotated[UUID, PlainSerializer(_uuid_to_hex, when_used="json")]
"""UUID type that serialises to a hex string (no hyphens) in JSON."""
