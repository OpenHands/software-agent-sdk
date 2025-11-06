"""Test LLM JSON storage and loading functionality."""

import json
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk.llm import LLM


def test_llm_store_and_load_json():
    """Test storing LLM to JSON and loading back with fields unchanged."""
    # Create original LLM with secrets
    original_llm = LLM(
        usage_id="test-llm",
        model="test-model",
        temperature=0.7,
        max_output_tokens=2000,
        top_p=0.9,
        api_key=SecretStr("secret-api-key"),
        aws_access_key_id=SecretStr("aws-access-key"),
        aws_secret_access_key=SecretStr("aws-secret-key"),
        base_url="https://api.example.com",
        num_retries=3,
    )

    # Store to JSON and load back
    with tempfile.TemporaryDirectory() as temp_dir:
        filepath = Path(temp_dir) / "test_llm.json"

        # Store to JSON with secrets exposed
        data = original_llm.model_dump(context={"expose_secrets": True})
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

        loaded_llm = LLM.load_from_json(str(filepath))

        # Verify all fields remain unchanged
        assert loaded_llm.model == original_llm.model
        assert loaded_llm.temperature == original_llm.temperature
        assert loaded_llm.max_output_tokens == original_llm.max_output_tokens
        assert loaded_llm.top_p == original_llm.top_p
        assert loaded_llm.base_url == original_llm.base_url
        assert loaded_llm.num_retries == original_llm.num_retries

        # Verify secrets are preserved
        assert loaded_llm.api_key is not None
        assert loaded_llm.aws_access_key_id is not None
        assert loaded_llm.aws_secret_access_key is not None
        assert original_llm.api_key is not None
        assert original_llm.aws_access_key_id is not None
        assert original_llm.aws_secret_access_key is not None
        assert isinstance(loaded_llm.api_key, SecretStr)
        assert isinstance(original_llm.api_key, SecretStr)
        assert isinstance(loaded_llm.aws_access_key_id, SecretStr)
        assert isinstance(original_llm.aws_access_key_id, SecretStr)
        assert isinstance(loaded_llm.aws_secret_access_key, SecretStr)
        assert isinstance(original_llm.aws_secret_access_key, SecretStr)
        assert (
            loaded_llm.api_key.get_secret_value()
            == original_llm.api_key.get_secret_value()
        )
        assert (
            loaded_llm.aws_access_key_id.get_secret_value()
            == original_llm.aws_access_key_id.get_secret_value()
        )
        assert (
            loaded_llm.aws_secret_access_key.get_secret_value()
            == original_llm.aws_secret_access_key.get_secret_value()
        )
