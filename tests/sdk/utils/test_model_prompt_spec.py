"""Tests for model prompt spec utilities."""

import pytest

from openhands.sdk.llm.utils.model_prompt_spec import (
    get_model_prompt_spec,
)


@pytest.mark.parametrize(
    ("model_name", "canonical_name", "expected_variant"),
    [
        # Non-codex variants
        ("gpt-5", None, "gpt-5"),
        ("gpt-5.1", None, "gpt-5"),
        ("gpt-5.2", None, "gpt-5"),
        # Codex variants
        ("gpt-5-codex", None, "gpt-5-codex"),
        ("gpt-5.1-codex", None, "gpt-5-codex"),
        ("gpt-5.2-codex", None, "gpt-5-codex"),
        ("gpt-5.3-codex", None, "gpt-5-codex"),
        # With canonical names
        ("gpt-5.2-codex", "openai/gpt-5.2-codex", "gpt-5-codex"),
        ("gpt-5.3-codex", "openai/gpt-5.3-codex", "gpt-5-codex"),
        # Provider-prefixed variants
        ("openai/gpt-5.2-codex-mini", None, "gpt-5-codex"),
        ("openai/gpt-5.3-codex-pro", None, "gpt-5-codex"),
    ],
)
def test_gpt5_variant_detection(
    model_name: str,
    canonical_name: str | None,
    expected_variant: str,
) -> None:
    """Test that GPT-5 variants are correctly detected."""
    result = get_model_prompt_spec(model_name, canonical_name)
    assert result.variant == expected_variant
    assert result.family == "openai_gpt"


@pytest.mark.parametrize(
    ("model_name", "canonical_name", "expected_family"),
    [
        ("claude-3-5-sonnet-20241022", None, "anthropic_claude"),
        ("gemini-2.0-flash", None, "google_gemini"),
        ("llama-3.1-70b-instruct", None, "meta_llama"),
        ("mistral-large-2411", None, "mistral"),
        ("deepseek-chat", None, "deepseek"),
        ("qwen-2.5-72b-instruct", None, "alibaba_qwen"),
    ],
)
def test_other_families(
    model_name: str,
    canonical_name: str | None,
    expected_family: str,
) -> None:
    """Test that other model families are correctly detected."""
    result = get_model_prompt_spec(model_name, canonical_name)
    assert result.family == expected_family
    assert result.variant is None
