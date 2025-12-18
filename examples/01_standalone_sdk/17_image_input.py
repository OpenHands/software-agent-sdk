"""OpenHands Agent SDK — Image Input Example.

This script mirrors the basic setup from ``examples/01_hello_world.py`` but adds
vision support by sending an image to the agent alongside text instructions.
"""

import os

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Event,
    ImageContent,
    LLMConvertibleEvent,
    Message,
    TextContent,
    get_logger,
)
from openhands.sdk.tool.spec import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


logger = get_logger(__name__)

# Configure LLM (vision-capable model)
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")
llm = LLM(
    usage_id="vision-llm",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
)
assert llm.vision_is_active(), "The selected LLM model does not support vision input."

cwd = os.getcwd()

agent = Agent(
    llm=llm,
    tools=[
        Tool(
            name=TerminalTool.name,
        ),
        Tool(name=FileEditorTool.name),
        Tool(name=TaskTrackerTool.name),
    ],
)

llm_messages = []  # collect raw LLM messages for inspection


def conversation_callback(event: Event) -> None:
    if isinstance(event, LLMConvertibleEvent):
        llm_messages.append(event.to_llm_message())


conversation = Conversation(
    agent=agent, callbacks=[conversation_callback], workspace=cwd
)

IMAGE_URL = "https://github.com/OpenHands/docs/raw/main/openhands/static/img/logo.png"

conversation.send_message(
    Message(
        role="user",
        content=[
            TextContent(
                text=(
                    "Study this image and describe the key elements you see. "
                    "Summarize them in a short paragraph and suggest a catchy caption."
                )
            ),
            ImageContent(image_urls=[IMAGE_URL]),
        ],
    )
)
conversation.run()

conversation.send_message(
    "Great! Please save your description and caption into image_report.md."
)
conversation.run()

print("=" * 100)
print("Conversation finished. Got the following LLM messages:")
for i, message in enumerate(llm_messages):
    print(f"Message {i}: {str(message)[:200]}")

# Report cost
cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
