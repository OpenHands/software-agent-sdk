"""
Example demonstrating the ask_agent functionality for getting quick responses
from the agent without affecting conversation state.

This example showcases the ask_agent method, which allows you to:
1. Ask simple questions to the agent during conversation execution
2. Get context-aware responses based on the current conversation
3. Maintain thread safety while the conversation is running
4. Avoid affecting the conversation state or event flow

Demonstration Flow:
1. Create a conversation and send initial messages to build context
2. Start the conversation running in a background thread
3. While the conversation is processing, use ask_agent to:
   - Ask about the conversation context
   - Get clarifications without interrupting the main flow
   - Demonstrate thread-safe concurrent access
4. Show that conversation state remains unaffected

Key Benefits:
- Thread-safe: Can be called while conversation.run() is executing
- Context-aware: Includes current conversation context in responses
- State-preserving: Doesn't affect conversation events or execution flow
- Simple interface: Just pass a string question, get a string response
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

    def on_event(self, event: Event) -> None:
        """Handle events for minimal progress visualization."""
        print(f"\n\n[EVENT] {type(event).__name__}")


# Agent
agent = Agent(llm=llm, tools=tools)
conversation = Conversation(agent=agent, workspace=cwd, visualizer=MinimalVisualizer)


def timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


print("=== Ask Agent Example ===")
print("This example demonstrates asking questions during conversation execution")

# Step 1: Build conversation context
print(f"\n[{timestamp()}] Building conversation context...")
conversation.send_message("Explore this project and describe the current architecture.")

# Step 2: Start conversation in background thread
print(f"[{timestamp()}] Starting conversation in background thread...")
thread = threading.Thread(target=conversation.run)
thread.start()

# Give the agent time to start processing
time.sleep(2)

# Step 3: Use ask_agent while conversation is running
print(f"\n[{timestamp()}] Using ask_agent while conversation is processing...")

# Store initial event count to verify state isn't affected
initial_event_count = len(conversation.state.events)
print(f"Initial event count: {initial_event_count}")

# Ask context-aware questions
questions_and_responses = []

print(f"\n[{timestamp()}] Asking: What was my original request?")
response1 = conversation.ask_agent("What was my original request?")
questions_and_responses.append(("What was my original request?", response1))
print(f"Response: {response1}")

time.sleep(1)

print(f"\n[{timestamp()}] Asking: What specific requirements did I mention?")
response2 = conversation.ask_agent("What specific requirements did I mention?")
questions_and_responses.append(("What specific requirements did I mention?", response2))
print(f"Response: {response2}")

time.sleep(1)

print(f"\n[{timestamp()}] Asking: What tools should I expect you to use?")
response3 = conversation.ask_agent("What tools should I expect you to use?")
questions_and_responses.append(("What tools should I expect you to use?", response3))
print(f"Response: {response3}")

# Step 4: Wait for conversation to complete
print(f"\n[{timestamp()}] Waiting for conversation to complete...")
thread.join()

# Step 5: Verify conversation state wasn't affected
final_event_count = len(conversation.state.events)
print(f"\n[{timestamp()}] Conversation completed!")
print(f"Final event count: {final_event_count}")
print(f"Events added by ask_agent: {final_event_count - initial_event_count}")

if final_event_count == initial_event_count:
    print("✅ SUCCESS: ask_agent didn't affect conversation state!")
else:
    print("⚠️  WARNING: Conversation state may have been affected")

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
print(f"Total questions asked: {len(questions_and_responses) + 1}")
print(f"Conversation events before ask_agent: {initial_event_count}")
print(f"Conversation events after ask_agent: {final_event_count}")
state_status = "✅ PASSED" if final_event_count == initial_event_count else "❌ FAILED"
print(f"State preservation: {state_status}")

print("\nQuestions and Responses:")
for i, (question, response) in enumerate(questions_and_responses, 1):
    print(f"\n{i}. Q: {question}")
    print(f"   A: {response[:100]}{'...' if len(response) > 100 else ''}")

final_truncated = final_response[:100] + ("..." if len(final_response) > 100 else "")
print(f"\nFinal Question Response: {final_truncated}")

# Report cost
cost = llm.metrics.accumulated_cost
print(f"\nEXAMPLE_COST: {cost}")

print("\n" + "=" * 60)
print("Key Takeaways:")
print("- ask_agent works concurrently with conversation.run()")
print("- Responses are context-aware based on conversation history")
print("- Conversation state and events remain unaffected")
print("- Simple string-in, string-out interface for quick questions")
print("=" * 60)
