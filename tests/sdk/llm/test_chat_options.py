from dataclasses import dataclass
from typing import Any

from openhands.sdk.llm.options.chat_options import select_chat_options


@dataclass
class DummyLLM:
    model: str
    top_k: int | None = None
    top_p: float | None = 1.0
    temperature: float | None = 0.0
    max_output_tokens: int = 1024
    extra_headers: dict[str, str] | None = None
    reasoning_effort: str | None = None
    extended_thinking_budget: int | None = None
    safety_settings: list[dict[str, Any]] | None = None
    litellm_extra_body: dict[str, Any] | None = None
    # Align with LLM default; only emitted for models that support it
    prompt_cache_retention: str | None = "24h"


def test_opus_4_5_uses_reasoning_effort_and_strips_temp_top_p():
    llm = DummyLLM(
        model="claude-opus-4-5-20251101",
        top_p=0.9,
        temperature=0.7,
        reasoning_effort="medium",
    )
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    # LiteLLM automatically maps reasoning_effort to output_config
    assert out.get("reasoning_effort") == "medium"
    assert "output_config" not in out

    # LiteLLM automatically adds the required beta header
    assert "extra_headers" not in out or "anthropic-beta" not in out.get(
        "extra_headers", {}
    )

    # Strips temperature/top_p for reasoning models
    assert "temperature" not in out
    assert "top_p" not in out


def test_gpt5_uses_reasoning_effort_and_strips_temp_top_p():
    llm = DummyLLM(
        model="gpt-5-mini-2025-08-07",
        temperature=0.5,
        top_p=0.8,
        reasoning_effort="high",
    )
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    assert out.get("reasoning_effort") == "high"
    assert "output_config" not in out
    headers = out.get("extra_headers") or {}
    assert "anthropic-beta" not in headers
    assert "temperature" not in out
    assert "top_p" not in out


def test_gemini_2_5_pro_defaults_reasoning_effort_low_when_none():
    llm = DummyLLM(model="gemini-2.5-pro-experimental", reasoning_effort=None)
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    assert out.get("reasoning_effort") == "low"


def test_non_reasoning_model_preserves_temp_and_top_p():
    llm = DummyLLM(model="gpt-4o", temperature=0.6, top_p=0.7)
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    # Non-reasoning models should retain temperature/top_p defaults
    assert out.get("temperature") == 0.6
    assert out.get("top_p") == 0.7


def test_azure_renames_max_completion_tokens_to_max_tokens():
    llm = DummyLLM(model="azure/gpt-4o")
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    assert "max_completion_tokens" not in out
    assert out.get("max_tokens") == llm.max_output_tokens


def test_tools_removed_when_has_tools_false():
    llm = DummyLLM(model="gpt-4o")
    uk = {"tools": ["t1"], "tool_choice": "auto"}
    out = select_chat_options(llm, user_kwargs=uk, has_tools=False)

    assert "tools" not in out
    assert "tool_choice" not in out


def test_extra_body_is_forwarded():
    llm = DummyLLM(model="gpt-4o", litellm_extra_body={"x": 1})
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    assert out.get("extra_body") == {"x": 1}


def test_extended_thinking_budget_clamped_below_max_tokens():
    """Test that thinking.budget_tokens is clamped to max_output_tokens - 1."""
    # Case 1: extended_thinking_budget exceeds max_output_tokens
    llm = DummyLLM(
        model="claude-sonnet-4-5-20250929",
        max_output_tokens=1000,
        extended_thinking_budget=2000,
    )
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    # budget_tokens should be clamped to max_output_tokens - 1 = 999
    assert out.get("thinking") == {
        "type": "enabled",
        "budget_tokens": 999,
    }
    assert out.get("max_tokens") == 1000

    # Case 2: extended_thinking_budget equals max_output_tokens
    llm = DummyLLM(
        model="claude-sonnet-4-5-20250929",
        max_output_tokens=1000,
        extended_thinking_budget=1000,
    )
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    # budget_tokens should be clamped to max_output_tokens - 1 = 999
    assert out.get("thinking") == {
        "type": "enabled",
        "budget_tokens": 999,
    }
    assert out.get("max_tokens") == 1000

    # Case 3: extended_thinking_budget is already below max_output_tokens
    llm = DummyLLM(
        model="claude-sonnet-4-5-20250929",
        max_output_tokens=1000,
        extended_thinking_budget=500,
    )
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    # budget_tokens should remain as-is
    assert out.get("thinking") == {
        "type": "enabled",
        "budget_tokens": 500,
    }
    assert out.get("max_tokens") == 1000


def test_claude_opus_4_6_removes_top_p_when_both_temp_and_top_p_present():
    """Test that Claude Opus 4.6 removes top_p when both temperature and top_p are set.

    Claude Opus 4.6 (and similar Anthropic models) cannot have both temperature
    and top_p specified in the same request. The SDK should prefer temperature
    and remove top_p.
    """
    # Test with dash variant (claude-opus-4-6)
    llm = DummyLLM(
        model="anthropic/claude-opus-4-6",
        temperature=0.7,
        top_p=0.9,
    )
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    # For reasoning models, both temp and top_p are removed by the reasoning logic
    # But if temperature is added back (e.g., by retry logic), top_p should
    # not be present. Since claude-opus-4-6 is a reasoning model, both should
    # be removed initially
    assert "temperature" not in out
    assert "top_p" not in out

    # Test with dot variant (claude-opus-4.6)
    llm = DummyLLM(
        model="anthropic/claude-opus-4.6",
        temperature=0.7,
        top_p=0.9,
    )
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    # claude-opus-4.6 (with dot) is NOT in REASONING_EFFORT_MODELS, so temp/top_p
    # are not removed by reasoning logic. But the exclusive sampling logic should
    # remove top_p when both are present.
    assert out.get("temperature") == 0.7
    assert "top_p" not in out


def test_claude_opus_4_6_with_user_provided_temperature():
    """Test that user-provided temperature is preserved and top_p is removed."""
    llm = DummyLLM(
        model="claude-opus-4.6",
        temperature=0.5,
        top_p=0.8,
    )
    # User provides temperature in kwargs
    out = select_chat_options(llm, user_kwargs={"temperature": 1.0}, has_tools=True)

    # User-provided temperature should be preserved, top_p should be removed
    assert out.get("temperature") == 1.0
    assert "top_p" not in out


def test_claude_opus_4_6_only_temperature_no_top_p():
    """Test that when only temperature is set (no top_p), it's preserved."""
    llm = DummyLLM(
        model="claude-opus-4.6",
        temperature=0.7,
        top_p=None,
    )
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    # Only temperature should be present
    assert out.get("temperature") == 0.7
    assert "top_p" not in out


def test_claude_opus_4_6_only_top_p_no_temperature():
    """Test that when only top_p is set (no temperature), it's preserved."""
    llm = DummyLLM(
        model="claude-opus-4.6",
        temperature=None,
        top_p=0.9,
    )
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    # Only top_p should be present (no conflict since temperature is None)
    assert "temperature" not in out
    assert out.get("top_p") == 0.9
