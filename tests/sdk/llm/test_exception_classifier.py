import pytest
from litellm.exceptions import (
    APIConnectionError,
    BadRequestError,
    ContextWindowExceededError,
    InternalServerError,
    RateLimitError,
)

from openhands.sdk.llm.exceptions import (
    is_context_window_exceeded,
    is_prompt_cache_too_small,
    is_quota_exhaustion_error,
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


def test_is_context_window_exceeded_llama_cpp_token_counts():
    error = BadRequestError(
        (
            "OpenAIException - request (138229 tokens) exceeds the available "
            "context size (133376 tokens), try increasing it"
        ),
        MODEL,
        PROVIDER,
    )

    assert is_context_window_exceeded(error) is True


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


def test_looks_like_malformed_conversation_history_error_moonshot():
    error = BadRequestError(
        (
            "an assistant message with 'tool_calls' must be followed by tool "
            "messages responding to each 'tool_call_id'"
        ),
        MODEL,
        PROVIDER,
    )

    assert looks_like_malformed_conversation_history_error(error) is True
    assert is_context_window_exceeded(error) is False


def test_looks_like_malformed_conversation_history_error_orphan_tool_message():
    """DeepSeek's and OpenAI's wording for a tool result without its call."""
    error = BadRequestError(
        (
            'DeepseekException - {"error":{"message":"Messages with role \'tool\' '
            "must be a response to a preceding message with 'tool_calls' "
            '(request_id: ddd8f0aa)","type":"invalid_request_error"}}'
        ),
        MODEL,
        PROVIDER,
    )

    assert looks_like_malformed_conversation_history_error(error) is True
    assert is_context_window_exceeded(error) is False


def test_looks_like_malformed_conversation_history_error_openai_tool_json_parse():
    error = InternalServerError(
        (
            "OpenAIException - Failed to parse tool call arguments as JSON: "
            "[json.exception.parse_error.101] parse error at line 1, column 113"
        ),
        PROVIDER,
        MODEL,
    )

    assert looks_like_malformed_conversation_history_error(error) is True
    assert is_context_window_exceeded(error) is False


def test_looks_like_malformed_conversation_history_error_anthropic_first_sentence():
    error = BadRequestError(
        (
            "messages.134: `tool_use` ids were found without `tool_result` "
            "blocks immediately after: toolu_01Aye4s5HrR2uXwXFYgtQi4H."
        ),
        MODEL,
        PROVIDER,
    )

    assert looks_like_malformed_conversation_history_error(error) is True
    assert is_context_window_exceeded(error) is False


def test_is_context_window_exceeded_negative():
    assert (
        is_context_window_exceeded(BadRequestError("irrelevant", MODEL, PROVIDER))
        is False
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        pytest.param("Invalid API key", True, id="positive"),
        pytest.param("Something else", False, id="negative"),
    ],
)
def test_looks_like_auth_error(message: str, expected: bool):
    assert looks_like_auth_error(BadRequestError(message, MODEL, PROVIDER)) is expected


def test_is_prompt_cache_too_small_positive():
    """Vertex AI rejects caching when cached content is below minimum token count."""
    vertex_error = BadRequestError(
        (
            "Vertex_aiException BadRequestError - "
            '{"error":{"code":400,'
            '"message":"The cached content is of 1171 tokens. '
            'The minimum token count to start caching is 4096.",'
            '"status":"INVALID_ARGUMENT"}}'
        ),
        MODEL,
        PROVIDER,
    )
    assert is_prompt_cache_too_small(vertex_error) is True


def test_is_prompt_cache_too_small_negative():
    assert (
        is_prompt_cache_too_small(BadRequestError("irrelevant", MODEL, PROVIDER))
        is False
    )


def test_is_prompt_cache_too_small_context_window_not_cache_too_small():
    """Context window exceeded is a different error from cache too small."""
    ctx_error = BadRequestError(
        "The request exceeds the available context size", MODEL, PROVIDER
    )
    assert is_prompt_cache_too_small(ctx_error) is False
    assert is_context_window_exceeded(ctx_error) is True


@pytest.mark.parametrize(
    ("message", "llm_provider", "model", "expected"),
    [
        # OpenAI's usage_limit_reached is a hard quota outcome, not a transient 429.
        pytest.param(
            (
                "RateLimitError: OpenAIException - "
                '{"error":{"type":"usage_limit_reached",'
                '"message":"The usage limit has been reached","plan_type":"team"}}'
            ),
            "openai",
            "gpt-5.6-sol",
            True,
            id="usage_limit_reached",
        ),
        pytest.param(
            (
                "RateLimitError: OpenAIException - "
                '{"error":{"type":"insufficient_quota",'
                '"message":"You exceeded your current quota"}}'
            ),
            "openai",
            "gpt-5.6-sol",
            True,
            id="insufficient_quota",
        ),
        pytest.param(
            "RateLimitError: Rate limit exceeded",
            "openai",
            "gpt-5.6-sol",
            False,
            id="transient_rate_limit",
        ),
        pytest.param(
            "Quota exceeded for quota metric Generate Content API requests per minute",
            "vertex_ai",
            "gemini-test",
            False,
            id="transient_quota_metric",
        ),
    ],
)
def test_is_quota_exhaustion_error(
    message: str, llm_provider: str, model: str, expected: bool
):
    error = RateLimitError(message=message, llm_provider=llm_provider, model=model)
    assert is_quota_exhaustion_error(error) is expected
