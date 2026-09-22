"""Tests for the refresh-on-401 mitigation on :class:`LLM`.

When a completion/responses call fails with an authentication error (HTTP 401,
e.g. a rotated/healed managed proxy key that LiteLLM reports as
``token_not_found_in_db``) and an API-key refresh hook is registered, the LLM
re-resolves the key once and retries the call a single time. See
OpenHands/software-agent-sdk#5189.
"""

from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest
from litellm.exceptions import AuthenticationError
from litellm.types.llms.openai import ResponseAPIUsage, ResponsesAPIResponse
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage
from openai.types.responses.response_output_message import ResponseOutputMessage
from openai.types.responses.response_output_text import ResponseOutputText
from pydantic import SecretStr

from openhands.sdk.llm import LLM, LLMResponse, Message, TextContent
from openhands.sdk.llm.exceptions import LLMAuthenticationError
from openhands.sdk.llm.llm import LLMCallContext


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


def create_mock_responses_response(content: str = "Recovered") -> ResponsesAPIResponse:
    """Build a minimal successful Responses API response."""
    msg = ResponseOutputMessage.model_construct(
        id="m1",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=content, annotations=[])],
    )
    usage = ResponseAPIUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    return ResponsesAPIResponse(
        id="r1",
        created_at=0,
        output=[msg],
        parallel_tool_calls=False,
        tool_choice="auto",
        top_p=None,
        tools=[],
        usage=usage,
        instructions="",
        status="completed",
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


def _make_llm(auth_type: Literal["api_key", "subscription"] = "api_key") -> LLM:
    return LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("stale_key"),
        auth_type=auth_type,
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
def test_completion_raising_hook_surfaces_original_error(mock_litellm_completion):
    """A hook that raises must not mask the original 401."""
    mock_litellm_completion.side_effect = _auth_error()

    def refresh() -> str:
        raise RuntimeError("hook boom")

    llm = _make_llm()
    llm.set_api_key_refresh_hook(refresh)

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


# ---------------------------------------------------------------------------
# Responses API paths (GPT-5 / reasoning-effort models) — the retry block is
# duplicated across all four call surfaces, so responses()/aresponses() need
# their own coverage; the completion tests above do not exercise them.
# ---------------------------------------------------------------------------


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_responses_refreshes_key_and_retries_once(mock_litellm_responses):
    """A 401 on the Responses path re-resolves the key and retries once."""
    mock_litellm_responses.side_effect = [
        _auth_error(),
        create_mock_responses_response("Recovered"),
    ]
    calls = {"count": 0}

    def refresh() -> str:
        calls["count"] += 1
        return "fresh_key"

    llm = _make_llm()
    llm.set_api_key_refresh_hook(refresh)

    response = llm.responses(messages=_message())

    assert isinstance(response, LLMResponse)
    assert mock_litellm_responses.call_count == 2  # initial + one refreshed retry
    assert calls["count"] == 1  # hook invoked exactly once
    # The retried call used the freshly-resolved key, not the stale one.
    assert mock_litellm_responses.call_args_list[1].kwargs["api_key"] == "fresh_key"


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_responses_refresh_retries_only_once(mock_litellm_responses):
    """If the refreshed key still 401s, the Responses path stops after one retry."""
    mock_litellm_responses.side_effect = [_auth_error(), _auth_error()]
    calls = {"count": 0}

    def refresh() -> str:
        calls["count"] += 1
        return f"fresh_key_{calls['count']}"

    llm = _make_llm()
    llm.set_api_key_refresh_hook(refresh)

    with pytest.raises(LLMAuthenticationError):
        llm.responses(messages=_message())
    assert mock_litellm_responses.call_count == 2  # initial + one retry, then stop
    assert calls["count"] == 1  # only refreshed once


@pytest.mark.asyncio
@patch("openhands.sdk.llm.llm.litellm_aresponses", new_callable=AsyncMock)
async def test_aresponses_refreshes_key_and_retries_once(mock_litellm_aresponses):
    """Async Responses path also refreshes the key and retries once on a 401."""
    mock_litellm_aresponses.side_effect = [
        _auth_error(),
        create_mock_responses_response("Recovered"),
    ]
    calls = {"count": 0}

    def refresh() -> str:
        calls["count"] += 1
        return "fresh_key"

    llm = _make_llm()
    llm.set_api_key_refresh_hook(refresh)

    response = await llm.aresponses(messages=_message())

    assert isinstance(response, LLMResponse)
    assert mock_litellm_aresponses.call_count == 2
    assert calls["count"] == 1
    assert mock_litellm_aresponses.call_args_list[1].kwargs["api_key"] == "fresh_key"


@pytest.mark.asyncio
@patch("openhands.sdk.llm.llm.litellm_aresponses", new_callable=AsyncMock)
async def test_aresponses_refresh_retries_only_once(mock_litellm_aresponses):
    """Async Responses path stops after a single refreshed retry on repeat 401s."""
    mock_litellm_aresponses.side_effect = [_auth_error(), _auth_error()]
    calls = {"count": 0}

    def refresh() -> str:
        calls["count"] += 1
        return f"fresh_key_{calls['count']}"

    llm = _make_llm()
    llm.set_api_key_refresh_hook(refresh)

    with pytest.raises(LLMAuthenticationError):
        await llm.aresponses(messages=_message())
    assert mock_litellm_aresponses.call_count == 2
    assert calls["count"] == 1


# ---------------------------------------------------------------------------
# Resolver guards — subscription auth and empty-key edges.
# ---------------------------------------------------------------------------


def test_non_api_key_auth_skips_refresh():
    """Subscription-mode LLMs must never let an api-key hook clobber the key."""
    llm = _make_llm(auth_type="subscription")
    llm.set_api_key_refresh_hook(lambda: "fresh_key")
    assert llm._resolve_refreshed_api_key(_auth_error()) is None


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_completion_no_retry_when_hook_returns_empty_key(mock_litellm_completion):
    """A hook returning an empty string does not trigger a (credential-less) retry."""
    mock_litellm_completion.side_effect = _auth_error()

    llm = _make_llm()
    llm.set_api_key_refresh_hook(lambda: "")

    with pytest.raises(LLMAuthenticationError):
        llm.completion(messages=_message())
    assert mock_litellm_completion.call_count == 1


# ---------------------------------------------------------------------------
# call_context parity — the refreshed retry re-issues the *same* logical call,
# so it must carry the same threaded per-conversation context (prompt_cache_key
# / x-litellm-session-id) as the initial attempt. The async paths previously
# dropped the threaded context and fell back to the LLM's bound _call_context,
# diverging from the sync paths (#3443). See OpenHands/software-agent-sdk#5218.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("openhands.sdk.llm.llm.litellm_acompletion")
async def test_acompletion_retry_preserves_threaded_call_context(
    mock_litellm_acompletion,
):
    """The async completion retry re-issues under the threaded call_context."""
    mock_litellm_acompletion.side_effect = [
        _auth_error(),
        create_mock_response("Recovered"),
    ]

    llm = _make_llm()  # bound _call_context is the empty default
    llm.set_api_key_refresh_hook(lambda: "fresh_key")

    ctx = LLMCallContext(prompt_cache_key="threaded-pck", session_id="threaded-sid")
    await llm.acompletion(messages=_message(), call_context=ctx)

    assert mock_litellm_acompletion.call_count == 2
    initial_kwargs = mock_litellm_acompletion.call_args_list[0].kwargs
    retry_kwargs = mock_litellm_acompletion.call_args_list[1].kwargs
    # The retry must use the fresh key AND the same threaded context as attempt 0.
    assert retry_kwargs["api_key"] == "fresh_key"
    assert retry_kwargs["prompt_cache_key"] == initial_kwargs["prompt_cache_key"]
    assert retry_kwargs["prompt_cache_key"] == "threaded-pck"
    assert (
        retry_kwargs["extra_headers"]["x-litellm-session-id"]
        == initial_kwargs["extra_headers"]["x-litellm-session-id"]
        == "threaded-sid"
    )


@pytest.mark.asyncio
@patch("openhands.sdk.llm.llm.litellm_aresponses", new_callable=AsyncMock)
async def test_aresponses_retry_preserves_threaded_call_context(
    mock_litellm_aresponses,
):
    """The async responses retry re-issues under the threaded call_context."""
    mock_litellm_aresponses.side_effect = [
        _auth_error(),
        create_mock_responses_response("Recovered"),
    ]

    llm = _make_llm()  # bound _call_context is the empty default
    llm.set_api_key_refresh_hook(lambda: "fresh_key")

    ctx = LLMCallContext(prompt_cache_key="threaded-pck", session_id="threaded-sid")
    await llm.aresponses(messages=_message(), call_context=ctx)

    assert mock_litellm_aresponses.call_count == 2
    initial_kwargs = mock_litellm_aresponses.call_args_list[0].kwargs
    retry_kwargs = mock_litellm_aresponses.call_args_list[1].kwargs
    assert retry_kwargs["api_key"] == "fresh_key"
    assert retry_kwargs["prompt_cache_key"] == initial_kwargs["prompt_cache_key"]
    assert retry_kwargs["prompt_cache_key"] == "threaded-pck"
    assert (
        retry_kwargs["extra_headers"]["x-litellm-session-id"]
        == initial_kwargs["extra_headers"]["x-litellm-session-id"]
        == "threaded-sid"
    )
