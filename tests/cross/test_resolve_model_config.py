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
    ROUTER_CONFIG_KIND,
    check_model,
    find_models_by_id,
    is_router_config,
    run_preflight_check,
)


class LLMConfig(BaseModel):
    """Pydantic model for LLM configuration validation."""

    model: str
    temperature: float | None = None
    top_p: float | None = None
    reasoning_effort: str | None = None
    disable_vision: bool | None = None
    inline_image_urls: bool | None = None
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


class RouterLLMConfig(BaseModel):
    """Pydantic model for ``intelligent-router-v0`` configuration validation.

    Router configs have no top-level ``model`` field; they carry a ``kind``
    discriminator and a ``tiers`` map. Each tier value must itself satisfy
    :class:`LLMConfig` (i.e. be a real downstream model config).
    """

    kind: str
    classifier_model_id: str
    fallback_model_id: str
    tiers: dict[str, LLMConfig]
    routing: dict[str, str]
    vision_capable_model_ids: list[str] | None = None

    @field_validator("kind")
    @classmethod
    def kind_must_be_router_v0(cls, v: str) -> str:
        if v != ROUTER_CONFIG_KIND:
            raise ValueError(f"router kind must be '{ROUTER_CONFIG_KIND}', got '{v}'")
        return v

    @model_validator(mode="after")
    def references_are_consistent(self) -> "RouterLLMConfig":
        if self.classifier_model_id not in self.tiers:
            raise ValueError(
                f"classifier_model_id '{self.classifier_model_id}' "
                "must be a key in tiers"
            )
        if self.fallback_model_id not in self.tiers:
            raise ValueError(
                f"fallback_model_id '{self.fallback_model_id}' must be a key in tiers"
            )
        unknown = {v for v in self.routing.values() if v not in self.tiers}
        if unknown:
            raise ValueError(f"routing targets not present in tiers: {sorted(unknown)}")
        if self.vision_capable_model_ids is not None:
            stray = [
                mid for mid in self.vision_capable_model_ids if mid not in self.tiers
            ]
            if stray:
                raise ValueError(
                    f"vision_capable_model_ids not present in tiers: {stray}"
                )
        return self


class EvalModelConfig(BaseModel):
    """Pydantic model for evaluation model configuration validation.

    ``llm_config`` is either a plain :class:`LLMConfig` or, when the entry
    represents intelligent routing (``kind: intelligent-router-v0``), a
    :class:`RouterLLMConfig`.
    """

    id: str
    display_name: str
    llm_config: RouterLLMConfig | LLMConfig

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

    with patch.dict("resolve_model_config.MODELS", mock_models, clear=True):
        result = find_models_by_id(model_ids)

    assert len(result) == 1
    assert result[0]["id"] == "gpt-4"
    assert result[0]["display_name"] == "GPT-4"


def test_find_models_by_id_multiple_models():
    """Test finding multiple models by ID."""
    mock_models = {
        "gpt-4": {"id": "gpt-4", "display_name": "GPT-4", "llm_config": {}},
        "gpt-3.5": {"id": "gpt-3.5", "display_name": "GPT-3.5", "llm_config": {}},
        "claude-3": {"id": "claude-3", "display_name": "Claude 3", "llm_config": {}},
    }
    model_ids = ["gpt-4", "claude-3"]

    with patch.dict("resolve_model_config.MODELS", mock_models, clear=True):
        result = find_models_by_id(model_ids)

    assert len(result) == 2
    assert result[0]["id"] == "gpt-4"
    assert result[1]["id"] == "claude-3"


def test_find_models_by_id_preserves_order():
    """Test that model order matches the requested IDs order."""
    mock_models = {
        "a": {"id": "a", "display_name": "A", "llm_config": {}},
        "b": {"id": "b", "display_name": "B", "llm_config": {}},
        "c": {"id": "c", "display_name": "C", "llm_config": {}},
    }
    model_ids = ["c", "a", "b"]

    with patch.dict("resolve_model_config.MODELS", mock_models, clear=True):
        result = find_models_by_id(model_ids)

    assert len(result) == 3
    assert [m["id"] for m in result] == model_ids


