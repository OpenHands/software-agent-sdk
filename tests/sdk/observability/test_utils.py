"""Tests for observability environment helpers."""

from pathlib import Path

import pytest

from openhands.sdk.observability import (
    laminar as laminar_module,
    utils as observability_utils,
)
from openhands.sdk.observability.utils import get_env


def test_get_env_prefers_environment_without_consulting_dotenv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OH_TEST_ENV_KEY", "from-env")

    def _fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("dotenv_values must not be consulted")

    monkeypatch.setattr(observability_utils, "dotenv_values", _fail)
    assert get_env("OH_TEST_ENV_KEY") == "from-env"


def test_get_env_empty_environment_value_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OH_TEST_ENV_KEY", "")
    assert get_env("OH_TEST_ENV_KEY") == ""


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("No such file or directory"),
        OSError("I/O error"),
        AssertionError("bad frame"),
    ],
)
def test_get_env_returns_none_when_dotenv_fails(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.delenv("OH_TEST_ENV_KEY", raising=False)

    def _fail(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(observability_utils, "dotenv_values", _fail)
    assert get_env("OH_TEST_ENV_KEY") is None


def test_get_env_reads_dotenv_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OH_TEST_ENV_KEY", raising=False)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("OH_TEST_ENV_KEY=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "dotenv.main.find_dotenv",
        lambda *args, **kwargs: str(dotenv_path),
        raising=True,
    )
    assert get_env("OH_TEST_ENV_KEY") == "from-dotenv"


def test_should_enable_observability_false_when_dotenv_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in laminar_module._OBSERVABILITY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(laminar_module, "_observability_enabled", False)

    def _fail(*args: object, **kwargs: object) -> object:
        raise OSError("No such file or directory")

    monkeypatch.setattr(observability_utils, "dotenv_values", _fail)
    assert laminar_module.should_enable_observability() is False
