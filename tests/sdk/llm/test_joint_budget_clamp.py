"""Tests for the joint input/output token budget clamp.

Some providers (notably AWS Bedrock for Anthropic models) enforce
``input_tokens + max_tokens <= context_window``. After BerriAI/litellm#17900
litellm's default for ``max_tokens`` became the model's full
``max_output_tokens`` (e.g. 64k for Sonnet 4.5). For Bedrock this means any
input above roughly ``context_window - max_output_tokens`` fails with "Input
is too long for requested model" even when the actual response would be small.

These tests cover the request-time clamp added to ``LLM._finalize_completion_params``.
"""

from __future__ import annotations

from unittest.mock import patch

from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.llm import (
    JOINT_BUDGET_MIN_OUTPUT_TOKENS,
    JOINT_BUDGET_SAFETY_MARGIN_TOKENS,
)


def _make_llm(model: str, *, max_input_tokens: int = 200_000) -> LLM:
    llm = LLM(
        usage_id="test-llm",
        model=model,
        api_key=SecretStr("test"),
        max_input_tokens=max_input_tokens,
    )
    # Stabilize across environments: skip the LiteLLM model-info lookup that
    # happens in ``_init_model_info_and_caps`` by pinning the effective window
    # directly. ``max_input_tokens=`` already feeds ``effective_max_input_tokens``.
    return llm


