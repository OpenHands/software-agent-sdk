import pytest

from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.error_classification import (
    ErrorClassification,
    FailureKind,
    classify_error,
)
from openhands.sdk.event.llm_convertible import AgentErrorEvent


@pytest.mark.parametrize(
    ("code", "detail", "kind"),
    [
        ("OpenAIError", "Incorrect API key provided", "auth"),
        ("APIError", "This request requires more credits", "quota"),
        ("OpenAIError", "Error code: 429", "rate_limit"),
        ("MaxBudgetReached", "", "quota"),
        ("OpenRouterException", "", "transient"),
        (
            "LLMBadRequestError",
            "LLM Provider NOT provided",
            "config",
        ),
        (
            "NoCondensationAvailableException",
            "Streaming requires an on_token callback",
            "internal",
        ),
        (
            "PydanticSerializationError",
            "surrogates not allowed",
            "internal",
        ),
        ("UnexpectedProviderError", "", "unknown"),
        # Run-limit is a known product outcome, not a diagnostic.
        ("MaxIterationsReached", "Agent reached maximum iterations", "agent_action"),
        ("ConversationOwnershipLostError", "", "agent_action"),
        # Context-window errors are recoverable via condensation.
        ("LLMContextWindowExceedError", "", "agent_action"),
        ("LLMMalformedConversationHistoryError", "", "agent_action"),
    ],
)
def test_conversation_error_classifies_sensitive_detail_without_serializing_it(
    code: str, detail: str, kind: str
) -> None:
    event = ConversationErrorEvent(source="environment", code=code, detail=detail)

    assert event.classification is not None
    assert event.classification.kind == kind
    if detail:
        assert detail not in event.classification.model_dump_json()


@pytest.mark.parametrize(
    ("code", "detail", "expected_kind"),
    [
        # Code-based classification must win over incidental detail wording.
        ("KeyError", "'timeout_seconds'", "internal"),
        ("AssertionError", "Tool result not found for call id abc123", "internal"),
        ("TypeError", "connection error during call", "internal"),
        ("AttributeError", "model not found in registry", "internal"),
        # Bare "429" inside a request id must NOT be classified as rate_limit.
        ("HTTPStatusError", "Connection reset for request req_814295af", "transient"),
        # "not found" inside a transient message must not be CONFIG when the
        # code is a known transient wrapper — but for an unknown code, the
        # detail-based "model not found" is CONFIG.
        ("UnexpectedError", "model not found", "config"),
    ],
)
def test_code_based_classification_takes_priority_over_detail(
    code: str, detail: str, expected_kind: str
) -> None:
    assert classify_error(code, detail).kind == expected_kind


def test_agent_error_event_defaults_to_unknown() -> None:
    """Without an explicit classification, AgentErrorEvent is UNKNOWN (diagnostic)."""
    event = AgentErrorEvent(
        error="something went wrong",
        tool_name="bash",
        tool_call_id="call-1",
    )
    assert event.classification is not None
    assert event.classification.kind == FailureKind.UNKNOWN
    assert event.classification.retryable is False


def test_agent_error_event_preserves_explicit_classification() -> None:
    """An explicitly-provided classification is not overwritten by the validator."""
    explicit = ErrorClassification(
        kind=FailureKind.AGENT_ACTION, retryable=True, user_action="retry"
    )
    event = AgentErrorEvent(
        error="validation failed",
        tool_name="bash",
        tool_call_id="call-1",
        classification=explicit,
    )
    assert event.classification is explicit
