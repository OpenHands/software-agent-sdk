import pytest

from openhands.sdk.llm.utils.model_features import (
    get_default_temperature,
    get_features,
    model_matches,
)


@pytest.mark.parametrize(
    "name,pattern,expected",
    [
        ("gpt-4o", "gpt-4o", True),
        ("openai/gpt-4o", "gpt-4o", True),
        ("litellm_proxy/gpt-4o-mini", "gpt-4o", True),
        ("claude-3-7-sonnet-20250219", "claude-3-7-sonnet", True),
        ("o1-2024-12-17", "o1", True),
        ("grok-4-0709", "grok-4-0709", True),
        ("grok-4-0801", "grok-4-0709", False),
    ],
)
def test_model_matches(name, pattern, expected):
    assert model_matches(name, [pattern]) is expected


@pytest.mark.parametrize(
    "model,expected_reasoning",
    [
        ("o1-2024-12-17", True),
        ("o1", True),
        ("o3-mini", True),
        ("o3", True),
        # Anthropic Opus 4.5 (dash variant only)
        ("claude-opus-4-5", True),
        ("nova-2-lite", True),
        ("gpt-4o", False),
        ("claude-3-5-sonnet", False),
        ("gemini-1.5-pro", False),
        ("unknown-model", False),
    ],
)
def test_reasoning_effort_support(model, expected_reasoning):
    features = get_features(model)
    assert features.supports_reasoning_effort == expected_reasoning


@pytest.mark.parametrize(
    "model,expected_cache",
    [
        ("claude-3-5-sonnet", True),
        ("claude-3-7-sonnet", True),
        ("claude-3-haiku-20240307", True),
        ("claude-3-opus-20240229", True),
        # AWS Bedrock model ids (provider-prefixed)
        ("bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0", True),
        ("bedrock/anthropic.claude-3-haiku-20240307-v1:0", True),
        # Anthropic 4.5 variants (dash only; official IDs use hyphens)
        ("claude-haiku-4-5", True),
        ("us.anthropic.claude-haiku-4-5-20251001", True),
        ("bedrock/anthropic.claude-3-opus-20240229-v1:0", True),
        ("claude-sonnet-4-5", True),
        ("claude-opus-4-5", True),
        # User-facing model names (no provider prefix)
        ("anthropic.claude-3-5-sonnet-20241022", True),
        ("anthropic.claude-3-haiku-20240307", True),
        ("anthropic.claude-3-opus-20240229", True),
        ("gpt-4o", False),  # OpenAI doesn't support explicit prompt caching
        ("gemini-1.5-pro", False),
        ("unknown-model", False),
    ],
)
def test_prompt_cache_support(model, expected_cache):
    features = get_features(model)
    assert features.supports_prompt_cache == expected_cache


@pytest.mark.parametrize(
    "model,expected_stop_words",
    [
        ("gpt-4o", True),
        ("gpt-4o-mini", True),
        ("claude-3-5-sonnet", True),
        ("gemini-1.5-pro", True),
        ("llama-3.1-70b", True),
        ("unknown-model", True),  # Most models support stop words
        # Models that don't support stop words
        ("o1", False),
        ("o1-2024-12-17", False),
        ("grok-4-0709", False),
        ("grok-code-fast-1", False),
        ("xai/grok-4-0709", False),
        ("xai/grok-code-fast-1", False),
    ],
)
def test_stop_words_support(model, expected_stop_words):
    features = get_features(model)
    assert features.supports_stop_words == expected_stop_words


def test_get_features_with_provider_prefix():
    """Test that get_features works with provider prefixes."""
    # Test with various provider prefixes
    assert get_features("openai/gpt-4o").supports_reasoning_effort is False
    assert (
        get_features("anthropic/claude-3-5-sonnet").supports_reasoning_effort is False
    )
    assert get_features("litellm_proxy/gpt-4o").supports_reasoning_effort is False


def test_get_features_case_insensitive():
    """Test that get_features is case insensitive."""
    features_lower = get_features("gpt-4o")
    features_upper = get_features("GPT-4O")
    features_mixed = get_features("Gpt-4O")

    assert (
        features_lower.supports_reasoning_effort
        == features_upper.supports_reasoning_effort
    )
    assert features_lower.supports_stop_words == features_upper.supports_stop_words
    assert (
        features_lower.supports_reasoning_effort
        == features_mixed.supports_reasoning_effort
    )


