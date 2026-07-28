import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from functools import lru_cache
from logging import getLogger

import httpx
from litellm.types.utils import ModelInfo
from litellm.utils import get_model_info
from pydantic import SecretStr

from openhands.sdk.llm.utils.openhands_provider import litellm_call_kwargs


logger = getLogger(__name__)

# Model-info discovery must never block LLM construction indefinitely.
#
# Some providers (self-hosted / OpenAI-compatible such as ``lemonade``,
# ``ollama`` and ``vllm``) resolve model info by making a live HTTP request to
# the model server rather than reading a static cost map. litellm performs that
# request synchronously with no timeout, so if the endpoint is unreachable -- or
# accidentally points back at the caller (e.g. a reverse proxy that forwards to
# this very process) -- the call hangs forever. Because ``LLM`` construction is
# synchronous and runs this probe in ``_post_init``, an indefinite hang freezes
# any caller, including an async server's event loop.
#
# Bound every probe with this deadline. ``model_info`` is an optional
# enhancement (callers already handle ``None``), so timing out degrades
# gracefully instead of deadlocking.
MODEL_INFO_DISCOVERY_TIMEOUT = 10.0

_discovery_executor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="model-info-discovery"
)


def _run_with_deadline[T](
    func: Callable[..., T],
    *args,
    timeout: float | None = None,
    **kwargs,
) -> T | None:
    """Run a (possibly network-bound, non-cancellable) sync call with a deadline.

    Returns ``None`` if the call does not finish within ``timeout`` so that
    model-info discovery never blocks the caller indefinitely. A timed-out
    worker thread is abandoned (it cannot be cancelled) but is bounded by
    litellm's own socket timeout and does not block the caller.
    """
    if timeout is None:
        timeout = MODEL_INFO_DISCOVERY_TIMEOUT
    future = _discovery_executor.submit(func, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError:
        logger.warning(
            "Timed out after %ss while fetching model info; continuing without it.",
            timeout,
        )
        return None


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

        response = httpx.get(
            f"{base_url}/v1/model/info",
            headers=headers,
            timeout=MODEL_INFO_DISCOVERY_TIMEOUT,
        )
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

    # Try to get model info via openrouter or litellm proxy first
    try:
        if model.startswith("openrouter"):
            model_info = _run_with_deadline(get_model_info, model)
            if model_info:
                return model_info
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
        model_info = _run_with_deadline(get_model_info, model.split(":")[0])
        if model_info:
            return model_info
    except Exception:
        pass
    try:
        model_info = _run_with_deadline(get_model_info, model.split("/")[-1])
        if model_info:
            return model_info
    except Exception:
        pass

    return None
