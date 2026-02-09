import os
from unittest.mock import patch

import pytest
from deprecation import DeprecatedWarning
from pydantic import SecretStr, ValidationError

from openhands.sdk.llm import LLM


def test_llm_config_defaults():
    """Test LLM with default values."""
    config = LLM(model="gpt-4o-mini", usage_id="test-llm")
    assert config.model == "gpt-4o-mini"
    assert config.api_key is None
    assert config.base_url is None
    assert config.api_version is None
    assert config.num_retries == 5
    assert config.retry_multiplier == 8
    assert config.retry_min_wait == 8
    assert config.retry_max_wait == 64
    assert config.timeout == 300  # Default timeout is 5 minutes
    assert config.max_message_chars == 30_000
    assert config.temperature == 0.0
    assert config.top_p == 1.0
    assert config.top_k is None
    assert config.max_input_tokens == 128000  # Auto-populated from model info
    assert config.max_output_tokens == 16384  # Auto-populated from model info
    assert config.input_cost_per_token is None
    assert config.output_cost_per_token is None
    assert config.ollama_base_url is None
    assert config.drop_params is True
    assert config.modify_params is True
    assert config.disable_vision is None
    assert config.disable_stop_word is False
    assert config.caching_prompt is True
    assert config.log_completions is False
    assert config.custom_tokenizer is None
    assert config.native_tool_calling is True
    assert config.reasoning_effort == "high"
    assert config.seed is None
    assert config.safety_settings is None


def test_llm_config_custom_values():
    """Test LLM with custom values."""
    # safety_settings is deprecated starting in 1.10.0
    # Mock the version to simulate being on 1.10.0+ to trigger the warning
    with (
        patch(
            "openhands.sdk.utils.deprecation._current_version", return_value="1.10.0"
        ),
        pytest.warns(DeprecatedWarning, match="LLM.safety_settings"),
    ):
        config = LLM(
            usage_id="test-llm",
            model="gpt-4o-mini",
            api_key=SecretStr("test-key"),
            base_url="https://api.example.com",
            api_version="v1",
            num_retries=3,
            retry_multiplier=2,
            retry_min_wait=1,
            retry_max_wait=10,
            timeout=30,
            max_message_chars=10000,
            temperature=0.5,
            top_p=0.9,
            top_k=50,
            max_input_tokens=20000,
            max_output_tokens=1000,
            input_cost_per_token=0.001,
            output_cost_per_token=0.002,
            ollama_base_url="http://localhost:11434",
            drop_params=False,
            modify_params=False,
            disable_vision=True,
            disable_stop_word=True,
            caching_prompt=False,
            log_completions=True,
            custom_tokenizer=None,  # Avoid HF API call
            native_tool_calling=True,
            reasoning_effort="high",
            seed=42,
            safety_settings=[
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_MEDIUM_AND_ABOVE",
                }
            ],
        )

    assert config.model == "gpt-4o-mini"
    assert config.api_key is not None
    assert isinstance(config.api_key, SecretStr)
    assert config.api_key.get_secret_value() == "test-key"
    assert config.base_url == "https://api.example.com"
    assert config.api_version == "v1"
    assert config.num_retries == 3
    assert config.retry_multiplier == 2
    assert config.retry_min_wait == 1
    assert config.retry_max_wait == 10
    assert config.timeout == 30
    assert config.max_message_chars == 10000
    assert config.temperature == 0.5
    assert config.top_p == 0.9
    assert config.top_k == 50
    assert config.max_input_tokens == 20000
    assert config.max_output_tokens == 1000
    assert config.input_cost_per_token == 0.001
    assert config.output_cost_per_token == 0.002
    assert config.ollama_base_url == "http://localhost:11434"
    assert config.drop_params is False
    assert config.modify_params is False
    assert config.disable_vision is True
    assert config.disable_stop_word is True
    assert config.caching_prompt is False
    assert config.log_completions is True
    assert config.custom_tokenizer is None
    assert config.native_tool_calling is True
    assert config.reasoning_effort == "high"
    assert config.seed == 42
    assert config.safety_settings == [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}
    ]


