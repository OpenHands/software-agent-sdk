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
        """Parse a model string using LiteLLM's provider inference logic."""
        try:
            get_llm_provider = cast(Any, litellm).get_llm_provider
            parsed_model, provider_name, _dynamic_key, _resolved_api_base = (
                get_llm_provider(
                    model=model,
                    custom_llm_provider=None,
                    api_base=api_base,
                    api_key=None,
                )
            )
        except Exception as exc:
            logger.debug(
                "Failed to parse LiteLLM provider for model=%s: %s",
                model,
                exc,
            )
            parsed_model = model
            provider_name = None

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
        # When the parsed model still starts with the provider prefix (e.g.
        # "openai/example-model" for provider "openai"), LiteLLM re-parses and
        # strips the prefix again on the Responses API path, sending the wrong
        # model upstream (e.g. "example-model" instead of "openai/example-
        # model"). Re-prefix so LiteLLM strips exactly one prefix and lands on
        # the parsed model.
        model = self.model
        if self.name is not None and model.startswith(f"{self.name}/"):
            model = f"{self.name}/{model}"
        kwargs = {"model": model}
        if self.name is not None:
            kwargs["custom_llm_provider"] = self.name
        normalized_api_key = self.api_key_for_litellm(api_key)
        if normalized_api_key is not None:
            kwargs["api_key"] = normalized_api_key
        return kwargs
