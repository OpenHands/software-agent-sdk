"""Browser tools using browser-use integration."""

from openhands.tools.browser_use.definition import (
    BrowserClickAction,
    BrowserClickTool,
    BrowserCloseTabAction,
    BrowserCloseTabTool,
    BrowserGetContentAction,
    BrowserGetContentTool,
    BrowserGetStateAction,
    BrowserGetStateTool,
    BrowserGoBackAction,
    BrowserGoBackTool,
    BrowserListTabsAction,
    BrowserListTabsTool,
    BrowserNavigateTool,
    BrowserNavigateURLAction,
    BrowserObservation,
    BrowserScrollAction,
    BrowserScrollTool,
    BrowserSwitchTabAction,
    BrowserSwitchTabTool,
    BrowserToolSet,
    BrowserTypeTextAction,
    BrowserTypeTool,
)


__all__ = [
    # Tool classes
    "BrowserNavigateTool",
    "BrowserClickTool",
    "BrowserTypeTool",
    "BrowserGetStateTool",
    "BrowserGetContentTool",
    "BrowserScrollTool",
    "BrowserGoBackTool",
    "BrowserListTabsTool",
    "BrowserSwitchTabTool",
    "BrowserCloseTabTool",
    # Actions
    "BrowserNavigateURLAction",
    "BrowserClickAction",
    "BrowserTypeTextAction",
    "BrowserGetStateAction",
    "BrowserGetContentAction",
    "BrowserScrollAction",
    "BrowserGoBackAction",
    "BrowserListTabsAction",
    "BrowserSwitchTabAction",
    "BrowserCloseTabAction",
    # Observations
    "BrowserObservation",
    "BrowserToolSet",
]
