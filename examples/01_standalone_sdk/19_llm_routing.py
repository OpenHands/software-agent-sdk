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
from openhands.sdk.llm.router import MultimodalRouter
from openhands.tools.preset.default import get_default_tools


logger = get_logger(__name__)

# Configure LLM
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")

primary_llm = LLM(
    usage_id="agent-primary",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
)
secondary_llm = LLM(
    usage_id="agent-secondary",
    model="openhands/devstral-small-2507",
    base_url=base_url,
    api_key=SecretStr(api_key),
)
multimodal_router = MultimodalRouter(
    usage_id="multimodal-router",
    llms_for_routing={"primary": primary_llm, "secondary": secondary_llm},
)

# Tools
tools = get_default_tools()  # Use our default openhands experience

# Agent
agent = Agent(llm=multimodal_router, tools=tools)

llm_messages = []  # collect raw LLM messages


def conversation_callback(event: Event):
    if isinstance(event, LLMConvertibleEvent):
        llm_messages.append(event.to_llm_message())


conversation = Conversation(
    agent=agent, callbacks=[conversation_callback], workspace=os.getcwd()
)

conversation.send_message(
    message=Message(
        role="user",
        content=[TextContent(text=("Hi there, who trained you?"))],
    )
)
conversation.run()

conversation.send_message(
    message=Message(
        role="user",
        content=[
            ImageContent(
                image_urls=["http://images.cocodataset.org/val2017/000000039769.jpg"]
            ),
            TextContent(text=("What do you see in the image above?")),
        ],
    )
)
conversation.run()

conversation.send_message(
    message=Message(
        role="user",
        content=[TextContent(text=("Who trained you as an LLM?"))],
    )
)
conversation.run()

print("=" * 100)
print("Conversation finished. Got the following LLM messages:")
for i, message in enumerate(llm_messages):
    print(f"Message {i}: {str(message)[:200]}")

# Report cost
cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"EXAMPLE_COST: {cost}")
