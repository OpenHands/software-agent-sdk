"""Databricks AI Gateway native provider for the OpenHands V1 SDK.

PWAF-compliant. Uses a direct synchronous ``httpx`` transport against the
Databricks AI Gateway rather than routing HTTP through ``litellm.completion``,
and dispatches by provider family to the correct native surface:

* **OpenAI Chat**       → ``/serving-endpoints/{endpoint}/invocations`` (universal)
* **OpenAI Responses**  → ``/serving-endpoints/v1/responses`` (GPT-5 series)
* **Anthropic Messages** → ``/serving-endpoints/anthropic/v1/messages`` (Claude)
* **Gemini generateContent** → ``/serving-endpoints/gemini/v1beta/models/{endpoint}:generateContent``

Routing is metadata-first (``GET /api/2.0/serving-endpoints/{name}`` →
``foundation_model.api_types`` / ``external_model.provider``) with a
name-pattern fallback.

Auth: PAT, OAuth M2M (client_credentials), OAuth U2M (browser PKCE),
``~/.databrickscfg`` profile, and unified Databricks SDK chain — see
:mod:`.auth` for details. The PWAF Partner AI Dev Kit skills are the
authoritative reference for credential handling.

See the companion skill ``databricks-ai-gateway-fm-apis`` (in ``_local/skills``)
for the routing table, worked examples, and a runnable ``probe.py`` that
self-verifies every native path.

Typical usage (via :func:`openhands.sdk.create_llm` factory):

.. code-block:: python

    from openhands.sdk import create_llm
    from openhands.sdk.llm.message import Message, TextContent
    from pydantic import SecretStr

    llm = create_llm(
        model="databricks/databricks-claude-sonnet-4-5",
        databricks_host="https://adb-xxx.cloud.databricks.com",
        api_key=SecretStr("dapi..."),      # or pass databricks_profile="DEFAULT"
        usage_id="my-agent",
    )
    print(llm.predicted_family)            # ProviderFamily.ANTHROPIC (no HTTP)
    print(llm.resolve_family())            # ANTHROPIC (metadata-confirmed)

    resp = llm.completion(messages=[
        Message(role="user", content=[TextContent(text="Hello!")]),
    ])
"""

from openhands.sdk.llm.providers.databricks.auth import (
    AuthStrategy,
    DatabricksCredentials,
)
from openhands.sdk.llm.providers.databricks.discovery import (
    CURATED_DATABRICKS_MODELS,
    DiscoveredEndpoint,
    ModelPickerEntry,
    get_picker_entries,
    list_chat_endpoints,
    list_foundation_models,
    list_models_from_env,
)
from openhands.sdk.llm.providers.databricks.llm import DatabricksLLM
from openhands.sdk.llm.providers.databricks.models import (
    AIGatewayPaths,
    ProviderFamily,
    StoredU2MTokens,
    detect_family,
    pick_family_from_api_types,
)
from openhands.sdk.llm.providers.databricks.pkce import (
    async_exchange_code_for_tokens,
    build_authorize_url,
    exchange_code_for_tokens,
    generate_pkce,
)
from openhands.sdk.llm.providers.databricks.settings_bridge import kwargs_from_settings


__all__ = [
    # LLM
    "DatabricksLLM",
    # Routing primitives
    "ProviderFamily",
    "AIGatewayPaths",
    "detect_family",
    "pick_family_from_api_types",
    # Auth
    "AuthStrategy",
    "DatabricksCredentials",
    "StoredU2MTokens",
    # U2M browser-login PKCE primitives (shared by web + CLI)
    "generate_pkce",
    "build_authorize_url",
    "exchange_code_for_tokens",
    "async_exchange_code_for_tokens",
    # Discovery
    "DiscoveredEndpoint",
    "list_chat_endpoints",
    "list_foundation_models",
    "list_models_from_env",
    # Two-tier model picker
    "ModelPickerEntry",
    "CURATED_DATABRICKS_MODELS",
    "get_picker_entries",
    # Settings → create_llm bridge (drift-guarded)
    "kwargs_from_settings",
]
