"""Iterative Refinement with Critic Model Example.

This is EXPERIMENTAL.

This example demonstrates how to use a critic model to shepherd an agent through
complex, multi-step tasks. The critic evaluates the agent's progress and provides
feedback that can trigger follow-up prompts when the agent hasn't completed the
task successfully.

Key concepts demonstrated:
1. Setting up a critic to evaluate agent actions in real-time
2. Capturing critic results via callbacks
3. Using low critic scores to trigger corrective follow-up prompts
4. Iterating until the task is completed successfully or max iterations reached

For All-Hands LLM proxy (llm-proxy.*.all-hands.dev), the critic is auto-configured
using the same base_url with /vllm suffix and "critic" as the model name.
"""

import os
import re
import tempfile
from pathlib import Path

from openhands.sdk import LLM, Agent, Conversation, Event, Tool
from openhands.sdk.critic import APIBasedCritic, CriticResult
from openhands.sdk.critic.base import CriticBase
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


# Configuration
SUCCESS_THRESHOLD = float(os.getenv("CRITIC_SUCCESS_THRESHOLD", "0.6"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise ValueError(
        f"Missing required environment variable: {name}. "
        f"Set {name} before running this example."
    )


def get_default_critic(llm: LLM) -> CriticBase | None:
    """Auto-configure critic for All-Hands LLM proxy.

    When the LLM base_url matches `llm-proxy.*.all-hands.dev`, returns an
    APIBasedCritic configured with:
    - server_url: {base_url}/vllm
    - api_key: same as LLM
    - model_name: "critic"

    Returns None if base_url doesn't match or api_key is not set.
    """
    base_url = llm.base_url
    api_key = llm.api_key
    if base_url is None or api_key is None:
        return None

    # Match: llm-proxy.{env}.all-hands.dev (e.g., staging, prod, eval)
    pattern = r"^https?://llm-proxy\.[^./]+\.all-hands\.dev"
    if not re.match(pattern, base_url):
        return None

    return APIBasedCritic(
        server_url=f"{base_url.rstrip('/')}/vllm",
        api_key=api_key,
        model_name="critic",
    )


class CriticResultCollector:
    """Collects critic results from conversation events via callback."""

    def __init__(self) -> None:
        self.results: list[CriticResult] = []
        self.latest_result: CriticResult | None = None

    def callback(self, event: Event) -> None:
        """Callback to capture critic results from events."""
        if isinstance(event, (ActionEvent, MessageEvent)):
            if event.critic_result is not None:
                self.results.append(event.critic_result)
                self.latest_result = event.critic_result
                print(f"\n📊 Critic Score: {event.critic_result.score:.3f}")
                if event.critic_result.message:
                    print(f"   Details: {event.critic_result.message[:100]}...")

    def reset(self) -> None:
        """Reset collected results for a new iteration."""
        self.results = []
        self.latest_result = None


def get_initial_task_prompt() -> str:
    """Generate a complex, multi-step task prompt.

    This task is designed to be challenging enough that the agent may
    not complete it perfectly on the first try, demonstrating the
    value of the critic in guiding improvements.
    """
    return (
        "Please help me create a complete Python project structure "
        "with the following:\n\n"
        "1. Create a directory called 'calculator_app'\n\n"
        "2. Inside calculator_app, create these files:\n"
        "   - __init__.py (empty)\n"
        "   - calculator.py - A Calculator class with add, subtract, "
        "multiply, divide methods\n"
        "   - utils.py - Helper functions for input validation\n"
        "   - main.py - CLI entry point that takes two numbers and an operation\n\n"
        "3. Create a tests/ directory with:\n"
        "   - __init__.py (empty)\n"
        "   - test_calculator.py - Unit tests for the Calculator class "
        "(at least 4 tests)\n\n"
        "4. Create a README.md with:\n"
        "   - Project title and description\n"
        "   - Installation instructions\n"
        "   - Usage examples\n"
        "   - Test running instructions\n\n"
        "5. Run the tests to verify everything works correctly\n\n"
        "Please complete ALL of these steps. After you're done, verify each "
        "file exists and the tests pass."
    )


def get_followup_prompt(critic_result: CriticResult, iteration: int) -> str:
    """Generate a follow-up prompt based on critic feedback.

    Uses the critic's evaluation to craft specific guidance for improvement.
    """
    score_percent = critic_result.score * 100

    # Extract potential issues from critic metadata if available
    issues = []
    if critic_result.metadata and "categorized_features" in critic_result.metadata:
        categorized = critic_result.metadata["categorized_features"]
        if "agent_behavioral_issues" in categorized:
            issues = [
                f.get("display_name", f.get("name", "Unknown issue"))
                for f in categorized["agent_behavioral_issues"]
            ]

    issues_text = ""
    if issues:
        issues_text = f"\nPotential issues identified: {', '.join(issues)}"

    return (
        f"The task appears incomplete (iteration {iteration}, "
        f"success likelihood: {score_percent:.1f}%).\n"
        f"{issues_text}\n\n"
        "Please review what you've done so far and complete any remaining steps:\n\n"
        "1. Check if ALL files were created in calculator_app/:\n"
        "   - __init__.py, calculator.py, utils.py, main.py\n\n"
        "2. Check if tests/ directory exists with:\n"
        "   - __init__.py, test_calculator.py\n\n"
        "3. Check if README.md exists with all required sections\n\n"
        "4. Make sure the tests actually pass when run\n\n"
        "List what files exist and what's missing, then complete the remaining tasks.\n"
        "Use the finish tool only when everything is truly complete."
    )


llm_api_key = get_required_env("LLM_API_KEY")
llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-haiku-4-5"),
    api_key=llm_api_key,
    base_url=os.getenv("LLM_BASE_URL", None),
)

# Setup critic
critic = get_default_critic(llm)
if critic is None:
    print("⚠️  No All-Hands LLM proxy detected, trying explicit env vars...")
    critic = APIBasedCritic(
        server_url=get_required_env("CRITIC_SERVER_URL"),
        api_key=get_required_env("CRITIC_API_KEY"),
        model_name=get_required_env("CRITIC_MODEL_NAME"),
    )

# Create agent with critic
agent = Agent(
    llm=llm,
    tools=[
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
        Tool(name=TaskTrackerTool.name),
    ],
    critic=critic,
)

# Create workspace and collector
workspace = Path(tempfile.mkdtemp(prefix="critic_demo_"))
print(f"📁 Created workspace: {workspace}")
collector = CriticResultCollector()

# Create conversation with callback
conversation = Conversation(
    agent=agent,
    workspace=str(workspace),
    callbacks=[collector.callback],
)

print("\n" + "=" * 70)
print("🚀 Starting Iterative Refinement with Critic Model")
print("=" * 70)
print(f"Success threshold: {SUCCESS_THRESHOLD:.0%}")
print(f"Max iterations: {MAX_ITERATIONS}")

# Initial task
print("\n--- Iteration 1: Initial Task ---")
conversation.send_message(get_initial_task_prompt())
conversation.run()

iteration = 1
while iteration < MAX_ITERATIONS:
    # Check critic result
    if collector.latest_result is None:
        print("\n⚠️  No critic result available, assuming task incomplete")
        score = 0.0
    else:
        score = collector.latest_result.score

    print(f"\n📈 Iteration {iteration} final score: {score:.3f}")

    if score >= SUCCESS_THRESHOLD:
        print(f"✅ Success threshold ({SUCCESS_THRESHOLD:.0%}) met!")
        break

    # Prepare for next iteration
    iteration += 1
    collector.reset()

    print(f"\n--- Iteration {iteration}: Follow-up Refinement ---")
    print(f"Score {score:.3f} < threshold {SUCCESS_THRESHOLD:.3f}, sending feedback...")

    followup_prompt = get_followup_prompt(
        collector.latest_result or CriticResult(score=0.0, message=None),
        iteration,
    )
    conversation.send_message(followup_prompt)
    conversation.run()

# Final summary
print("\n" + "=" * 70)
print("📊 ITERATIVE REFINEMENT COMPLETE")
print("=" * 70)
print(f"Total iterations: {iteration}")

if collector.latest_result:
    final_score = collector.latest_result.score
    print(f"Final critic score: {final_score:.3f}")
    print(f"Success: {'✅ YES' if final_score >= SUCCESS_THRESHOLD else '❌ NO'}")
else:
    print("Final critic score: N/A (no critic results)")

# List created files
print("\nCreated files:")
for path in sorted(workspace.rglob("*")):
    if path.is_file():
        relative = path.relative_to(workspace)
        print(f"  - {relative}")

# Report cost
cost = llm.metrics.accumulated_cost
print(f"\nEXAMPLE_COST: {cost:.4f}")

# Cleanup workspace
os.rmdir(workspace)
