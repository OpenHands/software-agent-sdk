"""Test conversation restore (resume) behavior.

This integration test exercises the key behavior of PR #1542:
- On resume, we use the runtime-provided Agent.
- Tool compatibility is verified (tools used in history must still exist).
- Conversation-state settings are restored from persistence (e.g.
  confirmation_policy, execution_status).

Note: This test does not require the agent to take any actions; it verifies the
resume semantics directly.
"""

from __future__ import annotations

import json
import os

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
)
from openhands.sdk.llm import LLM
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


INSTRUCTION = "Create a new conversation."  # Not used; we validate restore behavior.


class RestoreConversationTest(BaseIntegrationTest):
    """Ensure resume restores persisted state but uses runtime Agent configuration."""

    INSTRUCTION: str = INSTRUCTION

    @property
    def tools(self) -> list[Tool]:
        register_tool("TerminalTool", TerminalTool)
        return [Tool(name="TerminalTool")]

    def setup(self) -> None:
        # We want persistence in the integration test workspace.
        # Keep persisted conversations somewhere easy to inspect locally.
        # This is intentionally outside the ephemeral runner workspace.
        self.persistence_dir = os.path.join(
            os.getcwd(), "tests", "integration", "outputs", "local_persist_t10"
        )
        os.makedirs(self.persistence_dir, exist_ok=True)

    def verify_result(self) -> TestResult:
        # First run: create conversation with agent1
        llm1 = LLM(
            model="gpt-5.1-codex-max",
            base_url=self.llm.base_url,
            api_key=self.llm.api_key,
            usage_id="restore-test-llm-1",
            max_input_tokens=100_000,
        )
        agent1 = Agent(llm=llm1, tools=self.tools)

        conv1 = LocalConversation(
            agent=agent1,
            workspace=self.workspace,
            persistence_dir=self.persistence_dir,
            visualizer=None,
        )

        # Persisted state settings (should be restored from persistence on resume)
        conv1.state.confirmation_policy = AlwaysConfirm()
        conv1.state.execution_status = ConversationExecutionStatus.ERROR

        # Ensure there's at least one user + assistant message pair in history.
        # This exercises the full create -> persist -> resume path with events.
        conv1.send_message(INSTRUCTION)
        conv1.run()

        conversation_id = conv1.id
        conv1_event_count = len(conv1.state.events)
        print(f"[t10] conv1 persisted events: {conv1_event_count}")

        # Read persisted base_state.json and ensure it contains the original model.
        # LocalConversation persists to:
        #   <persistence_dir>/<conversation_id.hex>/base_state.json
        base_state_path = os.path.join(
            self.persistence_dir, conversation_id.hex, "base_state.json"
        )
        if not os.path.exists(base_state_path):
            return TestResult(
                success=False,
                reason=(
                    f"Expected persisted base_state.json not found at {base_state_path}"
                ),
            )

        with open(base_state_path) as f:
            base_state = json.load(f)

        persisted_model = (
            base_state.get("agent", {}).get("llm", {}).get("model")
            if isinstance(base_state, dict)
            else None
        )
        if persisted_model != "gpt-5.1-codex-max":
            return TestResult(
                success=False,
                reason=(
                    "Expected persisted agent.llm.model to be 'gpt-5.1-codex-max', "
                    f"got {persisted_model!r}"
                ),
            )

        del conv1

        # Resume: provide a *different* runtime agent/LLM configuration.
        llm2 = LLM(
            model="gpt-5.2",
            base_url=self.llm.base_url,
            api_key=self.llm.api_key,
            usage_id="restore-test-llm-2",
            max_input_tokens=50_000,
        )
        agent2 = Agent(llm=llm2, tools=self.tools)

        conv2 = LocalConversation(
            agent=agent2,
            workspace=self.workspace,
            persistence_dir=self.persistence_dir,
            conversation_id=conversation_id,
            visualizer=None,
        )

        conv2_event_count = len(conv2.state.events)
        print(f"[t10] conv2 loaded events: {conv2_event_count}")
        if conv2_event_count != conv1_event_count:
            return TestResult(
                success=False,
                reason=(
                    "Event count mismatch after restore: "
                    f"before={conv1_event_count} after={conv2_event_count}"
                ),
            )

        # 1) Persisted state settings should be restored on resume.
        if not conv2.state.confirmation_policy.should_confirm():
            return TestResult(
                success=False,
                reason="confirmation_policy was not restored from persistence",
            )

        # The restored conversation should be in a normal resumable state.
        # We expect it to have reached FINISHED after the initial run.
        if conv2.state.execution_status != ConversationExecutionStatus.FINISHED:
            return TestResult(
                success=False,
                reason=(
                    "Expected execution_status=FINISHED after restore, got "
                    f"{conv2.state.execution_status!r}"
                ),
            )

        # Prove the restored conversation can continue.
        conv2.state.execution_status = ConversationExecutionStatus.ERROR
        conv2.send_message("are you still there?")
        conv2.run()

        # After a successful run, we should not remain in an error state.
        if conv2.state.execution_status == ConversationExecutionStatus.ERROR:
            return TestResult(
                success=False,
                reason=(
                    "Expected restored conversation to make progress after a new "
                    "user message, but execution_status is still ERROR."
                ),
            )

        # 2) Runtime agent/LLM should be used.
        if conv2.agent.llm.model != "gpt-5.2":
            return TestResult(
                success=False,
                reason=(
                    "Expected runtime agent llm.model 'gpt-5.2' after resume, "
                    f"got {conv2.agent.llm.model!r}"
                ),
            )
        if conv2.agent.llm.max_input_tokens != 50_000:
            return TestResult(
                success=False,
                reason=(
                    "Expected runtime max_input_tokens=50000 after resume, "
                    f"got {conv2.agent.llm.max_input_tokens!r}"
                ),
            )

        return TestResult(success=True, reason="Restore semantics verified")
