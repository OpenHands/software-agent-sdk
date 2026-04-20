from litellm.exceptions import (
    APIConnectionError,
    BadRequestError,
    ContextWindowExceededError,
    InternalServerError,
)

from openhands.sdk.llm.exceptions import (
    is_context_window_exceeded,
    looks_like_auth_error,
    looks_like_malformed_conversation_history_error,
)


MODEL = "test-model"
PROVIDER = "test-provider"


def test_is_context_window_exceeded_direct_type():
    assert (
        is_context_window_exceeded(ContextWindowExceededError("boom", MODEL, PROVIDER))
        is True
    )


def test_is_context_window_exceeded_via_text():
    # BadRequest containing context-window-ish text should be detected
    e1 = BadRequestError(
        "The request exceeds the available context size", MODEL, PROVIDER
    )
    e2 = BadRequestError(
        (
            "Your input exceeds the context window of this model. "
            "Please adjust your input and try again."
        ),
        MODEL,
        PROVIDER,
    )
    assert is_context_window_exceeded(e1) is True
    assert is_context_window_exceeded(e2) is True


def test_is_context_window_exceeded_minimax_api_connection_error():
    """Minimax provider wraps context window errors in APIConnectionError."""
    minimax_error = APIConnectionError(
        message=(
            'MinimaxException - {"type":"error","error":{"type":"bad_request_error",'
            '"message":"invalid params, context window exceeds limit (2013)"}}'
        ),
        model=MODEL,
        llm_provider=PROVIDER,
    )
    assert is_context_window_exceeded(minimax_error) is True


def test_looks_like_malformed_conversation_history_error_positive():
    malformed_history_error = BadRequestError(
        (
            'AnthropicException - {"type":"error","error":{'
            '"type":"invalid_request_error","message":'
            '"messages.134: `tool_use` ids were found without `tool_result` '
            "blocks immediately after: toolu_01Aye4s5HrR2uXwXFYgtQi4H. Each "
            "`tool_use` block must have a corresponding `tool_result` "
            'block in the next message."}}'
        ),
        MODEL,
        PROVIDER,
    )

    assert (
        looks_like_malformed_conversation_history_error(malformed_history_error) is True
    )
    assert is_context_window_exceeded(malformed_history_error) is False


def test_is_context_window_exceeded_negative():
    assert (
        is_context_window_exceeded(BadRequestError("irrelevant", MODEL, PROVIDER))
        is False
    )


def test_looks_like_malformed_history_from_internal_server_error():
    """llama-server returns 500 when tool call arguments are malformed JSON (#2887)."""
    e = InternalServerError(
        (
            '{"error":{"code":500,"message":"Failed to parse tool call arguments '
            'as JSON: ..."}}'
        ),
        MODEL,
        PROVIDER,
    )
    assert looks_like_malformed_conversation_history_error(e) is True
    assert is_context_window_exceeded(e) is False


def test_looks_like_auth_error_positive():
    assert (
        looks_like_auth_error(BadRequestError("Invalid API key", MODEL, PROVIDER))
        is True
    )


def test_looks_like_auth_error_negative():
    assert (
        looks_like_auth_error(BadRequestError("Something else", MODEL, PROVIDER))
        is False
    )
