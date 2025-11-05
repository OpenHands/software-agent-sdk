"""MCPTool definition and implementation."""

import json
from typing import Any

import mcp.types
from pydantic import Field
from rich.text import Text

from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import (
    Observation,
)
from openhands.sdk.tool.schema import Action
from openhands.sdk.utils.visualize import display_dict


logger = get_logger(__name__)


# NOTE: We don't define MCPToolAction because it
# will be dynamically created from the MCP tool schema.


class MCPToolAction(Action):
    """Schema for MCP input action.

    It is just a thin wrapper around raw JSON and does
    not do any validation.

    Validation will be performed by MCPTool.__call__
    by constructing dynamically created Pydantic model
    from the MCP tool input schema.
    """

    data: dict[str, Any] = Field(
        default_factory=dict, description="Dynamic data fields from the tool call"
    )

    def to_mcp_arguments(self) -> dict:
        """Return the data field as MCP tool call arguments.

        This is used to convert this action to MCP tool call arguments.
        The data field contains the dynamic fields from the tool call.
        """
        return self.data


class MCPToolObservation(Observation):
    """Observation from MCP tool execution."""

    tool_name: str = Field(description="Name of the tool that was called")

    @classmethod
    def from_call_tool_result(
        cls, tool_name: str, result: mcp.types.CallToolResult
    ) -> "MCPToolObservation":
        """Create an MCPToolObservation from a CallToolResult."""
        content: list[mcp.types.ContentBlock] = result.content
        converted_content: list[TextContent | ImageContent] = []

        for block in content:
            if isinstance(block, mcp.types.TextContent):
                converted_content.append(TextContent(text=block.text))
            elif isinstance(block, mcp.types.ImageContent):
                converted_content.append(
                    ImageContent(
                        image_urls=[f"data:{block.mimeType};base64,{block.data}"],
                    )
                )
            else:
                logger.warning(
                    f"Unsupported MCP content block type: {type(block)}. Ignoring."
                )

        # Build initial message
        initial_message = f"[Tool '{tool_name}' executed.]"

        # Prepend initial message to content
        content_with_header = [TextContent(text=initial_message)] + converted_content

        return cls(
            content=content_with_header,
            is_error=result.isError,
            tool_name=tool_name,
        )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this observation."""
        content_obj = Text()
        content_obj.append(f"[MCP Tool '{self.tool_name}' Observation]\n", style="bold")
        if self.is_error:
            content_obj.append("[Error during execution]\n", style="bold red")
        for block in self.content:
            if isinstance(block, TextContent):
                # try to see if block.text is a JSON
                try:
                    parsed = json.loads(block.text)
                    content_obj.append(display_dict(parsed))
                    continue
                except (json.JSONDecodeError, TypeError):
                    content_obj.append(block.text + "\n")
            elif isinstance(block, ImageContent):
                content_obj.append(f"[Image with {len(block.image_urls)} URLs]\n")
        return content_obj
