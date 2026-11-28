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
from openhands.sdk.utils.visualize import display_json


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
    structured_content: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured_content payload returned by the MCP tool.",
    )
    mcp_content_blocks: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Lossless serialized MCP content blocks from CallToolResult.",
    )

    @classmethod
    def from_call_tool_result(
        cls, tool_name: str, result: mcp.types.CallToolResult
    ) -> "MCPToolObservation":
        """Create an MCPToolObservation from a CallToolResult."""

        native_content: list[mcp.types.ContentBlock] = result.content
        raw_blocks = [
            block.model_dump(mode="json", by_alias=True, exclude_none=True)
            for block in native_content
        ]
        content: list[TextContent | ImageContent] = [
            TextContent(text=f"[Tool '{tool_name}' executed.]")
        ]
        for block in native_content:
            if isinstance(block, mcp.types.TextContent):
                content.append(TextContent(text=block.text))
            elif isinstance(block, mcp.types.ImageContent):
                content.append(
                    ImageContent(
                        image_urls=[f"data:{block.mime_type};base64,{block.data}"],
                    )
                )
            elif isinstance(block, mcp.types.EmbeddedResource):
                res = block.resource
                uri = str(getattr(res, "uri", ""))
                if hasattr(res, "text"):
                    content.append(TextContent(text=f"[Resource {uri}]\n{res.text}"))
                elif hasattr(res, "blob"):
                    mime = getattr(res, "mime_type", None) or "application/octet-stream"
                    content.append(
                        TextContent(
                            text=f"[Binary Resource {uri} ({mime}) base64={res.blob}]"
                        )
                    )
            else:
                content.append(
                    TextContent(
                        text=json.dumps(
                            block.model_dump(
                                mode="json", by_alias=True, exclude_none=True
                            )
                        )
                    )
                )

        if "structured_content" in type(result).model_fields:
            structured = result.structured_content
        else:
            structured = getattr(result, "structuredContent", None)
        if structured is not None and not native_content:
            content.append(TextContent(text=json.dumps(structured)))

        return cls(
            content=content,
            is_error=result.is_error,
            tool_name=tool_name,
            structured_content=structured,
            mcp_content_blocks=raw_blocks,
        )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this observation."""
        text = Text()

        if self.is_error:
            text.append("❌ ", style="red bold")
            text.append(self.ERROR_MESSAGE_HEADER, style="bold red")

        text.append(f"[MCP Tool '{self.tool_name}' Observation]\n", style="bold")
        for block in self.content:
            if isinstance(block, TextContent):
                # try to see if block.text is a JSON
                try:
                    parsed = json.loads(block.text)
                    text.append(display_json(parsed))
                    continue
                except (json.JSONDecodeError, TypeError):
                    text.append(block.text + "\n")
            elif isinstance(block, ImageContent):
                text.append(f"[Image with {len(block.image_urls)} URLs]\n")
        return text
