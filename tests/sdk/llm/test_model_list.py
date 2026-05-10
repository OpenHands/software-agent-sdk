import sys
from unittest.mock import patch

from openhands.sdk.llm.utils.unverified_models import (
    _list_bedrock_foundation_models,
    get_unverified_models,
)
from openhands.sdk.llm.utils.verified_models import (
    VERIFIED_MODELS,
    VERIFIED_OPENHANDS_MODELS,
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


def test_openhands_models_all_have_provider_list():
    """Every model in VERIFIED_OPENHANDS_MODELS must also appear in at least one
    provider-specific list so that the UI can display it under its actual provider.

    Exception: models that are only available through the OpenHands provider
    (e.g. ``trinity-large-thinking``) are not exposed under any other provider.
    """
    openhands_only_models = {"trinity-large-thinking"}

    provider_models = set()
    for provider, models in VERIFIED_MODELS.items():
        if provider == "openhands":
            continue
        provider_models.update(models)

    missing = [
        m
        for m in VERIFIED_OPENHANDS_MODELS
        if m not in provider_models and m not in openhands_only_models
    ]
    assert not missing, (
        f"Models in VERIFIED_OPENHANDS_MODELS missing from any provider list: {missing}"
    )


def test_trinity_model_is_openhands_only():
    """trinity-large-thinking should be available only via the OpenHands provider
    and must not be listed under any other provider.
    """
    assert "trinity-large-thinking" in VERIFIED_OPENHANDS_MODELS
    assert "trinity" not in VERIFIED_MODELS
    for provider, models in VERIFIED_MODELS.items():
        if provider == "openhands":
            continue
        assert "trinity-large-thinking" not in models, (
            f"trinity-large-thinking should not be in provider list {provider!r}"
        )
