from __future__ import annotations

import os
import sys

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Event,
    LLMConvertibleEvent,
    get_logger,
)
from openhands.sdk.tool import Tool
from openhands.tools.browser_use import BrowserToolSet
from openhands.tools.file_editor import FileEditorTool


logger = get_logger(__name__)

api_key = os.getenv("LLM_API_KEY")
if api_key is None:
    raise RuntimeError("LLM_API_KEY environment variable is not set.")

model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")

llm = LLM(
    usage_id="agent",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
)

cwd = os.getcwd()
tools = [
    Tool(name=FileEditorTool.name),
    Tool(name=BrowserToolSet.name),
]

agent = Agent(llm=llm, tools=tools)

llm_messages = []


def _safe_preview(text: str, limit: int = 200) -> str:
    truncated = text[:limit]
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return truncated.encode(encoding, errors="replace").decode(encoding)


def conversation_callback(event: Event) -> None:
    if isinstance(event, LLMConvertibleEvent):
        llm_messages.append(event.to_llm_message())


conversation = Conversation(
    agent=agent,
    callbacks=[conversation_callback],
    workspace=cwd,
    visualizer=None,
)

try:
    conversation.send_message(
        "Open https://openhands.dev/blog in the browser and summarize the key points "
        "from the latest post."
    )
    conversation.run()
finally:
    conversation.close()

print("=" * 100)
print("Conversation finished. Got the following LLM messages:")
for i, message in enumerate(llm_messages):
    preview = _safe_preview(str(message))
    print(f"Message {i}: {preview}")

cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
