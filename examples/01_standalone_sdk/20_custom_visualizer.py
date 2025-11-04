"""Example demonstrating custom visualizer usage.

This example shows how to pass a custom ConversationVisualizer directly
to the Conversation, making it easy to customize the visualization without
the need for callbacks.
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.conversation.visualizer import ConversationVisualizer


def main():
    # Get API key from environment
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        raise ValueError("LLM_API_KEY environment variable is not set")

    # Create LLM and Agent
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr(api_key))
    agent = Agent(llm=llm, tools=[])

    # Create a custom visualizer with specific highlighting
    custom_visualizer = ConversationVisualizer(
        highlight_regex={
            r"^Reasoning:": "bold cyan",
            r"^Thought:": "bold green",
            r"^Action:": "bold yellow",
        },
        skip_user_messages=False,  # Show user messages
    )

    # Pass the custom visualizer directly to the conversation
    # This is more intuitive than visualize=False + callbacks=[...]
    conversation = Conversation(
        agent=agent,
        workspace="./workspace",
        visualize=custom_visualizer,  # Direct and clear!
    )

    # Send a message and run
    conversation.send_message("What is 2 + 2?")
    conversation.run()

    print("\n✅ Example completed!")
    print("The conversation used a custom visualizer with custom highlighting.")


if __name__ == "__main__":
    main()
