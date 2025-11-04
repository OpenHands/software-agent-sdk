"""Browser-use tool implementation for web automation."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, Self

from pydantic import Field

from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
)
from openhands.sdk.utils import maybe_truncate


# Lazy import to avoid hanging during module import
if TYPE_CHECKING:
    from openhands.tools.browser_use.impl import BrowserToolExecutor


# Maximum output size for browser observations
MAX_BROWSER_OUTPUT_SIZE = 50000


class BrowserObservation(Observation):
    """Base observation for browser operations."""

    output: str = Field(description="The output message from the browser operation")
    error: str | None = Field(default=None, description="Error message if any")
    screenshot_data: str | None = Field(
        default=None, description="Base64 screenshot data if available"
    )

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        if self.error:
            return [TextContent(text=f"Error: {self.error}")]

        content: list[TextContent | ImageContent] = [
            TextContent(text=maybe_truncate(self.output, MAX_BROWSER_OUTPUT_SIZE))
        ]

        if self.screenshot_data:
            mime_type = "image/png"
            if self.screenshot_data.startswith("/9j/"):
                mime_type = "image/jpeg"
            elif self.screenshot_data.startswith("iVBORw0KGgo"):
                mime_type = "image/png"
            elif self.screenshot_data.startswith("R0lGODlh"):
                mime_type = "image/gif"
            elif self.screenshot_data.startswith("UklGR"):
                mime_type = "image/webp"
            # Convert base64 to data URL format for ImageContent
            data_url = f"data:{mime_type};base64,{self.screenshot_data}"
            content.append(ImageContent(image_urls=[data_url]))

        return content


# ============================================
# Base Browser Action
# ============================================
class BrowserAction(Action):
    """Base class for all browser actions.

    This base class serves as the parent for all browser-related actions,
    enabling proper type hierarchy and eliminating the need for union types.
    """

    pass


# ============================================
# `go_to_url`
# ============================================
class BrowserNavigateAction(BrowserAction):
    """Schema for browser navigation."""

    url: str = Field(description="The URL to navigate to")
    new_tab: bool = Field(
        default=False, description="Whether to open in a new tab. Default: False"
    )


BROWSER_NAVIGATE_DESCRIPTION = """Navigate to a URL in the browser.

This tool allows you to navigate to any web page. You can optionally open the URL in a new tab.

Parameters:
- url: The URL to navigate to (required)
- new_tab: Whether to open in a new tab (optional, default: False)

Examples:
- Navigate to Google: url="https://www.google.com"
- Open GitHub in new tab: url="https://github.com", new_tab=True
"""  # noqa: E501


class BrowserNavigateTool(ToolDefinition[BrowserNavigateAction, BrowserObservation]):
    """Tool for browser navigation."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
                name="browser_navigate",
                description=BROWSER_NAVIGATE_DESCRIPTION,
                action_type=BrowserNavigateAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_navigate",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                executor=executor,
            )
        ]


# ============================================
# `browser_click`
# ============================================
class BrowserClickAction(BrowserAction):
    """Schema for clicking elements."""

    index: int = Field(
        ge=0, description="The index of the element to click (from browser_get_state)"
    )
    new_tab: bool = Field(
        default=False,
        description="Whether to open any resulting navigation in a new tab. Default: False",  # noqa: E501
    )


BROWSER_CLICK_DESCRIPTION = """Click an element on the page by its index.

Use this tool to click on interactive elements like buttons, links, or form controls. 
The index comes from the browser_get_state tool output.

Parameters:
- index: The index of the element to click (from browser_get_state)
- new_tab: Whether to open any resulting navigation in a new tab (optional)

Important: Only use indices that appear in your current browser_get_state output.
"""  # noqa: E501


