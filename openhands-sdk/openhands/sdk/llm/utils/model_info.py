import time
from functools import lru_cache
from logging import getLogger

import httpx
from litellm.types.utils import ModelInfo
from litellm.utils import get_model_info
from pydantic import SecretStr

from openhands.sdk.llm.utils.openhands_provider import (
    is_openhands_proxy_base_url,
    litellm_call_kwargs,
)


logger = getLogger(__name__)


def _get_api_key_value(secret_api_key: SecretStr | str | None) -> str | None:
    if isinstance(secret_api_key, SecretStr):
        return secret_api_key.get_secret_value()
    return secret_api_key


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
        api_key = _get_api_key_value(secret_api_key)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        normalized_base_url = base_url.rstrip("/")
        if normalized_base_url.endswith("/v1"):
            normalized_base_url = normalized_base_url[:-3]
        response = httpx.get(f"{normalized_base_url}/v1/model/info", headers=headers)
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
            return model_info
    except Exception as e:
        logger.debug(
            f"Error fetching model info from proxy: {e}",
            exc_info=True,
            stack_info=True,
        )


def get_litellm_model_info(
    secret_api_key: SecretStr | str | None, base_url: str | None, model: str
) -> ModelInfo | None:
    call_kwargs = litellm_call_kwargs(model, base_url)
    model = call_kwargs["model"]
    base_url = call_kwargs["api_base"]
    api_key = _get_api_key_value(secret_api_key)

    # Try to get model info via openrouter or litellm proxy first
    try:
        if model.startswith("openrouter"):
            model_info = get_model_info(model)
            if model_info:
                return model_info
    except Exception as e:
        logger.debug(f"get_model_info(openrouter) failed: {e}")

    if model.startswith("litellm_proxy/") and base_url:
        if is_openhands_proxy_base_url(base_url) and not api_key:
            logger.debug(
                "Skipping unauthenticated model-info lookup for managed "
                "OpenHands proxy"
            )
        else:
            # Use the current hour as a cache key - only refresh hourly
            cache_key = int(time.time() / 3600)

            model_info = _get_model_info_from_litellm_proxy(
                secret_api_key=api_key,
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
            return model_info
    except Exception:
        pass
    try:
        model_info = get_model_info(model.split("/")[-1])
        if model_info:
            return model_info
    except Exception:
        pass

    return None