def test_bedrock_clamps_max_tokens_when_input_is_large():
    """Bedrock with 195k input + 64k requested output must be clamped."""
    llm = _make_llm("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    call_kwargs = {"max_completion_tokens": 64_000}

    with patch("openhands.sdk.llm.llm.token_counter", return_value=195_000):
        out = llm._clamp_max_tokens_for_joint_budget(call_kwargs, [], [])

    # Headroom = 200_000 - 195_000 - 256 = 4_744; well above the floor.
    expected = 200_000 - 195_000 - JOINT_BUDGET_SAFETY_MARGIN_TOKENS
    assert out["max_completion_tokens"] == expected
    assert out["max_completion_tokens"] < 64_000


def test_bedrock_does_not_clamp_when_input_is_small():
    """Bedrock with small input fits the full 64k output budget unchanged."""
    llm = _make_llm("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    call_kwargs = {"max_completion_tokens": 64_000}

    # 50k input + 64k output = 114k, well under the 200k window.
    with patch("openhands.sdk.llm.llm.token_counter", return_value=50_000):
        out = llm._clamp_max_tokens_for_joint_budget(call_kwargs, [], [])

    assert out["max_completion_tokens"] == 64_000
    # Same dict identity not required, but should be unchanged in content.
    assert out == call_kwargs


def test_bedrock_floor_when_input_nearly_fills_window():
    """When headroom < floor, clamp to the floor (and warn)."""
    llm = _make_llm("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    call_kwargs = {"max_completion_tokens": 64_000}

    # 199.9k input leaves only ~100 tokens of true headroom, well below floor.
    with patch("openhands.sdk.llm.llm.token_counter", return_value=199_900):
        out = llm._clamp_max_tokens_for_joint_budget(call_kwargs, [], [])

    assert out["max_completion_tokens"] == JOINT_BUDGET_MIN_OUTPUT_TOKENS


def test_anthropic_direct_is_never_clamped():
    """Anthropic direct API has independent input/output budgets -- no clamp."""
    llm = _make_llm("claude-sonnet-4-5-20250929")
    call_kwargs = {"max_completion_tokens": 64_000}

    # Even a huge input must not clamp on a non-joint provider.
    with patch("openhands.sdk.llm.llm.token_counter", return_value=195_000):
        out = llm._clamp_max_tokens_for_joint_budget(call_kwargs, [], [])

    assert out is call_kwargs
    assert out["max_completion_tokens"] == 64_000


def test_clamps_max_tokens_key_too():
    """The clamp targets whichever budget key is present (Azure / thinking use max_tokens)."""  # noqa: E501
    llm = _make_llm("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    call_kwargs = {"max_tokens": 64_000}

    with patch("openhands.sdk.llm.llm.token_counter", return_value=195_000):
        out = llm._clamp_max_tokens_for_joint_budget(call_kwargs, [], [])

    assert "max_completion_tokens" not in out
    assert out["max_tokens"] == 200_000 - 195_000 - JOINT_BUDGET_SAFETY_MARGIN_TOKENS


def test_no_clamp_when_no_budget_key_present():
    """If neither max_tokens nor max_completion_tokens is set, leave kwargs alone."""
    llm = _make_llm("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    call_kwargs = {"temperature": 0.0}

    with patch("openhands.sdk.llm.llm.token_counter", return_value=195_000):
        out = llm._clamp_max_tokens_for_joint_budget(call_kwargs, [], [])

    assert out == {"temperature": 0.0}


def test_user_supplied_smaller_budget_is_preserved():
    """If the caller already passed a budget that fits, don't change it."""
    llm = _make_llm("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    call_kwargs = {"max_completion_tokens": 2_000}

    # 195k input + user-supplied 2k output = 197k, still fits the 200k window
    # after the safety margin. Should not be clamped.
    with patch("openhands.sdk.llm.llm.token_counter", return_value=195_000):
        out = llm._clamp_max_tokens_for_joint_budget(call_kwargs, [], [])

    assert out["max_completion_tokens"] == 2_000


def test_clamp_skipped_when_token_counter_raises():
    """If counting fails, fall back to the unclamped behavior (and log)."""
    llm = _make_llm("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    call_kwargs = {"max_completion_tokens": 64_000}

    with patch(
        "openhands.sdk.llm.llm.token_counter",
        side_effect=RuntimeError("boom"),
    ):
        out = llm._clamp_max_tokens_for_joint_budget(call_kwargs, [], [])

    assert out["max_completion_tokens"] == 64_000


def test_finalize_completion_params_applies_clamp_end_to_end():
    """Reproduces the customer error path: large input on Bedrock Sonnet 4.5
    must result in a clamped budget, not the model's full 64k output."""
    llm = _make_llm("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    llm._effective_max_output_tokens = 64_000  # mirror what registry would set

    messages = [Message(role="user", content=[TextContent(text="hello")])]
    formatted = llm.format_messages_for_llm(messages)

    with patch("openhands.sdk.llm.llm.token_counter", return_value=195_000):
        (
            _formatted,
            _cc_tools,
            _use_mock_tools,
            call_kwargs,
            _telemetry,
        ) = llm._finalize_completion_params(
            formatted_messages=formatted,
            tools=None,
            add_security_risk_prediction=False,
            kwargs={},
        )

    budget = call_kwargs.get("max_tokens") or call_kwargs.get("max_completion_tokens")
    assert budget is not None
    assert budget < 64_000
    # Should have clamped to headroom (well above floor for 195k input).
    assert budget == 200_000 - 195_000 - JOINT_BUDGET_SAFETY_MARGIN_TOKENS


def test_no_clamp_when_context_window_unknown():
    """If we don't know the context window, we can't safely clamp."""
    llm = LLM(
        usage_id="test-llm",
        model="bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        api_key=SecretStr("test"),
    )
    # Force unknown window after init.
    llm._effective_max_input_tokens = None
    call_kwargs = {"max_completion_tokens": 64_000}

    with patch("openhands.sdk.llm.llm.token_counter", return_value=195_000):
        out = llm._clamp_max_tokens_for_joint_budget(call_kwargs, [], [])

    assert out["max_completion_tokens"] == 64_000


def test_thinking_budget_clamped_below_clamped_max_tokens():
    """On the extended-thinking path, lowering max_tokens must also lower the
    thinking budget so it stays strictly below max_tokens.

    chat_options sets ``max_tokens`` (without popping the default
    ``max_completion_tokens``) plus a ``thinking.budget_tokens`` just under the
    full output budget. Clamping only ``max_tokens`` for a large input would
    leave ``budget_tokens >= max_tokens``, which Bedrock/Anthropic reject.
    """
    llm = _make_llm("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    call_kwargs = {
        "max_tokens": 64_000,
        "max_completion_tokens": 64_000,
        "thinking": {"type": "enabled", "budget_tokens": 63_999},
    }

    with patch("openhands.sdk.llm.llm.token_counter", return_value=180_000):
        out = llm._clamp_max_tokens_for_joint_budget(call_kwargs, [], [])

    clamped = 200_000 - 180_000 - JOINT_BUDGET_SAFETY_MARGIN_TOKENS
    assert out["max_tokens"] == clamped
    # The sibling budget key is clamped too, so a stale full-size copy cannot
    # defeat the clamp downstream.
    assert out["max_completion_tokens"] == clamped
    # Thinking budget stays strictly below the clamped max_tokens.
    assert out["thinking"]["budget_tokens"] < out["max_tokens"]
    assert out["thinking"]["budget_tokens"] == clamped - 1
    # The caller's nested thinking dict must not be mutated in place.
    assert call_kwargs["thinking"]["budget_tokens"] == 63_999


def test_thinking_budget_preserved_when_no_clamp_needed():
    """Small input: nothing is clamped and the thinking budget is preserved."""
    llm = _make_llm("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    call_kwargs = {
        "max_tokens": 64_000,
        "thinking": {"type": "enabled", "budget_tokens": 63_999},
    }

    with patch("openhands.sdk.llm.llm.token_counter", return_value=50_000):
        out = llm._clamp_max_tokens_for_joint_budget(call_kwargs, [], [])

    assert out["max_tokens"] == 64_000
    assert out["thinking"]["budget_tokens"] == 63_999
