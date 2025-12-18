#!/usr/bin/env python3
"""Demo script showing Weave observability integration with OpenHands SDK.

This script demonstrates how Weave provides **automatic LLM tracing** for the
OpenHands SDK. The key insight is that Weave automatically patches LiteLLM
when initialized, so all LLM calls are traced without any manual decoration!

## Key Features Demonstrated

1. **Automatic LLM Tracing**: Just call `init_weave()` and all LiteLLM calls
   are automatically traced - no `@weave.op` decorators needed for LLM calls!

2. **Custom Function Tracing**: Use `@weave_op` for custom agent logic you
   want to trace (tool execution, agent steps, etc.)

3. **Conversation Grouping**: Use `weave_attributes()` to group related
   operations under a conversation or session.

## How It Works

The SDK uses LiteLLM for all LLM calls. When you call `init_weave()`:
1. Weave's `implicit_patch()` automatically patches LiteLLM
2. All `litellm.completion()` and `litellm.acompletion()` calls are traced
3. You see full traces in the Weave UI without any code changes!

## Prerequisites

- Set WANDB_API_KEY environment variable (valid W&B API key)
- Set WEAVE_PROJECT environment variable (e.g., "your-team/openhands-demo")
- Optionally set OPENAI_API_KEY for actual LLM calls

## Usage

    export WANDB_API_KEY="your-api-key"
    export WEAVE_PROJECT="your-team/openhands-demo"
    python examples/weave_observability_demo.py

Note:
    If WANDB_API_KEY is not set or invalid, the demo will still run
    but without Weave tracing. This allows testing the functionality
    without requiring valid credentials.
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
    weave_attributes,
    weave_thread,
    start_weave_span,
    end_weave_span,
    observe_weave,
    get_weave_op,
)


# Example 1: Using the @weave_op decorator
@weave_op(name="process_message")
def process_message(message: str) -> dict:
    """Process a user message and return a response."""
    # Simulate some processing
    word_count = len(message.split())
    return {
        "original": message,
        "word_count": word_count,
        "processed": True,
    }


# Example 2: Using observe_weave for compatibility with Laminar
@observe_weave(name="analyze_sentiment")
def analyze_sentiment(text: str) -> str:
    """Analyze the sentiment of text."""
    # Simple mock sentiment analysis
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


# Example 3: Nested operations with thread grouping
@weave_op(name="agent_step")
def agent_step(step_num: int, user_input: str) -> dict:
    """Simulate an agent step with nested operations."""
    # Process the message
    processed = process_message(user_input)

    # Analyze sentiment
    sentiment = analyze_sentiment(user_input)

    return {
        "step": step_num,
        "processed": processed,
        "sentiment": sentiment,
    }


# Example 4: Manual span management
def manual_span_example():
    """Demonstrate manual span creation and management."""
    # Start a span
    start_weave_span("manual_operation", inputs={"task": "demo"})

    try:
        # Do some work
        result = {"status": "completed", "items_processed": 42}
        end_weave_span(output=result)
        return result
    except Exception as e:
        end_weave_span(error=e)
        raise


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

    # Initialize Weave
    print(f"\n📊 Initializing Weave for project: {project}")
    success = maybe_init_weave()

    if success:
        print("✅ Weave initialized successfully!")
        print(f"   View traces at: https://wandb.ai/{project}/weave")
        print("\n   🎉 KEY FEATURE: All LiteLLM calls are now AUTOMATICALLY traced!")
        print("   No need to decorate LLM calls - Weave patches LiteLLM for you.")
    else:
        print("⚠️  Weave not initialized (missing credentials or package)")
        print("   Running demo without tracing...")

    print("\n" + "-" * 60)
    print("Running demo operations...")
    print("-" * 60)

    # Demo 1: Simple decorated function
    print("\n1️⃣  Custom function tracing with @weave_op decorator:")
    print("   (Use this for custom agent logic you want to trace)")
    result = process_message("Hello, this is a test message for the agent!")
    print(f"   Result: {result}")

    # Demo 2: Sentiment analysis with observe_weave
    print("\n2️⃣  Laminar-compatible interface with @observe_weave:")
    print("   (Easy migration from Laminar to Weave)")
    sentiment = analyze_sentiment("This is a great and excellent demo!")
    print(f"   Sentiment: {sentiment}")

    # Demo 3: Conversation grouping with weave_attributes
    print("\n3️⃣  Conversation grouping with weave_attributes:")
    print("   (Group all operations under a conversation ID)")
    conversation_id = "demo-conversation-001"

    with weave_attributes(conversation_id=conversation_id, user_id="demo-user"):
        for i, msg in enumerate([
            "Hello, I need help with my code",
            "The function is not working correctly",
            "Great, that fixed it! Thank you!",
        ], 1):
            result = agent_step(i, msg)
            print(f"   Step {i}: sentiment={result['sentiment']}")

    # Demo 4: Manual span management
    print("\n4️⃣  Manual span management (for advanced use cases):")
    result = manual_span_example()
    print(f"   Result: {result}")

    # Demo 5: Show how to get weave.op for dynamic decoration
    print("\n5️⃣  Dynamic decoration with get_weave_op():")
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
        print("\n💡 Remember: LLM calls via LiteLLM are traced AUTOMATICALLY!")
        print("   Just use the SDK's LLM class normally - no decoration needed.")
    print("=" * 60)


if __name__ == "__main__":
    run_demo()
