"""Tests for the refresh-on-401 mitigation on :class:`LLM`.

When a completion/responses call fails with an authentication error (HTTP 401,
e.g. a rotated/healed managed proxy key that LiteLLM reports as
``token_not_found_in_db``) and an API-key refresh hook is registered, the LLM
re-resolves the key once and retries the call a single time. See
OpenHands/software-agent-sdk#5189.
"""

from unittest.mock import patch

import pytest
from litellm.exceptions import AuthenticationError
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage
from pydantic import SecretStr

from openhands.sdk.llm import LLM, LLMResponse, Message, TextContent
from openhands.sdk.llm.exceptions import LLMAuthenticationError


def create_mock_response(content: str = "Test response", response_id: str = "test-id"):
    return ModelResponse(
        id=response_id,
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(content=content, role="assistant"),
            )
        ],
        created=1234567890,
        model="gpt-4o",
        object="chat.completion",
        system_fingerprint="test",
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _auth_error() -> AuthenticationError:
    return AuthenticationError(
        message=(
            "Invalid proxy server token passed. Unable to find token in cache or "
            "LiteLLM_VerificationTokenTable (token_not_found_in_db)"
        ),
        llm_provider="openhands",
        model="gpt-4o",
    )


def _make_llm() -> LLM:
    return LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("stale_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )


def _message() -> list[Message]:
    return [Message(role="user", content=[TextContent(text="Hello!")])]


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_completion_refreshes_key_and_retries_once(mock_litellm_completion):
    """A 401 with a refresh hook re-resolves the key and retries once."""
    mock_litellm_completion.side_effect = [
        _auth_error(),
        create_mock_response("Recovered"),
    ]
    calls = {"count": 0}

    def refresh() -> str:
        calls["count"] += 1
        return "fresh_key"

    llm = _make_llm()
    llm.set_api_key_refresh_hook(refresh)

    response = llm.completion(messages=_message())

    assert isinstance(response, LLMResponse)
    assert mock_litellm_completion.call_count == 2  # initial + one refreshed retry
    assert calls["count"] == 1  # hook invoked exactly once
    # The retried call used the freshly-resolved key, not the stale one.
    assert mock_litellm_completion.call_args_list[1].kwargs["api_key"] == "fresh_key"


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_completion_no_refresh_without_hook(mock_litellm_completion):
    """Without a hook a 401 surfaces immediately with no retry."""
    mock_litellm_completion.side_effect = _auth_error()

    llm = _make_llm()

    with pytest.raises(LLMAuthenticationError):
        llm.completion(messages=_message())
    assert mock_litellm_completion.call_count == 1


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_completion_refresh_retries_only_once(mock_litellm_completion):
    """If the refreshed key still 401s, the error surfaces (no infinite loop)."""
    mock_litellm_completion.side_effect = [_auth_error(), _auth_error()]
    calls = {"count": 0}

    def refresh() -> str:
        calls["count"] += 1
        return f"fresh_key_{calls['count']}"

    llm = _make_llm()
    llm.set_api_key_refresh_hook(refresh)

    with pytest.raises(LLMAuthenticationError):
        llm.completion(messages=_message())
    assert mock_litellm_completion.call_count == 2  # initial + one retry, then stop
    assert calls["count"] == 1  # only refreshed once


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_completion_no_retry_when_hook_returns_none(mock_litellm_completion):
    """A hook that yields no key does not trigger a retry."""
    mock_litellm_completion.side_effect = _auth_error()

    llm = _make_llm()
    llm.set_api_key_refresh_hook(lambda: None)

    with pytest.raises(LLMAuthenticationError):
        llm.completion(messages=_message())
    assert mock_litellm_completion.call_count == 1


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_completion_no_retry_when_hook_returns_same_key(mock_litellm_completion):
    """A hook that returns the already-rejected key does not retry."""
    mock_litellm_completion.side_effect = _auth_error()

    llm = _make_llm()
    llm.set_api_key_refresh_hook(lambda: "stale_key")

    with pytest.raises(LLMAuthenticationError):
        llm.completion(messages=_message())
    assert mock_litellm_completion.call_count == 1


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_hook_not_called_for_non_auth_error(mock_litellm_completion):
    """Non-auth errors never invoke the refresh hook."""
    from litellm.exceptions import APIConnectionError

    mock_litellm_completion.side_effect = APIConnectionError(
        message="API connection error",
        llm_provider="test_provider",
        model="test_model",
    )
    calls = {"count": 0}

    def refresh() -> str:
        calls["count"] += 1
        return "fresh_key"

    llm = _make_llm()
    llm.set_api_key_refresh_hook(refresh)

    with pytest.raises(Exception):
        llm.completion(messages=_message())
    assert calls["count"] == 0  # hook untouched for connection errors


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_refresh_hook_not_serialized(mock_litellm_completion):
    """The hook is a private attr and must not leak into serialized state."""
    llm = _make_llm()
    llm.set_api_key_refresh_hook(lambda: "fresh_key")
    dumped = llm.model_dump()
    assert "_api_key_refresh_hook" not in dumped
    assert "api_key_refresh_hook" not in dumped


@pytest.mark.asyncio
@patch("openhands.sdk.llm.llm.litellm_acompletion")
async def test_acompletion_refreshes_key_and_retries_once(mock_litellm_acompletion):
    """Async path also refreshes the key and retries once on a 401."""
    mock_litellm_acompletion.side_effect = [
        _auth_error(),
        create_mock_response("Recovered"),
    ]
    calls = {"count": 0}

    def refresh() -> str:
        calls["count"] += 1
        return "fresh_key"

    llm = _make_llm()
    llm.set_api_key_refresh_hook(refresh)

    response = await llm.acompletion(messages=_message())

    assert isinstance(response, LLMResponse)
    assert mock_litellm_acompletion.call_count == 2
    assert calls["count"] == 1
    assert mock_litellm_acompletion.call_args_list[1].kwargs["api_key"] == "fresh_key"
