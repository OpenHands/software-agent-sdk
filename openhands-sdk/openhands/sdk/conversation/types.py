import re
import uuid
from collections.abc import Callable
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field

from openhands.sdk.event.base import Event
from openhands.sdk.llm.streaming import TokenCallbackType


ConversationCallbackType = Callable[[Event], None]
"""Type alias for event callback functions."""

ConversationTokenCallbackType = TokenCallbackType
"""Callback type invoked for streaming LLM deltas."""

ConversationID = uuid.UUID
"""Type alias for conversation IDs."""

TAG_KEY_PATTERN = re.compile(r"^[a-z0-9]+$")
TAG_VALUE_MAX_LENGTH = 256


def _validate_tags(v: dict[str, str] | None) -> dict[str, str]:
    if v is None:
        return {}
    for key, value in v.items():
        if not TAG_KEY_PATTERN.match(key):
            raise ValueError(
                f"Tag key '{key}' is invalid: keys must be lowercase alphanumeric only"
            )
        if len(value) > TAG_VALUE_MAX_LENGTH:
            raise ValueError(
                f"Tag value for '{key}' exceeds maximum length of "
                f"{TAG_VALUE_MAX_LENGTH} characters"
            )
    return v


ConversationTags = Annotated[dict[str, str], BeforeValidator(_validate_tags)]
"""Validated dict of conversation tags.

Keys must be lowercase alphanumeric. Values are arbitrary strings up to 256 chars.
"""

type TraceMetadataValue = (
    str | bool | int | float | list[str] | list[bool] | list[int] | list[float]
)


def _validate_observability_metadata(
    v: Any,
) -> dict[str, TraceMetadataValue]:
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValueError("Observability metadata must be a dictionary")
    for key, value in v.items():
        if not isinstance(key, str) or not key:
            raise ValueError("Observability metadata keys must be non-empty strings")
        if isinstance(value, str | bool | int | float):
            continue
        if isinstance(value, list):
            if all(isinstance(item, str) for item in value):
                continue
            if all(isinstance(item, bool) for item in value):
                continue
            if all(
                isinstance(item, int) and not isinstance(item, bool) for item in value
            ):
                continue
            if all(isinstance(item, float) for item in value):
                continue
        raise ValueError(
            f"Observability metadata value for '{key}' must be a scalar "
            "or a homogeneous sequence of strings, booleans, integers, or floats "
            "(mixed numeric types such as [1, 1.5] are not supported by OpenTelemetry)"
        )
    return v


ConversationObservabilityMetadata = Annotated[
    dict[str, TraceMetadataValue],
    BeforeValidator(_validate_observability_metadata),
]
"""Validated dict of Laminar/OTel trace metadata for a conversation."""


def _validate_observability_tags(v: Any) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValueError("Observability tags must be a list")
    if not all(isinstance(tag, str) and tag for tag in v):
        raise ValueError("Observability tags must be non-empty strings")
    return v


ConversationObservabilityTags = Annotated[
    list[str],
    BeforeValidator(_validate_observability_tags),
]
"""Validated list of Laminar/OTel span tags for a conversation."""


class StuckDetectionThresholds(BaseModel):
    """Configuration for stuck detection thresholds.

    Attributes:
        action_observation: Number of repetitions before triggering
            action-observation loop detection
        action_error: Number of repetitions before triggering
            action-error loop detection
        monologue: Number of consecutive agent messages before triggering
            monologue detection
        alternating_pattern: Number of repetitions before triggering
            alternating pattern detection
    """

    action_observation: int = Field(
        default=4, ge=1, description="Threshold for action-observation loop detection"
    )
    action_error: int = Field(
        default=3, ge=1, description="Threshold for action-error loop detection"
    )
    monologue: int = Field(
        default=3, ge=1, description="Threshold for agent monologue detection"
    )
    alternating_pattern: int = Field(
        default=6, ge=1, description="Threshold for alternating pattern detection"
    )
