from openhands.sdk.llm.auth import (
    OPENAI_CODEX_MODELS,
    CredentialStore,
    OAuthCredentials,
    OpenAISubscriptionAuth,
)
from openhands.sdk.llm.fallback_strategy import FallbackStrategy
from openhands.sdk.llm.llm import LLM, LLM_PROFILE_SCHEMA_VERSION
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.llm_registry import LLMRegistry, RegistryEvent
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.llm.message import (
    ImageContent,
    Message,
    MessageToolCall,
    ReasoningItemModel,
    RedactedThinkingBlock,
    TextContent,
    ThinkingBlock,
    content_to_str,
)
from openhands.sdk.llm.router import RouterLLM
from openhands.sdk.llm.streaming import (
    AsyncTokenCallbackType,
    LLMStreamChunk,
    TokenCallbackType,
)
from openhands.sdk.llm.utils.metrics import Metrics, MetricsSnapshot, TokenUsage
from openhands.sdk.llm.utils.unverified_models import (
    UNVERIFIED_MODELS_EXCLUDING_BEDROCK,
    get_unverified_models,
)
from openhands.sdk.llm.utils.verified_models import VERIFIED_MODELS

# Eagerly import DatabricksLLM so it registers with LLM.__subclasses__() at
# module-load time.  This ensures that LLM._dispatch_to_provider_subclass can
# reconstruct a DatabricksLLM when deserializing persisted agent JSON that
# carries provider="databricks" — even in processes (e.g. the agent server)
# that never explicitly import the Databricks provider.
try:
    from openhands.sdk.llm.providers.databricks.llm import DatabricksLLM  # noqa: F401
except Exception:  # pragma: no cover — optional dependency
    pass


__all__ = [
    # Auth
    "CredentialStore",
    "OAuthCredentials",
    "OpenAISubscriptionAuth",
    "OPENAI_CODEX_MODELS",
    # Core
    "FallbackStrategy",
    "LLMResponse",
    "LLM",
    "LLM_PROFILE_SCHEMA_VERSION",
    "LLMRegistry",
    "LLMProfileStore",
    "RouterLLM",
    "RegistryEvent",
    # Messages
    "Message",
    "MessageToolCall",
    "TextContent",
    "ImageContent",
    "ThinkingBlock",
    "RedactedThinkingBlock",
    "ReasoningItemModel",
    "content_to_str",
    # Streaming
    "AsyncTokenCallbackType",
    "LLMStreamChunk",
    "TokenCallbackType",
    # Metrics
    "Metrics",
    "MetricsSnapshot",
    "TokenUsage",
    # Models
    "VERIFIED_MODELS",
    "UNVERIFIED_MODELS_EXCLUDING_BEDROCK",
    "get_unverified_models",
]
