"""Example: Interrupting agent execution with Ctrl+C.

This example demonstrates how to use conversation.interrupt() to immediately
cancel an in-flight LLM call when the user presses Ctrl+C.

Unlike pause(), which waits for the current LLM call to complete,
interrupt() cancels the call immediately by:
- Cancelling the async task running the LLM call
- Closing the HTTP connection
- Raising LLMCancelledError

This is useful for:
- Long-running reasoning tasks that you want to stop immediately
- Expensive API calls you want to cancel to save costs
- Interactive applications where responsiveness is important

Usage:
    LLM_API_KEY=your_key python 43_interrupt_example.py

Press Ctrl+C at any time to interrupt the agent.
"""

import os
import signal
import sys
import threading
import time

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.terminal import TerminalTool


PROMPT = """
I need you to solve this complex logic puzzle step by step, showing your reasoning:

There are 5 houses in a row, each a different color (Red, Green, Blue, Yellow, White).
Each house is occupied by a person of different nationality.
Each person has a different pet, drink, and cigarette brand.

Clues:
1. The British person lives in the red house.
2. The Swedish person keeps dogs as pets.
3. The Danish person drinks tea.
4. The green house is on the left of the white house.
5. The green house's owner drinks coffee.
6. The person who smokes Pall Mall rears birds.
7. The owner of the yellow house smokes Dunhill.
8. The person living in the center house drinks milk.
9. The Norwegian lives in the first house.
10. The person who smokes Blend lives next to the one who keeps cats.
11. The person who keeps horses lives next to the one who smokes Dunhill.
12. The person who smokes Blue Master drinks beer.
13. The German smokes Prince.
14. The Norwegian lives next to the blue house.
15. The person who smokes Blend has a neighbor who drinks water.

Question: Who owns the fish?

Please solve this completely, showing your full reasoning process with all deductions.
After solving, create a file called 'puzzle_solution.txt' with your complete solution.
"""


def main():
    # Track timing
    start_time: float | None = None
    interrupt_time: float | None = None

    # Configure LLM - use gpt-5.2 for long reasoning tasks
    # Falls back to environment variable model if gpt-5.2 not available
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        print("Error: LLM_API_KEY environment variable is not set.")
        sys.exit(1)

    model = os.getenv("LLM_MODEL", "openai/gpt-5.2")
    base_url = os.getenv("LLM_BASE_URL")

    print("=" * 70)
    print("Interrupt Example - Press Ctrl+C to immediately stop the agent")
    print("=" * 70)
    print()

    llm = LLM(
        usage_id="reasoning-agent",
        model=model,
        base_url=base_url,
        api_key=api_key,
    )

    print(f"Using model: {model}")
    print()

    # Create agent with minimal tools
    agent = Agent(
        llm=llm,
        tools=[Tool(name=TerminalTool.name)],
    )

    conversation = Conversation(agent=agent, workspace=os.getcwd())

    # Set up Ctrl+C handler
    def signal_handler(_signum, _frame):
        nonlocal interrupt_time
        interrupt_time = time.time()
        print("\n")
        print("=" * 70)
        print("Ctrl+C detected! Interrupting agent...")
        print("=" * 70)

        # Call interrupt() - this immediately cancels any in-flight LLM call
        conversation.interrupt()

    signal.signal(signal.SIGINT, signal_handler)

    # Send a task that requires long reasoning
    print("Sending a complex reasoning task to the agent...")
    print("(This task is designed to take a while - press Ctrl+C to interrupt)")
    print()

    conversation.send_message(PROMPT)
    print(f"Agent status: {conversation.state.execution_status}")
    print()

    # Run in background thread so we can handle signals
    def run_agent():
        conversation.run()

    start_time = time.time()
    thread = threading.Thread(target=run_agent)
    thread.start()

    print("Agent is working... (press Ctrl+C to interrupt)")
    print()

    # Wait for thread to complete (either normally or via interrupt)
    thread.join()

    end_time = time.time()

    # Report timing
    print()
    print("=" * 70)
    print("Results")
    print("=" * 70)
    print()
    print(f"Final status: {conversation.state.execution_status}")
    print()

    if interrupt_time:
        interrupt_latency = end_time - interrupt_time
        total_time = end_time - start_time
        print(f"Total time from start to stop: {total_time:.2f} seconds")
        print(f"Time from Ctrl+C to full stop: {interrupt_latency:.3f} seconds")
        print()
        print("The agent was interrupted immediately!")
        print("Without interrupt(), you would have had to wait for the full")
        print("LLM response to complete before the agent would stop.")
    else:
        total_time = end_time - start_time
        print(f"Total time: {total_time:.2f} seconds")
        print("Agent completed normally (was not interrupted)")

    print()

    # Report cost
    cost = llm.metrics.accumulated_cost
    print(f"Accumulated cost: ${cost:.6f}")
    print(f"EXAMPLE_COST: {cost}")


if __name__ == "__main__":
    main()
