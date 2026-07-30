import pytest

from openhands.sdk.event.conversation_error import ConversationErrorEvent


@pytest.mark.parametrize(
    ("code", "detail", "kind"),
    [
        ("OpenAIError", "Incorrect API key provided", "auth"),
        ("APIError", "This request requires more credits", "quota"),
        ("OpenAIError", "Error code: 429", "rate_limit"),
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
    ],
)
def test_conversation_error_classifies_sensitive_detail_without_serializing_it(
    code: str, detail: str, kind: str
) -> None:
    event = ConversationErrorEvent(source="environment", code=code, detail=detail)

    assert event.classification is not None
    assert event.classification.kind == kind
    assert detail not in event.classification.model_dump_json()
