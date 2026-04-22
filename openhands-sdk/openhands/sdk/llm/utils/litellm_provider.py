from __future__ import annotations

import warnings
from dataclasses import dataclass
from functools import cached_property
from typing import Any, cast


with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import litellm
    from litellm.types.utils import LlmProviders
    from litellm.utils import ProviderConfigManager


@dataclass(frozen=True)
class LLMProvider:
    """LiteLLM-parsed provider metadata for a model string.

    The SDK accepts full model strings at the boundary, but internal provider
    logic should work from LiteLLM's parsed ``provider`` + ``model`` view.
    """

    model: str
    name: str | None
    resolved_api_base: str | None

    @classmethod
    def from_model(cls, *, model: str, api_base: str | None) -> LLMProvider:
        """Parse a model string using LiteLLM's provider inference logic."""
        try:
            get_llm_provider = cast(Any, litellm).get_llm_provider
            parsed_model, provider_name, _dynamic_key, resolved_api_base = (
                get_llm_provider(
                    model=model,
                    custom_llm_provider=None,
                    api_base=api_base,
                    api_key=None,
                )
            )
        except Exception:
            parsed_model = model
            provider_name = None
            resolved_api_base = api_base

        return cls(
            model=parsed_model,
            name=provider_name,
            resolved_api_base=resolved_api_base,
        )

    @cached_property
    def provider_enum(self) -> LlmProviders | None:
        if self.name is None:
            return None

        try:
            return LlmProviders(self.name)
        except ValueError:
            return None

    @cached_property
    def model_info(self) -> Any | None:
        if self.provider_enum is None:
            return None

        try:
            return ProviderConfigManager.get_provider_model_info(
                self.model, self.provider_enum
            )
        except Exception:
            return None

    @property
    def canonical_name(self) -> str:
        if self.name is None:
            return self.model
        return f"{self.name}/{self.model}"

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

    def infer_api_base(self) -> str | None:
        """Infer a provider API base without reimplementing provider logic."""
        try:
            get_api_base = cast(Any, litellm).get_api_base
            api_base = get_api_base(self.canonical_name, {})
            if api_base:
                return cast(str, api_base)
        except Exception:
            pass

        if self.model_info is not None and hasattr(self.model_info, "get_api_base"):
            try:
                api_base = self.model_info.get_api_base()
            except NotImplementedError:
                api_base = None
            except Exception:
                api_base = None
            if api_base:
                return cast(str, api_base)

        return self.resolved_api_base
