"""Tests for SecretsManager class."""

from pydantic import SecretStr

from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.secret import SecretSource, StaticSecret


def test_update_secrets_with_static_values():
    """Test updating secrets with static string values."""
    secret_registry = SecretRegistry()
    secrets = {
        "API_KEY": "test-api-key",
        "DATABASE_URL": "postgresql://localhost/test",
    }

    secret_registry.update_secrets(secrets)
    assert secret_registry.secret_sources == {
        "API_KEY": StaticSecret(value=SecretStr("test-api-key")),
        "DATABASE_URL": StaticSecret(value=SecretStr("postgresql://localhost/test")),
    }


def test_update_secrets_overwrites_existing():
    """Test that update_secrets overwrites existing keys."""
    secret_registry = SecretRegistry()

    # Add initial secrets
    secret_registry.update_secrets({"API_KEY": "old-value"})
    assert secret_registry.secret_sources["API_KEY"] == StaticSecret(
        value=SecretStr("old-value")
    )

    # Update with new value
    secret_registry.update_secrets({"API_KEY": "new-value", "NEW_KEY": "key-value"})
    assert secret_registry.secret_sources["API_KEY"] == StaticSecret(
        value=SecretStr("new-value")
    )

    secret_registry.update_secrets({"API_KEY": "new-value-2"})
    assert secret_registry.secret_sources["API_KEY"] == StaticSecret(
        value=SecretStr("new-value-2")
    )


def test_find_secrets_in_text_case_insensitive():
    """Test that find_secrets_in_text is case insensitive."""
    secret_registry = SecretRegistry()
    secret_registry.update_secrets(
        {
            "API_KEY": "test-key",
            "DATABASE_PASSWORD": "test-password",
        }
    )

    # Test various case combinations
    found = secret_registry.find_secrets_in_text("echo api_key=$API_KEY")
    assert found == {"API_KEY"}

    found = secret_registry.find_secrets_in_text("echo $database_password")
    assert found == {"DATABASE_PASSWORD"}

    found = secret_registry.find_secrets_in_text("API_KEY and DATABASE_PASSWORD")
    assert found == {"API_KEY", "DATABASE_PASSWORD"}

    found = secret_registry.find_secrets_in_text("echo hello world")
    assert found == set()


def test_find_secrets_in_text_partial_matches():
    """Test that find_secrets_in_text handles partial matches correctly."""
    secret_registry = SecretRegistry()
    secret_registry.update_secrets(
        {
            "API_KEY": "test-key",
            "API": "test-api",  # Shorter key that's contained in API_KEY
        }
    )

    # Both should be found since "API" is contained in "API_KEY"
    found = secret_registry.find_secrets_in_text("export API_KEY=$API_KEY")
    assert "API_KEY" in found
    assert "API" in found


def test_get_secrets_as_env_vars_static_values():
    """Test get_secrets_as_env_vars with static values."""
    secret_registry = SecretRegistry()
    secret_registry.update_secrets(
        {
            "API_KEY": "test-api-key",
            "DATABASE_URL": "postgresql://localhost/test",
        }
    )

    env_vars = secret_registry.get_secrets_as_env_vars("curl -H 'X-API-Key: $API_KEY'")
    assert env_vars == {"API_KEY": "test-api-key"}

    env_vars = secret_registry.get_secrets_as_env_vars(
        "export API_KEY=$API_KEY && export DATABASE_URL=$DATABASE_URL"
    )
    assert env_vars == {
        "API_KEY": "test-api-key",
        "DATABASE_URL": "postgresql://localhost/test",
    }


def test_get_secrets_as_env_vars_callable_values():
    """Test get_secrets_as_env_vars with callable values."""
    secret_registry = SecretRegistry()

    class MyTokenSource(SecretSource):
        def get_value(self):
            return "dynamic-token-456"

    secret_registry.update_secrets(
        {
            "STATIC_KEY": "static-value",
            "DYNAMIC_TOKEN": MyTokenSource(),
        }
    )

    env_vars = secret_registry.get_secrets_as_env_vars(
        "export DYNAMIC_TOKEN=$DYNAMIC_TOKEN"
    )
    assert env_vars == {"DYNAMIC_TOKEN": "dynamic-token-456"}


def test_get_secrets_as_env_vars_handles_callable_exceptions():
    """Test that get_secrets_as_env_vars handles exceptions from callables."""
    secret_registry = SecretRegistry()

    class MyFailingTokenSource(SecretSource):
        def get_value(self):
            raise ValueError("Secret retrieval failed")

    class MyWorkingTokenSource(SecretSource):
        def get_value(self):
            return "working-value"

    secret_registry.update_secrets(
        {
            "FAILING_SECRET": MyFailingTokenSource(),
            "WORKING_SECRET": MyWorkingTokenSource(),
        }
    )

    # Should not raise exception, should skip failing secret
    env_vars = secret_registry.get_secrets_as_env_vars(
        "export FAILING_SECRET=$FAILING_SECRET && export WORKING_SECRET=$WORKING_SECRET"
    )

    # Only working secret should be returned
    assert env_vars == {"WORKING_SECRET": "working-value"}


