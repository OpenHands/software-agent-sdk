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


INSTRUCTION = "Create a new conversation."


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
        # First run: create conversation with agent1.
        # Use the runner-provided LLM config for llm1.
        llm1 = LLM(
            model=self.llm.model,
            base_url=self.llm.base_url,
            api_key=self.llm.api_key,
            usage_id="restore-test-llm-1",
            max_input_tokens=self.llm.max_input_tokens,
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

        persisted_llm = base_state.get("agent", {}).get("llm", {})
        persisted_model = persisted_llm.get("model")
        persisted_max_input_tokens = persisted_llm.get("max_input_tokens")
        persisted_usage_id = persisted_llm.get("usage_id")

        if persisted_model != llm1.model:
            return TestResult(
                success=False,
                reason=(
                    "Expected persisted agent.llm.model to match runtime llm1.model, "
                    f"got {persisted_model!r} (expected {llm1.model!r})"
                ),
            )

        if persisted_max_input_tokens != llm1.max_input_tokens:
            return TestResult(
                success=False,
                reason=(
                    "Expected persisted agent.llm.max_input_tokens to match runtime "
                    f"llm1.max_input_tokens={llm1.max_input_tokens!r}, got "
                    f"{persisted_max_input_tokens!r}"
                ),
            )

        if persisted_usage_id != "restore-test-llm-1":
            return TestResult(
                success=False,
                reason=(
                    "Expected persisted agent.llm.usage_id to be 'restore-test-llm-1', "
                    f"got {persisted_usage_id!r}"
                ),
            )

        del conv1

        # Resume: provide a *different* runtime agent/LLM configuration.
        # We load llm2 config from RESTORE_LLM_CONFIG_2 (JSON string), but always
        # use the CI-provided base_url/api_key.
        llm2_config_raw = os.environ.get("RESTORE_LLM_CONFIG_2")
        if not llm2_config_raw:
            return TestResult(
                success=False,
                reason="RESTORE_LLM_CONFIG_2 is required for t10_restore_conversation",
            )

        try:
            llm2_config = json.loads(llm2_config_raw)
        except json.JSONDecodeError as e:
            return TestResult(
                success=False,
                reason=f"RESTORE_LLM_CONFIG_2 is not valid JSON: {e}",
            )

        llm2 = LLM(
            model=llm2_config["model"],
            base_url=self.llm.base_url,
            api_key=self.llm.api_key,
            usage_id="restore-test-llm-2",
            max_input_tokens=llm2_config.get("max_input_tokens"),
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
        if conv2.agent.llm.model != llm2.model:
            return TestResult(
                success=False,
                reason=(
                    "Expected runtime agent llm.model to match llm2.model after "
                    f"resume, got {conv2.agent.llm.model!r} (expected {llm2.model!r})"
                ),
            )
        if (
            llm2.max_input_tokens is not None
            and conv2.agent.llm.max_input_tokens != llm2.max_input_tokens
        ):
            return TestResult(
                success=False,
                reason=(
                    "Expected runtime max_input_tokens to match llm2.max_input_tokens "
                    f"after resume, got {conv2.agent.llm.max_input_tokens!r} "
                    f"(expected {llm2.max_input_tokens!r})"
                ),
            )

        return TestResult(success=True, reason="Restore semantics verified")
