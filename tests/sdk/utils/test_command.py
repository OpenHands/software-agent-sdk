from collections import OrderedDict

import pytest

from openhands.sdk.utils.command import sanitized_env


def test_sanitized_env_returns_copy():
    """Returns a dict copy, not the original."""
    env = {"FOO": "bar"}
    result = sanitized_env(env)
    assert result == {"FOO": "bar"}
    assert result is not env


def test_sanitized_env_defaults_to_os_environ(monkeypatch):
    """When env is None, returns a dict based on os.environ."""
    monkeypatch.setenv("TEST_SANITIZED_ENV_VAR", "test_value")
    result = sanitized_env(None)
    assert result["TEST_SANITIZED_ENV_VAR"] == "test_value"


def test_sanitized_env_accepts_mapping_types():
    """Accepts any Mapping type, not just dict."""
    env: OrderedDict[str, str] = OrderedDict([("KEY", "value")])
    assert isinstance(sanitized_env(env), dict)


@pytest.mark.parametrize(
    ("env", "expected_ld_path"),
    [
        # ORIG present and non-empty: restore original value
        (
            {"LD_LIBRARY_PATH": "/pyinstaller", "LD_LIBRARY_PATH_ORIG": "/original"},
            "/original",
        ),
        # ORIG absent: leave unchanged
        ({"LD_LIBRARY_PATH": "/some/path"}, "/some/path"),
    ],
)
def test_sanitized_env_ld_library_path(env: dict[str, str], expected_ld_path: str):
    """LD_LIBRARY_PATH is restored from ORIG or left unchanged."""
    assert sanitized_env(env)["LD_LIBRARY_PATH"] == expected_ld_path


def test_sanitized_env_removes_ld_library_path_when_orig_empty():
    """When LD_LIBRARY_PATH_ORIG is empty, removes LD_LIBRARY_PATH."""
    env = {"LD_LIBRARY_PATH": "/pyinstaller", "LD_LIBRARY_PATH_ORIG": ""}
    assert "LD_LIBRARY_PATH" not in sanitized_env(env)
