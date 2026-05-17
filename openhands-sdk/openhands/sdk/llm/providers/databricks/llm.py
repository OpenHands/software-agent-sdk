"""DatabricksLLM — native Databricks AI Gateway provider for the OpenHands V1 SDK.

Subclasses LLM and overrides the transport layer to talk to the AI Gateway
``/ai-gateway/<route>`` directly over httpx.

Usage (via factory — preferred):
    from openhands.sdk import create_llm
    llm = create_llm(
        "databricks/databricks-claude-opus-4-6",
        databricks_host="https://adb-1234.cloud.databricks.com",
        api_key=SecretStr("dapi..."),
    )

The workspace URL (``databricks_host``) is the canonical configured host.
The SDK derives the AI Gateway base from it
(``<host>/ai-gateway/<family-route>``) for every FM invocation.

``databricks_ai_gateway_host`` is an optional override for deployments
with a dedicated gateway hostname (``<workspace_id>.ai-gateway.cloud.databricks.com``);
when set, FM invocations route through it directly. Discovery, auth, and
metadata probes always go to ``databricks_host`` regardless.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from pydantic import PrivateAttr, SecretStr, field_serializer, field_validator, model_validator

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.providers.databricks.auth import (
    DatabricksCredentials,
    resolve_credentials,
)
from openhands.sdk.llm.providers.databricks.client import DatabricksFMAPIClient
from openhands.sdk.llm.providers.databricks.models import (
    ProviderFamily,
    StoredU2MTokens,
    detect_family,
)
from openhands.sdk.llm.providers.databricks.utils import DatabricksTimeouts

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model capability tables
# ---------------------------------------------------------------------------

# Context windows (input tokens) for known Databricks FMAPI models.
# Unknown models fall back to 128K.
#
# Values reflect what the AI Gateway endpoint accepts, not the raw upstream model
# limits — e.g. Anthropic Claude endpoints are gateway-capped at 200K even if
# the upstream contract supports more. Keep this table conservative.
DATABRICKS_CONTEXT_WINDOWS: dict[str, int] = {
    # --- Databricks-hosted FM (OpenAI Chat family) ---
    "databricks/databricks-dbrx-instruct": 32_768,
    "databricks/databricks-meta-llama-3-1-70b-instruct": 128_000,
    "databricks/databricks-meta-llama-3-1-405b-instruct": 128_000,
    "databricks/databricks-meta-llama-3-3-70b-instruct": 128_000,
    "databricks/databricks-meta-llama-4-maverick": 128_000,
    "databricks/databricks-mixtral-8x7b-instruct": 32_768,
    "databricks/databricks-gpt-oss-20b": 128_000,
    "databricks/databricks-gpt-oss-120b": 128_000,
    # --- Anthropic native (Claude series on gateway) ---
    "databricks/databricks-claude-3-5-sonnet-2":  200_000,
    "databricks/databricks-claude-3-7-sonnet":    200_000,
    "databricks/databricks-claude-sonnet-4":      200_000,
    "databricks/databricks-claude-sonnet-4-5":    200_000,
    "databricks/databricks-claude-opus-4-6":      200_000,
    "databricks/databricks-claude-haiku-4-5":     200_000,
    # --- Google Gemini native ---
    "databricks/databricks-gemini-2-5-flash":     1_048_576,
    "databricks/databricks-gemini-2-5-pro":       1_048_576,
    # --- OpenAI Responses (GPT-5 series) ---
    "databricks/databricks-gpt-5":                400_000,
    "databricks/databricks-gpt-5-2":              400_000,
    "databricks/databricks-gpt-5-4":              400_000,
    "databricks/databricks-gpt-5-4-mini":         400_000,
    "databricks/databricks-gpt-5-4-nano":         400_000,
}

# Maximum output tokens for known Databricks FMAPI models.
# Unknown models fall back to 16K.
#
# For reasoning-capable endpoints (gpt-5 series, gemini 2.5, gpt-oss), output
# tokens include internal thinking tokens — the budget must be generous enough
# that visible text actually fits. See ``databricks-ai-gateway-fm-apis`` skill.
DATABRICKS_MAX_OUTPUT: dict[str, int] = {
    # --- OpenAI Chat family ---
    "databricks/databricks-dbrx-instruct": 4_096,
    "databricks/databricks-meta-llama-3-1-70b-instruct": 4_096,
    "databricks/databricks-meta-llama-3-1-405b-instruct": 4_096,
    "databricks/databricks-meta-llama-3-3-70b-instruct": 4_096,
    "databricks/databricks-meta-llama-4-maverick":       8_192,
    "databricks/databricks-mixtral-8x7b-instruct": 4_096,
    "databricks/databricks-gpt-oss-20b":   16_384,   # reasoning capacity
    "databricks/databricks-gpt-oss-120b":  16_384,
    # --- Anthropic ---
    "databricks/databricks-claude-3-5-sonnet-2":  8_192,
    "databricks/databricks-claude-3-7-sonnet":    8_192,
    "databricks/databricks-claude-sonnet-4":      8_192,
    "databricks/databricks-claude-sonnet-4-5":   64_000,
    "databricks/databricks-claude-opus-4-6":     32_000,
    "databricks/databricks-claude-haiku-4-5":     8_192,
    # --- Gemini (budget includes thinking) ---
    "databricks/databricks-gemini-2-5-flash":    65_536,
    "databricks/databricks-gemini-2-5-pro":      65_536,
    # --- OpenAI Responses (GPT-5) — generous default so reasoning fits ---
    "databricks/databricks-gpt-5":              16_384,
    "databricks/databricks-gpt-5-2":            16_384,
    "databricks/databricks-gpt-5-4":            16_384,
    "databricks/databricks-gpt-5-4-mini":       16_384,
    "databricks/databricks-gpt-5-4-nano":       16_384,
}


# ---------------------------------------------------------------------------
# DatabricksLLM
# ---------------------------------------------------------------------------

class DatabricksLLM(LLM):
    """Native Databricks Foundation Model API provider. PWAF-compliant.

    Uses a direct httpx transport to the Databricks AI Gateway instead of
    routing HTTP through litellm.completion. Supports OAuth U2M (browser
    PKCE), OAuth M2M (client credentials), PAT, CLI profile, and the
    databricks-sdk unified auth chain.
    """

    # Pydantic provider discriminator.  Serialized by ``SerializeAsAny`` on
    # ``AgentBase.llm`` / ``LLMSummarizingCondenser.llm``; read back by the
    # ``_dispatch_to_provider_subclass`` wrap-validator on the base ``LLM``
    # class to route the payload to this subclass on load.
    provider: Literal["databricks"] = "databricks"

    # --- Databricks-specific fields ---

    databricks_ai_gateway_host: str | None = None
    """Optional AI Gateway override — host only, scheme + hostname[:port], no path.

    When set, all FM invocations route through this host instead of the
    workspace URL. Use this for deployments with a dedicated gateway, e.g.
    ``https://<workspace_id>.ai-gateway.cloud.databricks.com``.

    Leave unset for the common single-URL deployment — the SDK then routes
    invocations through ``<databricks_host>/ai-gateway/<family-route>``.

    Must start with ``https://`` and contain no path."""

    databricks_host: str | None = None
    """Workspace URL — the canonical Databricks endpoint.

    Used for:

    * FM invocations (default base, becomes ``<host>/ai-gateway/<route>``)
      unless ``databricks_ai_gateway_host`` is set.
    * Auth / token resolution (OAuth flows mint tokens here).
    * Discovery and the opt-in metadata probe
      (``GET /api/2.0/serving-endpoints/...``).

    Required for OAuth-based auth (``profile`` / ``m2m`` / ``u2m`` /
    unified). For PAT auth it's optional only when
    ``databricks_ai_gateway_host`` is also set (the gateway then has its
    own URL and the workspace isn't needed)."""

    databricks_metadata_probe: bool = False
    """When True, ``resolve_family`` issues
    ``GET /api/2.0/serving-endpoints/{name}`` against ``databricks_host``
    to authoritatively determine the AI Gateway path family from the
    server-side ``api_types``. Results cached in-process for 5 minutes
    per endpoint. Default False (name-pattern resolution only)."""

    databricks_client_id: str | None = None
    """M2M service principal application ID (OAuth client_credentials grant).
    NOT the same as DATABRICKS_U2M_CLIENT_ID (browser OAuth app).
    Set DATABRICKS_CLIENT_SECRET alongside this."""

    databricks_client_secret: SecretStr | None = None
    """M2M service principal OAuth secret. Paired with databricks_client_id."""

    databricks_profile: str | None = None
    """Databricks CLI profile name from ~/.databrickscfg. Requires databricks-sdk."""

    databricks_ssl_verify: bool = True
    """SSL/TLS verification. Set to path string for custom CA bundle."""

    stored_u2m_tokens: StoredU2MTokens | None = None
    """U2M OAuth tokens from browser login flow. Passed from app layer.
    Highest-priority auth path (PWAF: OAuth primary)."""

    # --- Resilience knobs ---

    databricks_max_retries: int = 3
    databricks_connect_timeout_s: float = 10.0
    databricks_read_timeout_s: float = 120.0
    databricks_chunk_timeout_s: float = 30.0

    # --- Private state (not serialized) ---

    _db_credentials: DatabricksCredentials = PrivateAttr()
    _db_client: DatabricksFMAPIClient = PrivateAttr()

    # ---------------------------------------------------------------------------
    # Validators
    # ---------------------------------------------------------------------------

    @field_validator("databricks_host", mode="before")
    @classmethod
    def _validate_host(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("https://"):
            raise ValueError(
                f"databricks_host must start with 'https://'. Got: {v!r}"
            )
        return v

    @field_validator("databricks_ai_gateway_host", mode="before")
    @classmethod
    def _validate_ai_gateway_host(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not v.startswith("https://"):
            raise ValueError(
                "databricks_ai_gateway_host must start with 'https://'. "
                f"Got: {v!r}"
            )
        from urllib.parse import urlsplit
        parts = urlsplit(v)
        # Allow trailing ``/ai-gateway`` so users who copy-paste the full
        # gateway base URL aren't penalised; everything else must be host-only.
        path = (parts.path or "").rstrip("/")
        if path and path != "/ai-gateway":
            raise ValueError(
                "databricks_ai_gateway_host must be host-only (optionally "
                "ending in '/ai-gateway'); the SDK appends the per-family "
                f"route itself. Got path={parts.path!r} in {v!r}"
            )
        if parts.query or parts.fragment:
            raise ValueError(
                "databricks_ai_gateway_host must not include a query string "
                f"or fragment. Got: {v!r}"
            )
        return v.rstrip("/")

    @field_serializer("databricks_client_secret", when_used="always")
    def _serialize_databricks_secret(
        self, v: SecretStr | None, info
    ) -> str | None:
        """Serialize databricks_client_secret respecting the expose_secrets context.

        DatabricksLLM-specific secret fields are not in the base LLM_SECRET_FIELDS
        tuple so they don't benefit from the base _serialize_secrets serializer.
        This serializer mirrors the same logic so save/load round-trips work:
          - expose_secrets=True  → plaintext (AgentStore.save path)
          - default              → redacted string "**********"
        Always returns str | None (never SecretStr) to avoid Pydantic warnings.
        """
        if v is None:
            return None
        from openhands.sdk.utils.pydantic_secrets import (
            REDACTED_SECRET_VALUE,
            serialize_secret,
        )
        result = serialize_secret(v, info)
        # serialize_secret returns the SecretStr object in redact mode; convert to str.
        if isinstance(result, SecretStr):
            return REDACTED_SECRET_VALUE
        return result

    @model_validator(mode="after")
    def _init_databricks(self) -> "DatabricksLLM":
        if not (self.databricks_ai_gateway_host or self.databricks_host):
            raise ValueError(
                "databricks_host is required (or databricks_ai_gateway_host "
                "as an override). FM invocations route through "
                "<databricks_host>/ai-gateway/<route> by default."
            )
        self._db_credentials = resolve_credentials(self)
        self._db_client = DatabricksFMAPIClient(
            credentials=self._db_credentials,
            ai_gateway_host=self.databricks_ai_gateway_host,
            timeouts=DatabricksTimeouts(
                connect_s=self.databricks_connect_timeout_s,
                read_s=self.databricks_read_timeout_s,
                chunk_s=self.databricks_chunk_timeout_s,
            ),
            max_retries=self.databricks_max_retries,
            ssl_verify=self.databricks_ssl_verify,
            metadata_probe=self.databricks_metadata_probe,
        )
        return self

    # ---------------------------------------------------------------------------
    # PWAF surfaces (observability, diagnostics, pickers)
    # ---------------------------------------------------------------------------

    @property
    def auth_method(self) -> str:
        """Resolved auth strategy: ``pat`` | ``m2m`` | ``u2m`` | ``profile`` | ``unified`` | ``env``.

        Read-only — set by ``resolve_credentials()`` during construction.
        Handy for log correlation and operator dashboards.
        """
        return self._db_credentials.auth_method

    @property
    def predicted_family(self) -> ProviderFamily:
        """Provider family predicted by **name pattern only** (no HTTP call).

        Useful for picker UIs and validation — gives an immediate answer without
        hitting the ``/api/2.0/serving-endpoints/{name}`` describe endpoint. For
        the authoritative family used at request time, call
        :meth:`resolve_family` (it performs a metadata probe with in-process
        caching and falls back to this same prediction on error).
        """
        return detect_family(self.model)

    def resolve_family(self) -> ProviderFamily:
        """Provider family used at request time.

        Default (``databricks_metadata_probe=False``): pure name-pattern
        resolution — same as :attr:`predicted_family`, no network call.

        Opt-in (``databricks_metadata_probe=True``): metadata-first with
        name-pattern fallback. Triggers at most one
        ``GET /api/2.0/serving-endpoints/{name}`` per endpoint per 5-minute
        TTL window against the workspace URL.
        """
        endpoint = self.model.removeprefix("databricks/")
        return self._db_client.resolve_family(endpoint)

    # ---------------------------------------------------------------------------
    # LLM overrides
    # ---------------------------------------------------------------------------

    def _init_model_info_and_caps(self) -> None:
        """Override: set context windows from Databricks capability tables."""
        self.max_input_tokens = DATABRICKS_CONTEXT_WINDOWS.get(self.model, 128_000)
        self.max_output_tokens = DATABRICKS_MAX_OUTPUT.get(self.model, 16_384)
        self._validate_context_window_size()

    def _get_litellm_api_key_value(self) -> str | None:
        """Override: return a fresh Databricks token rather than the static api_key."""
        return self._db_credentials.get_token()

    def _transport_call(
        self,
        *,
        messages: list[dict[str, Any]],
        enable_streaming: bool = False,
        on_token=None,
        **kwargs,
    ) -> "ModelResponse":
        """Override: call the Databricks FMAPI directly via httpx."""
        model_name = self.model.removeprefix("databricks/")
        logger.debug(
            "databricks_transport_call",
            extra={
                "endpoint": model_name,
                "auth_method": self._db_credentials.auth_method,
                "predicted_family": detect_family(self.model).value,
                # Authoritative family is resolved inside the client (with cache);
                # we don't re-probe here to avoid a second call path.
                "streaming": enable_streaming,
            },
        )
        # Strip litellm-specific kwargs that must not appear in the JSON body
        # forwarded to the Databricks AI Gateway.
        #   - stream: controlled via enable_streaming to avoid duplicate kwarg
        #   - extra_headers: litellm convention; headers are set by _make_headers()
        #   - extra_body: litellm convention; unsupported by the gateway
        for _k in ("stream", "extra_headers", "extra_body"):
            kwargs.pop(_k, None)
        return self._db_client.chat_completion(
            model=model_name,
            messages=messages,
            stream=enable_streaming,
            on_token=on_token,
            **kwargs,
        )
