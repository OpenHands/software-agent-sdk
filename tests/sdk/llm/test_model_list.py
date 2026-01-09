import sys
from unittest.mock import patch

from openhands.sdk.llm.utils.unverified_models import (
    _list_bedrock_foundation_models,
    get_unverified_models,
)


def test_organize_models_and_providers():
    models = [
        "openai/gpt-4o",
        "anthropic/claude-sonnet-4-20250514",
        "o3",
        "o4-mini",
        "devstral-small-2505",
        "mistral/devstral-small-2505",
        "anthropic.claude-3-5",  # Ignore dot separator for anthropic
        "unknown-model",
        "custom-provider/custom-model",  # invalid provider -> bucketed under "other"
        "us.anthropic.claude-3-5-sonnet-20241022-v2:0",  # invalid provider prefix
        "1024-x-1024/gpt-image-1.5",  # invalid provider prefix
        "openai/another-model",
    ]

    with patch(
        "openhands.sdk.llm.utils.unverified_models.get_supported_llm_models",
        return_value=models,
    ):
        result = get_unverified_models()

        assert "openai" in result
        assert "anthropic" not in result  # don't include verified models
        assert "mistral" not in result
        assert "other" in result

        assert len(result["openai"]) == 1
        assert "another-model" in result["openai"]

        assert len(result["other"]) == 4
        assert "unknown-model" in result["other"]
        assert "custom-provider/custom-model" in result["other"]
        assert "us.anthropic.claude-3-5-sonnet-20241022-v2:0" in result["other"]
        assert "1024-x-1024/gpt-image-1.5" in result["other"]


def test_list_bedrock_models_without_boto3(monkeypatch):
    """Should warn and return empty list if boto3 is missing."""
    # Pretend boto3 is not installed
    monkeypatch.setitem(sys.modules, "boto3", None)

    # Mock the logger to verify warning is called
    with patch("openhands.sdk.llm.utils.unverified_models.logger") as mock_logger:
        result = _list_bedrock_foundation_models("us-east-1", "key", "secret")

    assert result == []
    mock_logger.warning.assert_called_once_with(
        "boto3 is not installed. To use Bedrock models,"
        "install with: openhands-sdk[boto3]"
    )


def test_list_bedrock_models_with_boto3(monkeypatch):
    """Should return prefixed bedrock model IDs if boto3 is present."""

    class FakeClient:
        def list_foundation_models(self, **kwargs):
            return {"modelSummaries": [{"modelId": "anthropic.claude-3"}]}

    class FakeBoto3:
        def client(self, *args, **kwargs):
            return FakeClient()

    # Inject fake boto3
    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())

    result = _list_bedrock_foundation_models("us-east-1", "key", "secret")

    assert result == ["bedrock/anthropic.claude-3"]
