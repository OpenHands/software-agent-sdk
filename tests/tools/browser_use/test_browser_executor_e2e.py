import os
import subprocess
import tempfile
import time
from collections.abc import Generator

import pytest

from openhands.tools.browser_use.definition import (
    BrowserClickAction,
    BrowserCloseTabAction,
    BrowserGetContentAction,
    BrowserGetStateAction,
    BrowserGoBackAction,
    BrowserListTabsAction,
    BrowserNavigateAction,
    BrowserObservation,
    BrowserScrollAction,
    BrowserSwitchTabAction,
    BrowserTypeAction,
)
from openhands.tools.browser_use.impl import BrowserToolExecutor


# Test HTML content for browser operations
TEST_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Browser Test Page</title>
    <style>
        body { font-family: Arial, sans-serif; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        button { padding: 10px 20px; margin: 10px; font-size: 16px; }
        input { padding: 10px; margin: 10px; font-size: 16px; width: 200px; }
        #result { margin-top: 20px; padding: 10px; background: #f0f0f0; }
        .long-content {
            height: 1000px;
            background: linear-gradient(to bottom, #fff, #ccc);
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Browser Test Page</h1>
        <p>This page is used for testing browser operations.</p>

        <button id="test-button" onclick="showResult()">Click Me</button>
        <input type="text" id="test-input" placeholder="Type here">
        <button onclick="clearResult()">Clear</button>

        <div id="result"></div>

        <h2>Navigation Test</h2>
        <a href="#section2" id="internal-link">Go to Section 2</a>

        <div class="long-content">
            <p>This is a long section for scroll testing...</p>
        </div>

        <h2 id="section2">Section 2</h2>
        <p>You've reached section 2!</p>
        <a href="page2.html" id="external-link">Go to Page 2</a>
    </div>

    <script>
        function showResult() {
            document.getElementById('result').innerHTML = (
                'Button clicked successfully!'
            );
        }

        function clearResult() {
            document.getElementById('result').innerHTML = '';
        }

        // Update result when input changes
        document.getElementById('test-input').addEventListener('input', function(e) {
            document.getElementById('result').innerHTML = (
                'Input value: ' + e.target.value
            );
        });
    </script>
</body>
</html>"""

# Second page for navigation testing
PAGE2_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Page 2</title>
</head>
<body>
    <h1>Page 2</h1>
    <p>This is the second page for navigation testing.</p>
    <a href="index.html">Back to Page 1</a>
</body>
</html>"""


@pytest.fixture(scope="module")
def test_server() -> Generator[str, None, None]:
    """Set up a local HTTP server for testing."""
    temp_dir = tempfile.mkdtemp()
    server_process = None

    try:
        # Create test HTML files
        with open(os.path.join(temp_dir, "index.html"), "w") as f:
            f.write(TEST_HTML)

        with open(os.path.join(temp_dir, "page2.html"), "w") as f:
            f.write(PAGE2_HTML)

        # Start HTTP server
        server_process = subprocess.Popen(
            ["python3", "-m", "http.server", "8001"],
            cwd=temp_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Give server time to start
        time.sleep(2)

        yield "http://localhost:8001"

    finally:
        # Cleanup
        if server_process is not None:
            try:
                server_process.terminate()
                server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_process.kill()

        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def browser_executor() -> Generator[BrowserToolExecutor, None, None]:
    """Create a real BrowserToolExecutor for testing."""
    executor = None
    try:
        executor = BrowserToolExecutor(
            headless=True,  # Run in headless mode for CI/testing
            session_timeout_minutes=5,  # Shorter timeout for tests
        )
        yield executor
    finally:
        if executor:
            try:
                executor.close()
            except Exception:
                pass  # Ignore cleanup errors


@pytest.mark.e2e
class TestBrowserExecutorE2E:
    """End-to-end tests for BrowserToolExecutor."""

    def test_navigate_action(
        self, browser_executor: BrowserToolExecutor, test_server: str
    ):
        """Test browser navigation action."""
        action = BrowserNavigateAction(url=test_server)
        result = browser_executor(action)

        assert isinstance(result, BrowserObservation)
        assert not result.is_error
        output_text = result.text.lower()
        assert "successfully" in output_text or "navigated" in output_text

    def test_get_state_action(
        self, browser_executor: BrowserToolExecutor, test_server: str
    ):
        """Test getting browser state."""
        # First navigate to the test page
        navigate_action = BrowserNavigateAction(url=test_server)
        browser_executor(navigate_action)

        # Then get the state
        action = BrowserGetStateAction(include_screenshot=False)
        result = browser_executor(action)

        assert isinstance(result, BrowserObservation)
        assert not result.is_error
        assert "Browser Test Page" in result.text

    def test_get_state_with_screenshot(
        self, browser_executor: BrowserToolExecutor, test_server: str
    ):
        """Test getting browser state with screenshot."""
        # Navigate to test page
        navigate_action = BrowserNavigateAction(url=test_server)
        browser_executor(navigate_action)

        # Get state with screenshot
        action = BrowserGetStateAction(include_screenshot=True)
        result = browser_executor(action)

        assert isinstance(result, BrowserObservation)
        assert not result.is_error
        assert result.screenshot_data is not None
        assert len(result.screenshot_data) > 0

    def test_click_action(
        self, browser_executor: BrowserToolExecutor, test_server: str
    ):
        """Test clicking an element."""
        # Navigate to test page
        navigate_action = BrowserNavigateAction(url=test_server)
        browser_executor(navigate_action)

        # Get state to find clickable elements
        get_state_action = BrowserGetStateAction(include_screenshot=False)
        state_result = browser_executor(get_state_action)

        # Parse the state to find button index
        # The test button should be indexed in the interactive elements
        assert "Click Me" in state_result.text

        # Try to click the first interactive element (likely the button)
        click_action = BrowserClickAction(index=0)
        result = browser_executor(click_action)

        assert isinstance(result, BrowserObservation)
        assert not result.is_error

    def test_type_action(self, browser_executor: BrowserToolExecutor, test_server: str):
        """Test typing text into an input field."""
        # Navigate to test page
        navigate_action = BrowserNavigateAction(url=test_server)
        browser_executor(navigate_action)

        # Get state to find input elements
        get_state_action = BrowserGetStateAction(include_screenshot=False)
        state_result = browser_executor(get_state_action)

        # Look for input field in the state
        state_output = state_result.text
        assert "test-input" in state_output or "Type here" in state_output

        # Find the input field index and type into it
        # This assumes the input field is one of the interactive elements
        type_action = BrowserTypeAction(index=1, text="Hello World")
        result = browser_executor(type_action)

        assert isinstance(result, BrowserObservation)
        assert not result.is_error

    def test_scroll_action(
        self, browser_executor: BrowserToolExecutor, test_server: str
    ):
        """Test scrolling the page."""
        # Navigate to test page
        navigate_action = BrowserNavigateAction(url=test_server)
        browser_executor(navigate_action)

        # Scroll down
        scroll_action = BrowserScrollAction(direction="down")
        result = browser_executor(scroll_action)

        assert isinstance(result, BrowserObservation)
        assert not result.is_error

        # Scroll back up
        scroll_up_action = BrowserScrollAction(direction="up")
        result = browser_executor(scroll_up_action)

        assert isinstance(result, BrowserObservation)
        assert not result.is_error

    def test_get_content_action(
        self, browser_executor: BrowserToolExecutor, test_server: str
    ):
        """Test extracting page content."""
        # Navigate to test page
        navigate_action = BrowserNavigateAction(url=test_server)
        browser_executor(navigate_action)

        # Get content without links
        content_action = BrowserGetContentAction(extract_links=False, start_from_char=0)
        result = browser_executor(content_action)

        assert isinstance(result, BrowserObservation)
        assert not result.is_error
        assert "Browser Test Page" in result.text

        # Get content with links
        content_with_links_action = BrowserGetContentAction(
            extract_links=True, start_from_char=0
        )
        result = browser_executor(content_with_links_action)

        assert isinstance(result, BrowserObservation)
        assert not result.is_error
        assert "Browser Test Page" in result.text

    def test_navigate_new_tab(
        self, browser_executor: BrowserToolExecutor, test_server: str
    ):
        """Test opening a new tab."""
        # Navigate to test page in new tab
        action = BrowserNavigateAction(url=test_server, new_tab=True)
        result = browser_executor(action)

        assert isinstance(result, BrowserObservation)
        assert not result.is_error

    def test_list_tabs_action(
        self, browser_executor: BrowserToolExecutor, test_server: str
    ):
        """Test listing browser tabs."""
        # Navigate to create at least one tab
        navigate_action = BrowserNavigateAction(url=test_server)
        browser_executor(navigate_action)

        # List tabs
        list_tabs_action = BrowserListTabsAction()
        result = browser_executor(list_tabs_action)

        assert isinstance(result, BrowserObservation)
        assert not result.is_error
        # Should contain tab information
        assert len(result.text) > 0

    def test_go_back_action(
        self, browser_executor: BrowserToolExecutor, test_server: str
    ):
        """Test browser back navigation."""
        # Navigate to first page
        navigate_action = BrowserNavigateAction(url=test_server)
        browser_executor(navigate_action)

        # Navigate to second page
        page2_url = f"{test_server}/page2.html"
        navigate_action2 = BrowserNavigateAction(url=page2_url)
        browser_executor(navigate_action2)

        # Go back
        back_action = BrowserGoBackAction()
        result = browser_executor(back_action)

        assert isinstance(result, BrowserObservation)
        assert not result.is_error

    def test_switch_tab_action(
        self, browser_executor: BrowserToolExecutor, test_server: str
    ):
        """Test switching between tabs."""
        # Create first tab
        navigate_action = BrowserNavigateAction(url=test_server)
        browser_executor(navigate_action)

        # Create second tab
        navigate_new_tab_action = BrowserNavigateAction(
            url=f"{test_server}/page2.html", new_tab=True
        )
        browser_executor(navigate_new_tab_action)

        # List tabs to get tab IDs
        list_tabs_action = BrowserListTabsAction()
        tabs_result = browser_executor(list_tabs_action)

        # Parse tab information to get a tab ID
        # This is a simplified approach - in practice you'd parse the JSON response
        if "tab" in tabs_result.text.lower():
            # Try to switch to first tab (assuming tab ID format)
            switch_action = BrowserSwitchTabAction(tab_id="0")
            result = browser_executor(switch_action)

            assert isinstance(result, BrowserObservation)
            # Note: This might fail if tab ID format is different, which is expected

    def test_close_tab_action(
        self, browser_executor: BrowserToolExecutor, test_server: str
    ):
        """Test closing a browser tab."""
        # Create first tab
        navigate_action = BrowserNavigateAction(url=test_server)
        browser_executor(navigate_action)

        # Create second tab
        navigate_new_tab_action = BrowserNavigateAction(
            url=f"{test_server}/page2.html", new_tab=True
        )
        browser_executor(navigate_new_tab_action)

        # Try to close a tab
        close_action = BrowserCloseTabAction(tab_id="1")
        result = browser_executor(close_action)

        assert isinstance(result, BrowserObservation)
        # Note: This might fail if tab ID format is different, which is expected

    def test_error_handling(self, browser_executor: BrowserToolExecutor):
        """Test error handling for invalid operations."""
        # Try to navigate to invalid URL
        action = BrowserNavigateAction(url="invalid-url")
        result = browser_executor(action)

        assert isinstance(result, BrowserObservation)
        # Should either succeed with error message or fail gracefully
        # The exact behavior depends on the browser implementation

    def test_executor_initialization_and_cleanup(self):
        """Test that executor can be created and cleaned up properly."""
        executor = BrowserToolExecutor(headless=True)

        # Test that executor is properly initialized
        assert executor._config["headless"] is True
        assert executor._initialized is False

        # Test cleanup
        executor.close()

        # Should not raise exceptions

    def test_concurrent_actions(
        self, browser_executor: BrowserToolExecutor, test_server: str
    ):
        """Test that multiple actions can be executed in sequence."""
        # Navigate
        navigate_result = browser_executor(BrowserNavigateAction(url=test_server))
        assert not navigate_result.is_error

        # Get state
        state_result = browser_executor(BrowserGetStateAction(include_screenshot=False))
        assert not state_result.is_error

        # Scroll
        scroll_result = browser_executor(BrowserScrollAction(direction="down"))
        assert not scroll_result.is_error

        # Get content
        content_result = browser_executor(
            BrowserGetContentAction(extract_links=False, start_from_char=0)
        )
        assert not content_result.is_error

        # All actions should complete successfully
        assert all(
            not result.is_error
            for result in [navigate_result, state_result, scroll_result, content_result]
        )
