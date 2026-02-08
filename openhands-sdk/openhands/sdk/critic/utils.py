"""Utility functions for critic configuration.

This module provides helper functions for configuring critics, including
auto-configuration for the All-Hands LLM proxy.
"""

import re
from typing import TYPE_CHECKING

from openhands.sdk.critic.base import CriticBase
from openhands.sdk.critic.impl.api import APIBasedCritic


if TYPE_CHECKING:
    from openhands.sdk.llm import LLM


def get_default_critic(llm: "LLM") -> CriticBase | None:
    """Auto-configure critic for All-Hands LLM proxy.

    When the LLM base_url matches `llm-proxy.*.all-hands.dev`, returns an
    APIBasedCritic configured with:
    - server_url: {base_url}/vllm
    - api_key: same as LLM
    - model_name: "critic"

    This is a convenience function for users of the All-Hands infrastructure.
    For other setups, create an APIBasedCritic directly with your own
    server_url, api_key, and model_name.

    Args:
        llm: The LLM instance to derive critic configuration from.

    Returns:
        An APIBasedCritic if the LLM is configured for All-Hands proxy,
        None otherwise.

    Example:
        llm = LLM(
            model="anthropic/claude-sonnet-4-5",
            api_key=api_key,
            base_url="https://llm-proxy.eval.all-hands.dev",
        )
        critic = get_default_critic(llm)
        if critic is None:
            # Fall back to explicit configuration
            critic = APIBasedCritic(
                server_url="https://my-critic-server.com",
                api_key="my-api-key",
                model_name="my-critic-model",
            )
    """
    base_url = llm.base_url
    api_key = llm.api_key
    if base_url is None or api_key is None:
        return None

    # Match: llm-proxy.{env}.all-hands.dev (e.g., staging, prod, eval)
    pattern = r"^https?://llm-proxy\.[^./]+\.all-hands\.dev"
    if not re.match(pattern, base_url):
        return None

    return APIBasedCritic(
        server_url=f"{base_url.rstrip('/')}/vllm",
        api_key=api_key,
        model_name="critic",
    )
