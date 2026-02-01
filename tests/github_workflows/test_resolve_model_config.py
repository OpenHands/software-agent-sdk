"""Tests for resolve_model_config.py GitHub Actions script."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# Import the functions from resolve_model_config.py
run_eval_path = Path(__file__).parent.parent.parent / ".github" / "run-eval"
sys.path.append(str(run_eval_path))
from resolve_model_config import (  # noqa: E402  # type: ignore[import-not-found]
    MODELS,
    find_models_by_id,
)


def test_find_models_by_id_single_model():
    """Test finding a single model by ID."""
    mock_models = {
        "gpt-4": {"id": "gpt-4", "display_name": "GPT-4", "llm_config": {}},
        "gpt-3.5": {"id": "gpt-3.5", "display_name": "GPT-3.5", "llm_config": {}},
    }
    model_ids = ["gpt-4"]

    with patch.dict("resolve_model_config.MODELS", mock_models):
        result = find_models_by_id(model_ids)

    assert len(result) == 1
    assert result[0]["id"] == "claude-sonnet-4-5-20250929"
    assert result[0]["display_name"] == "Claude Sonnet 4.5"


def test_find_models_by_id_multiple_models():
    """Test finding multiple models by ID."""
    mock_models = {
        "gpt-4": {"id": "gpt-4", "display_name": "GPT-4", "llm_config": {}},
        "gpt-3.5": {"id": "gpt-3.5", "display_name": "GPT-3.5", "llm_config": {}},
        "claude-3": {"id": "claude-3", "display_name": "Claude 3", "llm_config": {}},
    }
    model_ids = ["gpt-4", "claude-3"]

    with patch.dict("resolve_model_config.MODELS", mock_models):
        result = find_models_by_id(model_ids)

    assert len(result) == 2
    assert result[0]["id"] == "claude-sonnet-4-5-20250929"
    assert result[1]["id"] == "deepseek-chat"


def test_find_models_by_id_preserves_order():
    """Test that model order matches the requested IDs order."""
    mock_models = {
        "a": {"id": "a", "display_name": "A", "llm_config": {}},
        "b": {"id": "b", "display_name": "B", "llm_config": {}},
        "c": {"id": "c", "display_name": "C", "llm_config": {}},
    }
    model_ids = ["c", "a", "b"]

    with patch.dict("resolve_model_config.MODELS", mock_models):
        result = find_models_by_id(model_ids)

    assert len(result) == 3
    assert [m["id"] for m in result] == model_ids


def test_find_models_by_id_missing_model_exits():
    """Test that missing model ID causes exit."""

    mock_models = {
        "gpt-4": {"id": "gpt-4", "display_name": "GPT-4", "llm_config": {}},
    }
    model_ids = ["gpt-4", "nonexistent"]

    with patch.dict("resolve_model_config.MODELS", mock_models):
        with pytest.raises(SystemExit) as exc_info:
            find_models_by_id(model_ids)

    assert exc_info.value.code == 1


def test_find_models_by_id_empty_list():
    """Test finding models with empty list."""
    mock_models = {
        "gpt-4": {"id": "gpt-4", "display_name": "GPT-4", "llm_config": {}},
    }
    model_ids = []

    with patch.dict("resolve_model_config.MODELS", mock_models):
        result = find_models_by_id(model_ids)

    assert result == []


def test_find_models_by_id_preserves_full_config():
    """Test that full model configuration is preserved."""
    mock_models = {
        "custom-model": {
            "id": "custom-model",
            "display_name": "Custom Model",
            "llm_config": {
                "model": "custom-model",
                "api_key": "test-key",
                "base_url": "https://example.com",
            },
            "extra_field": "should be preserved",
        }
    }
    model_ids = ["custom-model"]

    with patch.dict("resolve_model_config.MODELS", mock_models):
        result = find_models_by_id(model_ids)

    assert len(result) == 1
    assert result[0]["id"] == "claude-sonnet-4-5-20250929"
    assert (
        result[0]["llm_config"]["model"] == "litellm_proxy/claude-sonnet-4-5-20250929"
    )
    assert result[0]["llm_config"]["temperature"] == 0.0


# Tests for expected models from issue #1495
# Note: claude-4.5-sonnet is implemented as claude-sonnet-4-5-20250929 (pinned version)
EXPECTED_MODELS = [
    "claude-4.5-opus",
    "claude-sonnet-4-5-20250929",
    "gemini-3-pro",
    "gemini-3-flash",
    "gpt-5.2",
    "gpt-5.2-high-reasoning",
    "kimi-k2-thinking",
    "minimax-m2",
    "minimax-m2.1",
    "deepseek-v3.2-reasoner",
    "qwen-3-coder",
    "glm-4.7",
]


def test_all_expected_models_present():
    """Test that all expected models from issue #1495 are present."""
    for model_id in EXPECTED_MODELS:
        assert model_id in MODELS, f"Model '{model_id}' is missing from MODELS"


def test_expected_models_have_required_fields():
    """Test that all expected models have required fields."""
    for model_id in EXPECTED_MODELS:
        model = MODELS[model_id]
        assert "id" in model, f"Model '{model_id}' missing 'id' field"
        assert "display_name" in model, f"Model '{model_id}' missing 'display_name'"
        assert "llm_config" in model, f"Model '{model_id}' missing 'llm_config'"
        assert "model" in model["llm_config"], (
            f"Model '{model_id}' missing 'model' in llm_config"
        )


def test_expected_models_id_matches_key():
    """Test that model id field matches the dictionary key."""
    for model_id in EXPECTED_MODELS:
        model = MODELS[model_id]
        assert model["id"] == model_id, (
            f"Model key '{model_id}' doesn't match id field '{model['id']}'"
        )


def test_find_all_expected_models():
    """Test that find_models_by_id works for all expected models."""
    result = find_models_by_id(EXPECTED_MODELS)

    assert len(result) == len(EXPECTED_MODELS)
    for i, model_id in enumerate(EXPECTED_MODELS):
        assert result[i]["id"] == model_id


def test_gpt_5_2_high_reasoning_config():
    """Test that gpt-5.2-high-reasoning has correct configuration."""
    model = MODELS["gpt-5.2-high-reasoning"]

    assert model["id"] == "gpt-5.2-high-reasoning"
    assert model["display_name"] == "GPT-5.2 High Reasoning"
    assert model["llm_config"]["model"] == "litellm_proxy/openai/gpt-5.2-2025-12-11"
    assert model["llm_config"]["reasoning_effort"] == "high"
