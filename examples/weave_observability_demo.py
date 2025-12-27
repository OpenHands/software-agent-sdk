#!/usr/bin/env python3
"""Demo script showing Weave observability integration with OpenHands SDK.

This script demonstrates how Weave provides **automatic LLM tracing** for the
OpenHands SDK. The key insight is that Weave automatically patches LiteLLM
when initialized, so all LLM calls are traced without any manual decoration!

## Key Features Demonstrated

1. **Automatic LLM Tracing**: Just set environment variables and all LiteLLM calls
   are automatically traced - no `@weave.op` decorators needed for LLM calls!

2. **Custom Function Tracing**: Use `@weave_op` for custom agent logic you
   want to trace (tool execution, agent steps, etc.)

3. **Conversation Threading**: The SDK automatically wraps conversation runs
   in `weave.thread()` to group all operations under the conversation ID.
   This enables conversation-level tracing in the Weave UI!

## How It Works

The SDK uses LiteLLM for all LLM calls. When Weave is initialized:
1. Weave's autopatching automatically patches LiteLLM
2. All `litellm.completion()` and `litellm.acompletion()` calls are traced
3. LocalConversation.run() wraps the event loop in `weave.thread(conversation_id)`
4. You see full conversation traces in the Weave UI without any code changes!

## Prerequisites

- Install with Weave support: `pip install openhands-sdk[weave]`
- Set WANDB_API_KEY environment variable
- Set WEAVE_PROJECT environment variable (e.g., "your-team/openhands-demo")

## Usage

    export WANDB_API_KEY="your-api-key"
    export WEAVE_PROJECT="your-team/openhands-demo"
    python examples/weave_observability_demo.py

Note:
    If WANDB_API_KEY is not set or the weave package is not installed,
    the demo will still run but without Weave tracing.
"""

import os
import sys

# Add the SDK to the path for development
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "openhands-sdk"))

from openhands.sdk.observability import (
    init_weave,
    is_weave_initialized,
    maybe_init_weave,
    weave_op,
    get_weave_op,
)


# Example 1: Using the @weave_op decorator for custom function tracing
@weave_op(name="process_message")
def process_message(message: str) -> dict:
    """Process a user message and return a response.

    When Weave is initialized, this function will appear in traces
    with the name "process_message".
    """
    word_count = len(message.split())
    return {
        "original": message,
        "word_count": word_count,
        "processed": True,
    }


# Example 2: Another traced function
@weave_op(name="analyze_sentiment")
def analyze_sentiment(text: str) -> str:
    """Analyze the sentiment of text.

    This demonstrates how @weave_op works as a no-op when Weave
    is not initialized - your code runs normally either way.
    """
    positive_words = {"good", "great", "excellent", "happy", "love"}
    negative_words = {"bad", "terrible", "sad", "hate", "awful"}

    words = set(text.lower().split())
    pos_count = len(words & positive_words)
    neg_count = len(words & negative_words)

    if pos_count > neg_count:
        return "positive"
    elif neg_count > pos_count:
        return "negative"
    return "neutral"


# Example 3: Nested traced functions
@weave_op(name="agent_step")
def agent_step(step_num: int, user_input: str) -> dict:
    """Simulate an agent step with nested traced operations.

    When this function calls process_message and analyze_sentiment,
    they appear as child spans in the Weave trace.
    """
    processed = process_message(user_input)
    sentiment = analyze_sentiment(user_input)

    return {
        "step": step_num,
        "processed": processed,
        "sentiment": sentiment,
    }


def run_demo():
    """Run the Weave observability demo."""
    print("=" * 60)
    print("Weave Observability Demo for OpenHands SDK")
    print("=" * 60)

    # Check environment
    api_key = os.environ.get("WANDB_API_KEY")
    project = os.environ.get("WEAVE_PROJECT")

    if not api_key:
        print("\n⚠️  WANDB_API_KEY not set. Weave tracing will be disabled.")
        print("   Set it with: export WANDB_API_KEY='your-api-key'")

    if not project:
        print("\n⚠️  WEAVE_PROJECT not set. Using default project name.")
        project = "openhands-sdk-demo"
        os.environ["WEAVE_PROJECT"] = project

    # Initialize Weave (or use maybe_init_weave() for conditional init)
    print(f"\n📊 Initializing Weave for project: {project}")
    success = maybe_init_weave()

    if success:
        print("✅ Weave initialized successfully!")
        print(f"   View traces at: https://wandb.ai/{project}/weave")
        print("\n   🎉 KEY FEATURES:")
        print("   • All LiteLLM calls are AUTOMATICALLY traced (no decoration needed)")
        print("   • Conversation.run() automatically groups operations by conversation ID")
        print("   • Use @weave_op for custom functions you want to trace")
    else:
        print("⚠️  Weave not initialized (missing credentials or package)")
        print("   Running demo without tracing...")
        print("   Install with: pip install openhands-sdk[weave]")

    print("\n" + "-" * 60)
    print("Running demo operations...")
    print("-" * 60)

    # Demo 1: Simple decorated function
    print("\n1️⃣  Custom function tracing with @weave_op decorator:")
    print("   (Use this for custom agent logic you want to trace)")
    result = process_message("Hello, this is a test message for the agent!")
    print(f"   Result: {result}")

    # Demo 2: Nested function calls
    print("\n2️⃣  Nested traced function calls:")
    print("   (Child functions appear as child spans in the trace)")
    result = agent_step(1, "This is a great example of tracing!")
    print(f"   Result: {result}")

    # Demo 3: Multiple steps to show trace structure
    print("\n3️⃣  Multiple agent steps:")
    for i, msg in enumerate([
        "Hello, I need help with my code",
        "The function is not working correctly",
        "Great, that fixed it! Thank you!",
    ], 1):
        result = agent_step(i, msg)
        print(f"   Step {i}: sentiment={result['sentiment']}")

    # Demo 4: Dynamic decoration with get_weave_op()
    print("\n4️⃣  Dynamic decoration with get_weave_op():")
    print("   (Useful for conditionally applying tracing)")
    op = get_weave_op()

    @op
    def dynamically_traced_function(x: int) -> int:
        return x * 2

    result = dynamically_traced_function(21)
    print(f"   Result: {result}")

    print("\n" + "=" * 60)
    print("Demo completed!")

    if is_weave_initialized():
        print(f"\n🔗 View your traces at: https://wandb.ai/{project}/weave")
        print("\n💡 Key Integration Points:")
        print("   • LLM calls via LiteLLM are traced AUTOMATICALLY")
        print("   • Conversation.run() groups all operations by conversation ID")
        print("   • Use @weave_op for custom agent logic you want to trace")
        print("\n📝 Minimal setup (zero code changes):")
        print("   1. pip install openhands-sdk[weave]")
        print("   2. export WANDB_API_KEY='your-key'")
        print("   3. export WEAVE_PROJECT='team/project'")
        print("   That's it! All LLM calls are now traced.")
    else:
        print("\n📝 To enable tracing:")
        print("   1. pip install openhands-sdk[weave]")
        print("   2. export WANDB_API_KEY='your-api-key'")
        print("   3. export WEAVE_PROJECT='your-team/your-project'")
        print("   4. Run this demo again")

    print("=" * 60)


if __name__ == "__main__":
    run_demo()
