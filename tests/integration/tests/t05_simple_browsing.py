"""Test that an agent can browse a local web page and extract information."""

import os
import re
import subprocess
import time

from openhands.sdk import get_logger
from openhands.sdk.conversation import get_agent_final_response
from tests.integration.base import BaseIntegrationTest, TestResult


INSTRUCTION = "Browse localhost:8000, and tell me the ultimate answer to life."

HTML_FILE = (
    "<!DOCTYPE html>\n"
    '<html lang="en">\n'
    "<head>\n"
    '    <meta charset="UTF-8">\n'
    '    <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
    "    <title>The Ultimate Answer</title>\n"
    "    <style>\n"
    "        body {\n"
    "            display: flex;\n"
    "            justify-content: center;\n"
    "            align-items: center;\n"
    "            height: 100vh;\n"
    "            margin: 0;\n"
    "            background: linear-gradient(to right, #1e3c72, #2a5298);\n"
    "            color: #fff;\n"
    "            font-family: 'Arial', sans-serif;\n"
    "            text-align: center;\n"
    "        }\n"
    "        .container {\n"
    "            text-align: center;\n"
    "            padding: 20px;\n"
    "            background: rgba(255, 255, 255, 0.1);\n"
    "            border-radius: 10px;\n"
    "            box-shadow: 0 0 10px rgba(0, 0, 0, 0.2);\n"
    "        }\n"
    "        h1 {\n"
    "            font-size: 36px;\n"
    "            margin-bottom: 20px;\n"
    "        }\n"
    "        p {\n"
    "            font-size: 18px;\n"
    "            margin-bottom: 30px;\n"
    "        }\n"
    "        #showButton {\n"
    "            padding: 10px 20px;\n"
    "            font-size: 16px;\n"
    "            color: #1e3c72;\n"
    "            background: #fff;\n"
    "            border: none;\n"
    "            border-radius: 5px;\n"
    "            cursor: pointer;\n"
    "            transition: background 0.3s ease;\n"
    "        }\n"
    "        #showButton:hover {\n"
    "            background: #f0f0f0;\n"
    "        }\n"
    "        #result {\n"
    "            margin-top: 20px;\n"
    "            font-size: 24px;\n"
    "        }\n"
    "    </style>\n"
    "</head>\n"
    "<body>\n"
    '    <div class="container">\n'
    "        <h1>The Ultimate Answer</h1>\n"
    "        <p>Click the button to reveal the answer to life, the universe, "
    "and everything.</p>\n"
    '        <button id="showButton">Click me</button>\n'
    '        <div id="result"></div>\n'
    "    </div>\n"
    "    <script>\n"
    "        document.getElementById('showButton').addEventListener('click', "
    "function() {\n"
    "            document.getElementById('result').innerText = "
    "'The answer is OpenHands is all you need!';\n"
    "        });\n"
    "    </script>\n"
    "</body>\n"
    "</html>\n"
)


logger = get_logger(__name__)


class SimpleBrowsingTest(BaseIntegrationTest):
    """Test that an agent can browse a local web page and extract information."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.server_process: subprocess.Popen[bytes] | None = None

    @property
    def enable_browser(self) -> bool:
        """Enable browser tools for this browsing test."""
        return True

    def setup(self) -> None:
        """Set up a local web server with the HTML file."""

        try:
            # Write the HTML file to the workspace
            html_path = os.path.join(self.workspace, "index.html")
            with open(html_path, "w") as f:
                f.write(HTML_FILE)

            # Start the HTTP server in the background
            self.server_process: subprocess.Popen[bytes] | None = subprocess.Popen(
                ["python3", "-m", "http.server", "8000"],
                cwd=self.workspace,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Give the server a moment to start
            time.sleep(2)

            logger.info(f"Started HTTP server on port 8000 serving {html_path}")

        except Exception as e:
            raise RuntimeError(f"Failed to set up web server: {e}")

    def verify_result(self) -> TestResult:
        """Verify that the agent successfully browsed the page and found the answer."""
        # Use the utility function to get the agent's final response
        agent_response = get_agent_final_response(self.conversation.state.events)

        logger.info(f"Agent final response to analyze: {agent_response[:500]}...")

        # Use regex to check if the agent found the correct answer
        # The expected answer is "The answer is OpenHands is all you need!"
        # We'll be flexible with the exact wording but look for key components
        answer_patterns = [
            r"(?i)the answer is openhands is all you need",
            r"(?i)openhands is all you need",
            r"(?i)answer.*openhands.*all.*need",
        ]

        found_answer = False
        matched_pattern = None

        for pattern in answer_patterns:
            if re.search(pattern, agent_response):
                found_answer = True
                matched_pattern = pattern
                break

        if found_answer:
            return TestResult(
                success=True,
                reason=(
                    f"Agent successfully found the answer! "
                    f"Matched pattern: {matched_pattern}. "
                    f"Response contained the expected content about OpenHands."
                ),
            )
        else:
            return TestResult(
                success=False,
                reason=(
                    "Agent did not find the answer. "
                    f"Response: {agent_response[:200]}..."
                ),
            )

    def teardown(self):
        """Turn down the web server"""
        if self.server_process:
            try:
                self.server_process.terminate()
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_process.kill()
            except Exception as e:
                logger.warning(f"Error terminating server process: {e}")

        logger.info("Cleaned up web server")
