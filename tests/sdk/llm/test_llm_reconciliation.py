"""Tests for LLM flexibility - LLM can be freely changed between sessions."""

from pydantic import SecretStr

from openhands.sdk.llm import LLM


def test_llm_can_be_created_with_different_models():
    """Test that LLM instances can be created with different models."""
    llm1 = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    llm2 = LLM(model="gpt-4o", api_key=SecretStr("test-key"), usage_id="test-llm")
    llm3 = LLM(
        model="claude-sonnet-4-20250514",
        api_key=SecretStr("test-key"),
        usage_id="test-llm",
    )

    # All LLMs should be valid and independent
    assert llm1.model == "gpt-4o-mini"
    assert llm2.model == "gpt-4o"
    assert llm3.model == "claude-sonnet-4-20250514"


def test_llm_can_have_different_api_keys():
    """Test that LLM instances can have different API keys."""
    llm1 = LLM(model="gpt-4o-mini", api_key=SecretStr("key-1"), usage_id="test-llm")
    llm2 = LLM(model="gpt-4o-mini", api_key=SecretStr("key-2"), usage_id="test-llm")

    assert llm1.api_key is not None
    assert llm2.api_key is not None
    assert isinstance(llm1.api_key, SecretStr)
    assert isinstance(llm2.api_key, SecretStr)
    assert llm1.api_key.get_secret_value() == "key-1"
    assert llm2.api_key.get_secret_value() == "key-2"


def test_llm_serialization_masks_secrets():
    """Test that LLM serialization properly masks secrets."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("secret-key"), usage_id="test-llm")

    # JSON mode serialization should mask secrets
    dumped_json = llm.model_dump(mode="json")
    assert dumped_json["api_key"] == "**********"

    # Expose secrets context should reveal them
    dumped_exposed = llm.model_dump(mode="json", context={"expose_secrets": True})
    assert dumped_exposed["api_key"] == "secret-key"
