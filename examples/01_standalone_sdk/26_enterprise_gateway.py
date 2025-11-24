#!/usr/bin/env python3
"""
Enterprise Gateway Example

Demonstrates configuring OpenHands for environments that route LLM traffic through
an API gateway requiring custom headers and optional TLS overrides.
"""

import os
import uuid
from datetime import datetime

from pydantic import SecretStr

from openhands.sdk import Conversation
from openhands.sdk.llm import LLM
from openhands.tools.preset.default import get_default_agent


def build_gateway_llm() -> LLM:
    """Create an LLM instance configured for an enterprise gateway."""
    now = datetime.utcnow()
    correlation_id = uuid.uuid4().hex
    request_id = uuid.uuid4().hex

    ssl_env = os.getenv("LLM_SSL_VERIFY")
    ssl_verify: bool | str = ssl_env if ssl_env is not None else False

    return LLM(
        model=os.getenv("LLM_MODEL", "gemini-2.5-flash"),
        base_url=os.getenv(
            "LLM_BASE_URL", "https://your-corporate-proxy.company.com/api/llm"
        ),
        # an api_key input is always required but is unused when api keys
        # are passed via extra headers
        api_key=SecretStr(os.getenv("LLM_API_KEY", "placeholder")),
        custom_llm_provider=os.getenv("LLM_CUSTOM_LLM_PROVIDER", "openai"),
        ssl_verify=ssl_verify,
        extra_headers={
            # Typical headers forwarded by gateways
            "Authorization": os.getenv("LLM_GATEWAY_TOKEN", "Bearer YOUR_TOKEN"),
            "Content-Type": "application/json",
            "x-correlation-id": correlation_id,
            "x-request-id": request_id,
            "x-request-date": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
            "x-client-id": os.getenv("LLM_CLIENT_ID", "YOUR_CLIENT_ID"),
            "X-USECASE-ID": os.getenv("LLM_USECASE_ID", "YOUR_USECASE_ID"),
            "x-api-key": os.getenv("LLM_GATEWAY_API_KEY", "YOUR_API_KEY"),
        },
        # additional optional parameters
        timeout=30,
        num_retries=1,
    )


if __name__ == "__main__":
    print("=== Enterprise Gateway Configuration Example ===")

    # Build LLM with enterprise gateway configuration
    llm = build_gateway_llm()

    # Create agent and conversation
    agent = get_default_agent(llm=llm, cli_mode=True)
    conversation = Conversation(
        agent=agent,
        workspace=os.getcwd(),
    )

    # Send a message to test the enterprise gateway configuration
    conversation.send_message(
        "Analyze this codebase and create 3 facts about the current "
        "project into FACTS.txt. Do not write code."
    )
    conversation.run()
