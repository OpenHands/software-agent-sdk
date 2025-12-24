"""
Base classes for agent-sdk integration tests.
"""

import os
import sys
from abc import ABC, abstractmethod
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from typing import Any

from pydantic import BaseModel, SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Message,
    TextContent,
)
from openhands.sdk.context.condenser import CondenserBase
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.visualizer import DefaultConversationVisualizer
from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible import (
    MessageEvent,
)
from openhands.sdk.tool import Tool
from tests.integration.early_stopper import EarlyStopperBase, EarlyStopResult


class SkipTest(Exception):
    """
    Exception raised to indicate that a test should be skipped.

    This is useful for tests that require specific capabilities (e.g., vision)
    that may not be available in all LLMs.
    """

    pass


class TestResult(BaseModel):
    """Result of an integration test."""

    success: bool
    reason: str | None = None
    skipped: bool = False


class BaseIntegrationTest(ABC):
    """
    Base class for agent-sdk integration tests.

    This class provides a structured approach to writing integration tests
    that use real LLM calls. It handles common setup like LLM configuration,
    temporary directory management, and agent creation.

    Unlike the OpenHands approach which uses a Runtime, this uses tools
    directly with temporary directories for isolation.
    """

    INSTRUCTION: str

    def __init__(
        self,
        instruction: str,
        llm_config: dict[str, Any],
        instance_id: str,
        workspace: str,
    ):
        self.instruction: str = instruction
        self.llm_config: dict[str, Any] = llm_config
        self.workspace: str = workspace
        self.instance_id: str = instance_id
        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            raise ValueError(
                "LLM_API_KEY environment variable not set. Skipping real LLM test."
            )
        base_url = os.getenv("LLM_BASE_URL")
        if not base_url:
            raise ValueError(
                "LLM_BASE_URL environment variable not set. Skipping real LLM test."
            )

        # Create LLM with all config parameters
        llm_kwargs = {
            **self.llm_config,  # Pass through all config parameters
            "base_url": base_url,
            "api_key": SecretStr(api_key),
        }

        self.llm: LLM = LLM(**llm_kwargs, usage_id="test-llm")
        self.agent: Agent = Agent(
            llm=self.llm, tools=self.tools, condenser=self.condenser
        )
        self.collected_events: list[Event] = []
        self.llm_messages: list[dict[str, Any]] = []

        # Create log file path for this test instance
        self.log_file_path: str = os.path.join(
            self.workspace, f"{self.instance_id}_agent_logs.txt"
        )

        # Early stopping support - must be initialized BEFORE LocalConversation
        # since the callback may access these attributes
        self.early_stopper: EarlyStopperBase | None = None
        self.early_stop_result: EarlyStopResult | None = None

        self.conversation: LocalConversation = LocalConversation(
            agent=self.agent,
            workspace=self.workspace,
            callbacks=[self.conversation_callback],
            visualizer=DefaultConversationVisualizer(),  # Use default visualizer
            max_iteration_per_run=self.max_iteration_per_run,
        )

    def conversation_callback(self, event: Event):
        """Callback to collect conversation events."""
        self.collected_events.append(event)
        if isinstance(event, MessageEvent):
            self.llm_messages.append(event.llm_message.model_dump())

        # Check early stopping condition
        if self.early_stopper and not self.early_stop_result:
            result = self.early_stopper.check(self.collected_events)
            if result.should_stop:
                self.early_stop_result = result
                self.conversation.pause()  # Trigger graceful stop

    def run_instruction(self) -> TestResult:
        """
        Run user instruction through the agent and verify results.

        Returns:
            TestResult: The result of the test
        """
        try:
            # Setup
            self.setup()

            # Initialize log file with header
            with open(self.log_file_path, "w") as f:
                f.write(f"Agent Logs for Test: {self.instance_id}\n")
                f.write("=" * 50 + "\n\n")

            # Capture stdout and stderr during conversation
            stdout_buffer = StringIO()
            stderr_buffer = StringIO()

            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                self.conversation.send_message(
                    message=Message(
                        role="user", content=[TextContent(text=self.instruction)]
                    )
                )
                self.conversation.run()

            # Save captured output to log file
            captured_output = stdout_buffer.getvalue()
            captured_errors = stderr_buffer.getvalue()

            with open(self.log_file_path, "a") as f:
                if captured_output:
                    f.write("STDOUT:\n")
                    f.write(captured_output)
                    f.write("\n")
                if captured_errors:
                    f.write("STDERR:\n")
                    f.write(captured_errors)
                    f.write("\n")

            # Also print to console for debugging
            if captured_output:
                print(captured_output, end="")
            if captured_errors:
                print(captured_errors, file=sys.stderr, end="")

            # Check if early stopped - skip full verification
            if self.early_stop_result:
                return TestResult(
                    success=False,
                    reason=f"Early stopped: {self.early_stop_result.reason}",
                )

            # Verify results
            result = self.verify_result()

            return result

        except Exception as e:
            return TestResult(success=False, reason=f"Test execution failed: {str(e)}")

        finally:
            self.teardown()

    @property
    @abstractmethod
    def tools(self) -> list[Tool]:
        """List of tools available to the agent."""
        pass

    @property
    def condenser(self) -> CondenserBase | None:
        """Optional condenser for the agent. Override to provide a custom condenser.

        Returns:
            CondenserBase instance or None (default)
        """
        return None

    @property
    def max_iteration_per_run(self) -> int:
        """Maximum iterations per conversation run. Override to set a custom limit.

        Returns:
            Maximum iterations (default: 100)
        """
        return 100

    @abstractmethod
    def setup(self) -> None:
        """
        Initialize test-specific setup.

        This method should create any files, directories, or other
        resources needed for the test.
        """
        pass

    @abstractmethod
    def verify_result(self) -> TestResult:
        """
        Verify the result of the test.

        This method should check if the agent successfully completed
        the task by examining files in self.temp_dir, checking the
        events in self.events, or other verification methods.

        Returns:
            TestResult: The result of the verification
        """
        pass

    def add_judge_usage(
        self, prompt_tokens: int, completion_tokens: int, cost: float
    ) -> None:
        """
        Add LLM judge usage to conversation stats.

        This ensures judge costs are included in the total test cost.

        Args:
            prompt_tokens: Number of prompt tokens used by judge
            completion_tokens: Number of completion tokens used by judge
            cost: Cost of the judge call
        """
        from openhands.sdk.llm.utils.metrics import TokenUsage

        # Add to conversation stats for the test LLM
        stats = self.conversation.conversation_stats
        if stats:
            try:
                metrics = stats.get_metrics_for_usage("test-llm")
                # Update accumulated metrics
                if metrics.accumulated_token_usage:
                    metrics.accumulated_token_usage.prompt_tokens = (
                        metrics.accumulated_token_usage.prompt_tokens or 0
                    ) + prompt_tokens
                    metrics.accumulated_token_usage.completion_tokens = (
                        metrics.accumulated_token_usage.completion_tokens or 0
                    ) + completion_tokens
                else:
                    # Create new TokenUsage if it doesn't exist
                    metrics.accumulated_token_usage = TokenUsage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )
                metrics.accumulated_cost += cost
            except Exception:
                # If test-llm doesn't exist in stats yet, skip
                pass

    def teardown(self):
        """
        Clean up test resources.
        The workspace directory is torn down externally.
        Add any additional cleanup (git, server, ...) here if needed.
        """
