"""Tests for observability utils."""

import os
from unittest.mock import patch

import openhands.sdk.observability.utils as utils
from openhands.sdk.observability.utils import get_env


def test_get_env_from_environment():
    """get_env returns the value from the process environment."""
    with patch.dict(os.environ, {"TEST_VAR": "test_value"}):
        assert get_env("TEST_VAR") == "test_value"


def test_get_env_not_found():
    """get_env returns None when the variable is not set."""
    with patch.dict(os.environ, {}, clear=True):
        assert get_env("NONEXISTENT_VAR") is None


def test_get_env_does_not_use_python_dotenv():
    """Regression guard for issue #1325.

    The crash came from python-dotenv's ``find_dotenv()``, which walks the
    call stack and executes ``assert frame.f_back is not None``. That fails in
    deployments whose frames have no on-disk source file (packaged/frozen
    builds, a deleted CWD, threads running exec'd code). Loading ``.env`` is
    now the host application's responsibility, so ``get_env`` must read solely
    from ``os.environ`` and must not import or call python-dotenv.

    Reverting ``get_env`` to a ``dotenv_values()`` call makes this fail.
    """
    assert not hasattr(utils, "dotenv_values")
