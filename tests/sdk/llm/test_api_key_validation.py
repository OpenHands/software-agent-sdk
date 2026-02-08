from pydantic import SecretStr

from openhands.sdk.llm import LLM


def test_empty_api_key_string_converted_to_none():
    """Test that empty string API keys are converted to None."""
    llm = LLM(
        usage_id="test-llm",
        model="bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key=SecretStr(""),
    )
    assert llm.api_key is None


def test_whitespace_api_key_converted_to_none():
    """Test that whitespace-only API keys are converted to None."""
    llm = LLM(
        usage_id="test-llm",
        model="bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key=SecretStr("   "),
    )
    assert llm.api_key is None


def test_valid_api_key_preserved():
    """Test that valid API keys are preserved."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("valid-key"), usage_id="test-llm")
    assert llm.api_key is not None
    assert isinstance(llm.api_key, SecretStr)
    assert llm.api_key.get_secret_value() == "valid-key"


def test_none_api_key_preserved():
    """Test that None API keys remain None."""
    llm = LLM(
        model="bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key=None,
        usage_id="test-llm",
    )
    assert llm.api_key is None


def test_empty_string_direct_input():
    """Test that empty string passed directly (not as SecretStr) is converted to None."""  # noqa: E501
    # This tests the case where someone might pass a string directly
    # The field validator now accepts str and converts it to SecretStr
    data = {"model": "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0", "api_key": ""}
    llm = LLM(**data, usage_id="test-llm")  # pyright: ignore[reportArgumentType]
    assert llm.api_key is None


def test_whitespace_string_direct_input():
    """Test that whitespace string passed directly is converted to None."""
    data = {
        "model": "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
        "api_key": "   \t\n  ",
    }
    llm = LLM(**data, usage_id="test-llm")  # pyright: ignore[reportArgumentType]
    assert llm.api_key is None


def test_bedrock_model_with_none_api_key():
    """Test that Bedrock models work with None API key (for IAM auth)."""
    llm = LLM(
        model="bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key=None,
        aws_region_name="us-east-1",
        usage_id="test-llm",
    )
    assert llm.api_key is None
    assert llm.aws_region_name == "us-east-1"


def test_non_bedrock_model_with_valid_key():
    """Test that non-Bedrock models work normally with valid API keys."""
    llm = LLM(
        model="gpt-4o-mini", api_key=SecretStr("valid-openai-key"), usage_id="test-llm"
    )
    assert llm.api_key is not None
    assert isinstance(llm.api_key, SecretStr)
    assert llm.api_key.get_secret_value() == "valid-openai-key"


def test_aws_credentials_handling():
    """Test that AWS credentials are properly handled for Bedrock models."""
    llm = LLM(
        usage_id="test-llm",
        model="bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key=None,
        aws_access_key_id=SecretStr("test-access-key"),
        aws_secret_access_key=SecretStr("test-secret-key"),
        aws_region_name="us-west-2",
    )
    assert llm.api_key is None
    assert llm.aws_access_key_id is not None
    assert isinstance(llm.aws_access_key_id, SecretStr)
    assert llm.aws_access_key_id.get_secret_value() == "test-access-key"
    assert llm.aws_secret_access_key is not None
    assert isinstance(llm.aws_secret_access_key, SecretStr)
    assert llm.aws_secret_access_key.get_secret_value() == "test-secret-key"
    assert llm.aws_region_name == "us-west-2"


def test_plain_string_api_key():
    """Test that plain string API keys are converted to SecretStr."""
    llm = LLM(model="gpt-4o-mini", api_key="my-plain-string-key", usage_id="test-llm")
    assert llm.api_key is not None
    assert isinstance(llm.api_key, SecretStr)
    assert llm.api_key.get_secret_value() == "my-plain-string-key"


def test_plain_string_aws_credentials():
    """Test that plain string AWS credentials are converted to SecretStr."""
    llm = LLM(
        usage_id="test-llm",
        model="bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key=None,
        aws_access_key_id="plain-access-key",
        aws_secret_access_key="plain-secret-key",
        aws_region_name="us-west-2",
    )
    assert llm.api_key is None
    assert llm.aws_access_key_id is not None
    assert isinstance(llm.aws_access_key_id, SecretStr)
    assert llm.aws_access_key_id.get_secret_value() == "plain-access-key"
    assert llm.aws_secret_access_key is not None
    assert isinstance(llm.aws_secret_access_key, SecretStr)
    assert llm.aws_secret_access_key.get_secret_value() == "plain-secret-key"
    assert llm.aws_region_name == "us-west-2"
