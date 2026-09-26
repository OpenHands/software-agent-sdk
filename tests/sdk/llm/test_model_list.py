import sys
from unittest.mock import patch

from openhands.sdk.llm.utils.unverified_models import (
    _list_bedrock_foundation_models,
    get_unverified_models,
)
from openhands.sdk.llm.utils.verified_models import (
    VERIFIED_MODELS,
    VERIFIED_OPENAI_MODELS,
    VERIFIED_OPENHANDS_MODELS,
)


def test_organize_models_and_providers():
    models = [
        "openai/gpt-5.6",
        "anthropic/claude-sonnet-5",
        "gpt-5.3-codex",
        "gpt-6-astra",
        "devstral-2512",
        "mistral/devstral-2512",
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


def test_gpt_5_6_models_are_verified_for_openai():
    assert {
        "gpt-5.6",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-6-astra",
    }.issubset(VERIFIED_OPENAI_MODELS)


def test_kimi_k3_and_claude_opus_5_are_verified():
    assert "kimi-k3" in VERIFIED_MODELS["moonshot"]
    assert "kimi-k3" in VERIFIED_OPENHANDS_MODELS
    assert "claude-opus-5" in VERIFIED_MODELS["anthropic"]
    assert "claude-opus-5" in VERIFIED_OPENHANDS_MODELS


def test_nemotron_3_super_uses_full_infra_name():
    """The verified Nemotron Super entry must match the infra model name
    (``nemotron-3-super-120b-a12b``) and the short alias should not be listed.
    """
    full_name = "nemotron-3-super-120b-a12b"
    assert full_name in VERIFIED_MODELS["nvidia"]
    assert full_name in VERIFIED_OPENHANDS_MODELS
    for provider, models in VERIFIED_MODELS.items():
        assert "nemotron-3-super" not in models, (
            f"Short alias 'nemotron-3-super' should not be in provider {provider!r}"
        )


def test_openhands_haiku_uses_full_infra_name():
    """The OpenHands proxy serves dated snapshots for some Anthropic models
    (``claude-haiku-4-5-20251001``); bare aliases that the proxy does not know
    must not be offered under the OpenHands provider.
    """
    assert "claude-haiku-4-5" not in VERIFIED_OPENHANDS_MODELS
    # VERIFIED_ANTHROPIC_MODELS keeps the dated name; direct-Anthropic BYOK is fine.
    assert "claude-haiku-4-5-20251001" in VERIFIED_MODELS["anthropic"]


def test_verified_lists_keep_two_latest_versions_per_line():
    """Check the curation rule for every provider: the two latest versions of a
    line are present and the version before them is gone (see the module
    docstring and ``llm/utils/AGENTS.md``). Update the table when a new version
    lands.
    """
    expectations = {
        "openai": ({"gpt-6-astra", "gpt-5.6"}, {"gpt-5.5", "gpt-5.4", "gpt-4o", "o3"}),
        "anthropic": (
            {"claude-opus-5-5", "claude-opus-5"},
            {"claude-opus-4-8", "claude-opus-4-7"},
        ),
        "mistral": (
            {"devstral-2512", "devstral-medium-2512"},
            {"devstral-medium-2507"},
        ),
        "gemini": ({"gemini-3.8-flash", "gemini-3.7-flash"}, {"gemini-3.6-flash"}),
        "deepseek": ({"deepseek-v4-pro", "deepseek-v3.2-reasoner"}, set()),
        "moonshot": ({"kimi-k3", "kimi-k2.7-code"}, {"kimi-k2.6"}),
        "minimax": ({"minimax-m3", "minimax-m2.7"}, {"minimax-m2.5"}),
        "glm": ({"glm-5.3", "glm-5.2"}, {"glm-5.1"}),
        "nvidia": ({"nemotron-3.5-lightning-30b-a3b", "nemotron-3-nano"}, set()),
        "qwen": ({"qwen3.8-max", "qwen3.7-max"}, {"qwen3-max", "qwen3-6-plus"}),
    }
    assert set(expectations) == set(VERIFIED_MODELS) - {"openhands"}
    for provider, (present, absent) in expectations.items():
        models = set(VERIFIED_MODELS[provider])
        assert present <= models, f"{provider}: missing {present - models}"
        assert not (absent & models), f"{provider}: stale {absent & models}"
    assert {"gpt-6-astra", "gpt-5.6", "claude-opus-5-5", "claude-opus-5"} <= set(
        VERIFIED_OPENHANDS_MODELS
    )
    assert not {"gpt-5.5", "claude-opus-4-8", "claude-opus-4-7", "minimax-m2.5"} & set(
        VERIFIED_OPENHANDS_MODELS
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
