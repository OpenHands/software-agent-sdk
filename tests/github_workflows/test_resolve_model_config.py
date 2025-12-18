"""Tests for resolve_model_configs.py GitHub Actions script."""

import sys
from pathlib import Path


# Import the functions from resolve_model_configs.py
run_eval_path = Path(__file__).parent.parent.parent / ".github" / "run-eval"
sys.path.append(str(run_eval_path))
from resolve_model_configs import (  # noqa: E402  # type: ignore[import-not-found]
    find_models_by_id,
)


def test_find_models_by_id_single_model():
    """Test finding a single model by ID."""
    models = [
        {"id": "gpt-4", "display_name": "GPT-4", "llm_config": {}},
        {"id": "gpt-3.5", "display_name": "GPT-3.5", "llm_config": {}},
    ]
    model_ids = ["gpt-4"]

    result = find_models_by_id(models, model_ids)

    assert len(result) == 1
    assert result[0]["id"] == "gpt-4"
    assert result[0]["display_name"] == "GPT-4"


def test_find_models_by_id_multiple_models():
    """Test finding multiple models by ID."""
    models = [
        {"id": "gpt-4", "display_name": "GPT-4", "llm_config": {}},
        {"id": "gpt-3.5", "display_name": "GPT-3.5", "llm_config": {}},
        {"id": "claude-3", "display_name": "Claude 3", "llm_config": {}},
    ]
    model_ids = ["gpt-4", "claude-3"]

    result = find_models_by_id(models, model_ids)

    assert len(result) == 2
    assert result[0]["id"] == "gpt-4"
    assert result[1]["id"] == "claude-3"


def test_find_models_by_id_preserves_order():
    """Test that model order matches the requested IDs order."""
    models = [
        {"id": "a", "display_name": "A", "llm_config": {}},
        {"id": "b", "display_name": "B", "llm_config": {}},
        {"id": "c", "display_name": "C", "llm_config": {}},
    ]
    model_ids = ["c", "a", "b"]

    result = find_models_by_id(models, model_ids)

    assert len(result) == 3
    assert [m["id"] for m in result] == ["c", "a", "b"]


def test_find_models_by_id_missing_model_exits():
    """Test that missing model ID causes exit."""
    import pytest

    models = [
        {"id": "gpt-4", "display_name": "GPT-4", "llm_config": {}},
    ]
    model_ids = ["gpt-4", "nonexistent"]

    with pytest.raises(SystemExit) as exc_info:
        find_models_by_id(models, model_ids)

    assert exc_info.value.code == 1


def test_find_models_by_id_empty_list():
    """Test finding models with empty list."""
    models = [
        {"id": "gpt-4", "display_name": "GPT-4", "llm_config": {}},
    ]
    model_ids = []

    result = find_models_by_id(models, model_ids)

    assert result == []


def test_find_models_by_id_preserves_full_config():
    """Test that full model configuration is preserved."""
    models = [
        {
            "id": "custom-model",
            "display_name": "Custom Model",
            "llm_config": {
                "model": "custom-model",
                "api_key": "test-key",
                "base_url": "https://example.com",
            },
            "extra_field": "should be preserved",
        }
    ]
    model_ids = ["custom-model"]

    result = find_models_by_id(models, model_ids)

    assert len(result) == 1
    assert result[0]["id"] == "custom-model"
    assert result[0]["llm_config"]["model"] == "custom-model"
    assert result[0]["extra_field"] == "should be preserved"