def test_llm_config_secret_str():
    """Test that api_key is properly handled as SecretStr."""
    config = LLM(
        model="gpt-4o-mini", api_key=SecretStr("secret-key"), usage_id="test-llm"
    )
    assert config.api_key is not None
    assert isinstance(config.api_key, SecretStr)
    assert config.api_key.get_secret_value() == "secret-key"
    # Ensure the secret is not exposed in string representation
    assert "secret-key" not in str(config)


def test_llm_config_aws_credentials():
    """Test AWS credentials handling."""
    config = LLM(
        usage_id="test-llm",
        model="gpt-4o-mini",
        aws_access_key_id=SecretStr("test-access-key"),
        aws_secret_access_key=SecretStr("test-secret-key"),
        aws_region_name="us-east-1",
    )
    assert config.aws_access_key_id is not None
    assert isinstance(config.aws_access_key_id, SecretStr)
    assert config.aws_access_key_id.get_secret_value() == "test-access-key"
    assert config.aws_secret_access_key is not None
    assert isinstance(config.aws_secret_access_key, SecretStr)
    assert config.aws_secret_access_key.get_secret_value() == "test-secret-key"
    assert config.aws_region_name == "us-east-1"


def test_llm_config_openrouter_defaults():
    """Test OpenRouter default values."""
    config = LLM(model="gpt-4o-mini", usage_id="test-llm")
    assert config.openrouter_site_url == "https://docs.all-hands.dev/"
    assert config.openrouter_app_name == "OpenHands"


def test_llm_config_post_init_openrouter_env_vars():
    """Test that OpenRouter environment variables are set in post_init."""
    with patch.dict(os.environ, {}, clear=True):
        LLM(
            model="gpt-4o-mini",
            openrouter_site_url="https://custom.site.com",
            openrouter_app_name="CustomApp",
            usage_id="test-llm",
        )
        assert os.environ.get("OR_SITE_URL") == "https://custom.site.com"
        assert os.environ.get("OR_APP_NAME") == "CustomApp"


def test_llm_config_post_init_reasoning_effort_default():
    """Test reasoning_effort defaults to high."""
    config = LLM(model="gpt-4o-mini", usage_id="test-llm")
    assert config.reasoning_effort == "high"

    # Test that Gemini models also default to high
    config = LLM(model="gemini-2.5-pro-experimental", usage_id="test-llm")
    assert config.reasoning_effort == "high"

    # Test that explicit reasoning_effort is preserved
    config = LLM(model="gpt-4o-mini", reasoning_effort="low", usage_id="test-llm")
    assert config.reasoning_effort == "low"
    config = LLM(model="gpt-4o-mini", reasoning_effort="xhigh", usage_id="test-llm")
    assert config.reasoning_effort == "xhigh"


def test_llm_config_post_init_azure_api_version():
    """Test that Azure models get default API version."""
    config = LLM(model="azure/gpt-4o-mini", usage_id="test-llm")
    assert config.api_version == "2024-12-01-preview"

    # Test that non-Azure models don't get default API version
    config = LLM(model="gpt-4o-mini", usage_id="test-llm")
    assert config.api_version is None

    # Test that explicit API version is preserved
    config = LLM(
        model="azure/gpt-4o-mini", api_version="custom-version", usage_id="test-llm"
    )
    assert config.api_version == "custom-version"


def test_llm_config_post_init_aws_env_vars():
    """Test that AWS credentials are set as environment variables."""
    with patch.dict(os.environ, {}, clear=True):
        LLM(
            usage_id="test-llm",
            model="gpt-4o-mini",
            aws_access_key_id=SecretStr("test-access-key"),
            aws_secret_access_key=SecretStr("test-secret-key"),
            aws_region_name="us-west-2",
        )
        assert os.environ.get("AWS_ACCESS_KEY_ID") == "test-access-key"
        assert os.environ.get("AWS_SECRET_ACCESS_KEY") == "test-secret-key"
        assert os.environ.get("AWS_REGION_NAME") == "us-west-2"


def test_llm_config_log_completions_folder_default():
    """Test that log_completions_folder has a default value."""
    config = LLM(model="gpt-4o-mini", usage_id="test-llm")
    assert config.log_completions_folder is not None
    assert "completions" in config.log_completions_folder


def test_llm_config_extra_fields_permitted():
    """Test that extra fields are forbidden."""
    LLM(model="gpt-4o-mini", invalid_field="should_be_permitted", usage_id="test-llm")  # type: ignore


