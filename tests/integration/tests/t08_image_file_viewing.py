"""Test that an agent can view and analyze image files using FileEditor."""

import os
import urllib.request

from openhands.sdk import get_logger
from openhands.sdk.conversation.response_utils import get_agent_final_response
from tests.integration.base import BaseIntegrationTest, SkipTest, TestResult


INSTRUCTION = (
    "Please view the python_icon.png file in the current directory and tell me "
    "what colors you see in it. Does the icon contain yellow? Please analyze "
    "the image and provide your answer."
)

IMAGE_URL = "https://www.python.org/static/opengraph-icon-200x200.png"

logger = get_logger(__name__)


class ImageFileViewingTest(BaseIntegrationTest):
    """Test that an agent can view and analyze image files."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.icon_path: str = os.path.join(self.workspace, "python_icon.png")

        # Verify that the LLM supports vision
        if not self.llm.vision_is_active():
            raise SkipTest(
                "This test requires a vision-capable LLM model. "
                "Please use a model that supports image input."
            )

    def setup(self) -> None:
        """Download a Python icon for the agent to analyze."""
        try:
            urllib.request.urlretrieve(IMAGE_URL, self.icon_path)
            logger.info(f"Downloaded test icon to: {self.icon_path}")
        except Exception as e:
            logger.error(f"Failed to download icon: {e}")
            raise

    def verify_result(self) -> TestResult:
        """Verify that the agent identified yellow as one of the icon colors."""
        if not os.path.exists(self.icon_path):
            return TestResult(
                success=False, reason="Icon file not found after agent execution"
            )

        # Get the final response from agent (handles both MessageEvent and FinishAction)
        final_response = get_agent_final_response(self.collected_events).lower()

        if "yellow" in final_response:
            return TestResult(
                success=True,
                reason="Agent successfully identified yellow color in the icon",
            )
        else:
            return TestResult(
                success=False,
                reason=(
                    f"Agent did not identify yellow color in the icon. "
                    f"Response: {final_response[:500]}"
                ),
            )
