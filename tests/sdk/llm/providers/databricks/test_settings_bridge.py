"""Tests for the ``settings → create_llm(...)`` kwargs bridge.

Covers:

* Empty settings only yield ``usage_id``.
* Full passthrough with secret coercion and ``StoredU2MTokens`` normalization.
* ``None`` / empty-string dropping.
* ``model_override`` and ``base_url_fallback`` semantics.
* ``extras`` winning over settings values.
* Invalid ``stored_u2m_tokens`` dict silently ignored.
* **Contract / drift guard** — every public field on ``DatabricksLLM`` is either
  bridged from settings or explicitly listed in ``_NOT_BRIDGED``. Adding a new
  Databricks field without updating the bridge fails this test.
"""

from __future__ import annotations

from types import SimpleNamespace

from pydantic import SecretStr

from openhands.sdk.llm.providers.databricks import DatabricksLLM, StoredU2MTokens
from openhands.sdk.llm.providers.databricks.settings_bridge import (
    _BRIDGE_FIELDS,
    _NOT_BRIDGED,
    UserInfoAliases,
    kwargs_from_settings,
)


# ---------------------------------------------------------------------------
# Behavior
# ---------------------------------------------------------------------------


def test_empty_settings_only_includes_usage_id() -> None:
    kw = kwargs_from_settings(SimpleNamespace(), usage_id="agent")
    assert kw == {"usage_id": "agent"}


def test_full_settings_passthrough() -> None:
    settings = SimpleNamespace(
        model="databricks/databricks-claude-sonnet-4-5",
        api_key="dapi-1234",
        base_url="https://workspace.cloud.databricks.com",
        timeout=60.0,
        max_input_tokens=128_000,
        databricks_host="https://workspace.cloud.databricks.com",
        databricks_client_id="app-id",
        databricks_client_secret="client-secret-raw",
        databricks_profile="DEFAULT",
        databricks_ssl_verify=True,
        databricks_max_retries=5,
        databricks_connect_timeout_s=7.0,
        databricks_read_timeout_s=90.0,
        databricks_chunk_timeout_s=25.0,
        stored_u2m_tokens={
            "access_token": "at",
            "refresh_token": "rt",
            "expires_at": 9999999999.0,
            "client_id": "u2m-client",
            "host": "https://workspace.cloud.databricks.com",
        },
    )
    kw = kwargs_from_settings(settings, usage_id="agent")

    assert kw["usage_id"] == "agent"
    assert kw["model"] == "databricks/databricks-claude-sonnet-4-5"
    assert isinstance(kw["api_key"], SecretStr)
    assert kw["api_key"].get_secret_value() == "dapi-1234"
    assert isinstance(kw["databricks_client_secret"], SecretStr)
    assert kw["databricks_client_secret"].get_secret_value() == "client-secret-raw"
    assert isinstance(kw["stored_u2m_tokens"], StoredU2MTokens)
    assert kw["databricks_profile"] == "DEFAULT"
    assert kw["timeout"] == 60.0
    assert kw["max_input_tokens"] == 128_000


def test_secretstr_roundtrips_unchanged() -> None:
    s = SimpleNamespace(api_key=SecretStr("dapi-abc"))
    kw = kwargs_from_settings(s, usage_id="agent")
    assert isinstance(kw["api_key"], SecretStr)
    assert kw["api_key"].get_secret_value() == "dapi-abc"


def test_none_and_empty_strings_dropped() -> None:
    s = SimpleNamespace(model="", api_key=None, base_url="", databricks_profile=None)
    kw = kwargs_from_settings(s, usage_id="agent")
    assert set(kw) == {"usage_id"}


def test_model_override_wins_over_settings() -> None:
    s = SimpleNamespace(model="databricks/foo")
    kw = kwargs_from_settings(s, usage_id="agent", model_override="databricks/bar")
    assert kw["model"] == "databricks/bar"


