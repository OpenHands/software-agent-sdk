from __future__ import annotations

from openhands.sdk.llm import LLM


class DummyFeatures:
    """Simple stub for get_features results."""

    def __init__(self, model: str):
        self.model = model
        # Treat only the canonical model as feature-enabled
        self.supports_prompt_cache = model == "openai/gpt-5-mini"
        self.supports_responses_api = model == "openai/gpt-5-mini"
        self.force_string_serializer = False
        self.send_reasoning_content = False


def test_model_canonical_name_used_for_capabilities(monkeypatch):
    """Proxy/aliased model uses model_canonical_name for capability lookups."""

    model_info_calls: list[str] = []
    vision_calls: list[str] = []
    feature_calls: list[str] = []

    def fake_get_model_info(secret_api_key, base_url, model):
        model_info_calls.append(model)
        if model == "openai/gpt-5-mini":
            return {"supports_vision": True, "max_input_tokens": 128000}
        return None

    def fake_supports_vision(model: str) -> bool:
        vision_calls.append(model)
        return model.endswith("gpt-5-mini")

    def fake_get_features(model: str):
        feature_calls.append(model)
        return DummyFeatures(model)

    monkeypatch.setattr(
        "openhands.sdk.llm.llm.get_litellm_model_info", fake_get_model_info
    )
    monkeypatch.setattr("openhands.sdk.llm.llm.supports_vision", fake_supports_vision)
    monkeypatch.setattr("openhands.sdk.llm.llm.get_features", fake_get_features)

    real_llm = LLM(model="openai/gpt-5-mini")
    proxy_llm = LLM(
        model="proxy/test-renamed-model", model_canonical_name="openai/gpt-5-mini"
    )

    # Model info and vision support come from the canonical model name
    assert real_llm.model_info == {"supports_vision": True, "max_input_tokens": 128000}
    assert proxy_llm.model_info == real_llm.model_info
    assert real_llm.vision_is_active() is True
    assert proxy_llm.vision_is_active() is True

    # Feature lookups (prompt cache / responses API) also respect model_canonical_name
    assert real_llm.is_caching_prompt_active() is True
    assert proxy_llm.is_caching_prompt_active() is True
    assert real_llm.uses_responses_api() is True
    assert proxy_llm.uses_responses_api() is True

    # Ensure capability lookups invoked the canonical name at least once
    assert "openai/gpt-5-mini" in model_info_calls
    assert "openai/gpt-5-mini" in vision_calls
    assert "openai/gpt-5-mini" in feature_calls


def test_model_canonical_name_with_real_model_info():
    """Integration-style check using litellm's built-in model info."""

    base = LLM(model="gpt-4o-mini")
    proxied = LLM(model="proxy/test-renamed-model", model_canonical_name="gpt-4o-mini")

    # Model info and derived flags should align with the canonical model
    assert proxied.model_info == base.model_info
    assert proxied.vision_is_active() == base.vision_is_active()
    assert proxied.is_caching_prompt_active() == base.is_caching_prompt_active()
    assert proxied.uses_responses_api() == base.uses_responses_api()
