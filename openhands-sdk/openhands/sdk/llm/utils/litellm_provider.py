from __future__ import annotations

import warnings
from typing import Any, cast


with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import litellm


def infer_litellm_provider(*, model: str, api_base: str | None) -> str | None:
    """Infer the LiteLLM provider for a given model.

    This delegates to LiteLLM's provider inference logic (which includes model
    list lookups like Bedrock's regional model identifiers).
    """

    try:
        get_llm_provider = cast(Any, litellm).get_llm_provider
        _model, provider, _dynamic_key, _api_base = get_llm_provider(
            model=model,
            custom_llm_provider=None,
            api_base=api_base,
            api_key=None,
        )
    except Exception:
        return None

    return provider