def test_get_features_with_version_suffixes():
    """Test that get_features handles version suffixes correctly."""
    # Test that version suffixes are handled properly
    base_features = get_features("claude-3-5-sonnet")
    versioned_features = get_features("claude-3-5-sonnet-20241022")

    assert (
        base_features.supports_reasoning_effort
        == versioned_features.supports_reasoning_effort
    )
    assert base_features.supports_stop_words == versioned_features.supports_stop_words
    assert (
        base_features.supports_prompt_cache == versioned_features.supports_prompt_cache
    )


def test_model_matches_multiple_patterns():
    """Test model_matches with multiple patterns."""
    patterns = ["gpt-4", "claude-3", "gemini-"]

    assert model_matches("gpt-4o", patterns) is True
    assert model_matches("claude-3-5-sonnet", patterns) is True
    assert model_matches("gemini-1.5-pro", patterns) is True
    assert model_matches("llama-3.1-70b", patterns) is False


def test_model_matches_substring_semantics():
    """Test model_matches uses substring semantics (no globbing)."""
    patterns = ["gpt-4o", "claude-3-5-sonnet"]

    assert model_matches("gpt-4o", patterns) is True
    assert model_matches("claude-3-5-sonnet", patterns) is True
    # Substring match: 'gpt-4o' matches 'gpt-4o-mini'
    assert model_matches("gpt-4o-mini", patterns) is True
    assert model_matches("claude-3-haiku", patterns) is False


def test_get_features_unknown_model():
    """Test get_features with completely unknown model."""
    features = get_features("completely-unknown-model-12345")

    # Unknown models should have default feature values
    assert features.supports_reasoning_effort is False
    assert features.supports_prompt_cache is False
    assert features.supports_stop_words is True  # Most models support stop words


def test_get_features_empty_model():
    """Test get_features with empty or None model."""
    features_empty = get_features("")
    features_none = get_features(None)  # type: ignore[arg-type]

    # Empty models should have default feature values
    assert features_empty.supports_reasoning_effort is False
    assert features_none.supports_reasoning_effort is False
    assert features_empty.supports_stop_words is True
    assert features_none.supports_stop_words is True


def test_model_matches_with_provider_pattern():
    """model_matches uses substring on raw model name incl. provider prefixes."""
    assert model_matches("openai/gpt-4", ["openai/"])
    assert model_matches("anthropic/claude-3", ["anthropic/claude"])
    assert not model_matches("openai/gpt-4", ["anthropic/"])


def test_stop_words_grok_provider_prefixed():
    """Test that grok models don't support stop words with and without provider prefixes."""  # noqa: E501
    assert get_features("xai/grok-4-0709").supports_stop_words is False
    assert get_features("grok-4-0709").supports_stop_words is False
    assert get_features("xai/grok-code-fast-1").supports_stop_words is False
    assert get_features("grok-code-fast-1").supports_stop_words is False


@pytest.mark.parametrize(
    "model",
    [
        "o1-mini",
        "o1-2024-12-17",
        "xai/grok-4-0709",
        "xai/grok-code-fast-1",
    ],
)
def test_supports_stop_words_false_models(model):
    """Test models that don't support stop words."""
    features = get_features(model)
    assert features.supports_stop_words is False


@pytest.mark.parametrize(
    "model,expected_responses",
    [
        ("gpt-5.1", True),
        ("openai/gpt-5.1-codex-mini", True),
        ("gpt-5", True),
        ("openai/gpt-5-mini", True),
        ("codex-mini-latest", True),
        ("openai/codex-mini-latest", True),
        ("gpt-4o", False),
        ("unknown-model", False),
    ],
)
def test_responses_api_support(model, expected_responses):
    features = get_features(model)
    assert features.supports_responses_api is expected_responses


def test_force_string_serializer_full_model_names():
    """Ensure full model names match substring patterns for string serializer.

    Regression coverage for patterns like deepseek/glm without wildcards; Kimi
    should only match when provider-prefixed with groq/.
    """
    assert get_features("DeepSeek-V3.2-Exp").force_string_serializer is True
    assert get_features("GLM-4.5").force_string_serializer is True
    # Provider-agnostic Kimi should not force string serializer
    assert get_features("Kimi K2-Instruct-0905").force_string_serializer is False
    # Groq-prefixed Kimi should force string serializer
    assert get_features("groq/kimi-k2-instruct-0905").force_string_serializer is True


