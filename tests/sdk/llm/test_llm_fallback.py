from unittest.mock import patch

import pytest
from litellm.exceptions import (
    APIConnectionError,
    RateLimitError,
)
from litellm.types.utils import (
    Choices,
    Message as LiteLLMMessage,
    ModelResponse,
    Usage,
)
from pydantic import SecretStr

from openhands.sdk.llm import LLM, FallbackStrategy, Message, TextContent
from openhands.sdk.llm.exceptions import LLMServiceUnavailableError


def _get_mock_response(content: str = "ok", model: str = "gpt-4o") -> ModelResponse:
    return ModelResponse(
        id="resp-1",
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(content=content, role="assistant"),
            )
        ],
        created=1,
        model=model,
        object="chat.completion",
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _get_llm(model: str = "gpt-4o", **kw) -> LLM:
    return LLM(
        model=model,
        api_key=SecretStr("k"),
        usage_id=f"test-{model}",
        num_retries=0,
        retry_min_wait=0,
        retry_max_wait=0,
        **kw,
    )


_MSGS = [Message(role="user", content=[TextContent(text="hi")])]


def _patch_resolve(primary: LLM, fallback_instances: list[LLM]):
    """Pre-populate the resolved fallback cache, bypassing LLMProfileStore."""
    assert primary.fallback_strategy is not None
    primary.fallback_strategy._resolved = fallback_instances


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_primary_succeeds_fallback_not_tried(mock_comp):
    mock_comp.return_value = _get_mock_response("primary ok")

    fb = _get_llm("fallback-model")
    strategy = FallbackStrategy(fallback_llms=["fallback-profile"])
    primary = _get_llm("gpt-4o", fallback_strategy=strategy)
    _patch_resolve(primary, [fb])

    resp = primary.completion(_MSGS)
    content = resp.message.content[0]
    assert isinstance(content, TextContent)
    assert content.text == "primary ok"
    # Only one call – no fallback attempted
    assert mock_comp.call_count == 1


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_fallback_succeeds_after_primary_transient_failure(mock_comp):
    primary_error = APIConnectionError(
        message="connection reset", llm_provider="openai", model="gpt-4o"
    )

    def side_effect(**kwargs):
        if kwargs.get("model") == "gpt-4o":
            raise primary_error
        return _get_mock_response("fallback ok", model="fallback-model")

    mock_comp.side_effect = side_effect

    fb = _get_llm("fallback-model")
    strategy = FallbackStrategy(fallback_llms=["fallback-profile"])
    primary = _get_llm("gpt-4o", fallback_strategy=strategy)
    _patch_resolve(primary, [fb])

    resp = primary.completion(_MSGS)
    content = resp.message.content[0]
    assert isinstance(content, TextContent)
    assert content.text == "fallback ok"


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_all_fallbacks_fail_raises_primary_error(mock_comp):
    mock_comp.side_effect = APIConnectionError(
        message="down", llm_provider="openai", model="gpt-4o"
    )

    fb1 = _get_llm("fb1")
    fb2 = _get_llm("fb2")
    strategy = FallbackStrategy(fallback_llms=["fb1-profile", "fb2-profile"])
    primary = _get_llm("gpt-4o", fallback_strategy=strategy)
    _patch_resolve(primary, [fb1, fb2])

    # APIConnectionError is mapped to
    # LLMServiceUnavailableError by map_provider_exception
    with pytest.raises(LLMServiceUnavailableError):
        _ = primary.completion(_MSGS)


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_non_transient_error_skips_fallback(mock_comp):
    """A plain Exception is NOT in LLM_FALLBACK_EXCEPTIONS, so fallback
    should be skipped."""
    mock_comp.side_effect = Exception("bad request")

    fb = _get_llm("fb")
    strategy = FallbackStrategy(fallback_llms=["fb-profile"])
    primary = _get_llm("gpt-4o", fallback_strategy=strategy)
    _patch_resolve(primary, [fb])

    with pytest.raises(Exception, match="bad request"):
        _ = primary.completion(_MSGS)

    # Only the primary call – fallback never attempted
    assert mock_comp.call_count == 1


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_no_fallbacks_configured_normal_error(mock_comp):
    mock_comp.side_effect = APIConnectionError(
        message="down", llm_provider="openai", model="gpt-4o"
    )

    primary = _get_llm("gpt-4o")  # no fallback_strategy
    # APIConnectionError is mapped to
    # LLMServiceUnavailableError by map_provider_exception
    with pytest.raises(LLMServiceUnavailableError):
        _ = primary.completion(_MSGS)


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_metrics_merged_from_fallback(mock_comp):
    primary_error = RateLimitError(
        message="rate limited", llm_provider="openai", model="gpt-4o"
    )

    def side_effect(**kwargs):
        if kwargs.get("model") == "gpt-4o":
            raise primary_error
        return _get_mock_response("ok", model="fb")

    mock_comp.side_effect = side_effect

    fb = _get_llm("fb")
    strategy = FallbackStrategy(fallback_llms=["fb-profile"])
    primary = _get_llm("gpt-4o", fallback_strategy=strategy)
    _patch_resolve(primary, [fb])

    cost_before = primary.metrics.accumulated_cost
    token_usages_before = len(primary.metrics.token_usages)
    resp = primary.completion(_MSGS)

    content = resp.message.content[0]
    assert isinstance(content, TextContent)
    assert content.text == "ok"
    # The fallback's telemetry adds cost/tokens; verify they got merged
    # into the primary's metrics (accumulated_cost should be >= what it was).
    assert primary.metrics.accumulated_cost >= cost_before

    # Individual token_usage records carry the fallback model name,
    # so callers can distinguish which LLM produced the usage.
    new_usages = primary.metrics.token_usages[token_usages_before:]
    assert len(new_usages) >= 1
    assert any(u.model == "fb" for u in new_usages), (
        "Expected at least one token usage record from the fallback model 'fb'"
    )


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_second_fallback_succeeds(mock_comp):
    # Second fallback succeeds after first fallback fails
    call_count = {"n": 0}

    def side_effect(**kwargs):
        call_count["n"] += 1
        model = kwargs.get("model")
        if model in ("gpt-4o", "fb1"):
            raise APIConnectionError(message="down", llm_provider="openai", model=model)
        return _get_mock_response("fb2 ok", model="fb2")

    mock_comp.side_effect = side_effect

    fb1 = _get_llm("fb1")
    fb2 = _get_llm("fb2")
    strategy = FallbackStrategy(fallback_llms=["fb1-profile", "fb2-profile"])
    primary = _get_llm("gpt-4o", fallback_strategy=strategy)
    _patch_resolve(primary, [fb1, fb2])

    resp = primary.completion(_MSGS)
    content = resp.message.content[0]
    assert isinstance(content, TextContent)
    assert content.text == "fb2 ok"
    # primary(1) + fb1(1) + fb2(1) = 3
    assert call_count["n"] == 3


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_responses_fallback_succeeds(mock_resp):
    """Ensure fallback works through the responses() code path too."""
    from litellm.types.llms.openai import ResponsesAPIResponse

    primary_error = APIConnectionError(
        message="down", llm_provider="openai", model="gpt-4o"
    )

    # Build a minimal ResponsesAPIResponse for the fallback
    fallback_response = ResponsesAPIResponse(
        id="resp-fb",
        created_at=1,
        model="fb",
        object="response",
        output=[
            {
                "type": "message",
                "id": "msg-1",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "fb ok", "annotations": []}
                ],
            }
        ],
        parallel_tool_calls=False,
        tool_choice="auto",
        tools=[],
    )

    def side_effect(**kwargs):
        if kwargs.get("model") == "gpt-4o":
            raise primary_error
        return fallback_response

    mock_resp.side_effect = side_effect

    fb = _get_llm("fb")
    strategy = FallbackStrategy(fallback_llms=["fb-profile"])
    primary = _get_llm("gpt-4o", fallback_strategy=strategy)
    _patch_resolve(primary, [fb])

    resp = primary.responses(_MSGS)
    content = resp.message.content[0]
    assert isinstance(content, TextContent)
    assert content.text == "fb ok"


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_responses_non_transient_skips_fallback(mock_resp):
    mock_resp.side_effect = Exception("not transient")

    fb = _get_llm("fb")
    strategy = FallbackStrategy(fallback_llms=["fb-profile"])
    primary = _get_llm("gpt-4o", fallback_strategy=strategy)
    _patch_resolve(primary, [fb])

    with pytest.raises(Exception, match="not transient"):
        primary.responses(_MSGS)

    assert mock_resp.call_count == 1


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_fallback_profiles_resolved_via_store(mock_comp, tmp_path):
    """Verify that fallback profile names are resolved through LLMProfileStore."""
    from openhands.sdk.llm.llm_profile_store import LLMProfileStore

    primary_error = APIConnectionError(
        message="down", llm_provider="openai", model="gpt-4o"
    )

    def side_effect(**kwargs):
        if kwargs.get("model") == "gpt-4o":
            raise primary_error
        return _get_mock_response("from store", model="claude-sonnet-4-20250514")

    mock_comp.side_effect = side_effect

    # Save a fallback profile to a temp store
    store = LLMProfileStore(base_dir=tmp_path)
    fb_llm = _get_llm("claude-sonnet-4-20250514")
    store.save("my-fallback", fb_llm, include_secrets=True)

    strategy = FallbackStrategy(
        fallback_llms=["my-fallback"], profile_store_dir=tmp_path
    )
    primary = _get_llm("gpt-4o", fallback_strategy=strategy)

    resp = primary.completion(_MSGS)
    content = resp.message.content[0]
    assert isinstance(content, TextContent)
    assert content.text == "from store"
