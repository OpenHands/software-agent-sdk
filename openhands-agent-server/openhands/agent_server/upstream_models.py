"""Server-side model discovery for OpenAI-compatible custom ``base_url`` endpoints.

Fetches ``GET {base_url}/v1/models`` (normalizing trailing ``/v1`` or ``/v1/models``
segments) using ``httpx`` with a bounded timeout, normalizes both standard OpenAI
``{"data": [{"id": ...}]}`` payloads and flat ``{"models": [...]}`` variants
(``id``/``slug``/``name`` keys), and maps transport/parse failures to SDK domain
errors without leaking credentials in responses, errors, or logs.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from openhands.sdk.errors import Invalid, UpstreamFailed, UpstreamTimeout
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.redact import redact_text_secrets


logger = get_logger(__name__)

UPSTREAM_MODELS_TIMEOUT_SECONDS = 10.0

_MODEL_ID_KEYS = ("id", "slug", "name")
_BLOCKED_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "metadata",
    }
)


def redact_upstream_message(text: str, api_key: str | None) -> str:
    """Redact known secret patterns and the explicit upstream API key."""
    safe = redact_text_secrets(text)
    if api_key:
        safe = safe.replace(api_key, "[REDACTED]")
    return safe


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Reject link-local/metadata, unspecified, multicast, and broadcast IPs.

    Loopback (127.0.0.1, ::1) and private LAN addresses remain allowed so
    self-hosted / local OpenAI-compatible proxies (LiteLLM, Ollama, vLLM, etc.)
    work as intended.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_blocked_ip(ip.ipv4_mapped)
    return (
        ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or (
            isinstance(ip, ipaddress.IPv4Address)
            and ip == ipaddress.IPv4Address("255.255.255.255")
        )
    )


def _validate_upstream_hostname(hostname: str) -> None:
    """Validate host against malformed characters and blocked metadata targets."""
    host_lower = hostname.strip().rstrip(".").lower()
    if not host_lower or any(ch.isspace() or ord(ch) < 32 for ch in host_lower):
        raise Invalid("base_url contains an invalid hostname")
    if host_lower in _BLOCKED_METADATA_HOSTS:
        raise Invalid("base_url host is not allowed")
    try:
        ip = ipaddress.ip_address(host_lower)
    except ValueError:
        return
    if _is_blocked_ip(ip):
        raise Invalid("base_url IP address is not allowed")


async def _validate_resolved_host(hostname: str, port: int) -> None:
    """Resolve ``hostname`` asynchronously and block unsafe IP destinations."""
    try:
        ipaddress.ip_address(hostname)
        return
    except ValueError:
        pass

    loop = asyncio.get_running_loop()
    try:
        addrinfo = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UpstreamFailed(f"Failed to resolve upstream host '{hostname}'") from exc

    for _, _, _, _, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise Invalid("base_url resolves to a disallowed IP address")


def normalize_upstream_base_url(base_url: str) -> str:
    """Validate and normalize an upstream ``base_url`` without trailing slash."""
    raw = base_url.strip()
    if not raw:
        raise Invalid("base_url must be a non-empty http or https URL")
    if any(ch.isspace() or ord(ch) < 32 for ch in raw):
        raise Invalid("base_url must not contain whitespace or control characters")

    parsed = urlparse(raw)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or not parsed.hostname
    ):
        raise Invalid("base_url must be a valid http or https URL")
    if parsed.username or parsed.password:
        raise Invalid("base_url must not contain embedded credentials")

    try:
        port = parsed.port
    except ValueError as exc:
        raise Invalid("base_url contains an invalid port") from exc

    hostname = parsed.hostname.lower()
    _validate_upstream_hostname(hostname)

    host_part = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 80 if parsed.scheme == "http" else 443
    netloc = (
        f"{host_part}:{port}"
        if port is not None and port != default_port
        else host_part
    )
    normalized_path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, netloc, normalized_path, "", "", ""))


def build_upstream_models_url(base_url: str) -> str:
    """Build the ``/v1/models`` discovery URL from a validated ``base_url``."""
    normalized = normalize_upstream_base_url(base_url)
    parsed = urlparse(normalized)
    path = parsed.path

    if path.endswith("/v1/models"):
        models_path = path
    elif path.endswith("/v1"):
        models_path = f"{path}/models"
    else:
        models_path = f"{path}/v1/models" if path else "/v1/models"

    return urlunparse((parsed.scheme, parsed.netloc, models_path, "", "", ""))


def _extract_model_ids(entries: Iterable[Any]) -> list[str]:
    models: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            candidate = entry.strip()
            if candidate:
                models.append(candidate)
        elif isinstance(entry, dict):
            for key in _MODEL_ID_KEYS:
                value = entry.get(key)
                if isinstance(value, str) and value.strip():
                    models.append(value.strip())
                    break
    return sorted(set(models))


def parse_upstream_models_payload(payload: Any) -> list[str]:
    """Normalize OpenAI ``data[]`` and flat ``models[]`` catalog payloads."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return _extract_model_ids(data)
        models = payload.get("models")
        if isinstance(models, list):
            return _extract_model_ids(models)
        raise UpstreamFailed(
            "Upstream model catalog response has an unrecognized format"
        )

    if isinstance(payload, list):
        return _extract_model_ids(payload)

    raise UpstreamFailed("Upstream model catalog response is not a JSON object or list")