class BrowserClickTool(ToolDefinition[BrowserClickAction, BrowserObservation]):
    """Tool for clicking browser elements."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
                name="browser_click",
                description=BROWSER_CLICK_DESCRIPTION,
                action_type=BrowserClickAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_click",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                executor=executor,
            )
        ]


# ============================================
# `browser_type`
# ============================================
class BrowserTypeAction(BrowserAction):
    """Schema for typing text into elements."""

    index: int = Field(
        ge=0, description="The index of the input element (from browser_get_state)"
    )
    text: str = Field(description="The text to type")


BROWSER_TYPE_DESCRIPTION = """Type text into an input field.

Use this tool to enter text into form fields, search boxes, or other text input elements.
The index comes from the browser_get_state tool output.

Parameters:
- index: The index of the input element (from browser_get_state)
- text: The text to type

Important: Only use indices that appear in your current browser_get_state output.
"""  # noqa: E501


class BrowserTypeTool(ToolDefinition[BrowserTypeAction, BrowserObservation]):
    """Tool for typing text into browser elements."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
                name="browser_type",
                description=BROWSER_TYPE_DESCRIPTION,
                action_type=BrowserTypeAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_type",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                executor=executor,
            )
        ]


# ============================================
# `browser_get_state`
# ============================================
class BrowserGetStateAction(BrowserAction):
    """Schema for getting browser state."""

    include_screenshot: bool = Field(
        default=False,
        description="Whether to include a screenshot of the current page. Default: False",  # noqa: E501
    )


BROWSER_GET_STATE_DESCRIPTION = """Get the current state of the page including all interactive elements.

This tool returns the current page content with numbered interactive elements that you can 
click or type into. Use this frequently to understand what's available on the page.

Parameters:
- include_screenshot: Whether to include a screenshot (optional, default: False)
"""  # noqa: E501


class BrowserGetStateTool(ToolDefinition[BrowserGetStateAction, BrowserObservation]):
    """Tool for getting browser state."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
                name="browser_get_state",
                description=BROWSER_GET_STATE_DESCRIPTION,
                action_type=BrowserGetStateAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_get_state",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=True,
                ),
                executor=executor,
            )
        ]


# ============================================
# `browser_get_content`
# ============================================
class BrowserGetContentAction(BrowserAction):
    """Schema for getting page content in markdown."""

    extract_links: bool = Field(
        default=False,
        description="Whether to include links in the content (default: False)",
    )
    start_from_char: int = Field(
        default=0,
        ge=0,
        description="Character index to start from in the page content (default: 0)",
    )


BROWSER_GET_CONTENT_DESCRIPTION = """Extract the main content of the current page in clean markdown format. It has been filtered to remove noise and advertising content.

If the content was truncated and you need more information, use start_from_char parameter to continue from where truncation occurred.
"""  # noqa: E501


class BrowserGetContentTool(
    ToolDefinition[BrowserGetContentAction, BrowserObservation]
):
    """Tool for getting page content in markdown."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
                name="browser_get_content",
                description=BROWSER_GET_CONTENT_DESCRIPTION,
                action_type=BrowserGetContentAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_get_content",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=True,
                ),
                executor=executor,
            )
        ]


# ============================================
# `browser_scroll`
# ============================================
class BrowserScrollAction(BrowserAction):
    """Schema for scrolling the page."""

    direction: Literal["up", "down"] = Field(
        default="down",
        description="Direction to scroll. Options: 'up', 'down'. Default: 'down'",
    )


BROWSER_SCROLL_DESCRIPTION = """Scroll the page up or down.

Use this tool to scroll through page content when elements are not visible or when you need
to see more content.

Parameters:
- direction: Direction to scroll - "up" or "down" (optional, default: "down")
"""  # noqa: E501


class BrowserScrollTool(ToolDefinition[BrowserScrollAction, BrowserObservation]):
    """Tool for scrolling the browser page."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
                name="browser_scroll",
                description=BROWSER_SCROLL_DESCRIPTION,
                action_type=BrowserScrollAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_scroll",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                executor=executor,
            )
        ]


# ============================================
# `browser_go_back`
# ============================================
class BrowserGoBackAction(BrowserAction):
    """Schema for going back in browser history."""

    pass


BROWSER_GO_BACK_DESCRIPTION = """Go back to the previous page in browser history.

