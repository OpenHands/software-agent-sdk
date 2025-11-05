"""Tests for SecretsManager class."""

from pydantic import SecretStr

from openhands.sdk.conversation.secret_source import SecretSource, StaticSecret
from openhands.sdk.conversation.secrets_manager import SecretsManager


def test_update_secrets_with_static_values():
    """Test updating secrets with static string values."""
    manager = SecretsManager()
    secrets = {
        "API_KEY": "test-api-key",
        "DATABASE_URL": "postgresql://localhost/test",
    }

    manager.update_secrets(secrets)
    assert manager.secret_sources == {
        "API_KEY": StaticSecret(value=SecretStr("test-api-key")),
        "DATABASE_URL": StaticSecret(value=SecretStr("postgresql://localhost/test")),
    }


def test_update_secrets_overwrites_existing():
    """Test that update_secrets overwrites existing keys."""
    manager = SecretsManager()

    # Add initial secrets
    manager.update_secrets({"API_KEY": "old-value"})
    assert manager.secret_sources["API_KEY"] == StaticSecret(
        value=SecretStr("old-value")
    )

    # Update with new value
    manager.update_secrets({"API_KEY": "new-value", "NEW_KEY": "key-value"})
    assert manager.secret_sources["API_KEY"] == StaticSecret(
        value=SecretStr("new-value")
    )

    manager.update_secrets({"API_KEY": "new-value-2"})
    assert manager.secret_sources["API_KEY"] == StaticSecret(
        value=SecretStr("new-value-2")
    )


def test_find_secrets_in_text_case_insensitive():
    """Test that find_secrets_in_text is case insensitive."""
    manager = SecretsManager()
    manager.update_secrets(
        {
            "API_KEY": "test-key",
            "DATABASE_PASSWORD": "test-password",
        }
    )

    # Test various case combinations
    found = manager.find_secrets_in_text("echo api_key=$API_KEY")
    assert found == {"API_KEY"}

    found = manager.find_secrets_in_text("echo $database_password")
    assert found == {"DATABASE_PASSWORD"}

    found = manager.find_secrets_in_text("API_KEY and DATABASE_PASSWORD")
    assert found == {"API_KEY", "DATABASE_PASSWORD"}

    found = manager.find_secrets_in_text("echo hello world")
    assert found == set()


def test_find_secrets_in_text_partial_matches():
    """Test that find_secrets_in_text handles partial matches correctly."""
    manager = SecretsManager()
    manager.update_secrets(
        {
            "API_KEY": "test-key",
            "API": "test-api",  # Shorter key that's contained in API_KEY
        }
    )

    # Both should be found since "API" is contained in "API_KEY"
    found = manager.find_secrets_in_text("export API_KEY=$API_KEY")
    assert "API_KEY" in found
    assert "API" in found


def test_get_secrets_as_env_vars_static_values():
    """Test get_secrets_as_env_vars with static values."""
    manager = SecretsManager()
    manager.update_secrets(
        {
            "API_KEY": "test-api-key",
            "DATABASE_URL": "postgresql://localhost/test",
        }
    )

    env_vars = manager.get_secrets_as_env_vars("curl -H 'X-API-Key: $API_KEY'")
    assert env_vars == {"API_KEY": "test-api-key"}

    env_vars = manager.get_secrets_as_env_vars(
        "export API_KEY=$API_KEY && export DATABASE_URL=$DATABASE_URL"
    )
    assert env_vars == {
        "API_KEY": "test-api-key",
        "DATABASE_URL": "postgresql://localhost/test",
    }


def test_get_secrets_as_env_vars_callable_values():
    """Test get_secrets_as_env_vars with callable values."""
    manager = SecretsManager()

    class MyTokenSource(SecretSource):
        def get_value(self):
            return "dynamic-token-456"

    manager.update_secrets(
        {
            "STATIC_KEY": "static-value",
            "DYNAMIC_TOKEN": MyTokenSource(),
        }
    )

    env_vars = manager.get_secrets_as_env_vars("export DYNAMIC_TOKEN=$DYNAMIC_TOKEN")
    assert env_vars == {"DYNAMIC_TOKEN": "dynamic-token-456"}


def test_get_secrets_as_env_vars_handles_callable_exceptions():
    """Test that get_secrets_as_env_vars handles exceptions from callables."""
    manager = SecretsManager()

    class MyFailingTokenSource(SecretSource):
        def get_value(self):
            raise ValueError("Secret retrieval failed")

    class MyWorkingTokenSource(SecretSource):
        def get_value(self):
            return "working-value"

    manager.update_secrets(
        {
            "FAILING_SECRET": MyFailingTokenSource(),
            "WORKING_SECRET": MyWorkingTokenSource(),
        }
    )

    # Should not raise exception, should skip failing secret
    env_vars = manager.get_secrets_as_env_vars(
        "export FAILING_SECRET=$FAILING_SECRET && export WORKING_SECRET=$WORKING_SECRET"
    )

    # Only working secret should be returned
    assert env_vars == {"WORKING_SECRET": "working-value"}
