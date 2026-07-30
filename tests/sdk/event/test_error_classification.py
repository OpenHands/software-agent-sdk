import pytest

from openhands.sdk.event.conversation_error import ConversationErrorEvent


@pytest.mark.parametrize(
    ("code", "detail", "cause", "telemetry"),
    [
        ("OpenAIError", "Incorrect API key provided", "authentication", "outcome"),
        ("APIError", "This request requires more credits", "quota", "outcome"),
        ("OpenAIError", "Error code: 429", "rate_limit", "outcome"),
        (
            "LLMBadRequestError",
            "LLM Provider NOT provided",
            "configuration",
            "outcome",
        ),
        (
            "NoCondensationAvailableException",
            "Streaming requires an on_token callback",
            "internal",
            "diagnostic",
        ),
        (
            "PydanticSerializationError",
            "surrogates not allowed",
            "internal",
            "diagnostic",
        ),
    ],
)
def test_conversation_error_classifies_sensitive_detail_without_serializing_it(
    code: str, detail: str, cause: str, telemetry: str
) -> None:
    event = ConversationErrorEvent(source="environment", code=code, detail=detail)

    assert event.classification is not None
    assert event.classification.cause == cause
    assert event.classification.telemetry == telemetry
    assert detail not in event.classification.model_dump_json()