Use this tool to navigate back to the previously visited page, similar to clicking the 
browser's back button.
"""  # noqa: E501


class BrowserGoBackTool(ToolDefinition[BrowserGoBackAction, BrowserObservation]):
    """Tool for going back in browser history."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
                name="browser_go_back",
                description=BROWSER_GO_BACK_DESCRIPTION,
                action_type=BrowserGoBackAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_go_back",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                executor=executor,
            )
        ]


# ============================================
# `browser_list_tabs`
# ============================================
class BrowserListTabsAction(BrowserAction):
    """Schema for listing browser tabs."""

    pass


BROWSER_LIST_TABS_DESCRIPTION = """List all open browser tabs.

This tool shows all currently open tabs with their IDs, titles, and URLs. Use the tab IDs
with browser_switch_tab or browser_close_tab.
"""  # noqa: E501


class BrowserListTabsTool(ToolDefinition[BrowserListTabsAction, BrowserObservation]):
    """Tool for listing browser tabs."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
                name="browser_list_tabs",
                description=BROWSER_LIST_TABS_DESCRIPTION,
                action_type=BrowserListTabsAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_list_tabs",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


# ============================================
# `browser_switch_tab`
# ============================================
class BrowserSwitchTabAction(BrowserAction):
    """Schema for switching browser tabs."""

    tab_id: str = Field(
        description="4 Character Tab ID of the tab to switch"
        + " to (from browser_list_tabs)"
    )


BROWSER_SWITCH_TAB_DESCRIPTION = """Switch to a different browser tab.

Use this tool to switch between open tabs. Get the tab_id from browser_list_tabs.

Parameters:
- tab_id: 4 Character Tab ID of the tab to switch to
"""


class BrowserSwitchTabTool(ToolDefinition[BrowserSwitchTabAction, BrowserObservation]):
    """Tool for switching browser tabs."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
                name="browser_switch_tab",
                description=BROWSER_SWITCH_TAB_DESCRIPTION,
                action_type=BrowserSwitchTabAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_switch_tab",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


# ============================================
# `browser_close_tab`
# ============================================
class BrowserCloseTabAction(BrowserAction):
    """Schema for closing browser tabs."""

    tab_id: str = Field(
        description="4 Character Tab ID of the tab to close (from browser_list_tabs)"
    )


BROWSER_CLOSE_TAB_DESCRIPTION = """Close a specific browser tab.

Use this tool to close tabs you no longer need. Get the tab_id from browser_list_tabs.

Parameters:
- tab_id: 4 Character Tab ID of the tab to close
"""


class BrowserCloseTabTool(ToolDefinition[BrowserCloseTabAction, BrowserObservation]):
    """Tool for closing browser tabs."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
                name="browser_close_tab",
                description=BROWSER_CLOSE_TAB_DESCRIPTION,
                action_type=BrowserCloseTabAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_close_tab",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


class BrowserToolSet(ToolDefinition[BrowserAction, BrowserObservation]):
    """A set of all browser tools.

    This tool set includes all available browser-related tools
      for interacting with web pages.

    The toolset automatically checks for Chromium availability
    when created and automatically installs it if missing.
    """

    @classmethod
    def create(
        cls,
        **executor_config,
    ) -> list[ToolDefinition[BrowserAction, BrowserObservation]]:
        # Import executor only when actually needed to
        # avoid hanging during module import
        from openhands.tools.browser_use.impl import BrowserToolExecutor

        executor = BrowserToolExecutor(**executor_config)
        # Each tool.create() returns a Sequence[Self], so we flatten the results
        tools: list[ToolDefinition[BrowserAction, BrowserObservation]] = []
        for tool_class in [
            BrowserNavigateTool,
            BrowserClickTool,
            BrowserGetStateTool,
            BrowserGetContentTool,
            BrowserTypeTool,
            BrowserScrollTool,
            BrowserGoBackTool,
            BrowserListTabsTool,
            BrowserSwitchTabTool,
            BrowserCloseTabTool,
        ]:
            tools.extend(tool_class.create(executor))
        return tools
