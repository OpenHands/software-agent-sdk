"""Test that an agent can view and analyze image files using FileEditor."""

import os
import urllib.request

from openhands.sdk import TextContent, get_logger
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, SkipTest, TestResult


INSTRUCTION = (
    "Please view the logo.png file in the current directory and tell me what "
    "colors you see in it. Is the logo blue, yellow, or green? Please analyze "
    "the image and provide your answer."
)

IMAGE_URL = "https://github.com/OpenHands/docs/raw/main/openhands/static/img/logo.png"

logger = get_logger(__name__)


class ImageFileViewingTest(BaseIntegrationTest):
    """Test that an agent can view and analyze image files."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logo_path: str = os.path.join(self.workspace, "logo.png")

        # Verify that the LLM supports vision
        if not self.llm.vision_is_active():
            raise SkipTest(
                "This test requires a vision-capable LLM model. "
                "Please use a model that supports image input."
            )

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
        """Download the OpenHands logo for the agent to analyze."""
        try:
            urllib.request.urlretrieve(IMAGE_URL, self.logo_path)
            logger.info(f"Downloaded test logo to: {self.logo_path}")
        except Exception as e:
            logger.error(f"Failed to download logo: {e}")
            raise

    def verify_result(self) -> TestResult:
        """Verify that the agent identified yellow as one of the logo colors."""
        if not os.path.exists(self.logo_path):
            return TestResult(
                success=False, reason="Logo file not found after agent execution"
            )

        # Check the agent's responses in collected events
        # Look for messages mentioning yellow color
        agent_responses = []
        for event in self.collected_events:
            if isinstance(event, MessageEvent):
                message = event.llm_message
                if message.role == "assistant":
                    for content_item in message.content:
                        if isinstance(content_item, TextContent):
                            agent_responses.append(content_item.text.lower())

        combined_response = " ".join(agent_responses)

        if "yellow" in combined_response:
            return TestResult(
                success=True,
                reason="Agent successfully identified yellow color in the logo",
            )
        else:
            return TestResult(
                success=False,
                reason=(
                    f"Agent did not identify yellow color in the logo. "
                    f"Response: {combined_response[:500]}"
                ),
            )
