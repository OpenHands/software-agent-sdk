"""Browser-use tool implementation for web automation."""

import base64
import hashlib
import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self

from pydantic import Field

from openhands.sdk.context.prompts import render_template
from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    register_tool,
)
from openhands.sdk.utils import DEFAULT_TEXT_CONTENT_LIMIT, maybe_truncate


# Lazy import to avoid hanging during module import
if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState
    from openhands.tools.browser_use.impl import BrowserToolExecutor


# Directory where browser session recordings are saved
BROWSER_RECORDING_OUTPUT_DIR = os.path.join(".agent_tmp", "browser_observations")
PROMPT_DIR = Path(__file__).parent / "templates"

# Mapping of base64 prefixes to MIME types for image detection
BASE64_IMAGE_PREFIXES = {
    "/9j/": "image/jpeg",
    "iVBORw0KGgo": "image/png",
    "R0lGODlh": "image/gif",
    "UklGR": "image/webp",
}


def detect_image_mime_type(base64_data: str) -> str:
    """Detect MIME type from base64-encoded image data.

    Args:
        base64_data: Base64-encoded image data

    Returns:
        Detected MIME type, defaults to "image/png" if not detected
    """
    for prefix, mime_type in BASE64_IMAGE_PREFIXES.items():
        if base64_data.startswith(prefix):
            return mime_type
    return "image/png"


class BrowserObservation(Observation):
    """Base observation for browser operations."""

    screenshot_data: str | None = Field(
        default=None, description="Base64 screenshot data if available"
    )
    full_output_save_dir: str | None = Field(
        default=None,
        description="Directory where full output files are saved",
    )

    def _save_screenshot(self, base64_data: str, save_dir: str) -> str | None:
        try:
            save_dir_path = Path(save_dir)
            save_dir_path.mkdir(parents=True, exist_ok=True)

            mime_type = detect_image_mime_type(base64_data)
            ext = mime_type.split("/")[-1]
            if ext == "jpeg":
                ext = "jpg"

            # Generate hash for filename
            content_hash = hashlib.sha256(base64_data.encode("utf-8")).hexdigest()[:8]
            filename = f"browser_screenshot_{content_hash}.{ext}"
            file_path = save_dir_path / filename

            if not file_path.exists():
                image_data = base64.b64decode(base64_data)
                file_path.write_bytes(image_data)

            return str(file_path)
        except Exception:
            return None

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        llm_content: list[TextContent | ImageContent] = []

        # If is_error is true, prepend error message
        if self.is_error:
            llm_content.append(TextContent(text=self.ERROR_MESSAGE_HEADER))

        # Get text content and truncate if needed
        content_text = self.text
        if content_text:
            llm_content.append(
                TextContent(
                    text=maybe_truncate(
                        content=content_text,
                        truncate_after=DEFAULT_TEXT_CONTENT_LIMIT,
                        save_dir=self.full_output_save_dir,
                        tool_prefix="browser",
                    )
                )
            )

        if self.screenshot_data:
            mime_type = detect_image_mime_type(self.screenshot_data)

            # Save screenshot if directory is available
            if self.full_output_save_dir:
                saved_path = self._save_screenshot(
                    self.screenshot_data, self.full_output_save_dir
                )
                if saved_path:
                    llm_content.append(
                        TextContent(text=f"Screenshot saved to: {saved_path}")
                    )

            # Convert base64 to data URL format for ImageContent
            data_url = f"data:{mime_type};base64,{self.screenshot_data}"
            llm_content.append(ImageContent(image_urls=[data_url]))

        return llm_content


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


BROWSER_NAVIGATE_DESCRIPTION = render_template(
    prompt_dir=str(PROMPT_DIR),
    template_name="browser_navigate_description.j2",
)


class BrowserNavigateTool(ToolDefinition[BrowserNavigateAction, BrowserObservation]):
    """Tool for browser navigation."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
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


BROWSER_CLICK_DESCRIPTION = render_template(
    prompt_dir=str(PROMPT_DIR),
    template_name="browser_click_description.j2",
)


class BrowserClickTool(ToolDefinition[BrowserClickAction, BrowserObservation]):
    """Tool for clicking browser elements."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
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


BROWSER_TYPE_DESCRIPTION = render_template(
    prompt_dir=str(PROMPT_DIR),
    template_name="browser_type_description.j2",
)


class BrowserTypeTool(ToolDefinition[BrowserTypeAction, BrowserObservation]):
    """Tool for typing text into browser elements."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
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


BROWSER_GET_STATE_DESCRIPTION = render_template(
    prompt_dir=str(PROMPT_DIR),
    template_name="browser_get_state_description.j2",
)


class BrowserGetStateTool(ToolDefinition[BrowserGetStateAction, BrowserObservation]):
    """Tool for getting browser state."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
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


BROWSER_GET_CONTENT_DESCRIPTION = render_template(
    prompt_dir=str(PROMPT_DIR),
    template_name="browser_get_content_description.j2",
)


class BrowserGetContentTool(
    ToolDefinition[BrowserGetContentAction, BrowserObservation]
):
    """Tool for getting page content in markdown."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
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


BROWSER_SCROLL_DESCRIPTION = render_template(
    prompt_dir=str(PROMPT_DIR),
    template_name="browser_scroll_description.j2",
)


class BrowserScrollTool(ToolDefinition[BrowserScrollAction, BrowserObservation]):
    """Tool for scrolling the browser page."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
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


BROWSER_GO_BACK_DESCRIPTION = render_template(
    prompt_dir=str(PROMPT_DIR),
    template_name="browser_go_back_description.j2",
)


