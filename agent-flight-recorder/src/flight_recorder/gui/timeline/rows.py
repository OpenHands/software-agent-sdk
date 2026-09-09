from dataclasses import dataclass

from flight_recorder.models.envelopes import Record


@dataclass(frozen=True)
class ActivitySubRowType:
    key: str
    label: str
    color: str
    description: str


@dataclass(frozen=True)
class ActivityRowType:
    key: str
    label: str
    color: str
    description: str
    subrows: tuple[ActivitySubRowType, ...] = ()


ACTIVITY_ROW_TYPES = (
    ActivityRowType(
        "lifecycle",
        "Lifecycle",
        "#53645d",
        "When this agent started, remained active, and finished.",
    ),
    ActivityRowType(
        "user_conversation",
        "User Conversation",
        "#3d74b8",
        "Messages exchanged through the OpenHands conversation.",
        (
            ActivitySubRowType(
                "user_input",
                "User Input",
                "#3d74b8",
                "System context and messages supplied by the user.",
            ),
            ActivitySubRowType(
                "agent_response",
                "Agent Response",
                "#d06b3c",
                "Assistant messages returned through the conversation.",
            ),
        ),
    ),
    ActivityRowType(
        "llm_interaction",
        "LLM Interaction",
        "#7556a5",
        "Exact requests and responses exchanged with a language model.",
        (
            ActivitySubRowType(
                "llm_request",
                "Request",
                "#7556a5",
                "Prompts and requests sent to a language model.",
            ),
            ActivitySubRowType(
                "llm_response",
                "Response",
                "#2f7d67",
                "Responses and errors returned by a language model.",
            ),
        ),
    ),
    ActivityRowType(
        "tool",
        "Tool",
        "#d06b3c",
        "Tool calls and their returned observations.",
        (
            ActivitySubRowType(
                "tool_action",
                "Action",
                "#d06b3c",
                "Tool calls and other actions requested by this agent.",
            ),
            ActivitySubRowType(
                "tool_result",
                "Result",
                "#298f74",
                "Observations and outputs returned by the agent's actions.",
            ),
        ),
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
ACTIVITY_SUBROW_TYPES_BY_KEY = {
    subrow.key: subrow for row in ACTIVITY_ROW_TYPES for subrow in row.subrows
}
ACTIVITY_CATEGORY_TYPES_BY_KEY = {
    **ACTIVITY_ROW_TYPES_BY_KEY,
    **ACTIVITY_SUBROW_TYPES_BY_KEY,
}
ACTIVITY_ROW_KEY_BY_CATEGORY = {
    subrow.key: row.key for row in ACTIVITY_ROW_TYPES for subrow in row.subrows
}
DEFAULT_ACTIVITY_ROW_TYPES = frozenset(
    {
        "lifecycle",
        "user_conversation",
        "llm_interaction",
        "tool",
        "delegation",
        "context",
        "errors",
    }
)


def classify_record(record: Record) -> str:
    event_type = record.payload.get("event_type")
    tool_name = record.payload.get("tool_name")
    if record.kind == "llm.request":
        return "llm_request"
    if record.kind == "llm.response":
        return "llm_response"
    if event_type == "SystemPromptEvent":
        return "user_input"
    if event_type == "MessageEvent":
        message = record.payload.get("llm_message")
        role = message.get("role") if isinstance(message, dict) else None
        if record.payload.get("source") == "user" or role == "user":
            return "user_input"
        return "agent_response"
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
    if event_type == "ActionEvent":
        return "tool_action"
    if event_type == "ObservationEvent":
        return "tool_result"
    if event_type == "ConversationStateUpdateEvent":
        return "state"
    if record.kind == "metrics.snapshot":
        return "metrics"
    if record.kind.startswith(("run.", "agent.")):
        return "lifecycle"
    return "other"


def row_key_for_category(category: str) -> str:
    return ACTIVITY_ROW_KEY_BY_CATEGORY.get(category, category)
