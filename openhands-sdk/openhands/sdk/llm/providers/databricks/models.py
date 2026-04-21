"""Databricks AI Gateway routing config + shared Pydantic models.

All FM traffic is routed through **Databricks AI Gateway** under the
``/ai-gateway/<route>`` URL prefix. The gateway is reachable two ways:

* Reverse-proxied under the workspace host
  (``https://<workspace>/ai-gateway/...``) — this is what
  :func:`AIGatewayPaths.normalize_base` produces when the user only
  configures the workspace URL.
* Dedicated AI-Gateway hostname
  (``https://<workspace_id>.ai-gateway.cloud.databricks.com/...``) — used
  when the customer has a separate gateway endpoint (PrivateLink, Front
  Door). In that case the path templates are appended directly without
  the ``/ai-gateway`` prefix.

This module carries:

1. :class:`StoredU2MTokens` — OAuth token container shared with the app layer.
2. :class:`ProviderFamily` — dispatch key for which provider-native contract
   the target endpoint speaks (OpenAI Chat, Anthropic Messages, Google Gemini
   ``generateContent``, OpenAI Responses).
3. :class:`AIGatewayPaths` — path templates for each family's AI Gateway route.
4. :func:`detect_family` — name-pattern router (fast path, no HTTP call).
5. :func:`pick_family_from_api_types` — metadata router (authoritative; uses the
   ``foundation_model.api_types`` / ``external_model.provider`` signals returned
   by ``GET /api/2.0/serving-endpoints/{name}``).

The two routers mirror the `databricks-ai-gateway-fm-apis` skill exactly —
keep them in sync when the skill's routing table changes.

``StoredU2MTokens`` is defined **exactly once** here; `auth.py` and the
OpenHands app layer import from this module (no duplicate definitions).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Iterable

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# OAuth tokens (shared container)
# ---------------------------------------------------------------------------


class StoredU2MTokens(BaseModel):
    """OAuth tokens stored in the OpenHands user session after browser login.

    Passed from the app layer (after ``/auth/databricks/callback``) to
    ``resolve_credentials()``. The provider module never initiates the browser
    flow — it only manages token refresh.
    """

    access_token: str
    refresh_token: str
    expires_at: float  # Unix epoch seconds
    client_id: str    # DATABRICKS_U2M_CLIENT_ID — required for token refresh
    host: str         # Workspace host — fallback when databricks_host is unset


# ---------------------------------------------------------------------------
# Provider family — drives AI Gateway payload format + URL path
# ---------------------------------------------------------------------------


class ProviderFamily(str, Enum):
    """Which provider-native API format the AI Gateway endpoint speaks.

    Routing (in priority order — first match wins):

    =====================  ==========================  ==============================
    Family                  Name pattern                Metadata ``api_types`` entry
    =====================  ==========================  ==============================
    :attr:`ANTHROPIC`       ``*claude*``                ``anthropic/v1/messages``
    :attr:`GEMINI`          ``*gemini*``                ``gemini/v1/generateContent``
    :attr:`OPENAI_RESPONSES` ``databricks-gpt-5*``      ``openai/v1/responses``
    :attr:`OPENAI`          *(default)*                 ``mlflow/v1/chat/completions``
    =====================  ==========================  ==============================

    :attr:`OPENAI` is the **always-safe default** — every ``task=llm/v1/chat``
    endpoint accepts OpenAI-chat payloads at ``/{endpoint}/invocations`` and
    returns OpenAI ``ChatCompletion`` responses. The other families are opt-in
    and only used when there's positive evidence (metadata or name match) that
    the endpoint speaks the native contract.
    """

    OPENAI = "openai"                     # OpenAI Chat — universal default
    OPENAI_RESPONSES = "openai_responses" # OpenAI Responses — GPT-5 series only
    ANTHROPIC = "anthropic"               # Anthropic Messages — Claude models
    GEMINI = "gemini"                     # Google Gemini generateContent


# ---------------------------------------------------------------------------
# AI Gateway path templates
# ---------------------------------------------------------------------------


class AIGatewayPaths(BaseModel):
    """Path templates appended to the AI Gateway base for each native API.

    All four templates have been verified against a live Databricks workspace:

    * :attr:`openai` — OpenAI Chat Completions, mlflow flavor, universal
      default. Endpoint name is carried in the body.
    * :attr:`openai_responses` — OpenAI Responses API for the GPT-5 series.
      Endpoint name is in the body.
    * :attr:`anthropic` — Anthropic Messages API, native flavor for Claude
      models. Endpoint name is in the body.
    * :attr:`gemini` — Google Gemini ``generateContent`` native path. The
      endpoint name is part of the URL.

    Templates intentionally start at the AI Gateway base (without the
    ``/ai-gateway`` prefix). :meth:`url` calls :meth:`normalize_base` first
    to produce the right base URL given the configured host:

    * Workspace URL (``adb-*.cloud.databricks.com``) →
      ``<host>/ai-gateway`` (the gateway is reverse-proxied under the
      workspace control plane).
    * Dedicated gateway URL (``*.ai-gateway.*``) → host as-is, the gateway
      hostname is itself the base.

    Each template can be overridden for deployments with non-standard path
    layouts. ``{endpoint}`` is substituted with the bare endpoint name
    (after stripping the ``databricks/`` prefix).
    """

    openai: str = Field(
        default="/mlflow/v1/chat/completions",
        description="OpenAI Chat Completions (mlflow flavor; universal default).",
    )
    openai_responses: str = Field(
        default="/openai/v1/responses",
        description="OpenAI Responses API. Endpoint name is in the body.",
    )
    anthropic: str = Field(
        default="/anthropic/v1/messages",
        description="Anthropic Messages API. Endpoint name is in the body.",
    )
    gemini: str = Field(
        default="/gemini/v1beta/models/{endpoint}:generateContent",
        description="Google Gemini generateContent native path.",
    )

    @staticmethod
    def normalize_base(host: str) -> str:
        """Return the AI Gateway base URL for a configured host.

        - Hosts whose netloc matches ``*.ai-gateway.*`` are dedicated AI
          Gateway endpoints; the host itself is the base — return as-is
          (after stripping any trailing slash).
        - Hosts that already end with ``/ai-gateway`` are returned as-is.
        - Anything else is treated as a workspace URL with the gateway
          reverse-proxied; ``/ai-gateway`` is appended.
        """
        h = host.rstrip("/")
        # Crude netloc check; ``http(s)://<netloc>/...`` and bare ``netloc``
        # both work here without pulling in urllib for one substring match.
        scheme_split = h.split("://", 1)
        netloc = scheme_split[1].split("/", 1)[0] if len(scheme_split) == 2 else h
        if ".ai-gateway." in netloc:
            return h
        if h.endswith("/ai-gateway"):
            return h
        return h + "/ai-gateway"

    def url(self, host: str, family: ProviderFamily, endpoint: str) -> str:
        """Build the fully-qualified URL for a ``(family, endpoint)`` pair.

        ``host`` may be a workspace URL or a dedicated AI Gateway hostname;
        :meth:`normalize_base` figures out which prefix to apply.
        """
        tmpl = getattr(self, family.value)
        return self.normalize_base(host) + tmpl.format(endpoint=endpoint)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

# Strip these from the model id before matching. Keeps support for
# "databricks/databricks-claude-sonnet-4-5", "databricks-claude-...", etc.
_MODEL_PREFIXES = ("databricks/", "databricks-")


def _bare_name(model: str) -> str:
    name = model.lower().strip()
    for prefix in _MODEL_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name


def detect_family(model: str) -> ProviderFamily:
    """Name-pattern router (fast path, no extra API call).

    Mirrors the ``databricks-ai-gateway-fm-apis`` skill's ``route_by_name``.

    Priority (first match wins):

    1. ``*claude*``         → :attr:`ProviderFamily.ANTHROPIC`
    2. ``*gemini*``         → :attr:`ProviderFamily.GEMINI`
    3. ``gpt-<digit>*``     → :attr:`ProviderFamily.OPENAI_RESPONSES`
       The leading digit requirement naturally excludes ``gpt-oss-*``
       (starts with ``gpt-o``) without needing an explicit blocklist.
       Confirmed live against the full GPT-5 product line (April 2026):
       ``gpt-5``, ``gpt-5-1``, ``gpt-5-1-codex-{max,mini}``,
       ``gpt-5-2``, ``gpt-5-2-codex``, ``gpt-5-3-codex``, ``gpt-5-4``,
       ``gpt-5-4-{mini,nano}``, ``gpt-5-mini``, ``gpt-5-nano``.
       Any future ``gpt-6``, ``gpt-7``, … variants automatically inherit
       this rule — Databricks routes all numbered GPT generations through
       the OpenAI Responses API (``/openai/v1/responses``).
    4. Everything else      → :attr:`ProviderFamily.OPENAI` (universal
       MLflow Chat Completions — safe default for ``gpt-oss``, Llama,
       Mistral, DBRX, Qwen, …)
    """
    name = _bare_name(model)
    if "claude" in name:
        return ProviderFamily.ANTHROPIC
    if "gemini" in name:
        return ProviderFamily.GEMINI
    # ``_bare_name`` strips both ``databricks/`` and ``databricks-`` prefixes.
    # ``re.match`` anchors at the start only — ``gpt-\d`` requires a digit
    # immediately after the dash, so ``gpt-oss-*`` falls through cleanly.
    if re.match(r"gpt-\d", name):
        return ProviderFamily.OPENAI_RESPONSES
    return ProviderFamily.OPENAI


# ``api_types`` strings exposed by ``GET /api/2.0/serving-endpoints/{name}``
# (``config.served_entities[0].foundation_model.api_types``).
_API_TYPE_TO_FAMILY: dict[str, ProviderFamily] = {
    "anthropic/v1/messages":      ProviderFamily.ANTHROPIC,
    "gemini/v1/generateContent":  ProviderFamily.GEMINI,
    "openai/v1/responses":        ProviderFamily.OPENAI_RESPONSES,
    # mlflow/v1/chat/completions is the universal fallback → OPENAI,
    # handled by priority ordering below.
}

# ``external_model.provider`` values (external endpoints don't have
# ``foundation_model``; they expose a single upstream provider instead).
_EXTERNAL_PROVIDER_TO_FAMILY: dict[str, ProviderFamily] = {
    "anthropic":          ProviderFamily.ANTHROPIC,
    "bedrock-anthropic":  ProviderFamily.ANTHROPIC,
    "google":             ProviderFamily.GEMINI,
    "gemini":             ProviderFamily.GEMINI,
    # OpenAI & azure-openai endpoints speak Chat Completions on /invocations;
    # map to OPENAI so we take the default path.
    "openai":             ProviderFamily.OPENAI,
    "azure-openai":       ProviderFamily.OPENAI,
}

# Priority order when an endpoint exposes multiple ``api_types`` — prefer the
# most specific native API, fall back to OpenAI Chat. Reverse this list if you
# want "stay on OpenAI Chat unless explicitly overridden" behaviour.
_API_TYPE_PRIORITY: tuple[str, ...] = (
    "anthropic/v1/messages",
    "gemini/v1/generateContent",
    "openai/v1/responses",
)


def pick_family_from_api_types(
    api_types: Iterable[str] | None,
    external_provider: str | None = None,
) -> ProviderFamily:
    """Metadata-first router (authoritative — no name-based guessing).

    ``api_types`` comes from ``foundation_model.api_types`` on a foundation-model
    endpoint. ``external_provider`` comes from ``external_model.provider`` on an
    external-model endpoint. Exactly one of them is populated for a given
    endpoint; passing both is fine (foundation signals win).

    Returns :attr:`ProviderFamily.OPENAI` when no native signal is present —
    this is the always-safe default for any ``task=llm/v1/chat`` endpoint.
    """
    present = set(api_types or ())
    for key in _API_TYPE_PRIORITY:
        if key in present:
            return _API_TYPE_TO_FAMILY[key]
    if external_provider:
        return _EXTERNAL_PROVIDER_TO_FAMILY.get(
            external_provider.lower(), ProviderFamily.OPENAI
        )
    return ProviderFamily.OPENAI
