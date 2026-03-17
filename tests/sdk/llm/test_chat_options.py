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
    llm = DummyLLM(model="gemini-2.5-pro", reasoning_effort=None)
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


def test_claude_sonnet_4_6_strips_temp_and_top_p():
    """Test that claude-sonnet-4-6 strips temperature and top_p.

    This is a regression test for issue #2137 where Claude Sonnet 4.6
    rejects requests with both temperature AND top_p specified.
    """
    llm = DummyLLM(
        model="claude-sonnet-4-6",
        top_p=1.0,  # SDK default
        temperature=0.1,  # Often overridden by benchmarks
    )
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    # Extended thinking models should strip temperature/top_p to avoid API errors
    assert "temperature" not in out
    assert "top_p" not in out


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