def test_get_secret_value_static():
    """Test get_secret_value with static string values."""
    secret_registry = SecretRegistry()
    secret_registry.update_secrets(
        {
            "API_KEY": "test-api-key",
            "DATABASE_URL": "postgresql://localhost/test",
        }
    )

    assert secret_registry.get_secret_value("API_KEY") == "test-api-key"
    assert (
        secret_registry.get_secret_value("DATABASE_URL")
        == "postgresql://localhost/test"
    )
    assert secret_registry.get_secret_value("NONEXISTENT") is None


def test_get_secret_value_callable():
    """Test get_secret_value with callable values."""
    secret_registry = SecretRegistry()

    class MyTokenSource(SecretSource):
        def get_value(self):
            return "dynamic-token-456"

    secret_registry.update_secrets(
        {
            "STATIC_KEY": "static-value",
            "DYNAMIC_TOKEN": MyTokenSource(),
        }
    )

    assert secret_registry.get_secret_value("STATIC_KEY") == "static-value"
    assert secret_registry.get_secret_value("DYNAMIC_TOKEN") == "dynamic-token-456"


def test_get_secret_value_handles_exceptions():
    """Test that get_secret_value handles exceptions from callables gracefully."""
    secret_registry = SecretRegistry()

    class MyFailingTokenSource(SecretSource):
        def get_value(self):
            raise ValueError("Secret retrieval failed")

    class MyWorkingTokenSource(SecretSource):
        def get_value(self):
            return "working-value"

    secret_registry.update_secrets(
        {
            "FAILING_SECRET": MyFailingTokenSource(),
            "WORKING_SECRET": MyWorkingTokenSource(),
        }
    )

    # Should not raise exception, should return None for failing secret
    assert secret_registry.get_secret_value("FAILING_SECRET") is None
    assert secret_registry.get_secret_value("WORKING_SECRET") == "working-value"


def test_get_secret_value_empty_registry():
    """Test get_secret_value with empty registry."""
    secret_registry = SecretRegistry()
    assert secret_registry.get_secret_value("ANY_KEY") is None


def test_get_secret_value_as_callback():
    """Test using get_secret_value as a callback for dict-like lookup."""
    secret_registry = SecretRegistry()
    secret_registry.update_secrets(
        {
            "API_KEY": "test-api-key",
            "TOKEN": "test-token",
        }
    )

    # This is how it's used with expand_mcp_variables
    get_secret = secret_registry.get_secret_value

    assert get_secret("API_KEY") == "test-api-key"
    assert get_secret("TOKEN") == "test-token"
    assert get_secret("MISSING") is None


def test_get_secret_value_tracks_for_masking():
    """Test that get_secret_value adds secrets to _exported_values for masking.

    Secrets retrieved via get_secret_value (e.g., for MCP expansion) should be
    tracked so they can be masked in command outputs.
    """
    secret_registry = SecretRegistry()
    secret_registry.update_secrets(
        {
            "API_TOKEN": "super-secret-token-123",
            "DB_PASSWORD": "db-pass-456",
        }
    )

    # Initially, no exported values
    assert secret_registry._exported_values == {}

    # Retrieve a secret via get_secret_value
    value = secret_registry.get_secret_value("API_TOKEN")
    assert value == "super-secret-token-123"

    # The secret should now be tracked for masking
    assert "API_TOKEN" in secret_registry._exported_values
    assert secret_registry._exported_values["API_TOKEN"] == "super-secret-token-123"

    # Masking should work on the tracked secret
    output = "Response: super-secret-token-123"
    masked = secret_registry.mask_secrets_in_output(output)
    assert masked == "Response: <secret-hidden>"

    # Retrieve another secret
    secret_registry.get_secret_value("DB_PASSWORD")
    assert "DB_PASSWORD" in secret_registry._exported_values

    # Both should be masked now
    output2 = "API: super-secret-token-123, DB: db-pass-456"
    masked2 = secret_registry.mask_secrets_in_output(output2)
    assert masked2 == "API: <secret-hidden>, DB: <secret-hidden>"


def test_get_secret_value_missing_not_tracked():
    """Test that missing secrets don't get added to _exported_values."""
    secret_registry = SecretRegistry()
    secret_registry.update_secrets({"EXISTING": "value"})

    # Look up a missing key
    result = secret_registry.get_secret_value("NONEXISTENT")
    assert result is None
    assert "NONEXISTENT" not in secret_registry._exported_values
