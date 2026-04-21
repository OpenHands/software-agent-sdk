"""Settings → ``create_llm(...)`` kwargs bridge for the Databricks provider.

This is the single code path both the OpenHands backend and the OpenHands-CLI
go through when turning user settings (env vars, DB rows, TUI form state) into
kwargs for :func:`openhands.sdk.create_llm`.

Keeping it in the SDK prevents silent drift: when a new field is added to
``DatabricksLLM``, the contract test in ``test_settings_bridge.py`` fails until
the bridge is extended — which forces a conscious decision about whether the
new field should be exposed in settings UIs.

Usage:

    from openhands.sdk import create_llm
    from openhands.sdk.llm.providers.databricks.settings_bridge import (
        kwargs_from_settings,
    )

    kwargs = kwargs_from_settings(user, usage_id="agent")
    llm = create_llm(**kwargs)

The ``settings`` argument is deliberately duck-typed (Protocol, not a concrete
class). Any object exposing a subset of the attribute names below works:
pydantic models (``UserInfo``, ``CliSettings``, ``LLMEnvOverrides``), dataclasses,
``SimpleNamespace``, or plain ``dict``-wrappers.
"""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from openhands.sdk.llm.providers.databricks.models import StoredU2MTokens


# Fields the bridge recognizes. Every public, user-settable field on
# ``DatabricksLLM`` (and the subset of base ``LLM`` fields that UIs expose)
# must appear here. Enforced by
# ``test_bridge_covers_all_databricks_llm_public_fields``.
_BRIDGE_FIELDS: tuple[str, ...] = (
    # --- Base LLM fields commonly set from UI ---
    "model",
    "api_key",
    "base_url",
    "timeout",
    "max_input_tokens",
    # --- Databricks-specific ---
    "databricks_host",
    "databricks_ai_gateway_host",
    "databricks_metadata_probe",
    "databricks_client_id",
    "databricks_client_secret",
    "databricks_profile",
    "databricks_ssl_verify",
    "databricks_max_retries",
    "databricks_connect_timeout_s",
    "databricks_read_timeout_s",
    "databricks_chunk_timeout_s",
    "stored_u2m_tokens",
)

# Fields present on ``DatabricksLLM`` that are deliberately NOT bridged from
# settings — either internal pydantic discriminators or private state.
_NOT_BRIDGED: frozenset[str] = frozenset(
    {
        "provider",  # Literal discriminator, always "databricks"
    }
)

_SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "api_key",
        "databricks_client_secret",
    }
)


#: Useful when the settings object uses a different attribute name for a
#: bridged field, e.g. OpenHands' ``UserInfo`` uses ``llm_model`` /
#: ``llm_api_key`` / ``llm_base_url``. The bridge first tries the canonical
#: attribute name, then each alias in order.
UserInfoAliases: dict[str, tuple[str, ...]] = {
    "model": ("llm_model",),
    "api_key": ("llm_api_key",),
    "base_url": ("llm_base_url",),
}


def kwargs_from_settings(
    settings: Any,
    *,
    usage_id: str,
    model_override: str | None = None,
    base_url_fallback: str | None = None,
    extras: dict[str, Any] | None = None,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    """Build a kwargs dict ready for ``openhands.sdk.create_llm(**kwargs)``.

    Behavior:

    * Attributes are read via ``getattr`` — missing attrs are skipped (so the
      same bridge works for partial settings objects).
    * ``None`` and empty-string values are dropped so pydantic defaults apply.
    * Secret fields (``api_key``, ``databricks_client_secret``) are coerced
      to :class:`pydantic.SecretStr` if supplied as bare strings.
    * ``stored_u2m_tokens`` accepts both :class:`StoredU2MTokens` instances
      and plain dicts (validated with ``model_validate``; invalid dicts are
      silently dropped).
    * ``model_override`` wins over ``settings.model`` when both are set.
    * ``base_url_fallback`` is only applied when *neither* ``base_url`` nor
      ``databricks_host`` is present (preserves existing callers'
      host-vs-base-url disambiguation).
    * ``extras`` are merged last and win over everything else — use this for
      per-request overrides like session U2M tokens.
    * ``usage_id`` is always set; it's the one field not read from settings.

    Args:
        settings: Any object exposing a subset of the bridged field names.
        usage_id: Per-call usage id (``"agent"``, ``"condenser"``, ...).
        model_override: Replaces ``settings.model`` when non-None.
        base_url_fallback: Applied only when neither ``base_url`` nor
            ``databricks_host`` is populated.
        extras: Last-write-wins overrides.
        aliases: Optional map of canonical field → fallback attribute names.
            Tried in order after the canonical name itself. Convenient for
            settings shapes like OpenHands' ``UserInfo`` that prefix fields
            with ``llm_`` — pass :data:`UserInfoAliases`.

    Returns:
        A dict safe to splat into :func:`openhands.sdk.create_llm`.
    """
    kwargs: dict[str, Any] = {"usage_id": usage_id}
    aliases = aliases or {}

    for field in _BRIDGE_FIELDS:
        val = getattr(settings, field, None)
        if val is None or val == "":
            for alias in aliases.get(field, ()):
                val = getattr(settings, alias, None)
                if val not in (None, ""):
                    break
        if val is None or val == "":
            continue
        if field in _SECRET_FIELDS and not isinstance(val, SecretStr):
            val = SecretStr(str(val))
        if field == "stored_u2m_tokens" and isinstance(val, dict):
            try:
                val = StoredU2MTokens.model_validate(val)
            except Exception:
                continue
        kwargs[field] = val

    if model_override is not None:
        kwargs["model"] = model_override

    if (
        "base_url" not in kwargs
        and "databricks_host" not in kwargs
        and base_url_fallback
    ):
        kwargs["base_url"] = base_url_fallback

    if extras:
        for k, v in extras.items():
            if v is None:
                continue
            if k in _SECRET_FIELDS and not isinstance(v, SecretStr):
                v = SecretStr(str(v))
            kwargs[k] = v

    return kwargs


__all__ = [
    "kwargs_from_settings",
    "UserInfoAliases",
    "_BRIDGE_FIELDS",
    "_NOT_BRIDGED",
]
