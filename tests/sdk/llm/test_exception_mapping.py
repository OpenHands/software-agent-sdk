from litellm.exceptions import BadRequestError, InternalServerError

from openhands.sdk.llm.exceptions import (
    LLMAuthenticationError,
    LLMBadRequestError,
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


def test_map_malformed_tool_args_internal_server_error():
    """InternalServerError with malformed tool args maps to malformed history."""
    e = InternalServerError(
        (
            '{"error":{"code":500,"message":"Failed to parse tool call arguments '
            'as JSON: ..."}}'
        ),
        MODEL,
        PROVIDER,
    )
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMMalformedConversationHistoryError)


def test_map_generic_bad_request():
    e = BadRequestError("Some client-side error not related to auth", MODEL, PROVIDER)
    mapped = map_provider_exception(e)
    assert isinstance(mapped, LLMBadRequestError)


def test_passthrough_unknown_exception():
    class MyCustom(Exception):
        pass

    e = MyCustom("random")
    mapped = map_provider_exception(e)
    assert mapped is e