def test_llm_config_validation():
    """Test validation of LLM fields with ge constraints."""
    # Test that negative values are rejected for fields with ge constraints
    with pytest.raises(ValidationError) as exc_info:
        LLM(
            model="gpt-4o-mini",
            num_retries=-1,  # Should fail: ge=0
            retry_multiplier=-1,  # Should fail: ge=0
            retry_min_wait=-1,  # Should fail: ge=0
            retry_max_wait=-1,  # Should fail: ge=0
            timeout=-1,  # Should fail: ge=0
            max_message_chars=-1,  # Should fail: ge=1
            temperature=-1,  # Should fail: ge=0
            top_p=-1,  # Should fail: ge=0
            usage_id="test-llm",
        )

    # Verify that the validation error contains expected field names
    error_str = str(exc_info.value)
    expected_fields = [
        "num_retries",
        "retry_multiplier",
        "retry_min_wait",
        "retry_max_wait",
        "timeout",
        "max_message_chars",
        "temperature",
        "top_p",
    ]
    for field in expected_fields:
        assert field in error_str

    # Test that valid values (>= constraints) work correctly
    config = LLM(
        model="gpt-4o-mini",
        num_retries=0,  # Valid: ge=0
        retry_multiplier=0.0,  # Valid: ge=0
        retry_min_wait=0,  # Valid: ge=0
        retry_max_wait=0,  # Valid: ge=0
        timeout=0,  # Valid: ge=0
        max_message_chars=1,  # Valid: ge=1
        temperature=0.0,  # Valid: ge=0
        top_p=0.0,  # Valid: ge=0
        usage_id="test-llm",
    )
    assert config.num_retries == 0
    assert config.retry_multiplier == 0.0
    assert config.retry_min_wait == 0
    assert config.retry_max_wait == 0
    assert config.timeout == 0
    assert config.max_message_chars == 1
    assert config.temperature == 0.0
    assert config.top_p == 0.0


def test_llm_config_model_variants():
    """Test various model name formats."""
    models = [
        "gpt-4o-mini",
        "claude-3-sonnet",
        "azure/gpt-4o-mini",
        "anthropic/claude-3-sonnet",
        "gemini-2.5-pro-experimental",
    ]

    for model in models:
        config = LLM(model=model, usage_id="test-llm")
        assert config.model == model


def test_llm_config_boolean_fields():
    """Test boolean field handling."""
    config = LLM(
        model="gpt-4o-mini",
        modify_params=False,
        disable_vision=True,
        disable_stop_word=False,
        caching_prompt=True,
        log_completions=False,
        native_tool_calling=True,
        usage_id="test-llm",
    )

    assert config.drop_params is True
    assert config.modify_params is False
    assert config.disable_vision is True
    assert config.disable_stop_word is False
    assert config.caching_prompt is True
    assert config.log_completions is False
    assert config.native_tool_calling is True


def test_llm_config_optional_fields():
    """Test that optional fields can be None."""
    config = LLM(
        model="gpt-4o-mini",
        api_key=None,
        base_url=None,
        api_version=None,
        aws_access_key_id=None,
        aws_secret_access_key=None,
        aws_region_name=None,
        timeout=None,
        top_k=None,
        max_input_tokens=None,
        max_output_tokens=None,
        input_cost_per_token=None,
        output_cost_per_token=None,
        ollama_base_url=None,
        disable_vision=None,
        disable_stop_word=None,
        custom_tokenizer=None,
        reasoning_effort=None,
        seed=None,
        safety_settings=None,
        usage_id="test-llm",
    )

    assert config.api_key is None
    assert config.base_url is None
    assert config.api_version is None
    assert config.aws_access_key_id is None
    assert config.aws_secret_access_key is None
    assert config.aws_region_name is None
    assert config.timeout is None
    assert config.top_k is None
    assert (
        config.max_input_tokens == 128000
    )  # Auto-populated from model info even when set to None
    assert (
        config.max_output_tokens == 16384
    )  # Auto-populated from model info even when set to None
    assert config.input_cost_per_token is None
    assert config.output_cost_per_token is None
    assert config.ollama_base_url is None
    assert config.disable_vision is None
    assert config.disable_stop_word is None
    assert config.custom_tokenizer is None
    assert config.reasoning_effort is None  # Explicitly set to None overrides default
    assert config.seed is None
    assert config.safety_settings is None