def test_find_models_by_id_missing_model_exits():
    """Test that missing model ID causes exit."""

    mock_models = {
        "gpt-4": {"id": "gpt-4", "display_name": "GPT-4", "llm_config": {}},
    }
    model_ids = ["gpt-4", "nonexistent"]

    with patch.dict("resolve_model_config.MODELS", mock_models, clear=True):
        with pytest.raises(SystemExit) as exc_info:
            find_models_by_id(model_ids)

    assert exc_info.value.code == 1


def test_find_models_by_id_empty_list():
    """Test finding models with empty list."""
    mock_models = {
        "gpt-4": {"id": "gpt-4", "display_name": "GPT-4", "llm_config": {}},
    }
    model_ids = []

    with patch.dict("resolve_model_config.MODELS", mock_models, clear=True):
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

    with patch.dict("resolve_model_config.MODELS", mock_models, clear=True):
        result = find_models_by_id(model_ids)

    assert len(result) == 1
    assert result[0]["id"] == "custom-model"
    assert result[0]["llm_config"]["model"] == "custom-model"
    assert result[0]["llm_config"]["api_key"] == "test-key"
    assert result[0]["extra_field"] == "should be preserved"


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


def test_gpt_5_3_codex_config():
    """Test that gpt-5-3-codex has correct configuration."""
    model = MODELS["gpt-5-3-codex"]

    assert model["id"] == "gpt-5-3-codex"
    assert model["display_name"] == "GPT-5.3 Codex"
    assert model["llm_config"]["model"] == "litellm_proxy/gpt-5-3-codex"


def test_glm_5_config():
    """Test that glm-5 has correct configuration."""
    model = MODELS["glm-5"]

    assert model["id"] == "glm-5"
    assert model["display_name"] == "GLM-5"
    assert model["llm_config"]["model"] == "litellm_proxy/openrouter/z-ai/glm-5"
    assert model["llm_config"]["disable_vision"] is True


def test_glm_5_1_config():
    """Test that glm-5.1 has correct configuration."""
    model = MODELS["glm-5.1"]

    assert model["id"] == "glm-5.1"
    assert model["display_name"] == "GLM-5.1"
    assert model["llm_config"]["model"] == "litellm_proxy/openrouter/z-ai/glm-5.1"
    assert model["llm_config"]["disable_vision"] is True


# Tests for preflight check functionality


class TestTestModel:
    """Tests for the check_model function."""

    def test_successful_response(self):
        """Test that a successful model response returns True."""
        model_config = {
            "display_name": "Test Model",
            "llm_config": {"model": "litellm_proxy/test-model"},
        }
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="OK"))]

        with patch("litellm.completion", return_value=mock_response):
            success, message = check_model(model_config, "test-key", "https://test.com")

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
        mock_response.choices = [
            MagicMock(message=MagicMock(content="", reasoning_content=None))
        ]

        with patch("litellm.completion", return_value=mock_response):
            success, message = check_model(model_config, "test-key", "https://test.com")

        assert success is False
        assert "✗" in message
        assert "Empty response" in message

    def test_thinking_model_success(self):
        """Test that a thinking model with only reasoning_content passes."""
        model_config = {
            "display_name": "Thinking Model",
            "llm_config": {"model": "litellm_proxy/thinking-model"},
        }
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(content="", reasoning_content="Let me think...")
            )
        ]

        with patch("litellm.completion", return_value=mock_response):
            success, message = check_model(model_config, "test-key", "https://test.com")

        assert success is True
        assert "✓" in message

    def test_model_without_reasoning_content_attribute(self):
        """Test that models whose Message object lacks reasoning_content don't raise."""
        from types import SimpleNamespace

        model_config = {
            "display_name": "Standard Model",
            "llm_config": {"model": "litellm_proxy/standard-model"},
        }
        mock_response = MagicMock()
        # SimpleNamespace has only the attributes we give it - no reasoning_content
        message = SimpleNamespace(content="2")
        choice = MagicMock()
        choice.message = message
        mock_response.choices = [choice]

        with patch("litellm.completion", return_value=mock_response):
            success, message_str = check_model(
                model_config, "test-key", "https://test.com"
            )

        assert success is True
        assert "✓" in message_str

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
            success, message = check_model(model_config, "test-key", "https://test.com")

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
            success, message = check_model(model_config, "test-key", "https://test.com")

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
            success, message = check_model(model_config, "test-key", "https://test.com")

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
            check_model(model_config, "test-key", "https://test.com")

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
            with (
                patch(
                    "resolve_model_config._check_proxy_reachable",
                    return_value=(True, "Proxy reachable"),
                ),
                patch("litellm.completion", return_value=mock_response),
            ):
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
            with (
                patch(
                    "resolve_model_config._check_proxy_reachable",
                    return_value=(True, "Proxy reachable"),
                ),
                patch("litellm.completion", side_effect=mock_completion),
            ):
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


