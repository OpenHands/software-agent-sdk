from litellm.exceptions import BadRequestError, ContextWindowExceededError

from openhands.sdk.llm.exceptions import (
    is_context_window_exceeded,
    looks_like_auth_error,
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
    e = BadRequestError(
        "The request exceeds the available context size", MODEL, PROVIDER
    )
    assert is_context_window_exceeded(e) is True


def test_is_context_window_exceeded_negative():
    assert (
        is_context_window_exceeded(BadRequestError("irrelevant", MODEL, PROVIDER))
        is False
    )


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
