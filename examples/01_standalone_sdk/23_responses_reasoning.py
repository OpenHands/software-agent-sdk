"""
Example: Responses API path via LiteLLM in a Real Agent Conversation

- Runs a real Agent/Conversation to verify /responses path works
- Demonstrates rendering of Responses reasoning within normal conversation events
"""

from __future__ import annotations

import base64
import os

from pydantic import SecretStr

from openhands.sdk import (
    Conversation,
    Event,
    ImageContent,
    LLMConvertibleEvent,
    Message,
    TextContent,
    get_logger,
)
from openhands.sdk.llm import LLM
from openhands.tools.preset.default import get_default_agent


logger = get_logger(__name__)

api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
assert api_key, "Set LLM_API_KEY or OPENAI_API_KEY in your environment."

model = "gpt-5-mini"  # Use a model that supports Responses API + vision
base_url = os.getenv("LLM_BASE_URL")

llm = LLM(
    model=model,
    api_key=SecretStr(api_key),
    base_url=base_url,
    # Responses-path options
    reasoning_effort="high",
    # Logging / behavior tweaks
    log_completions=False,
    usage_id="agent",
)

assert llm.vision_is_active(), "Selected model does not support vision input."

image_path = os.path.join(
    os.path.dirname(__file__), "responses_reasoning_screenshot.png"
)

with open(image_path, "rb") as image_file:
    image_bytes = image_file.read()

image_b64 = base64.b64encode(image_bytes).decode("utf-8")
image_data_url = f"data:image/png;base64,{image_b64}"

print("\n=== Agent Conversation using /responses path ===")
agent = get_default_agent(
    llm=llm,
    cli_mode=True,  # disable browser tools for env simplicity
)

llm_messages = []  # collect raw LLM-convertible messages for inspection


def conversation_callback(event: Event):
    if isinstance(event, LLMConvertibleEvent):
        llm_messages.append(event.to_llm_message())


conversation = Conversation(
    agent=agent,
    callbacks=[conversation_callback],
    workspace=os.getcwd(),
)

# Keep the tasks short for demo purposes
conversation.send_message(
    Message(
        role="user",
        content=[
            TextContent(
                text=(
                    "Describe the key elements in this screenshot and summarize in 1-2 "
                    "sentences."
                )
            ),
            ImageContent(image_urls=[image_data_url]),
        ],
    )
)
conversation.run()

conversation.send_message(
    "Write the description into VISION_FACTS.md in the repo root."
)
conversation.run()

print("=" * 100)
print("Conversation finished. Got the following LLM messages:")
for i, message in enumerate(llm_messages):
    ms = str(message)
    print(f"Message {i}: {ms[:200]}{'...' if len(ms) > 200 else ''}")

# Report cost
cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