class BrowserGoBackTool(ToolDefinition[BrowserGoBackAction, BrowserObservation]):
    """Tool for going back in browser history."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
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


BROWSER_LIST_TABS_DESCRIPTION = render_template(
    prompt_dir=str(PROMPT_DIR),
    template_name="browser_list_tabs_description.j2",
)


class BrowserListTabsTool(ToolDefinition[BrowserListTabsAction, BrowserObservation]):
    """Tool for listing browser tabs."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
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


BROWSER_SWITCH_TAB_DESCRIPTION = render_template(
    prompt_dir=str(PROMPT_DIR),
    template_name="browser_switch_tab_description.j2",
)


class BrowserSwitchTabTool(ToolDefinition[BrowserSwitchTabAction, BrowserObservation]):
    """Tool for switching browser tabs."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
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


BROWSER_CLOSE_TAB_DESCRIPTION = render_template(
    prompt_dir=str(PROMPT_DIR),
    template_name="browser_close_tab_description.j2",
)


class BrowserCloseTabTool(ToolDefinition[BrowserCloseTabAction, BrowserObservation]):
    """Tool for closing browser tabs."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
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


# ============================================
# `browser_get_storage`
# ============================================
class BrowserGetStorageAction(BrowserAction):
    """Schema for getting browser storage (cookies, local storage, session storage)."""

    pass


BROWSER_GET_STORAGE_DESCRIPTION = render_template(
    prompt_dir=str(PROMPT_DIR),
    template_name="browser_get_storage_description.j2",
)


class BrowserGetStorageTool(
    ToolDefinition[BrowserGetStorageAction, BrowserObservation]
):
    """Tool for getting browser storage."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
                description=BROWSER_GET_STORAGE_DESCRIPTION,
                action_type=BrowserGetStorageAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_get_storage",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


# ============================================
# `browser_set_storage`
# ============================================
class BrowserSetStorageAction(BrowserAction):
    """Schema for setting browser storage (cookies, local storage, session storage)."""

    storage_state: dict = Field(
        description="Storage state dictionary containing 'cookies' and 'origins' (from browser_get_storage)"  # noqa: E501
    )


BROWSER_SET_STORAGE_DESCRIPTION = render_template(
    prompt_dir=str(PROMPT_DIR),
    template_name="browser_set_storage_description.j2",
)


class BrowserSetStorageTool(
    ToolDefinition[BrowserSetStorageAction, BrowserObservation]
):
    """Tool for setting browser storage."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
                description=BROWSER_SET_STORAGE_DESCRIPTION,
                action_type=BrowserSetStorageAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_set_storage",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


# ============================================
# `browser_start_recording`
# ============================================
class BrowserStartRecordingAction(BrowserAction):
    """Schema for starting browser session recording."""

    pass


BROWSER_START_RECORDING_DESCRIPTION = render_template(
    prompt_dir=str(PROMPT_DIR),
    template_name="browser_start_recording_description.j2",
    recording_output_dir=BROWSER_RECORDING_OUTPUT_DIR,
)


class BrowserStartRecordingTool(
    ToolDefinition[BrowserStartRecordingAction, BrowserObservation]
):
    """Tool for starting browser session recording."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
                description=BROWSER_START_RECORDING_DESCRIPTION,
                action_type=BrowserStartRecordingAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_start_recording",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


# ============================================
# `browser_stop_recording`
# ============================================
class BrowserStopRecordingAction(BrowserAction):
    """Schema for stopping browser session recording."""

    pass


BROWSER_STOP_RECORDING_DESCRIPTION = render_template(
    prompt_dir=str(PROMPT_DIR),
    template_name="browser_stop_recording_description.j2",
    recording_output_dir=BROWSER_RECORDING_OUTPUT_DIR,
)


class BrowserStopRecordingTool(
    ToolDefinition[BrowserStopRecordingAction, BrowserObservation]
):
    """Tool for stopping browser session recording."""

    @classmethod
    def create(cls, executor: "BrowserToolExecutor") -> Sequence[Self]:
        return [
            cls(
                description=BROWSER_STOP_RECORDING_DESCRIPTION,
                action_type=BrowserStopRecordingAction,
                observation_type=BrowserObservation,
                annotations=ToolAnnotations(
                    title="browser_stop_recording",
                    # Modifies state: stops recording, flushes events to disk
                    readOnlyHint=False,
                    destructiveHint=False,
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
        conv_state: "ConversationState",
        **executor_config,
    ) -> list[ToolDefinition[BrowserAction, BrowserObservation]]:
        # Import executor only when actually needed to
        # avoid hanging during module import
        import sys

        # Use Windows-specific executor on Windows systems
        if sys.platform == "win32":
            from openhands.tools.browser_use.impl_windows import (
                WindowsBrowserToolExecutor,
            )

            executor = WindowsBrowserToolExecutor(
                full_output_save_dir=conv_state.env_observation_persistence_dir,
                **executor_config,
            )
        else:
            from openhands.tools.browser_use.impl import BrowserToolExecutor

            executor = BrowserToolExecutor(
                full_output_save_dir=conv_state.env_observation_persistence_dir,
                **executor_config,
            )

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
            BrowserGetStorageTool,
            BrowserSetStorageTool,
            BrowserStartRecordingTool,
            BrowserStopRecordingTool,
        ]:
            tools.extend(tool_class.create(executor))
        return tools


register_tool(BrowserToolSet.name, BrowserToolSet)
