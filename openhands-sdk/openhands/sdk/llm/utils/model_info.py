import time
from collections.abc import Mapping
from functools import lru_cache
from logging import getLogger
from typing import Any

import httpx
import litellm
from litellm import model_cost
from litellm.utils import get_model_info
from pydantic import SecretStr

from openhands.sdk.llm.utils.openhands_provider import litellm_call_kwargs


logger = getLogger(__name__)


def _merge_raw_model_metadata(model_info: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve raw LiteLLM capability fields."""
    key = model_info.get("key")
    raw = model_cost.get(key) if isinstance(key, str) else None
    if not isinstance(raw, dict):
        return dict(model_info)
    return {**raw, **model_info}


# Pricing fields copied from the underlying model so a custom proxy alias
# becomes priceable in litellm's global `model_cost` map (#4816).
_PRICING_FIELDS: tuple[str, ...] = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
    "cache_creation_input_token_cost",
    "max_input_tokens",
    "max_output_tokens",
    "litellm_provider",
    "mode",
)


def _register_proxy_alias_pricing(
    alias: str,
    underlying_model_info: Mapping[str, Any] | None,
    proxy_model_info: Mapping[str, Any] | None,
) -> None:
    """Register a `litellm_proxy/*` alias into litellm's `model_cost` (#4816).

    litellm strips the `litellm_proxy/` prefix and parses the first segment as
    the provider, so a custom name like `prod/claude-sonnet-4-5-bedrock` makes
    `cost_per_token` raise and the span is silently priced $0. Registering the
    alias with the underlying model's pricing makes it priceable without
    renaming the proxy model.
    """
    if not alias or alias in model_cost:
        return
    try:
        litellm.cost_per_token(model=alias, prompt_tokens=1, completion_tokens=1)
        return
    except Exception:
        pass

    entry: dict[str, Any] = {}
    for src in (underlying_model_info, proxy_model_info):
        if isinstance(src, Mapping):
            entry.update({f: src[f] for f in _PRICING_FIELDS if f in src})

    # litellm's `get_llm_provider` cannot resolve `bedrock_converse` as a
    # provider label for an unknown alias id (only ``bedrock`` is accepted), so
    # normalize it before registering or `cost_per_token` keeps raising.

    if entry.get("litellm_provider") == "bedrock_converse":
        entry["litellm_provider"] = "bedrock"

    if (
        entry.get("input_cost_per_token") is None
        and entry.get("output_cost_per_token") is None
    ):
        return

    entry.setdefault("mode", "chat")
    try:
        litellm.register_model({alias: entry})
    except Exception as e:
        logger.debug("Failed to register litellm_proxy alias %r: %s", alias, e)


@lru_cache
def _get_model_info_from_litellm_proxy(
    secret_api_key: SecretStr | str | None,
    base_url: str,
    model: str,
    cache_key: int | None = None,
):
    logger.debug(f"Get model_info_from_litellm_proxy:{cache_key}")
    try:
        headers = {}
        if isinstance(secret_api_key, SecretStr):
            secret_api_key = secret_api_key.get_secret_value()
        if secret_api_key:
            headers["Authorization"] = f"Bearer {secret_api_key}"

        response = httpx.get(f"{base_url}/v1/model/info", headers=headers)
        data = response.json().get("data", [])
        # Match against either the public alias (`model_name`) or the
        # underlying provider/model_name form (`litellm_params.model`). The proxy itself
        # accepts requests by either form, and our proxy configs often
        # advertise a short alias (e.g. `claude-opus-4-8`) for a provider
        # id (`anthropic/claude-opus-4-8`). Without the second match,
        # `model_info` overrides set on the proxy are invisible to clients
        # that address the model by its provider id.
        stripped = model.removeprefix("litellm_proxy/")
        current = next(
            (
                info
                for info in data
                if info.get("model_name") == stripped
                or info.get("litellm_params", {}).get("model") == stripped
            ),
            None,
        )
        if current:
            model_info = current.get("model_info")
            logger.debug(f"Got model info from litellm proxy: {model_info}")

            # Make custom proxy aliases priceable so cost instrumentation does
            # not silently record $0 (#4816).
            underlying_model = current.get("litellm_params", {}).get("model")
            underlying_model_info = None
            if isinstance(underlying_model, str) and underlying_model != stripped:
                try:
                    underlying_model_info = get_model_info(underlying_model)
                except Exception as e:
                    logger.debug(
                        f"get_model_info(underlying={underlying_model}) failed: {e}"
                    )
            _register_proxy_alias_pricing(
                alias=stripped,
                underlying_model_info=underlying_model_info,
                proxy_model_info=model_info,
            )

            return model_info
    except Exception as e:
        logger.debug(
            f"Error fetching model info from proxy: {e}",
            exc_info=True,
            stack_info=True,
        )


def get_litellm_model_info(
    secret_api_key: SecretStr | str | None, base_url: str | None, model: str
) -> dict[str, Any] | None:
    call_kwargs = litellm_call_kwargs(model, base_url)
    model = call_kwargs["model"]
    base_url = call_kwargs["api_base"]

    # Try to get model info via openrouter or litellm proxy first
    try:
        if model.startswith("openrouter"):
            model_info = get_model_info(model)
            if model_info:
                return _merge_raw_model_metadata(model_info)
    except Exception as e:
        logger.debug(f"get_model_info(openrouter) failed: {e}")

    if model.startswith("litellm_proxy/") and base_url:
        # Use the current hour as a cache key - only refresh hourly
        cache_key = int(time.time() / 3600)

        model_info = _get_model_info_from_litellm_proxy(
            secret_api_key=secret_api_key,
            base_url=base_url,
            model=model,
            cache_key=cache_key,
        )
        if model_info:
            return model_info

    # Fallbacks: try base name variants
    try:
        model_info = get_model_info(model.split(":")[0])
        if model_info:
            return _merge_raw_model_metadata(model_info)
    except Exception:
        pass
    try:
        model_info = get_model_info(model.split("/")[-1])
        if model_info:
            return _merge_raw_model_metadata(model_info)
    except Exception:
        pass

    return None
