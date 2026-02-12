"""Tests for resolve_model_config.py GitHub Actions script."""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel, field_validator, model_validator


# Import the functions from resolve_model_config.py
run_eval_path = Path(__file__).parent.parent.parent / ".github" / "run-eval"
sys.path.append(str(run_eval_path))
from resolve_model_config import (  # noqa: E402  # type: ignore[import-not-found]
    MODELS,
    find_models_by_id,
)


class LLMConfig(BaseModel):
    """Pydantic model for LLM configuration validation."""

    model: str
    temperature: float | None = None
    top_p: float | None = None
    reasoning_effort: str | None = None
    disable_vision: bool | None = None
    litellm_extra_body: dict[str, Any] | None = None

    @field_validator("model")
    @classmethod
    def model_must_start_with_litellm_proxy(cls, v: str) -> str:
        if not v.startswith("litellm_proxy/"):
            raise ValueError(f"model must start with 'litellm_proxy/', got '{v}'")
        return v

    @field_validator("temperature")
    @classmethod
    def temperature_in_range(cls, v: float | None) -> float | None:
        if v is not None and not (0.0 <= v <= 2.0):
            raise ValueError(f"temperature must be between 0.0 and 2.0, got {v}")
        return v

    @field_validator("top_p")
    @classmethod
    def top_p_in_range(cls, v: float | None) -> float | None:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError(f"top_p must be between 0.0 and 1.0, got {v}")
        return v

    @field_validator("reasoning_effort")
    @classmethod
    def reasoning_effort_valid(cls, v: str | None) -> str | None:
        valid_values = {"low", "medium", "high"}
        if v is not None and v not in valid_values:
            raise ValueError(
                f"reasoning_effort must be one of {valid_values}, got '{v}'"
            )
        return v


class EvalModelConfig(BaseModel):
    """Pydantic model for evaluation model configuration validation."""

    id: str
    display_name: str
    llm_config: LLMConfig

    @field_validator("id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("id cannot be empty")
        return v

    @field_validator("display_name")
    @classmethod
    def display_name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("display_name cannot be empty")
        return v


class EvalModelsRegistry(BaseModel):
    """Pydantic model for the entire MODELS registry validation."""

    models: dict[str, EvalModelConfig]

    @model_validator(mode="after")
    def id_matches_key(self) -> "EvalModelsRegistry":
        for key, config in self.models.items():
            if config.id != key:
                raise ValueError(
                    f"Model key '{key}' doesn't match id field '{config.id}'"
                )
        return self


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


def test_all_models_valid_with_pydantic():
    """Test that all models pass Pydantic validation.

    This single test validates:
    - All required fields are present (id, display_name, llm_config, llm_config.model)
    - Model id field matches dictionary key
    - model starts with 'litellm_proxy/'
    - temperature is between 0.0 and 2.0 (if present)
    - top_p is between 0.0 and 1.0 (if present)
    - reasoning_effort is one of 'low', 'medium', 'high' (if present)
    """
    # This will raise ValidationError if any model is invalid
    registry = EvalModelsRegistry(models=MODELS)
    assert len(registry.models) == len(MODELS)


def test_find_all_models():
    """Test that find_models_by_id works for all models."""
    all_model_ids = list(MODELS.keys())
    result = find_models_by_id(all_model_ids)

    assert len(result) == len(all_model_ids)
    for i, model_id in enumerate(all_model_ids):
        assert result[i]["id"] == model_id


def test_gpt_5_2_high_reasoning_config():
    """Test that gpt-5.2-high-reasoning has correct configuration."""
    model = MODELS["gpt-5.2-high-reasoning"]

    assert model["id"] == "gpt-5.2-high-reasoning"
    assert model["display_name"] == "GPT-5.2 High Reasoning"
    assert model["llm_config"]["model"] == "litellm_proxy/openai/gpt-5.2-2025-12-11"
    assert model["llm_config"]["reasoning_effort"] == "high"


def test_gpt_oss_20b_config():
    """Test that gpt-oss-20b has correct configuration."""
    model = MODELS["gpt-oss-20b"]

    assert model["id"] == "gpt-oss-20b"
    assert model["display_name"] == "GPT OSS 20B"
    assert model["llm_config"]["model"] == "litellm_proxy/gpt-oss-20b"
