import os
import threading
import time

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
)
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.execute_bash import BashTool
from openhands.tools.file_editor import FileEditorTool


# Configure LLM
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")
llm = LLM(
    usage_id="agent",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
)

# Tools
register_tool("BashTool", BashTool)
register_tool("FileEditorTool", FileEditorTool)
tools = [
    Tool(
        name="BashTool",
    ),
    Tool(name="FileEditorTool"),
]

# Agent
agent = Agent(llm=llm, tools=tools)
conversation = Conversation(agent, workspace=os.getcwd())


print("=" * 60)
print("Pause and Continue Example")
print("=" * 60)
print()

# Phase 1: Start a long-running task
print("Phase 1: Starting agent with a task...")
conversation.send_message(
    "Create a file called countdown.txt and write numbers from 100 down to 1, "
    "one number per line. After you finish, summarize what you did."
)

print(f"Initial status: {conversation.state.agent_status}")
print()

# Start the agent in a background thread
thread = threading.Thread(target=conversation.run)
thread.start()

# Let the agent work for a few seconds
print("Letting agent work for 2 seconds...")
time.sleep(2)

# Phase 2: Pause the agent
print()
print("Phase 2: Pausing the agent...")
conversation.pause()

# Wait for the thread to finish (it will stop when paused)
thread.join()

print(f"Agent status after pause: {conversation.state.agent_status}")
print()


# Phase 3: Send a new message while paused
print("Phase 3: Sending a new message while agent is paused...")
conversation.send_message(
    "Actually, stop working on countdown.txt. Instead, create a file called "
    "hello.txt with just the text 'Hello, World!' in it."
)
print()

# Phase 4: Resume the agent with .run()
print("Phase 4: Resuming agent with .run()...")
print(f"Status before resume: {conversation.state.agent_status}")

# Resume execution
conversation.run()

print(f"Final status: {conversation.state.agent_status}")

# Report cost
cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
