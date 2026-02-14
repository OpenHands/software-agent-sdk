import os

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Event,
    LLMConvertibleEvent,
    get_logger,
)
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.execute_bash import BashTool


logger = get_logger(__name__)

# Configure LLM
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "litellm_proxy/openai/gpt-5-nano")
base_url = os.getenv("LLM_BASE_URL", "https://llm-proxy.eval.all-hands.dev")
llm = LLM(
    service_id="agent",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
    log_completions=True,
    log_completions_folder=os.path.join(os.getcwd(), ".pr", "completions"),
)

# Tools
cwd = os.getcwd()
register_tool("BashTool", BashTool)
tools = [
    Tool(
        name="BashTool",
        params={"no_change_timeout_seconds": 3},
    )
]

# Agent
agent = Agent(llm=llm, tools=tools)

llm_messages = []  # collect raw LLM messages


def conversation_callback(event: Event):
    if isinstance(event, LLMConvertibleEvent):
        llm_messages.append(event.to_llm_message())


conversation = Conversation(
    agent=agent, callbacks=[conversation_callback], workspace=cwd
)

conversation.send_message(
    "You can use the BashTool to run commands in the workspace. Do the following, "
    "in order, using tool calls:\n"
    "1) Run `python3` in interactive mode, print the current time, then exit.\n"
    "2) Create a file named `.pr/reasoning_example.txt` with the contents `alpha`\n"
    "3) Edit the file so the first line becomes `alpha-edited`\n"
    "4) Append a final line `omega`\n"
    "5) Print the file to confirm\n"
    "6) Delete the file\n"
    "7) Confirm deletion by listing `.pr/`\n"
    "Be explicit and verify each step before moving on."
)
conversation.run()

print("=" * 100)
print("Conversation finished. Got the following LLM messages:")
for i, message in enumerate(llm_messages):
    print(f"Message {i}: {str(message)[:200]}")