def test_gpt_5_4_config():
    """Test that gpt-5.4 has correct configuration."""
    model = MODELS["gpt-5.4"]

    assert model["id"] == "gpt-5.4"
    assert model["display_name"] == "GPT-5.4"
    assert model["llm_config"]["model"] == "litellm_proxy/openai/gpt-5.4"
    assert model["llm_config"]["reasoning_effort"] == "high"


def test_nemotron_3_super_120b_a12b_config():
    """Test that nemotron-3-super-120b-a12b has correct configuration."""
    model = MODELS["nemotron-3-super-120b-a12b"]

    assert model["id"] == "nemotron-3-super-120b-a12b"
    assert model["display_name"] == "NVIDIA Nemotron-3 Super 120B"
    assert (
        model["llm_config"]["model"]
        == "litellm_proxy/nvidia/nemotron-3-super-120b-a12b"
    )
    assert model["llm_config"]["temperature"] == 0.0


def test_converse_nemotron_super_3_120b_config():
    """Test that converse-nemotron-super-3-120b has correct configuration."""
    model = MODELS["converse-nemotron-super-3-120b"]

    assert model["id"] == "converse-nemotron-super-3-120b"
    assert model["display_name"] == "NVIDIA Converse Nemotron Super 3 120B"
    assert (
        model["llm_config"]["model"] == "litellm_proxy/converse-nemotron-super-3-120b"
    )
    assert model["llm_config"]["temperature"] == 0.0


def test_qwen3_6_plus_config():
    """Test that qwen3.6-plus has correct configuration."""
    model = MODELS["qwen3.6-plus"]

    assert model["id"] == "qwen3.6-plus"
    assert model["display_name"] == "Qwen3.6 Plus"
    assert model["llm_config"]["model"] == "litellm_proxy/dashscope/qwen3.6-plus"
    assert model["llm_config"]["temperature"] == 0.0


def test_trinity_large_thinking_config():
    """Test that trinity-large-thinking has correct configuration."""
    model = MODELS["trinity-large-thinking"]

    assert model["id"] == "trinity-large-thinking"
    assert model["display_name"] == "Trinity Large Thinking"
    assert model["llm_config"]["model"] == "litellm_proxy/trinity-large-thinking"
    assert model["llm_config"]["temperature"] == 1.0
    assert model["llm_config"]["top_p"] == 0.95


def test_claude_opus_4_7_config():
    """Test that claude-opus-4-7 has correct configuration."""
    model = MODELS["claude-opus-4-7"]

    assert model["id"] == "claude-opus-4-7"
    assert model["display_name"] == "Claude Opus 4.7"
    assert model["llm_config"]["model"] == "litellm_proxy/anthropic/claude-opus-4-7"


def test_kimi_k2_6_config():
    """Test that kimi-k2.6 has correct configuration."""
    model = MODELS["kimi-k2.6"]

    assert model["id"] == "kimi-k2.6"
    assert model["display_name"] == "Kimi K2.6"
    assert model["llm_config"]["model"] == "litellm_proxy/moonshot/kimi-k2.6"
    assert model["llm_config"]["temperature"] == 1.0


def test_gpt_5_5_config():
    """Test that gpt-5.5 has correct configuration."""
    model = MODELS["gpt-5.5"]

    assert model["id"] == "gpt-5.5"
    assert model["display_name"] == "GPT-5.5"
    assert model["llm_config"]["model"] == "litellm_proxy/openai/gpt-5.5"
    assert model["llm_config"]["reasoning_effort"] == "high"