def test_base_url_fallback_only_when_both_empty() -> None:
    kw = kwargs_from_settings(
        SimpleNamespace(),
        usage_id="agent",
        base_url_fallback="https://fallback.com",
    )
    assert kw["base_url"] == "https://fallback.com"

    kw_explicit = kwargs_from_settings(
        SimpleNamespace(base_url="https://explicit.com"),
        usage_id="agent",
        base_url_fallback="https://fallback.com",
    )
    assert kw_explicit["base_url"] == "https://explicit.com"

    kw_host_only = kwargs_from_settings(
        SimpleNamespace(databricks_host="https://ws.cloud.databricks.com"),
        usage_id="agent",
        base_url_fallback="https://fallback.com",
    )
    assert "base_url" not in kw_host_only
    assert kw_host_only["databricks_host"] == "https://ws.cloud.databricks.com"


def test_extras_win_over_settings_and_coerce_secrets() -> None:
    s = SimpleNamespace(api_key="dapi-stored", databricks_profile="OLD")
    kw = kwargs_from_settings(
        s,
        usage_id="agent",
        extras={"api_key": "dapi-session", "databricks_profile": "PROD"},
    )
    assert isinstance(kw["api_key"], SecretStr)
    assert kw["api_key"].get_secret_value() == "dapi-session"
    assert kw["databricks_profile"] == "PROD"


def test_extras_none_values_dropped() -> None:
    s = SimpleNamespace(api_key="dapi-stored")
    kw = kwargs_from_settings(
        s,
        usage_id="agent",
        extras={"api_key": None, "databricks_profile": None},
    )
    assert kw["api_key"].get_secret_value() == "dapi-stored"
    assert "databricks_profile" not in kw


def test_aliases_map_userinfo_style_prefixed_fields() -> None:
    """UserInfo uses ``llm_model`` / ``llm_api_key`` / ``llm_base_url``.

    The aliases mechanism lets the bridge read them without requiring each
    caller to build a shim object.
    """
    s = SimpleNamespace(
        llm_model="databricks/databricks-gemini-2-5-pro",
        llm_api_key="dapi-user",
        llm_base_url="https://ws.cloud.databricks.com",
        databricks_profile="PROD",
    )
    kw = kwargs_from_settings(s, usage_id="agent", aliases=UserInfoAliases)
    assert kw["model"] == "databricks/databricks-gemini-2-5-pro"
    assert isinstance(kw["api_key"], SecretStr)
    assert kw["api_key"].get_secret_value() == "dapi-user"
    assert kw["base_url"] == "https://ws.cloud.databricks.com"
    assert kw["databricks_profile"] == "PROD"


def test_canonical_name_wins_over_alias() -> None:
    """If both ``api_key`` and ``llm_api_key`` are set, canonical wins."""
    s = SimpleNamespace(api_key="canonical", llm_api_key="aliased")
    kw = kwargs_from_settings(s, usage_id="agent", aliases=UserInfoAliases)
    assert kw["api_key"].get_secret_value() == "canonical"


def test_invalid_stored_u2m_dict_is_skipped() -> None:
    s = SimpleNamespace(stored_u2m_tokens={"totally": "bogus"})
    kw = kwargs_from_settings(s, usage_id="agent")
    assert "stored_u2m_tokens" not in kw


def test_stored_u2m_instance_passes_through() -> None:
    tok = StoredU2MTokens(
        access_token="at",
        refresh_token="rt",
        expires_at=9999999999.0,
        client_id="u2m-client",
        host="https://workspace.cloud.databricks.com",
    )
    s = SimpleNamespace(stored_u2m_tokens=tok)
    kw = kwargs_from_settings(s, usage_id="agent")
    assert kw["stored_u2m_tokens"] is tok


# ---------------------------------------------------------------------------
# Drift guard
# ---------------------------------------------------------------------------


def test_bridge_covers_all_databricks_llm_public_fields() -> None:
    """Fails when a new ``DatabricksLLM`` field is added without updating the bridge.

    Guarantees OpenHands backend + OpenHands-CLI always build the full kwarg
    surface when the SDK gains a new Databricks-specific field.
    """
    own_fields = {
        name
        for name in DatabricksLLM.__annotations__
        if not name.startswith("_")
    }
    covered = set(_BRIDGE_FIELDS) | _NOT_BRIDGED
    missing = own_fields - covered

    assert not missing, (
        'New DatabricksLLM field(s) not handled by the settings bridge: '
        f'{sorted(missing)}. Extend _BRIDGE_FIELDS (and every call site) or add '
        'to _NOT_BRIDGED in settings_bridge.py.'
    )
