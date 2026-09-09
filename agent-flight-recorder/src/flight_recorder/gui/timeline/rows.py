from dataclasses import dataclass

from flight_recorder.models.envelopes import Record


@dataclass(frozen=True)
class ActivityRowType:
    key: str
    label: str
    color: str
    description: str


ACTIVITY_ROW_TYPES = (
    ActivityRowType(
        "lifecycle",
        "Lifecycle",
        "#53645d",
        "When this agent started, remained active, and finished.",
    ),
    ActivityRowType(
        "messages",
        "Messages",
        "#3d74b8",
        "Messages and system prompts received or produced by this agent.",
    ),
    ActivityRowType(
        "actions",
        "Actions",
        "#d06b3c",
        "Tool calls and other actions requested by this agent.",
    ),
    ActivityRowType(
        "results",
        "Results",
        "#298f74",
        "Observations and outputs returned by the agent's actions.",
    ),
    ActivityRowType(
        "delegation",
        "Delegation",
        "#8e5aa9",
        "Work handed off to child agents.",
    ),
    ActivityRowType(
        "context",
        "Context",
        "#d39b24",
        "Changes to model context, including condensation.",
    ),
    ActivityRowType(
        "state",
        "State",
        "#78837e",
        "Conversation status and state transitions.",
    ),
    ActivityRowType(
        "metrics",
        "Metrics",
        "#397f8f",
        "Token usage and cost snapshots.",
    ),
    ActivityRowType(
        "errors",
        "Errors",
        "#c0392b",
        "Recorded errors and recorder warnings.",
    ),
    ActivityRowType(
        "other",
        "Other",
        "#78837e",
        "Recorded activity that does not match another category.",
    ),
)
ACTIVITY_ROW_TYPES_BY_KEY = {row.key: row for row in ACTIVITY_ROW_TYPES}
DEFAULT_ACTIVITY_ROW_TYPES = frozenset(
    {
        "lifecycle",
        "messages",
        "actions",
        "results",
        "delegation",
        "context",
        "errors",
    }
)


def classify_record(record: Record) -> str:
    event_type = record.payload.get("event_type")
    tool_name = record.payload.get("tool_name")
    if (
        record.kind.endswith(".error")
        or record.kind == "recorder.warning"
        or record.payload.get("is_error") is True
    ):
        return "errors"
    if (
        record.kind.startswith("delegation.")
        or tool_name == "launch_child_conversation"
    ):
        return "delegation"
    if record.kind.startswith("context.") or event_type == "Condensation":
        return "context"
    if event_type in {"MessageEvent", "SystemPromptEvent"}:
        return "messages"
    if event_type == "ActionEvent":
        return "actions"
    if event_type == "ObservationEvent":
        return "results"
    if event_type == "ConversationStateUpdateEvent":
        return "state"
    if record.kind == "metrics.snapshot":
        return "metrics"
    if record.kind.startswith(("run.", "agent.")):
        return "lifecycle"
    return "other"