def test_deepseek_v4_pro_config():
    """Test that deepseek-v4-pro has correct configuration."""
    model = MODELS["deepseek-v4-pro"]

    assert model["id"] == "deepseek-v4-pro"
    assert model["display_name"] == "DeepSeek V4 Pro"
    assert model["llm_config"]["model"] == "litellm_proxy/deepseek/deepseek-v4-pro"


def test_deepseek_v4_flash_config():
    """Test that deepseek-v4-flash has correct configuration."""
    model = MODELS["deepseek-v4-flash"]

    assert model["id"] == "deepseek-v4-flash"
    assert model["display_name"] == "DeepSeek V4 Flash"
    assert model["llm_config"]["model"] == "litellm_proxy/deepseek/deepseek-v4-flash"


def test_gemini_3_5_flash_config():
    """Test that gemini-3.5-flash has correct configuration."""
    model = MODELS["gemini-3.5-flash"]

    assert model["id"] == "gemini-3.5-flash"
    assert model["display_name"] == "Gemini 3.5 Flash"
    assert model["llm_config"]["model"] == "litellm_proxy/gemini-3.5-flash"
    assert model["llm_config"]["temperature"] == 0.0
    assert model["llm_config"]["inline_image_urls"] is True


def test_gpt_oss_120b_config():
    """Test that gpt-oss-120b has correct configuration."""
    model = MODELS["gpt-oss-120b"]

    assert model["id"] == "gpt-oss-120b"
    assert model["display_name"] == "GPT OSS 120B"
    assert (
        model["llm_config"]["model"] == "litellm_proxy/openrouter/openai/gpt-oss-120b"
    )


def test_nemotron_3_ultra_550b_a55b_config():
    """Test that nemotron-3-ultra-550b-a55b has correct configuration."""
    model = MODELS["nemotron-3-ultra-550b-a55b"]

    assert model["id"] == "nemotron-3-ultra-550b-a55b"
    assert model["display_name"] == "NVIDIA Nemotron-3 Ultra 550B"
    assert model["llm_config"]["model"] == "litellm_proxy/nemotron-3-ultra-550b-a55b"
    assert model["llm_config"]["temperature"] == 1.0
    assert model["llm_config"]["top_p"] == 0.95


def test_nemotron_3_ultra_550b_a55b_or_paid_config():
    """Test nemotron-3-ultra-550b-a55b-or-paid (paid OpenRouter route) config."""
    model = MODELS["nemotron-3-ultra-550b-a55b-or-paid"]

    assert model["id"] == "nemotron-3-ultra-550b-a55b-or-paid"
    assert model["display_name"] == "NVIDIA Nemotron-3 Ultra 550B (OpenRouter, paid)"
    assert (
        model["llm_config"]["model"]
        == "litellm_proxy/nemotron-3-ultra-550b-a55b-or-paid"
    )
    assert model["llm_config"]["temperature"] == 1.0
    assert model["llm_config"]["top_p"] == 0.95


def test_claude_opus_4_8_config():
    """Test that claude-opus-4-8 has correct configuration."""
    model = MODELS["claude-opus-4-8"]

    assert model["id"] == "claude-opus-4-8"
    assert model["display_name"] == "Claude Opus 4.8"
    assert model["llm_config"]["model"] == "litellm_proxy/anthropic/claude-opus-4-8"


def test_minimax_m3_config():
    """Test that minimax-m3 has correct configuration."""
    model = MODELS["minimax-m3"]

    assert model["id"] == "minimax-m3"
    assert model["display_name"] == "MiniMax M3"
    assert model["llm_config"]["model"] == "litellm_proxy/minimax/MiniMax-M3"
    assert model["llm_config"]["temperature"] == 1.0
    assert model["llm_config"]["top_p"] == 0.95


