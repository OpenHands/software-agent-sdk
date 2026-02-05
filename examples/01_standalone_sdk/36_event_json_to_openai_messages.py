"""Load persisted events and convert them into LLM-ready messages."""

import json
import os
import uuid
from pathlib import Path

from pydantic import SecretStr


conversation_id = uuid.uuid4()
persistence_root = Path(".conversations")
log_dir = (
    persistence_root / "logs" / "event-json-to-openai-messages" / conversation_id.hex
)

os.environ.setdefault("LOG_JSON", "true")
os.environ.setdefault("LOG_TO_FILE", "true")
os.environ.setdefault("LOG_DIR", str(log_dir))
os.environ.setdefault("LOG_LEVEL", "INFO")

from openhands.sdk import (  # noqa: E402
    LLM,
    Agent,
    Conversation,
    Event,
    LLMConvertibleEvent,
    Tool,
)
from openhands.sdk.logger import get_logger, setup_logging  # noqa: E402
from openhands.tools.terminal import TerminalTool  # noqa: E402


setup_logging(log_to_file=True, log_dir=str(log_dir))
logger = get_logger(__name__)

api_key = os.getenv("LLM_API_KEY")
if not api_key:
    raise RuntimeError("LLM_API_KEY environment variable is not set.")

llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=SecretStr(api_key),
)

agent = Agent(
    llm=llm,
    tools=[Tool(name=TerminalTool.name)],
)

######
# Create a conversation that persists its events
######

conversation = Conversation(
    agent=agent,
    workspace=os.getcwd(),
    persistence_dir=str(persistence_root),
    conversation_id=conversation_id,
)

conversation.send_message(
    "Use the terminal tool to run `pwd` and write the output to tool_output.txt. "
    "Reply with a short confirmation once done."
)
conversation.run()

conversation.send_message(
    "Without using any tools, summarize in one sentence what you did."
)
conversation.run()

assert conversation.state.persistence_dir is not None
persistence_dir = Path(conversation.state.persistence_dir)
event_dir = persistence_dir / "events"

event_paths = sorted(event_dir.glob("event-*.json"))

if not event_paths:
    raise RuntimeError("No event files found. Was persistence enabled?")

######
# Read from serialized events
######


events = [Event.model_validate_json(path.read_text()) for path in event_paths]

convertible_events = [
    event for event in events if isinstance(event, LLMConvertibleEvent)
]
llm_messages = LLMConvertibleEvent.events_to_messages(convertible_events)

if llm.uses_responses_api():
    logger.info("Formatting messages for the OpenAI Responses API.")
    instructions, input_items = llm.format_messages_for_responses(llm_messages)
    logger.info("Responses instructions:\n%s", instructions)
    logger.info("Responses input:\n%s", json.dumps(input_items, indent=2))
else:
    logger.info("Formatting messages for the OpenAI Chat Completions API.")
    chat_messages = llm.format_messages_for_llm(llm_messages)
    logger.info("Chat Completions messages:\n%s", json.dumps(chat_messages, indent=2))

# Report cost
cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