def model_matches_upstream_catalog(model: str, catalog_models: Iterable[str]) -> bool:
    """Return True if ``model`` (or its unprefixed ID) appears in ``catalog_models``."""
    available = set(catalog_models)
    if not available:
        return True
    if model in available:
        return True
    # Profiles frequently use LiteLLM provider prefixes (e.g. ``openai/my-model``
    # or ``litellm_proxy/my-model``) while the upstream catalog lists ``my-model``.
    if "/" in model:
        _, remainder = model.split("/", 1)
        if remainder in available:
            return True
    return False


async def fetch_upstream_models(
    base_url: str,
    *,
    api_key: str | None = None,
    request_timeout: float | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Fetch and normalize available model IDs from an OpenAI-compatible endpoint.

    Raises:
        Invalid: ``base_url`` is missing or not a valid http(s) URL.
        UpstreamTimeout: The upstream catalog request timed out.
        UpstreamFailed: DNS/transport failure, non-2xx status, or invalid JSON/shape.
    """
    url = build_upstream_models_url(base_url)
    cleaned_key = api_key.strip() if api_key else None
    headers = {"Accept": "application/json"}
    if cleaned_key:
        headers["Authorization"] = f"Bearer {cleaned_key}"

    effective_timeout = (
        UPSTREAM_MODELS_TIMEOUT_SECONDS if request_timeout is None else request_timeout
    )
    own_client = http_client is None
    if own_client:
        parsed_url = urlparse(url)
        assert parsed_url.hostname is not None
        port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
        await _validate_resolved_host(parsed_url.hostname, port)

    client = http_client or httpx.AsyncClient(
        timeout=effective_timeout, follow_redirects=False
    )
    try:
        response = await client.get(url, headers=headers, follow_redirects=False)
    except httpx.InvalidURL as exc:
        raise Invalid("base_url is not a valid URL") from exc
    except httpx.TimeoutException as exc:
        logger.debug("Upstream model catalog request timed out for %s", url)
        raise UpstreamTimeout("Upstream model catalog request timed out") from exc
    except httpx.HTTPError as exc:
        safe_detail = redact_upstream_message(
            str(exc) or type(exc).__name__, cleaned_key
        )
        logger.debug(
            "Upstream model catalog request failed for %s: %s", url, safe_detail
        )
        raise UpstreamFailed(
            f"Failed to reach upstream model catalog: {safe_detail}"
        ) from exc
    finally:
        if own_client:
            await client.aclose()

    if response.status_code < 200 or response.status_code >= 300:
        logger.debug(
            "Upstream model catalog %s returned HTTP %s", url, response.status_code
        )
        raise UpstreamFailed(
            f"Upstream model catalog returned HTTP {response.status_code}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        logger.debug("Upstream model catalog %s returned invalid JSON", url)
        raise UpstreamFailed("Upstream model catalog returned invalid JSON") from exc

    return parse_upstream_models_payload(payload)