def test_step_3_7_flash_config():
    """Test that step-3.7-flash has correct configuration.

    The model path must match the eval LiteLLM proxy's `model_name` alias
    exactly so that `_get_model_info_from_litellm_proxy` matches the
    registry entry and picks up `supports_vision=true` from the
    proxy-side `model_info`.

    The retry envelope is bumped above the SDK default (5/8/64) because
    StepFun caps this model at 10 RPM on the current tier and parallel
    inference otherwise drains the retry budget before the rate-limit
    bucket resets, see OpenHands/software-agent-sdk#3496.
    """
    model = MODELS["step-3.7-flash"]

    assert model["id"] == "step-3.7-flash"
    assert model["display_name"] == "Step 3.7 Flash"
    assert model["llm_config"]["model"] == "litellm_proxy/step-3.7-flash"
    assert model["llm_config"]["temperature"] == 0.0

    # Retry settings must be at least these values to weather StepFun's
    # 10 RPM tier under parallel inference.
    assert model["llm_config"]["num_retries"] >= 10
    assert model["llm_config"]["retry_min_wait"] >= 15
    assert model["llm_config"]["retry_max_wait"] >= 90


# ---------------------------------------------------------------------------
# Tests for the intelligent-router-v0 entry (``router-classified-3tier``).
# ---------------------------------------------------------------------------


class TestRouterClassified3Tier:
    """Tests for the ``router-classified-3tier`` MODELS entry."""

    def test_entry_is_router_shaped(self):
        model = MODELS["router-classified-3tier"]

        assert model["id"] == "router-classified-3tier"
        # is_router_config is the canonical predicate consumed by check_model
        # and (via re-export) by build_matrix in OpenHands/evaluation.
        assert is_router_config(model["llm_config"])
        # Router payloads have NO top-level "model" — they would otherwise be
        # mistaken for a regular LLM config by downstream tooling.
        assert "model" not in model["llm_config"]

    def test_router_references_are_internally_consistent(self):
        """classifier/fallback IDs and all routing targets must exist in tiers."""
        cfg = MODELS["router-classified-3tier"]["llm_config"]
        tiers = cfg["tiers"]

        assert cfg["classifier_model_id"] in tiers
        assert cfg["fallback_model_id"] in tiers
        for category, target in cfg["routing"].items():
            assert target in tiers, (
                f"routing target for '{category}' missing from tiers: {target}"
            )
        for vision_mid in cfg.get("vision_capable_model_ids", []):
            assert vision_mid in tiers, (
                f"vision_capable model id missing from tiers: {vision_mid}"
            )

    def test_tier_configs_are_real_litellm_proxy_models(self):
        """Each tier value must itself be a valid plain LLM config."""
        cfg = MODELS["router-classified-3tier"]["llm_config"]
        for tier_id, tier_cfg in cfg["tiers"].items():
            assert tier_cfg["model"].startswith("litellm_proxy/"), (
                f"tier '{tier_id}' model must use litellm_proxy/: {tier_cfg['model']}"
            )
            # Pydantic LLMConfig acceptance is enforced by the registry-wide
            # ``test_all_models_valid_with_pydantic`` test, but we additionally
            # exercise it directly here to give a focused failure message.
            LLMConfig.model_validate(tier_cfg)

    def test_routing_table_matches_iter5_classifier_categories(self):
        """The 5 categories from the iter5 classifier prompt must all be mapped."""
        cfg = MODELS["router-classified-3tier"]["llm_config"]
        expected_categories = {
            "Frontend",
            "Issue Resolution (other)",
            "Greenfield",
            "Testing",
            "Information Gathering",
        }
        assert set(cfg["routing"].keys()) == expected_categories

    def test_router_config_validates_with_pydantic(self):
        """The router entry must satisfy the RouterLLMConfig validator."""
        # This is also covered transitively by ``test_all_models_valid_with_pydantic``
        # via ``EvalModelConfig.llm_config: RouterLLMConfig | LLMConfig``, but a
        # direct call keeps the failure mode obvious if the union ordering ever
        # changes.
        cfg = MODELS["router-classified-3tier"]["llm_config"]
        RouterLLMConfig.model_validate(cfg)


