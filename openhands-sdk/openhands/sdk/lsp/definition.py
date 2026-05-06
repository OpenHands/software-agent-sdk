"""LSP Tool action and observation schemas."""

from collections.abc import Sequence
from enum import Enum
from typing import Any, ClassVar
from urllib.parse import unquote, urlparse

from pydantic import Field

from openhands.sdk.llm import TextContent
from openhands.sdk.tool.schema import Action, Observation


class LSPOperation(str, Enum):
    """Supported LSP operations."""

    DEFINITION = "definition"
    REFERENCES = "references"
    HOVER = "hover"


class LSPToolAction(Action):
    """Action schema for LSP tool operations.

    Provides code intelligence capabilities via Language Server Protocol.
    """

    operation: LSPOperation = Field(
        description="The LSP operation to perform: "
        "'definition' to go to symbol definition, "
        "'references' to find all references, "
        "'hover' to get documentation/type info"
    )
    file_path: str = Field(
        description="Absolute path to the file to query"
    )
    line: int = Field(
        description="Line number (1-indexed, first line is 1)"
    )
    character: int = Field(
        description="Character position in the line (0-indexed)"
    )
    include_declaration: bool = Field(
        default=True,
        description="For 'references' operation: whether to include the declaration "
        "location in the results"
    )


class LSPToolObservation(Observation):
    """Observation from LSP tool operations.

    Contains formatted results from LSP queries.
    """

    ERROR_MESSAGE_HEADER: ClassVar[str] = "[LSP Error]\n"

    operation: LSPOperation = Field(description="The operation that was performed")
    file_path: str = Field(description="File that was queried")
    locations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of locations (for definition/references)"
    )
    hover_content: str | None = Field(
        default=None,
        description="Hover documentation content"
    )

    @classmethod
    def from_locations(
        cls,
        operation: LSPOperation,
        file_path: str,
        locations: list[dict[str, Any]],
    ) -> "LSPToolObservation":
        """Create observation from location results (definition/references).

        Args:
            operation: The LSP operation performed
            file_path: The file that was queried
            locations: List of location objects with uri and range

        Returns:
            Formatted observation
        """
        if not locations:
            text = (
                f"No {operation.value} found for the symbol at the specified position."
            )
            return cls(
                content=[TextContent(text=text)],
                operation=operation,
                file_path=file_path,
                locations=[],
            )

        # Format locations for display
        formatted_locations = []
        for loc in locations:
            uri = loc.get("uri", "")
            range_info = loc.get("range", {})
            start = range_info.get("start", {})
            end = range_info.get("end", {})

            # Convert URI to path
            loc_path = _uri_to_path(uri)
            start_line = start.get("line", 0) + 1  # Convert to 1-indexed
            start_char = start.get("character", 0)
            end_line = end.get("line", 0) + 1
            end_char = end.get("character", 0)

            formatted_locations.append({
                "path": loc_path,
                "start_line": start_line,
                "start_character": start_char,
                "end_line": end_line,
                "end_character": end_char,
            })

        # Build text output
        lines = [f"Found {len(formatted_locations)} {operation.value}(s):"]
        for i, loc in enumerate(formatted_locations, 1):
            lines.append(
                f"  {i}. {loc['path']}:{loc['start_line']}:{loc['start_character']}"
            )

        text = "\n".join(lines)
        return cls(
            content=[TextContent(text=text)],
            operation=operation,
            file_path=file_path,
            locations=formatted_locations,
        )

    @classmethod
    def from_hover(
        cls,
        file_path: str,
        hover_result: dict[str, Any] | None,
    ) -> "LSPToolObservation":
        """Create observation from hover result.

        Args:
            file_path: The file that was queried
            hover_result: Hover response with contents field

        Returns:
            Formatted observation with hover documentation
        """
        if hover_result is None:
            text = "No hover information available at the specified position."
            return cls(
                content=[TextContent(text=text)],
                operation=LSPOperation.HOVER,
                file_path=file_path,
                hover_content=None,
            )

        # Extract hover contents
        contents = hover_result.get("contents", "")
        hover_text = _extract_hover_content(contents)

        if not hover_text:
            text = "No hover information available at the specified position."
            return cls(
                content=[TextContent(text=text)],
                operation=LSPOperation.HOVER,
                file_path=file_path,
                hover_content=None,
            )

        return cls(
            content=[TextContent(text=hover_text)],
            operation=LSPOperation.HOVER,
            file_path=file_path,
            hover_content=hover_text,
        )

    @classmethod
    def from_error(
        cls,
        operation: LSPOperation,
        file_path: str,
        error_message: str,
    ) -> "LSPToolObservation":
        """Create error observation.

        Args:
            operation: The LSP operation that failed
            file_path: The file that was queried
            error_message: Error description

        Returns:
            Error observation
        """
        return cls(
            content=[TextContent(text=error_message)],
            is_error=True,
            operation=operation,
            file_path=file_path,
        )

    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        """Format observation for LLM consumption."""
        llm_content: list[TextContent] = []

        if self.is_error:
            llm_content.append(TextContent(text=self.ERROR_MESSAGE_HEADER))

        llm_content.extend(
            item for item in self.content if isinstance(item, TextContent)
        )

        return llm_content


def _uri_to_path(uri: str) -> str:
    """Convert a file:// URI to a file path."""
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        return unquote(parsed.path)
    return uri


def _extract_hover_content(contents: Any) -> str:
    """Extract text from LSP hover contents.

    The contents can be:
    - A string
    - A MarkupContent object {"kind": "markdown"|"plaintext", "value": "..."}
    - A MarkedString {"language": "...", "value": "..."}
    - A list of the above
    """
    if isinstance(contents, str):
        return contents

    if isinstance(contents, dict):
        # MarkupContent or MarkedString
        if "value" in contents:
            value = contents["value"]
            language = contents.get("language", "")
            if language:
                return f"```{language}\n{value}\n```"
            return value
        return ""

    if isinstance(contents, list):
        # List of content items
        parts = []
        for item in contents:
            part = _extract_hover_content(item)
            if part:
                parts.append(part)
        return "\n\n".join(parts)

    return ""
