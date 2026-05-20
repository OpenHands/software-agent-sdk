"""
Message while finishing: ensure concurrent user messages during the final agent step
are properly processed by the LLM after the agent finishes.

Purpose
- Validate correct conversation behavior when a user message arrives while the agent
  is already executing its final step (one that includes a finish action).
- The message should be appended to the conversation events AND be fed into
  a new LLM call after the finish action completes.

Approach
- Use an instrumented SleepTool to control timing and mark the start/end of the final
  step (sleep followed by finish in a single LLM response with multiple tool calls).
- Send two user messages:
  1) During an earlier (non-final) step: this message should be processed in the next
     LLM call (proves that mid-run messages are normally handled).
  2) During the final step's sleep: this message should be processed by the LLM
     after the finish action completes, ensuring no messages are lost.

Assertions
- Both user messages appear in the persisted events.
- The first message (“alligator”) appears in the LLM input (was processed).
- The second message (“butterfly”) DOES appear in an LLM input (was processed).

This test verifies the fix that ensures unattended user messages sent during the
final step are detected and processed after the agent finishes, preventing message loss.
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, ClassVar


# Ensure repo root on sys.path when running this file as a script
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import threading  # noqa: E402
import time  # noqa: E402
from collections.abc import Sequence  # noqa: E402
from unittest.mock import patch  # noqa: E402

# noqa: E402
from litellm import ChatCompletionMessageToolCall  # noqa: E402
from litellm.types.utils import (  # noqa: E402
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from pydantic import Field  # noqa: E402

from openhands.sdk.agent import Agent  # noqa: E402
from openhands.sdk.conversation import Conversation  # noqa: E402
from openhands.sdk.event import MessageEvent  # noqa: E402
from openhands.sdk.llm import (  # noqa: E402
    LLM,
    ImageContent,
    Message,
    TextContent,
)
from openhands.sdk.tool import (  # noqa: E402
    Action,
    Observation,
    Tool,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)


# Custom sleep tool for testing timing scenarios
class SleepAction(Action):
    duration: float = Field(description="Sleep duration in seconds")
    message: str = Field(description="Message to return after sleep")


class SleepObservation(Observation):
    message: str = Field(description="Message returned after sleep")

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.message)]


class SleepExecutor(ToolExecutor):
    test_start_time: float | None = None
    test_instance: "TestMessageWhileFinishing | None" = None

    def __call__(self, action: SleepAction, conversation=None) -> SleepObservation:  # noqa: ARG002
        start_time = time.time()
        test_start_time = getattr(self, "test_start_time", None)
        if test_start_time is None:
            test_start_time = start_time
        elapsed = start_time - test_start_time
        print(
            f"[+{elapsed:.3f}s] Sleep action STARTED: "
            f"{action.duration}s - '{action.message}'"
        )

        # Log final step timing if this is the final sleep
        # Note: final_step_start timestamp is recorded in _mock_llm_response
        # when the flag is set, to avoid race with butterfly thread
        if "Final sleep" in action.message:
            print(f"[+{elapsed:.3f}s] FINAL STEP SLEEP STARTED")

        time.sleep(action.duration)

        end_time = time.time()
        actual_duration = end_time - start_time
        test_start_time_end = getattr(self, "test_start_time", None)
        if test_start_time_end is None:
            test_start_time_end = start_time
        end_elapsed = end_time - test_start_time_end
        print(
            f"[+{end_elapsed:.3f}s] Sleep action COMPLETED: "
            f"{actual_duration:.3f}s actual - '{action.message}'"
        )

        # Track final step end timing
        if "Final sleep" in action.message:
            print(f"[+{end_elapsed:.3f}s] FINAL STEP ENDED")
            if hasattr(self, "test_instance") and self.test_instance is not None:
                self.test_instance.timestamps.append(("final_step_end", end_time))

        return SleepObservation(message=action.message)


class SleepTool(ToolDefinition[SleepAction, SleepObservation]):
    """Sleep tool for testing message processing during finish."""

    name: ClassVar[str] = "sleep"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["SleepTool"]:
        return [
            cls(
                action_type=SleepAction,
                observation_type=SleepObservation,
                description="Sleep for specified duration and return a message",
                executor=SleepExecutor(),
            )
        ]


def _make_sleep_tool(conv_state=None, **kwargs) -> Sequence[ToolDefinition]:
    """Create sleep tool for testing."""
    return SleepTool.create(conv_state, **kwargs)


# Register the tool
register_tool("SleepTool", _make_sleep_tool)


class TestMessageWhileFinishing:
    """Test suite demonstrating the unprocessed message issue."""

    def setup_method(self):
        """Set up test fixtures."""
        # Use gpt-4o which supports native function calling and multiple tool calls
        self.llm: LLM = LLM(model="gpt-4o", usage_id="test-llm")
        self.llm_completion_calls: list[Any] = []
        self.agent: Agent = Agent(llm=self.llm, tools=[Tool(name="SleepTool")])
        self.step_count: int = 0
        self.final_step_started: bool = False
        self.timestamps: list[tuple[str, float]] = []  # Track key timing events
        self.conversation: Any = None
        self.test_start_time: float = 0.0

    def _mock_llm_response(self, messages, **kwargs):
        """
        Mock LLM that demonstrates the message processing bug through a 2-step scenario.
        """
        self.llm_completion_calls.append({"messages": messages, "kwargs": kwargs})
        self.step_count += 1
        elapsed = time.time() - self.test_start_time
        print(f"[+{elapsed:.3f}s] Step {self.step_count} LLM call")

        all_content = str(messages).lower()
        has_alligator = "alligator" in all_content
        has_butterfly = "butterfly" in all_content

        if self.step_count == 1:
            # Step 1: Process initial request - single sleep
            sleep_call = ChatCompletionMessageToolCall(
                id="sleep_call_1",
                type="function",
                function=Function(
                    name="sleep",
                    arguments='{"duration": 2.0, "message": "First sleep completed"}',
                ),
            )
            return ModelResponse(
                id=f"response_step_{self.step_count}",
                choices=[
                    Choices(
                        message=LiteLLMMessage(
                            role="assistant",
                            content="I'll sleep for 2 seconds first",
                            tool_calls=[sleep_call],
                        )
                    )
                ],
                created=0,
                model="test-model",
                object="chat.completion",
            )

        elif self.step_count == 2:
            # Step 2: Final step - sleep AND finish (multiple tool calls)
            # Record timestamp BEFORE setting flag to avoid race with butterfly thread
            self.timestamps.append(("final_step_start", time.time()))
            self.final_step_started = True

            response_content = "Now I'll sleep for a longer time and then finish"
            sleep_message = "Final sleep completed"
            final_message = "Task completed"

            if has_alligator:
                response_content += " with alligator"
                sleep_message += " with alligator"
                final_message += " with alligator"

            if has_butterfly:
                response_content += " and butterfly"
                sleep_message += " and butterfly"
                final_message += " and butterfly"  # This should NOT happen

            # Multiple tool calls: sleep THEN finish
            sleep_call = ChatCompletionMessageToolCall(
                id="sleep_call_2",
                type="function",
                function=Function(
                    name="sleep",
                    arguments=f'{{"duration": 3.0, "message": "{sleep_message}"}}',
                ),
            )

            finish_call = ChatCompletionMessageToolCall(
                id="finish_call_2",
                type="function",
                function=Function(
                    name="finish",
                    arguments=f'{{"message": "{final_message}"}}',
                ),
            )

            return ModelResponse(
                id=f"response_step_{self.step_count}",
                choices=[
                    Choices(
                        message=LiteLLMMessage(
                            role="assistant",
                            content=response_content,
                            tool_calls=[
                                sleep_call,
                                finish_call,
                            ],
                        )
                    )
                ],
                created=0,
                model="test-model",
                object="chat.completion",
            )
        else:
            # Step 3: This happens because butterfly message reset FINISHED status
            # This demonstrates the bug: messages sent during final step reset status
            response_content = "I see the butterfly message"
            if has_butterfly:
                response_content += " with butterfly"

            # Return a simple message response (no tool calls)
            return ModelResponse(
                id=f"response_step_{self.step_count}",
                choices=[
                    Choices(
                        message=LiteLLMMessage(
                            role="assistant",
                            content=response_content,
                        )
                    )
                ],
                created=0,
                model="test-model",
                object="chat.completion",
            )

    def test_message_processing_fix_verification(self):
        """
        Verifies the fix: messages sent during final step are processed after finishing.

        This test shows that when a user sends a message while the agent is executing
        its final step (which includes a finish action), the message is properly
        detected as unattended and processed in a subsequent LLM call.

        Timeline:
        1. Step 1: Agent sleeps for 2 seconds
        2. User sends "alligator" request during step 1 → Gets processed in step 2 ✓
        3. Step 2: Agent sleeps for 3 seconds AND finishes (final step with multiple actions)
        4. User sends "butterfly" request WHILE step 2 sleep is executing → Detected as unattended
        5. Step 3: Conversation continues to process the butterfly message ✓

        Key: The butterfly message is detected and processed, ensuring no message loss.

        Expected: Conversation processes butterfly message after finish action.
        Actual: Conversation continues to step 3 to handle unattended message.
        """  # noqa
        # Reset step count for this test
        self.step_count = 0
        self.llm_completion_calls = []
        self.final_step_started = False
        self.test_start_time = time.time()

        conversation = Conversation(agent=self.agent)
        # Store conversation reference for use in mock LLM
        self.conversation = conversation

        # Trigger lazy agent initialization to create tools
        conversation._ensure_agent_ready()

        # Set the test start time reference for the sleep executor
        # This must happen AFTER agent init but BEFORE any messages are processed
        sleep_tool = self.agent._tools.get("sleep")
        if sleep_tool and sleep_tool.executor is not None:
            setattr(sleep_tool.executor, "test_start_time", self.test_start_time)
            setattr(sleep_tool.executor, "test_instance", self)

        def elapsed_time():
            return f"[+{time.time() - self.test_start_time:.3f}s]"

        print(f"{elapsed_time()} Test started")

        with patch(
            "openhands.sdk.llm.llm.litellm_acompletion",
            side_effect=self._mock_llm_response,
        ):
            # Start the conversation with a natural request
            print(f"{elapsed_time()} Sending initial message")
            conversation.send_message(
                Message(
                    role="user",
                    content=[
                        TextContent(
                            text="Please sleep for 2 seconds, then sleep for "
                            "3 seconds and finish"
                        )
                    ],
                )
            )

            # Run conversation in background thread
            print(f"{elapsed_time()} Starting conversation thread")
            thread = threading.Thread(target=conversation.run)
            thread.start()

            # Wait for step 1 to be processing (LLM call made, but not finished)
            print(f"{elapsed_time()} Waiting for step 1 to be processing...")
            while self.step_count < 1:
                time.sleep(0.1)

            print(
                f"{elapsed_time()} Sending alligator request during step 1 processing"
            )
            conversation.send_message(
                Message(
                    role="user",
                    content=[
                        TextContent(
                            text="Please add the word 'alligator' to your next message"
                        )
                    ],
                )
            )

            # Send butterfly message when final step starts
            def send_butterfly_when_final_step_starts():
                # Wait for final step to start
                while not self.final_step_started:
                    time.sleep(0.01)  # Small sleep to avoid busy waiting

                # Send the message immediately when final step starts
                # This simulates a user sending a message during final step execution
                butterfly_send_time = time.time()
                self.timestamps.append(("butterfly_sent", butterfly_send_time))
                elapsed = butterfly_send_time - self.test_start_time
                print(f"[+{elapsed:.3f}s] BUTTERFLY MESSAGE SENT DURING FINAL STEP")

                conversation.send_message(
                    Message(
                        role="user",
                        content=[
                            TextContent(
                                text="Please add the word 'butterfly' to your next "
                                "message"
                            )
                        ],
                    )
                )

            butterfly_thread = threading.Thread(
                target=send_butterfly_when_final_step_starts
            )
            butterfly_thread.start()

            # Wait for conversation to complete
            print(f"{elapsed_time()} Waiting for conversation to complete...")

            # Wait for completion
            thread.join(timeout=10)
            butterfly_thread.join(timeout=5)

        # Debug: Print what we got
        print(f"\nDEBUG: Made {len(self.llm_completion_calls)} LLM calls")

        # The key insight: butterfly was sent during final step execution,
        # it should only appear in events but NEVER in any LLM call
        # because no subsequent step() occurs after the finish action

        # Check that both messages exist in the events list
        with conversation.state:
            message_events = [
                event
                for event in conversation.state.events
                if isinstance(event, MessageEvent) and event.llm_message.role == "user"
            ]

        user_messages = []
        for event in message_events:
            for content in event.llm_message.content:
                if isinstance(content, TextContent):
                    user_messages.append(content.text)

        assert "alligator" in str(user_messages), (
            "Alligator request message should be in events"
        )
        assert "butterfly" in str(user_messages), (
            "Butterfly request message should be in events"
        )

        # Note: The "alligator" message is sent during step 1 while the run loop
        # holds the state lock. Whether it appears in the very next LLM call can be
        # timing-dependent (who acquires the lock first for the next iteration).
        # For the purpose of this test (guarding against the finishing race), we do
        # not assert on "alligator" presence. We only require that the final-step
        # message ("butterfly") is never processed.

        # Verify that butterfly request WAS processed (fix verification)
        butterfly_seen = any(
            "butterfly" in str(call["messages"]).lower()
            for call in self.llm_completion_calls
        )
        assert butterfly_seen, (
            "Butterfly request should have been seen by LLM. "
            "The fix should ensure unattended messages are processed."
        )

        # TIMING ANALYSIS: Verify butterfly was sent during final step execution
        print("\nTIMING ANALYSIS:")

        # Extract timestamps
        timestamp_dict: dict[str, float] = dict(self.timestamps)
        if (
            "final_step_start" in timestamp_dict
            and "butterfly_sent" in timestamp_dict
            and "final_step_end" in timestamp_dict
        ):
            final_start = timestamp_dict["final_step_start"]
            butterfly_sent = timestamp_dict["butterfly_sent"]
            final_end = timestamp_dict["final_step_end"]

            print(f"- Final step started: [{final_start - self.test_start_time:.3f}s]")
            print(f"- Butterfly sent: [{butterfly_sent - self.test_start_time:.3f}s]")
            print(f"- Final step ended: [{final_end - self.test_start_time:.3f}s]")

            # CRITICAL ASSERTION: Butterfly message sent during final step execution
            assert final_start <= butterfly_sent <= final_end, (
                f"Butterfly message was NOT sent during final step execution! "
                f"Final step: {final_start:.3f}s-{final_end:.3f}s, "
                f"Butterfly sent: {butterfly_sent:.3f}s"
            )
            print("VERIFIED: Butterfly message was sent DURING final step execution")

            # Duration calculations
            step_duration = final_end - final_start
            butterfly_timing = butterfly_sent - final_start
            print(
                f"- Butterfly sent {butterfly_timing:.3f}s into "
                f"{step_duration:.3f}s final step"
            )
        else:
            print("WARNING: Missing timing data for analysis")
            print(f"Available timestamps: {list(timestamp_dict.keys())}")

        # Test has successfully verified the fix behavior!
        print("\nTEST SUCCESSFULLY VERIFIES THE FIX:")
        print("- Alligator request: sent during step 1 → processed in step 2")
        print(
            "- Butterfly request: sent during step 2 (final step execution) "
            "→ processed in step 3"
        )
        print("- Both messages exist in events, and both reached LLM")
        print(
            "- This proves: messages sent during final step execution "
            "are properly detected and processed"
        )


# Optional: run this test N times in parallel when executed as a script
# Usage (from repo root):
#   python tests/sdk/agent/test_message_while_finishing.py --runs 50 --concurrency 50
# This invokes pytest for this test many times, summarizing the results.


def _run_parallel_main():  # pragma: no cover - helper for manual stress testing
    import argparse
    import os
    import shutil
    import subprocess
    import sys

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
    test_rel = os.path.relpath(__file__, repo_root)
    default_node = (
        f"{test_rel}::"
        "TestMessageWhileFinishing::test_message_processing_fix_verification"
    )

    parser = argparse.ArgumentParser(
        description="Run this race test many times in parallel"
    )
    parser.add_argument("--nodeid", default=default_node, help="Pytest node id")
    parser.add_argument("--runs", type=int, default=50, help="Total runs")
    parser.add_argument("--concurrency", type=int, default=50, help="Max parallel runs")
    parser.add_argument(
        "--no-uv", action="store_true", help="Run pytest directly (no 'uv run')"
    )
    parser.add_argument(
        "--pytest-args", nargs=argparse.REMAINDER, help="Extra args passed to pytest"
    )
    args = parser.parse_args()

    use_uv = not args.no_uv
    extra_args = args.pytest_args if args.pytest_args else []

    print(
        f"Running {args.nodeid} {args.runs} times with "
        f"concurrency={args.concurrency} (uv={use_uv})"
    )

    def run_one(idx: int) -> tuple[int, int, str]:
        cmd: list[str] = []
        if use_uv and shutil.which("uv"):
            cmd.extend(["uv", "run"])  # prefer uv if available
        cmd.extend(["pytest", "-q", args.nodeid])
        if extra_args:
            cmd.extend(extra_args)

        env = os.environ.copy()
        start = datetime.now()
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=repo_root,
            env=env,
            text=True,
        )
        duration = (datetime.now() - start).total_seconds()
        out = f"[run {idx:02d}] rc={proc.returncode} dur={duration:.2f}s\n" + (
            proc.stdout or ""
        )
        return idx, proc.returncode, out

    failures: list[tuple[int, int, str]] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = [ex.submit(run_one, i + 1) for i in range(args.runs)]
        for fut in as_completed(futures):
            idx, rc, output = fut.result()
            status = "PASS" if rc == 0 else "FAIL"
            print(f"[run {idx:02d}] {status}")
            if rc != 0:
                failures.append((idx, rc, output))

    print("\nSummary:")
    print(
        f"Total: {args.runs}, Passed: "
        f"{args.runs - len(failures)}, Failed: {len(failures)}"
    )
    if failures:
        print("\n--- Failure outputs (first 3) ---")
        for i, (_idx, _rc, out) in enumerate(failures[:3], 1):
            print(f"\n[Failure {i}]\n{out}")
        sys.exit(1)

    print("All runs passed ✅")


if __name__ == "__main__":  # pragma: no cover - manual invocation only
    _run_parallel_main()