class TestIsRouterConfig:
    """Tests for the ``is_router_config`` predicate."""

    def test_plain_llm_config_is_not_router(self):
        assert not is_router_config({"model": "litellm_proxy/foo"})

    def test_missing_kind_is_not_router(self):
        assert not is_router_config({"tiers": {"a": {"model": "litellm_proxy/x"}}})

    def test_missing_tiers_is_not_router(self):
        assert not is_router_config({"kind": ROUTER_CONFIG_KIND})

    def test_wrong_kind_is_not_router(self):
        assert not is_router_config(
            {"kind": "some-other-kind", "tiers": {"a": {"model": "litellm_proxy/x"}}}
        )

    def test_canonical_router_payload_is_router(self):
        assert is_router_config(
            {
                "kind": ROUTER_CONFIG_KIND,
                "tiers": {"a": {"model": "litellm_proxy/x"}},
            }
        )

    def test_non_dict_inputs_are_not_router(self):
        # The signature is typed as dict, but downstream code in
        # build_matrix.py may pass arbitrary JSON values; we guard regardless.
        assert not is_router_config(None)  # type: ignore[arg-type]
        assert not is_router_config([])  # type: ignore[arg-type]
        assert not is_router_config("string")  # type: ignore[arg-type]


class TestCheckModelRouterRecursion:
    """Tests for ``check_model``'s recursion into router tiers.

    These tests exercise the real ``check_model`` code path with
    ``litellm.completion`` mocked, the same pattern as the existing
    ``TestTestModel`` class above.
    """

    def _router_entry(self) -> dict[str, Any]:
        return {
            "id": "router-test",
            "display_name": "Router Test",
            "llm_config": {
                "kind": ROUTER_CONFIG_KIND,
                "classifier_model_id": "a",
                "fallback_model_id": "b",
                "tiers": {
                    "a": {"model": "litellm_proxy/provider/a"},
                    "b": {"model": "litellm_proxy/provider/b"},
                },
                "routing": {"Frontend": "a", "Issue Resolution (other)": "b"},
            },
        }

    def test_all_tiers_succeed(self):
        """When every tier sub-model passes, the router entry passes."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="OK"))]

        with patch("litellm.completion", return_value=mock_response) as mock_completion:
            success, message = check_model(
                self._router_entry(), "k", "https://t", timeout=5
            )

        assert success is True
        assert "Router Test" in message
        assert "2 tier(s)" in message
        # Each tier triggered exactly one litellm completion call.
        assert mock_completion.call_count == 2
        called_models = sorted(
            call.kwargs["model"] for call in mock_completion.call_args_list
        )
        assert called_models == [
            "litellm_proxy/provider/a",
            "litellm_proxy/provider/b",
        ]

    def test_one_tier_failure_fails_router(self):
        """If a single tier fails, the router entry as a whole fails."""
        import litellm

        ok_response = MagicMock()
        ok_response.choices = [MagicMock(message=MagicMock(content="OK"))]

        def side_effect(*_args, model: str, **_kwargs):
            if model.endswith("/b"):
                raise litellm.exceptions.NotFoundError(
                    "no such model", llm_provider="x", model=model
                )
            return ok_response

        with patch("litellm.completion", side_effect=side_effect):
            success, message = check_model(
                self._router_entry(), "k", "https://t", timeout=5
            )

        assert success is False
        assert "one or more tiers failed" in message
        # Diagnostic still surfaces the per-tier line.
        assert "litellm_proxy/provider/b" in message or "not found" in message

    def test_empty_tiers_fails_loudly(self):
        """A router with an empty tiers map should fail preflight, not crash."""
        entry = self._router_entry()
        entry["llm_config"]["tiers"] = {}
        # ``check_model`` short-circuits before calling litellm at all.
        with patch("litellm.completion") as mock_completion:
            success, message = check_model(entry, "k", "https://t", timeout=5)
        assert success is False
        assert "no tiers" in message
        mock_completion.assert_not_called()

    def test_tier_config_params_are_forwarded(self):
        """Tier-level temperature/top_p/etc. must reach litellm.completion."""
        entry = self._router_entry()
        entry["llm_config"]["tiers"]["a"]["temperature"] = 0.5
        entry["llm_config"]["tiers"]["a"]["top_p"] = 0.9

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="OK"))]
        with patch("litellm.completion", return_value=mock_response) as mock_completion:
            check_model(entry, "k", "https://t", timeout=5)

        # Find the call for tier 'a' specifically.
        a_call = next(
            call
            for call in mock_completion.call_args_list
            if call.kwargs["model"].endswith("/a")
        )
        assert a_call.kwargs["temperature"] == 0.5
        assert a_call.kwargs["top_p"] == 0.9
