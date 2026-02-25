"""
Interrupt Example - Demonstrating immediate termination of agent operations.

This example shows how to use the interrupt() method to immediately terminate
an in-progress LLM completion or tool execution, similar to Ctrl+C behavior.

Key differences from pause():
- pause(): Waits for the current operation to complete, then stops
- interrupt(): Immediately terminates in-progress LLM calls

IMPORTANT: For immediate interrupt of LLM calls, streaming must be enabled.
With streaming, the SDK checks the interrupt flag between chunks and can stop
immediately. Without streaming, the SDK can only attempt to close HTTP
connections which may not always work depending on the provider.

Press Ctrl+C during execution to interrupt the agent.
"""

import os
import signal
import sys
import threading

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    InterruptEvent,
)
from openhands.sdk.tool import Tool
from openhands.tools.terminal import TerminalTool


def main():
    # Configure LLM - using gpt-4o for a long reasoning task
    api_key = os.getenv("LLM_API_KEY")
    assert api_key is not None, "LLM_API_KEY environment variable is not set."

    base_url = os.getenv("LLM_BASE_URL")

    llm = LLM(
        usage_id="agent",
        model="openai/gpt-4o",  # Using gpt-4o for complex reasoning
        base_url=base_url,
        api_key=SecretStr(api_key),
        stream=True,  # Enable streaming for immediate interrupt support
    )

    # Tools
    tools = [
        Tool(name=TerminalTool.name),
    ]

    # Agent
    agent = Agent(llm=llm, tools=tools)
    conversation = Conversation(agent, workspace=os.getcwd())

    # Set up Ctrl+C handler for interrupt
    interrupt_count = [0]  # Use list to allow modification in nested function

    def signal_handler(_signum, _frame):
        interrupt_count[0] += 1
        if interrupt_count[0] == 1:
            print("\n" + "=" * 60)
            print("INTERRUPT RECEIVED (Ctrl+C)")
            print("Interrupting agent... (press Ctrl+C again to force quit)")
            print("=" * 60 + "\n")
            conversation.interrupt("User pressed Ctrl+C")
        else:
            print("\nForce quitting...")
            sys.exit(1)

    signal.signal(signal.SIGINT, signal_handler)

    print("=" * 60)
    print("Interrupt Example")
    print("=" * 60)
    print()
    print("This example asks the agent to perform a complex reasoning task")
    print("that requires a long LLM completion.")
    print()
    print("Press Ctrl+C at any time to immediately interrupt the agent.")
    print("=" * 60)
    print()

    # Ask for a complex reasoning task that will take time
    # This prompt encourages deep, step-by-step reasoning
    conversation.send_message(
        """
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
    )

    print("Starting agent with complex reasoning task...")
    print("(The LLM will now work on the Einstein's Riddle puzzle)")
    print()

    # Run in a thread so we can handle interrupts
    run_error: list[Exception | None] = [None]
    finished = threading.Event()

    def run_agent():
        try:
            conversation.run()
        except Exception as e:
            run_error[0] = e
        finally:
            finished.set()

    thread = threading.Thread(target=run_agent, daemon=True)
    thread.start()

    # Wait for completion or interrupt
    finished.wait()
    thread.join(timeout=1)

    print()
    print("=" * 60)
    print("EXECUTION SUMMARY")
    print("=" * 60)
    print(f"Final status: {conversation.state.execution_status}")

    # Check if interrupted
    interrupt_events = [
        e for e in conversation.state.events if isinstance(e, InterruptEvent)
    ]
    if interrupt_events:
        print(f"Interrupted: Yes ({interrupt_events[0].detail})")
    else:
        print("Interrupted: No (completed normally)")

    if run_error[0]:
        print(f"Error: {run_error[0]}")

    # Report metrics
    cost = llm.metrics.accumulated_cost
    print(f"Total cost: ${cost:.4f}")
    print(f"EXAMPLE_COST: {cost}")


if __name__ == "__main__":
    main()
