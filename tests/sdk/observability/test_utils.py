from unittest.mock import patch

from openhands.sdk.observability.utils import get_env


def test_get_env_returns_environment_value_without_loading_dotenv(monkeypatch):
    monkeypatch.setenv("TEST_ENV_VAR", "from-environment")

    with patch(
        "openhands.sdk.observability.utils.dotenv_values",
        side_effect=OSError,
    ) as mock_dotenv_values:
        assert get_env("TEST_ENV_VAR") == "from-environment"
        mock_dotenv_values.assert_not_called()


def test_get_env_returns_none_when_dotenv_loading_fails(monkeypatch):
    monkeypatch.delenv("MISSING_ENV_VAR", raising=False)

    with patch(
        "openhands.sdk.observability.utils.dotenv_values",
        side_effect=OSError,
    ):
        assert get_env("MISSING_ENV_VAR") is None
