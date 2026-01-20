"""Authentication module for LLM subscription-based access.

This module provides OAuth-based authentication for LLM providers that support
subscription-based access (e.g., ChatGPT Plus/Pro for OpenAI Codex models).
"""

from openhands.sdk.llm.auth.credentials import (
    CredentialStore,
    OAuthCredentials,
)
from openhands.sdk.llm.auth.openai import (
    OPENAI_CODEX_MODELS,
    OpenAISubscriptionAuth,
    SupportedVendor,
    inject_system_prefix,
    transform_for_subscription,
)


__all__ = [
    "CredentialStore",
    "OAuthCredentials",
    "OpenAISubscriptionAuth",
    "OPENAI_CODEX_MODELS",
    "SupportedVendor",
    "inject_system_prefix",
    "transform_for_subscription",
]
