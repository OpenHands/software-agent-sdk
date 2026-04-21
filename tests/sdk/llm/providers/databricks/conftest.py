"""Shared fixtures for the Databricks provider test suite.

This conftest exists to make the suite deterministic and fast regardless of the
developer machine's local Databricks state. It does two things on every test:

1. Scrubs ``DATABRICKS_*`` environment variables so tests cannot accidentally
   pick up credentials or a host from the developer's shell.
2. Replaces ``databricks.sdk.WorkspaceClient`` with a MagicMock so any code path
   that reaches PROFILE or UNIFIED auth resolution does not attempt a real
   network call or OAuth browser flow (which is what caused the multi-minute
   test hang when ``~/.databrickscfg`` contained a U2M profile).

Individual tests that need to exercise the real ``WorkspaceClient`` constructor
or a specific env var can override these fixtures locally with ``monkeypatch``.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


_DATABRICKS_ENV_VARS: tuple[str, ...] = (
    "DATABRICKS_HOST",
    "DATABRICKS_TOKEN",
    "DATABRICKS_ACCESS_TOKEN",
    "DATABRICKS_CLIENT_ID",
    "DATABRICKS_CLIENT_SECRET",
    "DATABRICKS_U2M_CLIENT_ID",
    "DATABRICKS_CONFIG_PROFILE",
    "DATABRICKS_CONFIG_FILE",
    "DATABRICKS_AUTH_TYPE",
    "DATABRICKS_CLUSTER_ID",
    "DATABRICKS_WAREHOUSE_ID",
)


@pytest.fixture(autouse=True)
def _scrub_databricks_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every DATABRICKS_* env var for the duration of a test.

    Prevents credential leakage from the developer's shell into tests and stops
    ``resolve_credentials`` from falling through to UNIFIED auth, which would
    construct a real ``WorkspaceClient``.
    """
    for name in _DATABRICKS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _mock_workspace_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``databricks.sdk.WorkspaceClient`` with a MagicMock.

    Safe no-op when ``databricks-sdk`` is not installed. When it is installed,
    this prevents any accidental real constructor call (which can trigger an
    OAuth browser flow or hang on token refresh) from reaching the network.

    Returns the mock class so tests that need to assert on constructor calls
    can request the fixture by name.
    """
    mock_cls = MagicMock(name="WorkspaceClient")
    mock_instance = MagicMock(name="WorkspaceClient_instance")
    mock_instance.config.authenticate.return_value = {
        "Authorization": "Bearer mock-unified-token"
    }
    mock_cls.return_value = mock_instance

    # Patch the already-imported module if present; otherwise inject a shim so
    # ``from databricks.sdk import WorkspaceClient`` inside auth.py resolves to
    # our mock regardless of whether the real package is installed.
    if "databricks.sdk" in sys.modules:
        monkeypatch.setattr(
            "databricks.sdk.WorkspaceClient", mock_cls, raising=False
        )
    else:
        sdk_mod = MagicMock(name="databricks.sdk")
        sdk_mod.WorkspaceClient = mock_cls
        monkeypatch.setitem(sys.modules, "databricks.sdk", sdk_mod)

    return mock_cls
