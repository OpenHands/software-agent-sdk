from __future__ import annotations

from typing import Any, Final, TypedDict
from urllib.parse import urlsplit, urlunsplit

from openhands.sdk.llm.utils.verified_models import VERIFIED_MODELS


OPENHANDS_PROVIDER_PREFIX: Final[str] = "openhands/"
LITELLM_PROXY_PREFIX: Final[str] = "litellm_proxy/"
OPENHANDS_LLM_PROXY_BASE_URL: Final[str] = "https://llm-proxy.app.all-hands.dev"
DIGITALOCEAN_PROVIDER_PREFIX: Final[str] = "digitalocean/"
DIGITALOCEAN_INFERENCE_BASE_URL: Final[str] = "https://inference.do-ai.run/v1"

# SDK provider prefix -> (LiteLLM prefix, default api_base).
# TODO: DigitalOcean goes through ``openai/`` only as a stopgap, because
# LiteLLM's native ``gradient_ai/`` route rejects ``tools``. Switch to
# ``gradient_ai/`` once LiteLLM supports tool calling there.
_ROUTED_PROVIDERS: Final[dict[str, tuple[str, str]]] = {
    OPENHANDS_PROVIDER_PREFIX: (LITELLM_PROXY_PREFIX, OPENHANDS_LLM_PROXY_BASE_URL),
    DIGITALOCEAN_PROVIDER_PREFIX: ("openai/", DIGITALOCEAN_INFERENCE_BASE_URL),
}


class LiteLLMCallKwargs(TypedDict):
    model: str
    api_base: str | None


_OPENHANDS_PROXY_BASE_URLS: Final[frozenset[str]] = frozenset(
    {
        "https://llm-proxy.app.all-hands.dev",
        "https://llm-proxy.app.all-hands.dev/v1",
    }
)


def is_openhands_provider_model(model: str | None) -> bool:
    return bool(model and model.startswith(OPENHANDS_PROVIDER_PREFIX))


def is_litellm_proxy_model(model: str | None) -> bool:
    return bool(model and model.startswith(LITELLM_PROXY_PREFIX))


def _normalize_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url.strip())
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def is_openhands_proxy_base_url(base_url: str | None) -> bool:
    if not base_url:
        return False
    return _normalize_base_url(base_url) in _OPENHANDS_PROXY_BASE_URLS


def _is_verified_openhands_model_name(model_name: str) -> bool:
    return model_name in VERIFIED_MODELS["openhands"]


def litellm_call_kwargs(model: str, base_url: str | None) -> LiteLLMCallKwargs:
    for prefix, (litellm_prefix, default_base_url) in _ROUTED_PROVIDERS.items():
        if model.startswith(prefix):
            return {
                "model": f"{litellm_prefix}{model.removeprefix(prefix)}",
                "api_base": base_url or default_base_url,
            }
    return {"model": model, "api_base": base_url}


def canonicalize_openhands_llm_payload(payload: dict[str, Any]) -> dict[str, Any]:
    model = payload.get("model")
    if not isinstance(model, str):
        return payload

    migrated = dict(payload)
    base_url = migrated.get("base_url")
    normalized_base_url = base_url if isinstance(base_url, str) else None

    if is_openhands_provider_model(model):
        if is_openhands_proxy_base_url(normalized_base_url):
            migrated.pop("base_url", None)
        return migrated

    if not (
        is_litellm_proxy_model(model)
        and is_openhands_proxy_base_url(normalized_base_url)
    ):
        return migrated

    model_name = model.removeprefix(LITELLM_PROXY_PREFIX)
    if not _is_verified_openhands_model_name(model_name):
        return migrated

    migrated["model"] = f"{OPENHANDS_PROVIDER_PREFIX}{model_name}"
    migrated.pop("base_url", None)
    return migrated