@pytest.mark.parametrize(
    "model,expected_retention",
    [
        ("gpt-5.1", True),
        ("openai/gpt-5.1-codex-mini", True),
        ("gpt-5", True),
        # New GPT-5.2 family should support extended retention
        ("gpt-5.2", True),
        ("openai/gpt-5.2-chat-latest", True),
        ("openai/gpt-5.2-pro", True),
        ("openai/gpt-5-mini", False),
        ("gpt-4o", False),
        ("openai/gpt-4.1", True),
        ("litellm_proxy/gpt-4.1", True),
        ("litellm_proxy/openai/gpt-4.1", True),
        ("litellm_proxy/openai/gpt-5", True),
        ("litellm_proxy/openai/gpt-5-mini", False),
        ("openai/gpt-5.1-mini", False),
        ("openai/gpt-5-mini-2025-08-07", False),
    ],
)
def test_prompt_cache_retention_support(model, expected_retention):
    features = get_features(model)
    assert features.supports_prompt_cache_retention is expected_retention

    # piggyback on this test to verify that force_string_serializer is correctly set
    assert get_features("GLM-4.5").force_string_serializer is True
    # Provider-agnostic Kimi should not force string serializer
    assert get_features("Kimi K2-Instruct-0905").force_string_serializer is False
    # Groq-prefixed Kimi should force string serializer
    assert get_features("groq/kimi-k2-instruct-0905").force_string_serializer is True


@pytest.mark.parametrize(
    "model,expected_send_reasoning",
    [
        ("kimi-k2-thinking", True),
        ("kimi-k2-thinking-0905", True),
        ("Kimi-K2-Thinking", True),  # Case insensitive
        ("moonshot/kimi-k2-thinking", True),  # With provider prefix
        # DeepSeek reasoner model
        ("deepseek/deepseek-reasoner", True),
        ("DeepSeek/deepseek-reasoner", True),
        # Models that should NOT match
        ("deepseek/deepseek-chat", False),  # Different DeepSeek model
        ("kimi-k2-instruct", False),  # Different variant
        ("gpt-4o", False),
        ("claude-3-5-sonnet", False),
        ("o1", False),
        ("unknown-model", False),
    ],
)
def test_send_reasoning_content_support(model, expected_send_reasoning):
    """Test that models like kimi-k2-thinking require send_reasoning_content."""
    features = get_features(model)
    assert features.send_reasoning_content is expected_send_reasoning


@pytest.mark.parametrize(
    "model,expected_temperature",
    [
        # kimi-k2-thinking models should default to 1.0
        ("kimi-k2-thinking", 1.0),
        ("kimi-k2-thinking-0905", 1.0),
        ("Kimi-K2-Thinking", 1.0),  # Case insensitive
        ("moonshot/kimi-k2-thinking", 1.0),  # With provider prefix
        ("litellm_proxy/kimi-k2-thinking", 1.0),  # With litellm proxy prefix
        # All other models should default to 0.0
        ("kimi-k2-instruct", 0.0),  # Different kimi variant
        ("gpt-4", 0.0),
        ("gpt-4o", 0.0),
        ("gpt-4o-mini", 0.0),
        ("claude-3-5-sonnet", 0.0),
        ("claude-3-7-sonnet", 0.0),
        ("gemini-1.5-pro", 0.0),
        ("gemini-2.5-pro-experimental", 0.0),
        ("o1", 0.0),
        ("o1-mini", 0.0),
        ("o3", 0.0),
        ("deepseek-chat", 0.0),
        ("llama-3.1-70b", 0.0),
        ("azure/gpt-4", 0.0),
        ("openai/gpt-4o", 0.0),
        ("anthropic/claude-3-5-sonnet", 0.0),
        ("unknown-model", 0.0),
    ],
)
def test_get_default_temperature(model, expected_temperature):
    """Test that get_default_temperature returns correct values for different models."""
    assert get_default_temperature(model) == expected_temperature


def test_get_default_temperature_fallback():
    """Test that get_default_temperature returns 0.0 for unknown models."""
    assert get_default_temperature("completely-unknown-model-12345") == 0.0
    assert get_default_temperature("some-random-model") == 0.0


def test_get_default_temperature_case_insensitive():
    """Test that get_default_temperature is case insensitive."""
    assert get_default_temperature("kimi-k2-thinking") == 1.0
    assert get_default_temperature("KIMI-K2-THINKING") == 1.0
    assert get_default_temperature("Kimi-K2-Thinking") == 1.0
    assert get_default_temperature("KiMi-k2-ThInKiNg") == 1.0
