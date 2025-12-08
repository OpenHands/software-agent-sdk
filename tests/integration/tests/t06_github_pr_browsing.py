"""Test that an agent can browse a GitHub PR and extract information."""

from openhands.sdk import get_logger
from openhands.sdk.conversation import get_agent_final_response
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


INSTRUCTION = (
    "Look at https://github.com/OpenHands/OpenHands/pull/8, and tell me "
    "what is happening there and what did @asadm suggest. "
)


logger = get_logger(__name__)


class GitHubPRBrowsingTest(BaseIntegrationTest):
    """Test that an agent can browse a GitHub PR and extract information."""

    INSTRUCTION: str = INSTRUCTION

    @property
    def tools(self) -> list[Tool]:
        """List of tools available to the agent."""
        register_tool("TerminalTool", TerminalTool)
        register_tool("FileEditorTool", FileEditorTool)
        return [
            Tool(name="TerminalTool"),
            Tool(name="FileEditorTool"),
        ]

    def setup(self) -> None:
        """No special setup needed for GitHub PR browsing."""

    def verify_result(self) -> TestResult:
        """Verify that the agent successfully browsed the GitHub PR."""

        # Get the agent's final answer/response to the instruction
        agent_answer = get_agent_final_response(self.conversation.state.events)

        if not agent_answer:
            return TestResult(
                success=False,
                reason=(
                    "No final answer found from agent. "
                    f"Events: {len(list(self.conversation.state.events))}, "
                    f"LLM messages: {len(self.llm_messages)}"
                ),
            )

        # Convert to lowercase for case-insensitive matching
        answer_text = agent_answer.lower()

        github_indicators = ["mit", "apache", "license"]

        if any(indicator in answer_text for indicator in github_indicators):
            return TestResult(
                success=True,
                reason="Agent's final answer contains information about the PR content",
            )
        else:
            return TestResult(
                success=False,
                reason=(
                    "Agent's final answer does not contain the expected information "
                    "about the PR content. "
                    f"Final answer preview: {agent_answer[:200]}..."
                ),
            )
