"""Tests for resolve_model_config.py GitHub Actions script."""

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, field_validator, model_validator


# Import the functions from resolve_model_config.py
run_eval_path = Path(__file__).parent.parent.parent / ".github" / "run-eval"
sys.path.append(str(run_eval_path))
from resolve_model_config import (  # noqa: E402  # type: ignore[import-not-found]
    MODELS,
    find_models_by_id,
    run_preflight_check,
    test_model,
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


def test_glm_5_config():
    """Test that glm-5 has correct configuration."""
    model = MODELS["glm-5"]

    assert model["id"] == "glm-5"
    assert model["display_name"] == "GLM-5"
    assert model["llm_config"]["model"] == "litellm_proxy/openrouter/z-ai/glm-5"
    assert model["llm_config"]["disable_vision"] is True


# Tests for preflight check functionality


class TestTestModel:
    """Tests for the test_model function."""

    def test_successful_response(self):
        """Test that a successful model response returns True."""
        model_config = {
            "display_name": "Test Model",
            "llm_config": {"model": "litellm_proxy/test-model"},
        }
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="OK"))]

        with patch("litellm.completion", return_value=mock_response):
            success, message = test_model(model_config, "test-key", "https://test.com")

        assert success is True
        assert "✓" in message
        assert "Test Model" in message

    def test_empty_response(self):
        """Test that an empty response returns False."""
        model_config = {
            "display_name": "Test Model",
            "llm_config": {"model": "litellm_proxy/test-model"},
        }
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=""))]

        with patch("litellm.completion", return_value=mock_response):
            success, message = test_model(model_config, "test-key", "https://test.com")

        assert success is False
        assert "✗" in message
        assert "Empty response" in message

    def test_timeout_error(self):
        """Test that timeout errors are handled correctly."""
        import litellm

        model_config = {
            "display_name": "Test Model",
            "llm_config": {"model": "litellm_proxy/test-model"},
        }

        with patch(
            "litellm.completion",
            side_effect=litellm.exceptions.Timeout(
                message="Timeout", model="test-model", llm_provider="test"
            ),
        ):
            success, message = test_model(model_config, "test-key", "https://test.com")

        assert success is False
        assert "✗" in message
        assert "timed out" in message

    def test_connection_error(self):
        """Test that connection errors are handled correctly."""
        import litellm

        model_config = {
            "display_name": "Test Model",
            "llm_config": {"model": "litellm_proxy/test-model"},
        }

        with patch(
            "litellm.completion",
            side_effect=litellm.exceptions.APIConnectionError(
                message="Connection failed", llm_provider="test", model="test-model"
            ),
        ):
            success, message = test_model(model_config, "test-key", "https://test.com")

        assert success is False
        assert "✗" in message
        assert "Connection error" in message

    def test_model_not_found_error(self):
        """Test that model not found errors are handled correctly."""
        import litellm

        model_config = {
            "display_name": "Test Model",
            "llm_config": {"model": "litellm_proxy/test-model"},
        }

        with patch(
            "litellm.completion",
            side_effect=litellm.exceptions.NotFoundError(
                "Model not found", llm_provider="test", model="test-model"
            ),
        ):
            success, message = test_model(model_config, "test-key", "https://test.com")

        assert success is False
        assert "✗" in message
        assert "not found" in message

    def test_passes_llm_config_params(self):
        """Test that llm_config parameters are passed to litellm."""
        model_config = {
            "display_name": "Test Model",
            "llm_config": {
                "model": "litellm_proxy/test-model",
                "temperature": 0.5,
                "top_p": 0.9,
            },
        }
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="OK"))]

        with patch("litellm.completion", return_value=mock_response) as mock_completion:
            test_model(model_config, "test-key", "https://test.com")

        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args[1]
        assert call_kwargs["temperature"] == 0.5
        assert call_kwargs["top_p"] == 0.9


class TestRunPreflightCheck:
    """Tests for the run_preflight_check function."""

    def test_skip_when_no_api_key(self):
        """Test that preflight check is skipped when LLM_API_KEY is not set."""
        models = [{"display_name": "Test", "llm_config": {"model": "test"}}]

        with patch.dict("os.environ", {}, clear=True):
            result = run_preflight_check(models)

        assert result is True  # Skipped = success

    def test_skip_when_skip_preflight_true(self):
        """Test that preflight check is skipped when SKIP_PREFLIGHT=true."""
        models = [{"display_name": "Test", "llm_config": {"model": "test"}}]

        with patch.dict(
            "os.environ", {"LLM_API_KEY": "test", "SKIP_PREFLIGHT": "true"}
        ):
            result = run_preflight_check(models)

        assert result is True  # Skipped = success

    def test_all_models_pass(self):
        """Test that preflight check returns True when all models pass."""
        models = [
            {"display_name": "Model A", "llm_config": {"model": "model-a"}},
            {"display_name": "Model B", "llm_config": {"model": "model-b"}},
        ]
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="OK"))]

        with patch.dict("os.environ", {"LLM_API_KEY": "test"}):
            with patch("litellm.completion", return_value=mock_response):
                result = run_preflight_check(models)

        assert result is True

    def test_any_model_fails(self):
        """Test that preflight check returns False when any model fails."""
        models = [
            {"display_name": "Model A", "llm_config": {"model": "model-a"}},
            {"display_name": "Model B", "llm_config": {"model": "model-b"}},
        ]
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="OK"))]

        def mock_completion(**kwargs):
            if kwargs["model"] == "model-b":
                raise Exception("Model B failed")
            return mock_response

        with patch.dict("os.environ", {"LLM_API_KEY": "test"}):
            with patch("litellm.completion", side_effect=mock_completion):
                result = run_preflight_check(models)

        assert result is False


def test_models_importable_without_litellm():
    """Test that MODELS dictionary can be imported without litellm installed.

    This is critical for the integration-runner workflow which uses MODELS
    in the setup-matrix job without installing litellm. The import should
    work in a clean Python environment.

    Regression test for issue #2124.
    """
    # Get the repository root (where .github/ is located)
    repo_root = Path(__file__).parent.parent.parent

    script = """
import sys
sys.path.insert(0, '.github/run-eval')

# This import should succeed without litellm being installed
from resolve_model_config import MODELS

# Verify we got the MODELS dictionary
assert isinstance(MODELS, dict)
assert len(MODELS) > 0
print(f"SUCCESS: Imported {len(MODELS)} models without litellm")
"""

    # Run the script in a subprocess with a clean environment
    # This ensures litellm is not available in sys.modules
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )

    # Check that the script succeeded
    assert result.returncode == 0, (
        f"Failed to import MODELS without litellm.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "SUCCESS" in result.stdout
