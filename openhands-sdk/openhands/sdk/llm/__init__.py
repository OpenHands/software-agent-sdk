from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openhands.sdk._lazy_imports import import_lazy_symbol, lazy_dir


if TYPE_CHECKING:
    from .auth import (
        OPENAI_CODEX_MODELS,
        CredentialStore,
        OAuthCredentials,
        OpenAISubscriptionAuth,
    )
    from .fallback_strategy import FallbackStrategy
    from .llm import LLM
    from .llm_profile_store import LLMProfileStore
    from .llm_registry import LLMRegistry, RegistryEvent
    from .llm_response import LLMResponse
    from .message import (
        ImageContent,
        Message,
        MessageToolCall,
        ReasoningItemModel,
        RedactedThinkingBlock,
        TextContent,
        ThinkingBlock,
        content_to_str,
    )
    from .router import RouterLLM
    from .streaming import LLMStreamChunk, TokenCallbackType
    from .utils.metrics import Metrics, MetricsSnapshot, TokenUsage
    from .utils.unverified_models import (
        UNVERIFIED_MODELS_EXCLUDING_BEDROCK,
        get_unverified_models,
    )
    from .utils.verified_models import VERIFIED_MODELS


__all__ = [
    "CredentialStore",
    "OAuthCredentials",
    "OpenAISubscriptionAuth",
    "OPENAI_CODEX_MODELS",
    "FallbackStrategy",
    "LLMResponse",
    "LLM",
    "LLMRegistry",
    "LLMProfileStore",
    "RouterLLM",
    "RegistryEvent",
    "Message",
    "MessageToolCall",
    "TextContent",
    "ImageContent",
    "ThinkingBlock",
    "RedactedThinkingBlock",
    "ReasoningItemModel",
    "content_to_str",
    "LLMStreamChunk",
    "TokenCallbackType",
    "Metrics",
    "MetricsSnapshot",
    "TokenUsage",
    "VERIFIED_MODELS",
    "UNVERIFIED_MODELS_EXCLUDING_BEDROCK",
    "get_unverified_models",
]

_LAZY_IMPORTS = {
    "CredentialStore": (".auth", "CredentialStore"),
    "OAuthCredentials": (".auth", "OAuthCredentials"),
    "OpenAISubscriptionAuth": (".auth", "OpenAISubscriptionAuth"),
    "OPENAI_CODEX_MODELS": (".auth", "OPENAI_CODEX_MODELS"),
    "FallbackStrategy": (".fallback_strategy", "FallbackStrategy"),
    "LLMResponse": (".llm_response", "LLMResponse"),
    "LLM": (".llm", "LLM"),
    "LLMRegistry": (".llm_registry", "LLMRegistry"),
    "LLMProfileStore": (".llm_profile_store", "LLMProfileStore"),
    "RouterLLM": (".router", "RouterLLM"),
    "RegistryEvent": (".llm_registry", "RegistryEvent"),
    "Message": (".message", "Message"),
    "MessageToolCall": (".message", "MessageToolCall"),
    "TextContent": (".message", "TextContent"),
    "ImageContent": (".message", "ImageContent"),
    "ThinkingBlock": (".message", "ThinkingBlock"),
    "RedactedThinkingBlock": (".message", "RedactedThinkingBlock"),
    "ReasoningItemModel": (".message", "ReasoningItemModel"),
    "content_to_str": (".message", "content_to_str"),
    "LLMStreamChunk": (".streaming", "LLMStreamChunk"),
    "TokenCallbackType": (".streaming", "TokenCallbackType"),
    "Metrics": (".utils.metrics", "Metrics"),
    "MetricsSnapshot": (".utils.metrics", "MetricsSnapshot"),
    "TokenUsage": (".utils.metrics", "TokenUsage"),
    "VERIFIED_MODELS": (".utils.verified_models", "VERIFIED_MODELS"),
    "UNVERIFIED_MODELS_EXCLUDING_BEDROCK": (
        ".utils.unverified_models",
        "UNVERIFIED_MODELS_EXCLUDING_BEDROCK",
    ),
    "get_unverified_models": (".utils.unverified_models", "get_unverified_models"),
}


def __getattr__(name: str) -> Any:
    return import_lazy_symbol(__name__, globals(), _LAZY_IMPORTS, name)


def __dir__() -> list[str]:
    return lazy_dir(globals(), __all__)
