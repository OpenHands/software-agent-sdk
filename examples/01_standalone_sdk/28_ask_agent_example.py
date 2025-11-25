"""
Example demonstrating the ask_agent functionality for getting sidebar replies
from the agent for a running conversation.

This example shows how to use ask_agent() to get quick responses from the agent
about the current conversation state without interrupting the main execution flow.
"""

import os
import threading
import time
from datetime import datetime

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
)
from openhands.sdk.conversation import ConversationVisualizerBase
from openhands.sdk.event import Event
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


# Configure LLM
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")
llm = LLM(
    usage_id="agent",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
)

# Tools
cwd = os.getcwd()
tools = [
    Tool(name=TerminalTool.name),
    Tool(name=FileEditorTool.name),
    Tool(name=TaskTrackerTool.name),
]


class MinimalVisualizer(ConversationVisualizerBase):
    """A minimal visualizer that print the raw events as they occur."""

    count = 0

    def on_event(self, event: Event) -> None:
        """Handle events for minimal progress visualization."""
        print(f"\n\n[EVENT {self.count}] {type(event).__name__}")
        self.count += 1


# Agent
agent = Agent(llm=llm, tools=tools)
conversation = Conversation(
    agent=agent, workspace=cwd, visualizer=MinimalVisualizer, max_iteration_per_run=5
)


def timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


print("=== Ask Agent Example ===")
print("This example demonstrates asking questions during conversation execution")

# Step 1: Build conversation context
print(f"\n[{timestamp()}] Building conversation context...")
conversation.send_message("Explore the current directory and describe the architecture")

# Step 2: Start conversation in background thread
print(f"[{timestamp()}] Starting conversation in background thread...")
thread = threading.Thread(target=conversation.run)
thread.start()

# Give the agent time to start processing
time.sleep(2)

# Step 3: Use ask_agent while conversation is running
print(f"\n[{timestamp()}] Using ask_agent while conversation is processing...")

# Ask context-aware questions
questions_and_responses = []

question_1 = "Summarize the activity so far in 1 sentence."
print(f"\n[{timestamp()}] Asking: {question_1}")
response1 = conversation.ask_agent(question_1)
questions_and_responses.append((question_1, response1))
print(f"Response: {response1}")

time.sleep(1)

question_2 = "How's the progress?"
print(f"\n[{timestamp()}] Asking: {question_2}")
response2 = conversation.ask_agent(question_2)
questions_and_responses.append((question_2, response2))
print(f"Response: {response2}")

time.sleep(1)

question_3 = "Have you finished running?"
print(f"\n[{timestamp()}] {question_3}")
response3 = conversation.ask_agent(question_3)
questions_and_responses.append((question_3, response3))
print(f"Response: {response3}")

# Step 4: Wait for conversation to complete
print(f"\n[{timestamp()}] Waiting for conversation to complete...")
thread.join()

# Step 5: Verify conversation state wasn't affected
final_event_count = len(conversation.state.events)
# Step 6: Ask a final question after conversation completion
print(f"\n[{timestamp()}] Asking final question after completion...")
final_response = conversation.ask_agent(
    "Can you summarize what you accomplished in this conversation?"
)
print(f"Final response: {final_response}")

# Step 7: Summary
print("\n" + "=" * 60)
print("SUMMARY OF ASK_AGENT DEMONSTRATION")
print("=" * 60)

print("\nQuestions and Responses:")
for i, (question, response) in enumerate(questions_and_responses, 1):
    print(f"\n{i}. Q: {question}")
    print(f"   A: {response[:100]}{'...' if len(response) > 100 else ''}")

final_truncated = final_response[:100] + ("..." if len(final_response) > 100 else "")
print(f"\nFinal Question Response: {final_truncated}")

# Report cost
cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost:.4f}")
