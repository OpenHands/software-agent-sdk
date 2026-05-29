from dataclasses import dataclass
from typing import Any

from openhands.sdk.llm import LLM
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
    litellm_extra_body: dict[str, Any] | None = None
    # Align with LLM default; only emitted for models that support it
    prompt_cache_retention: str | None = "24h"
    _prompt_cache_key: str | None = None
    openrouter_site_url: str = ""
    openrouter_app_name: str = ""

    def _openrouter_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.openrouter_site_url:
            headers["HTTP-Referer"] = self.openrouter_site_url
        if self.openrouter_app_name:
            headers["X-Title"] = self.openrouter_app_name
        return headers

    @property
    def effective_max_output_tokens(self) -> int:
        return self.max_output_tokens


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


def test_kimi_k2_thinking_does_not_send_reasoning_effort():
    llm = DummyLLM(
        model="litellm_proxy/moonshot/kimi-k2-thinking",
        temperature=1.0,
        reasoning_effort="high",
    )
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    assert "reasoning_effort" not in out
    assert out.get("temperature") == 1.0


def test_gemini_2_5_pro_without_reasoning_effort_preserves_temp_and_top_p():
    llm = DummyLLM(model="gemini-2.5-pro", reasoning_effort=None)
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    assert "reasoning_effort" not in out
    assert out.get("temperature") == 0.0
    assert out.get("top_p") == 1.0


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


def test_claude_opus_4_8_strips_temp_and_top_p():
    """Test that claude-opus-4-8 strips temperature and top_p.

    Anthropic rejects requests to Claude Opus 4.8 with `temperature`/`top_p`:
    ``"`temperature` is deprecated for this model."``. Routing the model
    through the extended-thinking path strips both params so requests
    succeed. (The legacy thinking block is deliberately NOT emitted for
    opus-4-8 -- see test_claude_opus_4_8_skips_legacy_thinking_block.)
    """
    llm = DummyLLM(
        model="litellm_proxy/anthropic/claude-opus-4-8",
        top_p=1.0,  # SDK default
        temperature=0.0,  # Often overridden by benchmarks (e.g. SWE-bench)
    )
    out = select_chat_options(llm, user_kwargs={}, has_tools=True)

    assert "temperature" not in out
    assert "top_p" not in out


def test_claude_opus_4_8_skips_legacy_thinking_block():
    """claude-opus-4-8 must not be sent the legacy ``thinking.type=enabled``
    block. Anthropic rejects it with:
    ``"thinking.type.enabled" is not supported for this model. Use
    "thinking.type.adaptive" and "output_config.effort" to control thinking
    behavior.`` Until the SDK builds that newer schema, the legacy block (and
    the ``interleaved-thinking-2025-05-14`` beta header that goes with it)
    must be omitted, even when ``extended_thinking_budget`` is set.

    Sibling models in EXTENDED_THINKING_MODELS (e.g. sonnet-4-5/4-6) still
    accept the legacy block, so this test also pins that those models are
    NOT affected by the opus-4-8 skip.
    """
    # opus-4-8: thinking block + beta header must be omitted, even with a
    # configured budget.
    opus = DummyLLM(
        model="litellm_proxy/anthropic/claude-opus-4-8",
        max_output_tokens=8192,
        extended_thinking_budget=4096,
    )
    out = select_chat_options(opus, user_kwargs={}, has_tools=True)

    assert "thinking" not in out
    assert "anthropic-beta" not in (out.get("extra_headers") or {})
    # The whole point of routing opus-4-8 through this path is to strip
    # temperature/top_p; make sure that side effect still happens.
    assert "temperature" not in out
    assert "top_p" not in out

    # sonnet-4-5 still gets the legacy thinking block; the opus-4-8 skip
    # must not regress other extended-thinking models.
    sonnet = DummyLLM(
        model="claude-sonnet-4-5",
        max_output_tokens=8192,
        extended_thinking_budget=4096,
    )
    sonnet_out = select_chat_options(sonnet, user_kwargs={}, has_tools=True)

    assert sonnet_out.get("thinking") == {
        "type": "enabled",
        "budget_tokens": 4096,
    }
    assert (sonnet_out.get("extra_headers") or {}).get(
        "anthropic-beta"
    ) == "interleaved-thinking-2025-05-14"


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


def test_chat_options_forwards_prompt_cache_key_when_set():
    """Regression test for #2904."""
    llm = LLM(model="gpt-4o")
    llm._prompt_cache_key = "conv-abc123"
    assert (
        select_chat_options(llm, user_kwargs={}, has_tools=True).get("prompt_cache_key")
        == "conv-abc123"
    )


def test_chat_options_omits_prompt_cache_key_when_unset():
    llm = LLM(model="gpt-4o")
    assert "prompt_cache_key" not in select_chat_options(
        llm, user_kwargs={}, has_tools=True
    )


def test_chat_options_injects_openrouter_headers_via_extra_headers():
    """OpenRouter site/app must flow per-call (issue #3138), not via env."""
    llm = DummyLLM(
        model="openrouter/anthropic/claude-3-5-sonnet",
        openrouter_site_url="https://app.example.com/",
        openrouter_app_name="ExampleApp",
    )
    out = select_chat_options(llm, user_kwargs={}, has_tools=False)
    assert out["extra_headers"]["HTTP-Referer"] == "https://app.example.com/"
    assert out["extra_headers"]["X-Title"] == "ExampleApp"


def test_chat_options_user_extra_headers_win_over_openrouter_defaults():
    """User-supplied extra_headers must override per-call OpenRouter values."""
    llm = DummyLLM(
        model="openrouter/anthropic/claude-3-5-sonnet",
        openrouter_site_url="https://app.example.com/",
        openrouter_app_name="ExampleApp",
        extra_headers={"X-Title": "UserOverride"},
    )
    out = select_chat_options(llm, user_kwargs={}, has_tools=False)
    assert out["extra_headers"]["X-Title"] == "UserOverride"
    # Site URL still injected since user didn't override it
    assert out["extra_headers"]["HTTP-Referer"] == "https://app.example.com/"


def test_chat_options_omits_openrouter_headers_when_unset():
    """Empty site/app must not add extra_headers."""
    llm = DummyLLM(model="gpt-4o")
    out = select_chat_options(llm, user_kwargs={}, has_tools=False)
    assert "extra_headers" not in out
