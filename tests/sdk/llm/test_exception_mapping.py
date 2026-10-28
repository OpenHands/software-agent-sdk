import httpx
import pytest
from litellm.exceptions import (
    APIError,
    AuthenticationError,
    BadRequestError,
    ContentPolicyViolationError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
)
from openai import OpenAIError

from openhands.sdk.llm.exceptions import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMContentPolicyViolationError,
    LLMError,
    LLMMalformedConversationHistoryError,
    map_provider_exception,
)


MODEL = "test-model"
PROVIDER = "test-provider"


def test_map_auth_error_from_bad_request():
    e = BadRequestError("Invalid API key provided", MODEL, PROVIDER)
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMAuthenticationError)


def test_map_auth_error_from_openai_error():
    # OpenAIError has odd behavior; create a BadRequestError that wraps an
    # auth-like message instead, as providers commonly route auth issues
    # through BadRequestError in LiteLLM
    e = BadRequestError("status 401 Unauthorized: missing API key", MODEL, PROVIDER)
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMAuthenticationError)


def test_map_typed_authentication_error_without_pattern_match():
    # Typed 401 from litellm whose message text doesn't contain any of the
    # auth heuristic patterns — should still map via the isinstance check.
    e = AuthenticationError("Bearer token expired", PROVIDER, MODEL)
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMAuthenticationError)


def test_map_typed_permission_denied_error():
    response = httpx.Response(
        status_code=403,
        request=httpx.Request("POST", "https://example.test"),
    )
    e = PermissionDeniedError("Region not allowed", PROVIDER, MODEL, response)
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMAuthenticationError)


def test_map_malformed_tool_history_bad_request():
    e = BadRequestError(
        (
            'AnthropicException - {"type":"error","error":{"type":'
            '"invalid_request_error","message":"messages.134: `tool_use` '
            "ids were found without `tool_result` blocks immediately after: "
            "toolu_01Aye4s5HrR2uXwXFYgtQi4H. Each `tool_use` block must have "
            'a corresponding `tool_result` block in the next message."}}'
        ),
        MODEL,
        PROVIDER,
    )
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMMalformedConversationHistoryError)


def test_map_openai_tool_argument_parse_internal_server_error():
    e = InternalServerError(
        (
            "OpenAIException - Failed to parse tool call arguments as JSON: "
            "invalid string: missing closing quote"
        ),
        PROVIDER,
        MODEL,
    )
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMMalformedConversationHistoryError)


def test_map_typed_content_policy_violation():
    e = ContentPolicyViolationError(
        "Output blocked by content filtering policy", MODEL, PROVIDER
    )
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMContentPolicyViolationError)
    # Subtype of LLMBadRequestError keeps back-compat for existing handlers.
    assert isinstance(mapped, LLMBadRequestError)


def test_map_content_policy_violation_text_fallback():
    # Proxied/aliased providers may flatten the typed class to a plain 400.
    e = BadRequestError(
        "AnthropicException - Output blocked by content filtering policy",
        MODEL,
        PROVIDER,
    )
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMContentPolicyViolationError)


def test_map_generic_bad_request():
    e = BadRequestError("Some client-side error not related to auth", MODEL, PROVIDER)
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMBadRequestError)
    # A generic 400 must NOT be misclassified as content-policy.
    assert not isinstance(mapped, LLMContentPolicyViolationError)


def test_passthrough_unknown_exception():
    class MyCustom(Exception):
        pass

    e = MyCustom("random")
    mapped = map_provider_exception(e)
    assert mapped is e


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "LLMAuthenticationError"),
        (403, "LLMAuthenticationError"),
        (404, "LLMNotFoundError"),
        (408, "LLMTimeoutError"),
        (409, "LLMServiceUnavailableError"),
        (418, "LLMBadRequestError"),
        (429, "LLMRateLimitError"),
        (503, "LLMServiceUnavailableError"),
        (504, "LLMTimeoutError"),
    ],
)
def test_generic_provider_status_maps_without_exposing_payload(status, code):
    error = APIError(status, "private-provider-payload", PROVIDER, MODEL)

    mapped = map_provider_exception(error)

    assert isinstance(mapped, LLMError)
    assert type(mapped).__name__ == code
    assert "private-provider-payload" not in str(mapped)


def test_model_not_found_has_safe_actionable_message():
    error = NotFoundError("private-provider-payload", MODEL, PROVIDER)

    mapped = map_provider_exception(error)

    assert isinstance(mapped, LLMBadRequestError)
    assert type(mapped).__name__ == "LLMNotFoundError"
    assert "private-provider-payload" not in str(mapped)
    assert "model" in str(mapped).lower()


def test_unknown_provider_error_has_stable_safe_fallback():
    error = OpenAIError("private-provider-payload")

    mapped = map_provider_exception(error)

    assert isinstance(mapped, LLMError)
    assert type(mapped).__name__ == "LLMProviderError"
    assert "private-provider-payload" not in str(mapped)


def test_generic_provider_status_wins_over_misleading_message():
    error = APIError(404, "invalid api key; prompt is too long", PROVIDER, MODEL)

    assert type(map_provider_exception(error)).__name__ == "LLMNotFoundError"


def test_http_status_error_uses_response_status():
    response = httpx.Response(
        404, request=httpx.Request("POST", "https://example.test")
    )
    error = httpx.HTTPStatusError(
        "private-provider-payload", request=response.request, response=response
    )

    mapped = map_provider_exception(error)

    assert isinstance(mapped, LLMBadRequestError)
    assert type(mapped).__name__ == "LLMNotFoundError"
    assert "private-provider-payload" not in str(mapped)
