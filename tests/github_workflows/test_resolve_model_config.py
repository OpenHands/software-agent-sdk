"""Tests for resolve_model_config.py GitHub Actions script."""

import sys
from pathlib import Path

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
    result = find_models_by_id(["claude-sonnet-4-5-20250929"])

    assert len(result) == 1
    assert result[0]["id"] == "claude-sonnet-4-5-20250929"
    assert result[0]["display_name"] == "Claude Sonnet 4.5"


def test_find_models_by_id_multiple_models():
    """Test finding multiple models by ID."""
    result = find_models_by_id(["claude-sonnet-4-5-20250929", "deepseek-chat"])

    assert len(result) == 2
    assert result[0]["id"] == "claude-sonnet-4-5-20250929"
    assert result[1]["id"] == "deepseek-chat"


def test_find_models_by_id_preserves_order():
    """Test that model order matches the requested IDs order."""
    model_ids = ["deepseek-chat", "claude-sonnet-4-5-20250929", "kimi-k2-thinking"]

    result = find_models_by_id(model_ids)

    assert len(result) == 3
    assert [m["id"] for m in result] == model_ids


def test_find_models_by_id_missing_model_exits():
    """Test that missing model ID causes exit."""
    with pytest.raises(SystemExit) as exc_info:
        find_models_by_id(["claude-sonnet-4-5-20250929", "nonexistent-model"])

    assert exc_info.value.code == 1


def test_find_models_by_id_empty_list():
    """Test finding models with empty list."""
    result = find_models_by_id([])

    assert result == []


def test_find_models_by_id_preserves_full_config():
    """Test that full model configuration is preserved."""
    result = find_models_by_id(["claude-sonnet-4-5-20250929"])

    assert len(result) == 1
    assert result[0]["id"] == "claude-sonnet-4-5-20250929"
    assert (
        result[0]["llm_config"]["model"] == "litellm_proxy/claude-sonnet-4-5-20250929"
    )
    assert result[0]["llm_config"]["temperature"] == 0.0


# Tests for expected models from issue #1495
EXPECTED_MODELS = [
    "claude-4.5-opus",
    "claude-4.5-sonnet",
    "gemini-3-pro",
    "gemini-3-flash",
    "gpt-5.2-high-reasoning",
    "gpt-5.2",
    "kimi-k2-thinking",
    "minimax-m2",
    "deepseek-v3.2-reasoner",
    "qwen-3-coder",
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
