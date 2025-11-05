import pytest

from openhands.sdk.llm.utils.model_features import (
    get_features,
    model_matches,
    normalize_model_name,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  OPENAI/gpt-4o  ", "gpt-4o"),
        ("anthropic/claude-3-7-sonnet", "claude-3-7-sonnet"),
        ("litellm_proxy/gemini-2.5-pro", "gemini-2.5-pro"),
        ("qwen3-coder-480b-a35b-instruct", "qwen3-coder-480b-a35b-instruct"),
        ("gpt-5", "gpt-5"),
        ("openai/GLM-4.5-GGUF", "glm-4.5"),
        ("openrouter/gpt-4o-mini", "gpt-4o-mini"),
        (
            "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
            "claude-3-5-sonnet-20241022-v2",
        ),
        ("", ""),
        (None, ""),  # type: ignore[arg-type]
    ],
)
def test_normalize_model_name(raw, expected):
    assert normalize_model_name(raw) == expected


@pytest.mark.parametrize(
    "name,pattern,expected",
    [
        ("gpt-4o", "gpt-4o*", True),
        ("openai/gpt-4o", "gpt-4o*", True),
        ("litellm_proxy/gpt-4o-mini", "gpt-4o*", True),
        ("claude-3-7-sonnet-20250219", "claude-3-7-sonnet*", True),
        ("o1-2024-12-17", "o1*", True),
        ("grok-4-0709", "grok-4-0709", True),
        ("grok-4-0801", "grok-4-0709", False),
    ],
)
def test_model_matches(name, pattern, expected):
    assert model_matches(name, [pattern]) is expected


@pytest.mark.parametrize(
    "model,expected_function_calling",
    [
        ("gpt-4o", True),
        ("gpt-4o-mini", True),
        ("claude-3-5-sonnet", True),
        ("claude-3-7-sonnet", True),
        ("gemini-2.5-pro", True),
        # AWS Bedrock models
        ("bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0", True),
        ("bedrock/anthropic.claude-3-7-sonnet-20250219-v1:0", True),
        ("bedrock/anthropic.claude-sonnet-4-20250514-v1:0", True),
        (
            "llama-3.1-70b",
            False,
        ),  # Most open source models don't support native function calling
        ("unknown-model", False),  # Default to False for unknown models
    ],
)
def test_function_calling_support(model, expected_function_calling):
    features = get_features(model)
    assert features.supports_function_calling == expected_function_calling


@pytest.mark.parametrize(
    "model,expected_reasoning",
    [
        ("o1-2024-12-17", True),
        ("o1", True),
        ("o3-mini", True),
        ("o3", True),
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
        # AWS Bedrock models
        ("bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0", True),
        ("bedrock/anthropic.claude-3-haiku-20240307-v1:0", True),
        ("bedrock/anthropic.claude-3-opus-20240229-v1:0", True),
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
    assert get_features("openai/gpt-4o").supports_function_calling is True
    assert get_features("anthropic/claude-3-5-sonnet").supports_function_calling is True
    assert get_features("litellm_proxy/gpt-4o").supports_function_calling is True


def test_get_features_case_insensitive():
    """Test that get_features is case insensitive."""
    features_lower = get_features("gpt-4o")
    features_upper = get_features("GPT-4O")
    features_mixed = get_features("Gpt-4O")

    assert (
        features_lower.supports_function_calling
        == features_upper.supports_function_calling
    )
    assert (
        features_lower.supports_reasoning_effort
        == features_upper.supports_reasoning_effort
    )
    assert (
        features_lower.supports_function_calling
        == features_mixed.supports_function_calling
    )


def test_get_features_with_version_suffixes():
    """Test that get_features handles version suffixes correctly."""
    # Test that version suffixes are handled properly
    base_features = get_features("claude-3-5-sonnet")
    versioned_features = get_features("claude-3-5-sonnet-20241022")

    assert (
        base_features.supports_function_calling
        == versioned_features.supports_function_calling
    )
    assert (
        base_features.supports_reasoning_effort
        == versioned_features.supports_reasoning_effort
    )
    assert (
        base_features.supports_prompt_cache == versioned_features.supports_prompt_cache
    )


def test_model_matches_multiple_patterns():
    """Test model_matches with multiple patterns."""
    patterns = ["gpt-4*", "claude-3*", "gemini-*"]

    assert model_matches("gpt-4o", patterns) is True
    assert model_matches("claude-3-5-sonnet", patterns) is True
    assert model_matches("gemini-1.5-pro", patterns) is True
    assert model_matches("llama-3.1-70b", patterns) is False


def test_model_matches_exact_match():
    """Test model_matches with exact patterns (no wildcards)."""
    patterns = ["gpt-4o", "claude-3-5-sonnet"]

    assert model_matches("gpt-4o", patterns) is True
    assert model_matches("claude-3-5-sonnet", patterns) is True
    assert model_matches("gpt-4o-mini", patterns) is False
    assert model_matches("claude-3-haiku", patterns) is False


def test_normalize_model_name_edge_cases():
    """Test normalize_model_name with edge cases."""
    # Test with multiple slashes
    assert normalize_model_name("provider/sub/model-name") == "model-name"

    # Test with colons and special characters
    assert normalize_model_name("provider/model:version:tag") == "model"

    # Test with whitespace and case
    assert normalize_model_name("  PROVIDER/Model-Name  ") == "model-name"

    # Test with underscores and hyphens
    assert normalize_model_name("provider/model_name-v1") == "model_name-v1"


def test_get_features_unknown_model():
    """Test get_features with completely unknown model."""
    features = get_features("completely-unknown-model-12345")

    # Unknown models should have conservative defaults
    assert features.supports_function_calling is False
    assert features.supports_reasoning_effort is False
    assert features.supports_prompt_cache is False
    assert features.supports_stop_words is True  # Most models support stop words


def test_get_features_empty_model():
    """Test get_features with empty or None model."""
    features_empty = get_features("")
    features_none = get_features(None)  # type: ignore[arg-type]

    # Both should return conservative defaults
    assert features_empty.supports_function_calling is False
    assert features_none.supports_function_calling is False
    assert features_empty.supports_reasoning_effort is False
    assert features_none.supports_reasoning_effort is False


def test_model_matches_with_provider_pattern():
    """Test model_matches with pattern containing '/' matches raw model string."""
    # Test pattern with '/' matches against raw model string (lines 43-44)
    assert model_matches("openai/gpt-4", ["openai/*"])
    assert model_matches("anthropic/claude-3", ["anthropic/claude*"])
    assert not model_matches("openai/gpt-4", ["anthropic/*"])


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
