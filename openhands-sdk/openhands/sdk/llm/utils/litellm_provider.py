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
    """Parsed LiteLLM provider metadata for a model string.

    The SDK accepts full model strings at the boundary, but internal provider
    logic should work from LiteLLM's parsed ``provider`` + ``model`` view.
    """

    requested_model: str
    model: str
    name: str | None
    requested_api_base: str | None
    resolved_api_base: str | None
    dynamic_api_key: str | None

    @classmethod
    def from_model(cls, *, model: str, api_base: str | None) -> LLMProvider:
        """Parse a model string using LiteLLM's provider inference logic."""
        try:
            get_llm_provider = cast(Any, litellm).get_llm_provider
            parsed_model, provider_name, dynamic_key, resolved_api_base = (
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
            dynamic_key = None
            resolved_api_base = api_base

        return cls(
            requested_model=model,
            model=parsed_model,
            name=provider_name,
            requested_api_base=api_base,
            resolved_api_base=resolved_api_base,
            dynamic_api_key=dynamic_key,
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

    @property
    def model_names(self) -> tuple[str, ...]:
        """Return the useful model-name variants for downstream matching."""
        names = [self.model]
        if self.canonical_name != self.model:
            names.append(self.canonical_name)
        return tuple(dict.fromkeys(names))

    def as_litellm_call_kwargs(self) -> dict[str, str]:
        kwargs = {"model": self.model}
        if self.name is not None:
            kwargs["custom_llm_provider"] = self.name
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


def infer_litellm_provider(*, model: str, api_base: str | None) -> str | None:
    """Infer the LiteLLM provider for a given model.

    This delegates to LiteLLM's provider inference logic (which includes model
    list lookups like Bedrock's regional model identifiers).
    """

    return LLMProvider.from_model(model=model, api_base=api_base).name
