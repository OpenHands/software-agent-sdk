def test_llm_malformed_action_error_default():
    """Test LLMMalformedActionError with default message."""
    from openhands.sdk.llm.exceptions import LLMMalformedActionError

    error = LLMMalformedActionError()
    assert str(error) == "Malformed response"
    assert error.message == "Malformed response"


def test_llm_malformed_action_error_custom():
    """Test LLMMalformedActionError with custom message."""
    from openhands.sdk.llm.exceptions import LLMMalformedActionError

    custom_message = "Custom malformed error"
    error = LLMMalformedActionError(custom_message)
    assert str(error) == custom_message
    assert error.message == custom_message


def test_llm_no_action_error_default():
    """Test LLMNoActionError with default message."""
    from openhands.sdk.llm.exceptions import LLMNoActionError

    error = LLMNoActionError()
    assert str(error) == "Agent must return an action"
    assert error.message == "Agent must return an action"


def test_llm_no_action_error_custom():
    """Test LLMNoActionError with custom message."""
    from openhands.sdk.llm.exceptions import LLMNoActionError

    custom_message = "Custom no action error"
    error = LLMNoActionError(custom_message)
    assert str(error) == custom_message
    assert error.message == custom_message


def test_llm_response_error_default():
    """Test LLMResponseError with default message."""
    from openhands.sdk.llm.exceptions import LLMResponseError

    error = LLMResponseError()
    assert str(error) == "Failed to retrieve action from LLM response"
    assert error.message == "Failed to retrieve action from LLM response"


def test_llm_response_error_custom():
    """Test LLMResponseError with custom message."""
    from openhands.sdk.llm.exceptions import LLMResponseError

    custom_message = "Custom response error"
    error = LLMResponseError(custom_message)
    assert str(error) == custom_message
    assert error.message == custom_message


def test_llm_context_window_exceed_error_default():
    """Test LLMContextWindowExceedError with default message."""
    from openhands.sdk.llm.exceptions import LLMContextWindowExceedError

    error = LLMContextWindowExceedError()
    expected_message = "Conversation history longer than LLM context window limit. "
    expected_message += "Consider enabling a condenser or shortening inputs."
    assert str(error) == expected_message
    assert error.message == expected_message


def test_llm_context_window_exceed_error_custom():
    """Test LLMContextWindowExceedError with custom message."""
    from openhands.sdk.llm.exceptions import LLMContextWindowExceedError

    custom_message = "Custom context window error"
    error = LLMContextWindowExceedError(custom_message)
    assert str(error) == custom_message
    assert error.message == custom_message


def test_llm_malformed_conversation_history_error_default():
    """Test LLMMalformedConversationHistoryError with default message."""
    from openhands.sdk.llm.exceptions import LLMMalformedConversationHistoryError

    error = LLMMalformedConversationHistoryError()
    expected_message = "Conversation history produced an invalid LLM request. "
    expected_message += (
        "Consider retrying with condensed history and investigating the event stream."
    )
    assert str(error) == expected_message
    assert error.message == expected_message


def test_llm_malformed_conversation_history_error_custom():
    """Test LLMMalformedConversationHistoryError with custom message."""
    from openhands.sdk.llm.exceptions import LLMMalformedConversationHistoryError

    custom_message = "Custom malformed history error"
    error = LLMMalformedConversationHistoryError(custom_message)
    assert str(error) == custom_message
    assert error.message == custom_message


def test_function_call_not_exists_error():
    """Test FunctionCallNotExistsError."""
    from openhands.sdk.llm.exceptions import FunctionCallNotExistsError

    message = "Function 'unknown_function' does not exist"
    error = FunctionCallNotExistsError(message)
    assert str(error) == message
    assert error.message == message


def test_user_cancelled_error_default():
    """Test UserCancelledError with default message."""
    from openhands.sdk.llm.exceptions import UserCancelledError

    error = UserCancelledError()
    assert str(error) == "User cancelled the request"


def test_user_cancelled_error_custom():
    """Test UserCancelledError with custom message."""
    from openhands.sdk.llm.exceptions import UserCancelledError

    custom_message = "Custom cancellation message"
    error = UserCancelledError(custom_message)
    assert str(error) == custom_message


def test_operation_cancelled_error_default():
    """Test OperationCancelled with default message."""
    from openhands.sdk.llm.exceptions import OperationCancelled

    error = OperationCancelled()
    assert str(error) == "Operation was cancelled"


def test_operation_cancelled_error_custom():
    """Test OperationCancelled with custom message."""
    from openhands.sdk.llm.exceptions import OperationCancelled

    custom_message = "Custom operation cancelled message"
    error = OperationCancelled(custom_message)
    assert str(error) == custom_message


def test_sdk_exception_types_declare_retry_metadata():
    """SDK exception types declare retry_attempt and max_retries explicitly."""
    from openhands.sdk.llm.exceptions import LLMError, SupportsRetryMetadata

    err = LLMError("base error")
    assert err.retry_attempt is None
    assert err.max_retries is None
    assert isinstance(err, SupportsRetryMetadata)

    err_with_meta = LLMError("with meta", retry_attempt=2, max_retries=5)
    assert err_with_meta.retry_attempt == 2
    assert err_with_meta.max_retries == 5
    assert str(err_with_meta) == "with meta"


def test_sdk_exception_subclasses_support_retry_metadata():
    """All LLMError subclasses declare and accept retry metadata."""
    from openhands.sdk.llm.exceptions import (
        LLMBadRequestError,
        LLMNoResponseError,
        LLMRateLimitError,
        LLMServiceUnavailableError,
        SupportsRetryMetadata,
    )

    subclasses = [
        LLMNoResponseError("no response", retry_attempt=1, max_retries=3),
        LLMRateLimitError("rate limit", retry_attempt=2, max_retries=4),
        LLMServiceUnavailableError("unavailable", retry_attempt=3, max_retries=5),
        LLMBadRequestError("bad request", retry_attempt=1, max_retries=1),
    ]
    for exc in subclasses:
        assert isinstance(exc, SupportsRetryMetadata)
        assert exc.retry_attempt is not None
        assert exc.max_retries is not None

        # Static field reassignment without setattr
        exc.retry_attempt = 10
        exc.max_retries = 20
        assert exc.retry_attempt == 10
        assert exc.max_retries == 20
