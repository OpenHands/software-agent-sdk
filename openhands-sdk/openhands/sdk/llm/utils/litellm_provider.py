from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from typing import Any, cast


with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import litellm


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMProvider:
    """LiteLLM-parsed provider metadata for a model string.

    The SDK accepts full model strings at the boundary, but internal provider
    logic should work from LiteLLM's parsed ``provider`` + ``model`` view.
    """

    model: str
    name: str | None
    # The requested API base, forwarded to LiteLLM verbatim. The api_base that
    # get_llm_provider returns is intentionally discarded: LiteLLM may rewrite
    # it (e.g. mistral appends "/v1", some providers inject a default base),
    # and forwarding that resolved value would freeze LiteLLM's per-call
    # resolution at parse time and change what the user configured.
    api_base: str | None

    @classmethod
    def from_model(cls, *, model: str, api_base: str | None) -> LLMProvider:
        """Parse a model string using LiteLLM's provider inference logic.

        LiteLLM infers the provider from the model string's first ``/``
        segment. When ``api_base`` points at a custom endpoint (a
        self-hosted server such as LM Studio, or a third-party router) and
        that segment isn't a LiteLLM-recognized provider name, inference
        raises instead of falling back to the custom base — so a model id
        like ``auto/coding`` configured against a personal OpenAI-compatible
        router fails with "LLM Provider NOT provided" deep inside the actual
        completion call, not here. Retry once with
        ``custom_llm_provider="openai"`` in that case: a caller-supplied
        ``api_base`` already implies an OpenAI-compatible endpoint, and
        LiteLLM only strips a *recognized* provider prefix, so an
        unrecognized one like ``auto/`` reaches the endpoint unchanged.
        """
        get_llm_provider = cast(Any, litellm).get_llm_provider
        custom_llm_providers: tuple[str | None, ...] = (
            (None,) if api_base is None else (None, "openai")
        )

        parsed_model, provider_name = model, None
        for custom_llm_provider in custom_llm_providers:
            try:
                parsed_model, provider_name, _dynamic_key, _resolved_api_base = (
                    get_llm_provider(
                        model=model,
                        custom_llm_provider=custom_llm_provider,
                        api_base=api_base,
                        api_key=None,
                    )
                )
            except Exception as exc:
                logger.debug(
                    "Failed to parse LiteLLM provider for model=%s "
                    "(custom_llm_provider=%s): %s",
                    model,
                    custom_llm_provider,
                    exc,
                )
                parsed_model, provider_name = model, None
            if provider_name is not None:
                break

        return cls(
            model=parsed_model,
            name=provider_name,
            api_base=api_base,
        )

    @property
    def is_bedrock(self) -> bool:
        return self.name == "bedrock"

    def api_key_for_litellm(self, api_key: str | None) -> str | None:
        # LiteLLM treats api_key for Bedrock as an AWS bearer token.
        # Passing a non-Bedrock key (e.g. OpenAI/Anthropic) can cause Bedrock
        # to reject the request with an "Invalid API Key format" error.
        # For IAM/SigV4 auth (the default Bedrock path), do not forward api_key.
        if api_key is not None and self.is_bedrock:
            return None
        return api_key

    def as_litellm_call_kwargs(self, *, api_key: str | None = None) -> dict[str, str]:
        kwargs = {"model": self.model}
        if self.name is not None:
            kwargs["custom_llm_provider"] = self.name
        normalized_api_key = self.api_key_for_litellm(api_key)
        if normalized_api_key is not None:
            kwargs["api_key"] = normalized_api_key
        return kwargs
