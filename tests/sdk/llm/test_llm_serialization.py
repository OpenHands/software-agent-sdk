"""Test LLM JSON serialization and deserialization."""

import json

from pydantic import BaseModel, SecretStr

from openhands.sdk.llm import LLM
from openhands.sdk.llm.utils.metrics import Metrics


def test_llm_basic_json_serialization() -> None:
    """Test that LLM supports basic JSON serialization/deserialization."""
    # Create LLM with basic configuration
    llm = LLM(
        model="test-model",
        temperature=0.5,
        max_output_tokens=1000,
        usage_id="test-llm",
    )

    # Serialize to JSON
    llm_json = llm.model_dump_json()

    # Deserialize from JSON
    deserialized_llm = LLM.model_validate_json(llm_json)

    # Should have same core fields
    assert deserialized_llm.model_dump() == llm.model_dump()


def test_llm_secret_fields_serialization() -> None:
    """Test that SecretStr fields are handled correctly during serialization."""
    # Create LLM with secret fields
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("secret-api-key"),
        aws_access_key_id=SecretStr("aws-access-key"),
        aws_secret_access_key=SecretStr("aws-secret-key"),
    )

    # Serialize to dict to check secret handling
    llm_dict = llm.model_dump()

    # Secret fields should be SecretStr objects with masked values in dict serialization
    assert isinstance(llm_dict["api_key"], SecretStr)
    assert llm_dict["api_key"].get_secret_value() == "secret-api-key"
    assert isinstance(llm_dict["aws_access_key_id"], SecretStr)
    assert llm_dict["aws_access_key_id"].get_secret_value() == "aws-access-key"
    assert isinstance(llm_dict["aws_secret_access_key"], SecretStr)
    assert llm_dict["aws_secret_access_key"].get_secret_value() == "aws-secret-key"

    # Serialize to JSON
    llm_json = llm.model_dump_json()

    # Deserialize from JSON
    deserialized_llm = LLM.model_validate_json(llm_json)

    # Secret fields should be None objects after JSON Deserialization
    assert deserialized_llm.api_key is None
    assert deserialized_llm.aws_access_key_id is None
    assert deserialized_llm.aws_secret_access_key is None


def test_llm_model_dump_json_masks_secrets() -> None:
    """Test that JSON serialization masks secrets by default."""
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("secret-api-key"),
    )

    dumped = llm.model_dump_json()
    assert "secret-api-key" not in dumped
    assert "**********" in dumped


def test_llm_excluded_fields_not_serialized() -> None:
    """Test that excluded fields are not included in serialization."""
    # Create LLM with excluded fields
    llm = LLM(model="test-model", usage_id="test-llm")

    # Serialize to dict
    llm_dict = llm.model_dump()

    # Excluded fields should not be present
    assert "metrics" not in llm_dict
    assert "retry_listener" not in llm_dict

    # Serialize to JSON and deserialize
    llm_json = llm.model_dump_json()
    deserialized_llm = LLM.model_validate_json(llm_json)

    # Excluded fields should have default values
    # (LLM automatically creates metrics during init)
    assert deserialized_llm.usage_id == "test-llm"
    assert isinstance(
        deserialized_llm.metrics, Metrics
    )  # LLM creates metrics automatically
    assert deserialized_llm.retry_listener is None


def test_llm_private_attributes_not_serialized() -> None:
    """Test that private attributes are not included in serialization."""
    # Create LLM
    llm = LLM(model="test-model", usage_id="test-llm")

    # Set private attributes (these would normally be set internally)
    llm._model_info = {"some": "info"}
    llm._tokenizer = "mock-tokenizer"

    # Serialize to dict
    llm_dict = llm.model_dump()

    # Private attributes should not be present
    assert "_model_info" not in llm_dict
    assert "_tokenizer" not in llm_dict
    assert "_telemetry" not in llm_dict

    # Serialize to JSON and deserialize
    llm_json = llm.model_dump_json()
    deserialized_llm = LLM.model_validate_json(llm_json)

    # Private attributes should have default values
    # (LLM creates telemetry automatically)
    assert deserialized_llm._model_info is None
    assert deserialized_llm._tokenizer is None
    assert deserialized_llm.native_tool_calling is True
    assert (
        deserialized_llm._telemetry is not None
    )  # LLM creates telemetry automatically
    assert deserialized_llm.model_dump() == llm.model_dump()


def test_llm_field_validation_during_deserialization() -> None:
    """Test that field validation works during deserialization."""
    # Create valid LLM dict
    llm_dict = {
        "model": "test-model",
        "temperature": 0.8,
        "num_retries": 3,
        "timeout": 30,
        "usage_id": "test-llm",
    }

    # Should deserialize successfully
    llm = LLM.model_validate(llm_dict)
    assert llm.model == "test-model"
    assert llm.temperature == 0.8
    assert llm.num_retries == 3
    assert llm.timeout == 30


def test_llm_supports_field_json_serialization() -> None:
    """Test that LLM supports JSON serialization when used as a field."""

    class Container(BaseModel):
        llm: LLM
        name: str

    # Create container with LLM
    llm = LLM(model="test-model", temperature=0.3, usage_id="test-llm")
    container = Container(llm=llm, name="test-container")

    # Serialize to JSON
    container_json = container.model_dump_json()

    # Deserialize from JSON
    deserialized_container = Container.model_validate_json(container_json)

    # Should preserve the LLM fields
    assert isinstance(deserialized_container.llm, LLM)
    assert deserialized_container.llm.model == llm.model
    assert deserialized_container.llm.temperature == llm.temperature
    assert deserialized_container.name == "test-container"
    assert deserialized_container.llm.model_dump() == llm.model_dump()


def test_llm_supports_nested_json_serialization() -> None:
    """Test that LLM supports nested JSON serialization."""

    class NestedContainer(BaseModel):
        llms: list[LLM]
        config_name: str

    # Create container with multiple LLMs
    llm1 = LLM(model="model-1", temperature=0.1, usage_id="test-llm")
    llm2 = LLM(model="model-2", temperature=0.9, usage_id="test-llm")
    container = NestedContainer(llms=[llm1, llm2], config_name="multi-llm")

    # Serialize to JSON
    container_json = container.model_dump_json()

    # Deserialize from JSON
    deserialized_container = NestedContainer.model_validate_json(container_json)

    # Should preserve all LLM fields
    assert len(deserialized_container.llms) == 2
    assert isinstance(deserialized_container.llms[0], LLM)
    assert isinstance(deserialized_container.llms[1], LLM)
    assert deserialized_container.llms[0].model == llm1.model
    assert deserialized_container.llms[1].model == llm2.model
    assert deserialized_container.llms[0].temperature == llm1.temperature
    assert deserialized_container.llms[1].temperature == llm2.temperature
    assert deserialized_container.config_name == "multi-llm"
    assert deserialized_container.llms[0].model_dump() == llm1.model_dump()
    assert deserialized_container.llms[1].model_dump() == llm2.model_dump()


def test_llm_model_validate_json_dict() -> None:
    """Test that LLM.model_validate works with dict from JSON."""
    # Create LLM
    llm = LLM(model="test-model", top_p=0.95, usage_id="test-llm")

    # Serialize to JSON, then parse to dict
    llm_json = llm.model_dump_json()
    llm_dict = json.loads(llm_json)

    # Deserialize from dict
    deserialized_llm = LLM.model_validate(llm_dict)

    assert deserialized_llm.model == llm.model
    assert deserialized_llm.top_p == llm.top_p
    assert deserialized_llm.model_dump() == llm.model_dump()
