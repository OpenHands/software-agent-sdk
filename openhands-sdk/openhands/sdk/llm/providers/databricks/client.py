"""Databricks AI Gateway synchronous HTTP client.

Two distinct Databricks surfaces, kept strictly separate:

* **AI Gateway** (FM invocations) → ``ai_gateway_host`` (optional override).
  Defaults to ``credentials.host``. All ``POST /ai-gateway/...`` traffic
  goes here, exclusively.
* **Workspace** (auth, discovery, metadata probes) → ``credentials.host``.
  Used by auth/token resolution and the opt-in metadata probe; never used
  for FM invocations.

Path templates by provider family (relative to the AI Gateway base URL,
which :meth:`AIGatewayPaths.normalize_base` produces from the configured
host — see ``models.py``):

* :attr:`ProviderFamily.OPENAI`           → ``POST {base}/mlflow/v1/chat/completions``
* :attr:`ProviderFamily.OPENAI_RESPONSES` → ``POST {base}/openai/v1/responses``
* :attr:`ProviderFamily.ANTHROPIC`        → ``POST {base}/anthropic/v1/messages``
* :attr:`ProviderFamily.GEMINI`           → ``POST {base}/gemini/v1beta/models/{endpoint}:generateContent``

Family routing:

* Default — ``detect_family(model)`` resolves the family from the model
  name; no network call.
* ``metadata_probe=True`` — issues
  ``GET /api/2.0/serving-endpoints/{name}`` against the workspace before
  each cache-miss invocation; results cached for 5 minutes per process.

Streaming stays on the universal OpenAI Chat SSE path
(``{base}/mlflow/v1/chat/completions`` with ``stream=true`` in the body).

Non-streaming: singleton ``httpx.Client`` for connection pooling, thread-safe.
Streaming: context-managed client per request — always closed, no leak.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

import httpx
from litellm.types.utils import ModelResponse

from openhands.sdk.llm.providers.databricks.auth import DatabricksCredentials
from openhands.sdk.llm.providers.databricks.models import (
    AIGatewayPaths,
    ProviderFamily,
    detect_family,
    pick_family_from_api_types,
)
from openhands.sdk.llm.providers.databricks.native import from_native, to_native
from openhands.sdk.llm.providers.databricks.utils import (
    USER_AGENT,
    DatabricksTimeouts,
    _raise_non_retryable,
    fetch_with_retry,
)

logger = logging.getLogger(__name__)

TokenCallbackType = Callable[[str], None]

# Metadata cache: endpoint name -> (family, expires_at_epoch_seconds).
_METADATA_TTL_S: float = 300.0
_METADATA_NEGATIVE_TTL_S: float = 60.0


def _bare_endpoint(model: str) -> str:
    """Strip ``databricks/`` / ``databricks-`` prefix to get the endpoint name."""
    name = model.strip()
    for prefix in ("databricks/",):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name


class DatabricksFMAPIClient:
    """Synchronous Foundation Model client for Databricks AI Gateway.

    Public API is unchanged from the previous single-route client —
    ``chat_completion(model=..., messages=..., stream=..., tools=..., **kwargs)`` —
    but internally the request is routed to the correct native surface by
    provider family.

    Thread-safety: the singleton ``self._http`` is used for all non-streaming
    calls (including metadata lookups). Streaming opens a fresh
    context-managed client per request.
    """

    def __init__(
        self,
        credentials: DatabricksCredentials,
        timeouts: DatabricksTimeouts,
        ai_gateway_host: str | None = None,
        max_retries: int = 3,
        ssl_verify: bool = True,
        paths: AIGatewayPaths | None = None,
        metadata_probe: bool = False,
    ) -> None:
        # AI Gateway host is an optional override; for the common
        # single-URL Databricks deployment the workspace host doubles as
        # the gateway base (``<workspace>/ai-gateway/<route>``).
        gateway_host = (ai_gateway_host or credentials.host or "").rstrip("/")
        if not gateway_host:
            raise ValueError(
                "Either ai_gateway_host or credentials.host must be provided; "
                "the FM client needs at least one URL to route invocations to."
            )
        self._credentials = credentials
        self._timeouts = timeouts
        self._max_retries = max_retries
        self._ssl_verify = ssl_verify
        self._paths = paths or AIGatewayPaths()
        self._ai_gateway_host = gateway_host
        self._metadata_probe = metadata_probe
        self._http = httpx.Client(
            verify=ssl_verify,
            timeout=httpx.Timeout(
                connect=timeouts.connect_s,
                read=timeouts.read_s,
                write=10.0,
                pool=timeouts.pool_s,
            ),
        )
        self._metadata_cache: dict[str, tuple[ProviderFamily, float]] = {}
        self._metadata_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __del__(self) -> None:  # pragma: no cover — best-effort cleanup
        try:
            self._http.close()
        except Exception:
            pass

    def close(self) -> None:
        """Explicitly close the singleton HTTP client."""
        self._http.close()

    # ------------------------------------------------------------------
    # Headers / auth
    # ------------------------------------------------------------------

    def _make_headers(self, family: ProviderFamily) -> dict[str, str]:
        """Build request headers. Never re-import USER_AGENT per request."""
        h = {
            "Authorization": f"Bearer {self._credentials.get_token()}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if family is ProviderFamily.ANTHROPIC:
            h["anthropic-version"] = "2023-06-01"
        return h

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def resolve_family(self, model: str) -> ProviderFamily:
        """Resolve the AI Gateway path family for ``model``.

        Default: name-pattern only via ``detect_family(model)``.

        Opt-in (``metadata_probe=True``): metadata-first with name-pattern
        fallback; issues ``GET /api/2.0/serving-endpoints/{name}`` against
        the workspace, cached for 5 minutes per endpoint.
        """
        if not self._metadata_probe:
            return detect_family(model)

        endpoint = _bare_endpoint(model)
        now = time.time()
        with self._metadata_lock:
            hit = self._metadata_cache.get(endpoint)
            if hit and hit[1] > now:
                return hit[0]
        family = self._probe_metadata(endpoint) or detect_family(model)
        ttl = _METADATA_TTL_S if self._probe_succeeded else _METADATA_NEGATIVE_TTL_S
        with self._metadata_lock:
            self._metadata_cache[endpoint] = (family, now + ttl)
        return family

    _probe_succeeded: bool = False  # set by _probe_metadata; read by resolve_family

    def _probe_metadata(self, endpoint: str) -> ProviderFamily | None:
        """GET /api/2.0/serving-endpoints/{endpoint} → family (or None)."""
        if not self._credentials.host:
            raise ValueError(
                "databricks_host is required when "
                "databricks_metadata_probe=True; the metadata probe targets "
                "the workspace control plane."
            )
        url = f"{self._credentials.host}/api/2.0/serving-endpoints/{endpoint}"
        try:
            resp = self._http.get(
                url,
                headers={
                    "Authorization": f"Bearer {self._credentials.get_token()}",
                    "User-Agent": USER_AGENT,
                },
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            logger.debug("databricks_metadata_probe_failed", extra={
                "endpoint": endpoint, "error": str(exc)
            })
            self._probe_succeeded = False
            return None
        if resp.status_code != 200:
            logger.debug("databricks_metadata_probe_nonok", extra={
                "endpoint": endpoint, "status": resp.status_code,
            })
            self._probe_succeeded = False
            return None
        try:
            meta = resp.json()
        except ValueError:
            self._probe_succeeded = False
            return None
        entities = ((meta.get("config") or {}).get("served_entities")) or []
        fm = (entities[0] if entities else {}).get("foundation_model") or {}
        em = (entities[0] if entities else {}).get("external_model") or {}
        family = pick_family_from_api_types(
            api_types=fm.get("api_types"),
            external_provider=em.get("provider"),
        )
        self._probe_succeeded = True
        return family

    def invalidate_metadata(self, model: str) -> None:
        """Drop the cached family for an endpoint (e.g. on 404/410)."""
        endpoint = _bare_endpoint(model)
        with self._metadata_lock:
            self._metadata_cache.pop(endpoint, None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat_completion(
        self,
        model: str,
        messages: list[dict],
        stream: bool = False,
        tools: list[dict] | None = None,
        on_token: TokenCallbackType | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Dispatch a chat-completion call across AI Gateway surfaces."""
        endpoint = _bare_endpoint(model)

        if stream:
            family = ProviderFamily.OPENAI
            url = self._paths.url(self._ai_gateway_host, family, endpoint)
            payload = to_native(
                family, endpoint, messages,
                tools=tools, stream=True, **kwargs,
            )
            return self._handle_stream(url, self._make_headers(family), payload,
                                       endpoint, on_token)

        family = self.resolve_family(model)
        url = self._paths.url(self._ai_gateway_host, family, endpoint)
        payload = to_native(family, endpoint, messages, tools=tools, **kwargs)
        headers = self._make_headers(family)

        response = fetch_with_retry(
            client=self._http,
            url=url,
            headers=headers,
            json=payload,
            max_retries=self._max_retries,
        )
        logger.debug(
            "databricks_ai_gateway_response",
            extra={
                "status": response.status_code,
                "request_id": response.headers.get("x-request-id"),
                "endpoint": endpoint,
                "family": family.value,
                "auth_method": self._credentials.auth_method,
                # Intentionally NOT logging: Authorization header, token values,
                # request/response bodies (may contain user prompts).
            },
        )
        return self._parse_response(response, endpoint, family)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(
        self,
        response: httpx.Response,
        model: str,
        family: ProviderFamily,
    ) -> ModelResponse:
        """Convert a native response to ``litellm.ModelResponse``.

        Flow: native JSON → ``from_native`` → OpenAI ChatCompletion dict →
        ``ModelResponse``. Fallback builds a minimal response if the native
        shape is unexpected.
        """
        try:
            data = response.json()
        except ValueError:
            return ModelResponse(id="databricks-response", choices=[], model=model)

        try:
            chat = from_native(family, model, data)
            return ModelResponse(**chat)
        except Exception:
            logger.warning(
                "databricks_parse_fallback",
                extra={"family": family.value, "endpoint": model},
            )
            return ModelResponse(
                id=data.get("id", "databricks-response"),
                choices=data.get("choices", []),
                model=data.get("model", model),
                usage=data.get("usage"),
            )

    # ------------------------------------------------------------------
    # Streaming (OpenAI Chat only for V1)
    # ------------------------------------------------------------------

    def _handle_stream(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict,
        model: str,
        on_token: TokenCallbackType | None,
    ) -> ModelResponse:
        """Stream ``/invocations`` with a fresh context-managed client."""
        chunk_count = 0
        accumulated_content = ""
        last_chunk_id = ""

        with httpx.Client(
            verify=self._ssl_verify,
            timeout=httpx.Timeout(
                connect=self._timeouts.connect_s,
                read=self._timeouts.chunk_s,
                write=10.0,
                pool=self._timeouts.pool_s,
            ),
        ) as stream_client:
            with stream_client.stream(
                "POST", url, headers=headers, json=payload
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    _raise_non_retryable(resp)
                for line in resp.iter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    chunk_count += 1
                    try:
                        chunk = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    last_chunk_id = chunk.get("id", last_chunk_id)
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        token = delta.get("content")
                        if token:
                            accumulated_content += token
                            if on_token is not None:
                                on_token(token)

        logger.debug(
            "databricks_stream_complete",
            extra={
                "chunks": chunk_count,
                "endpoint": model,
                "auth_method": self._credentials.auth_method,
            },
        )
        return self._build_stream_response(accumulated_content, last_chunk_id, model)

    def _build_stream_response(
        self, content: str, response_id: str, model: str,
    ) -> ModelResponse:
        """Build a ``ModelResponse`` from accumulated stream content."""
        return ModelResponse(
            id=response_id or "databricks-stream",
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            model=model,
            object="chat.completion",
        )
