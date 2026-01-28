"""LSP Tool definition and executor."""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openhands.sdk.logger import get_logger
from openhands.sdk.lsp.definition import (
    LSPOperation,
    LSPToolAction,
    LSPToolObservation,
)
from openhands.sdk.lsp.exceptions import (
    LSPConnectionError,
    LSPServerError,
    LSPServerNotFoundError,
    LSPTimeoutError,
)
from openhands.sdk.lsp.manager import LSPServerManager
from openhands.sdk.tool import ToolAnnotations, ToolDefinition, ToolExecutor


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation


logger = get_logger(__name__)


LSP_TOOL_DESCRIPTION = """Code intelligence tool using Language Server Protocol (LSP).

Provides capabilities to navigate and understand code:
- 'definition': Go to the definition of a symbol (function, class, variable)
- 'references': Find all references to a symbol across the codebase
- 'hover': Get documentation, type information, and other details about a symbol

Use this tool when you need to:
- Find where a function or class is defined
- Find all places where a symbol is used
- Get type information or documentation for a symbol

The tool supports multiple programming languages based on configured LSP servers.
"""


class LSPToolExecutor(ToolExecutor[LSPToolAction, LSPToolObservation]):
    """Executor for LSP tool operations."""

    def __init__(self, manager: LSPServerManager):
        """Initialize LSP tool executor.

        Args:
            manager: LSP server manager for handling server lifecycle
        """
        self._manager = manager

    def __call__(
        self,
        action: LSPToolAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> LSPToolObservation:
        """Execute an LSP operation.

        Args:
            action: The LSP action to execute
            conversation: Optional conversation context (unused)

        Returns:
            Observation with the LSP query results
        """
        file_path = action.file_path

        # Validate file exists
        if not Path(file_path).exists():
            return LSPToolObservation.from_error(
                operation=action.operation,
                file_path=file_path,
                error_message=f"File not found: {file_path}",
            )

        try:
            # Ensure document is open and get client
            client, language_id = self._manager.ensure_document_open(file_path)

            if client is None:
                # No LSP server for this file type
                ext = Path(file_path).suffix
                return LSPToolObservation.from_error(
                    operation=action.operation,
                    file_path=file_path,
                    error_message=f"No LSP server configured for file type '{ext}'. "
                    "Make sure an LSP server is configured with the appropriate "
                    "extensionToLanguage mapping.",
                )

            # Convert to URI and 0-indexed line
            uri = Path(file_path).as_uri()
            line = action.line - 1  # Convert from 1-indexed to 0-indexed
            character = action.character

            # Execute the appropriate operation
            match action.operation:
                case LSPOperation.DEFINITION:
                    return self._execute_definition(
                        client, action.operation, file_path, uri, line, character
                    )
                case LSPOperation.REFERENCES:
                    return self._execute_references(
                        client,
                        action.operation,
                        file_path,
                        uri,
                        line,
                        character,
                        action.include_declaration,
                    )
                case LSPOperation.HOVER:
                    return self._execute_hover(
                        client, action.operation, file_path, uri, line, character
                    )
                case _:
                    return LSPToolObservation.from_error(
                        operation=action.operation,
                        file_path=file_path,
                        error_message=f"Unsupported LSP operation: {action.operation}",
                    )

        except LSPServerNotFoundError as e:
            return LSPToolObservation.from_error(
                operation=action.operation,
                file_path=file_path,
                error_message=str(e),
            )
        except LSPTimeoutError as e:
            return LSPToolObservation.from_error(
                operation=action.operation,
                file_path=file_path,
                error_message=f"LSP request timed out after {e.timeout}s. "
                "The language server may be busy or unresponsive.",
            )
        except LSPServerError as e:
            return LSPToolObservation.from_error(
                operation=action.operation,
                file_path=file_path,
                error_message=f"LSP server error (code {e.code}): {e}",
            )
        except LSPConnectionError as e:
            return LSPToolObservation.from_error(
                operation=action.operation,
                file_path=file_path,
                error_message=f"LSP connection error: {e}",
            )
        except Exception as e:
            logger.exception(f"Unexpected error in LSP tool: {e}")
            return LSPToolObservation.from_error(
                operation=action.operation,
                file_path=file_path,
                error_message=f"Unexpected error: {e}",
            )

    def _execute_definition(
        self,
        client: Any,
        operation: LSPOperation,
        file_path: str,
        uri: str,
        line: int,
        character: int,
    ) -> LSPToolObservation:
        """Execute textDocument/definition request."""
        locations = client.call_sync(
            client.text_document_definition(uri, line, character)
        )
        return LSPToolObservation.from_locations(operation, file_path, locations)

    def _execute_references(
        self,
        client: Any,
        operation: LSPOperation,
        file_path: str,
        uri: str,
        line: int,
        character: int,
        include_declaration: bool,
    ) -> LSPToolObservation:
        """Execute textDocument/references request."""
        locations = client.call_sync(
            client.text_document_references(uri, line, character, include_declaration)
        )
        return LSPToolObservation.from_locations(operation, file_path, locations)

    def _execute_hover(
        self,
        client: Any,
        operation: LSPOperation,  # noqa: ARG002
        file_path: str,
        uri: str,
        line: int,
        character: int,
    ) -> LSPToolObservation:
        """Execute textDocument/hover request."""
        hover_result = client.call_sync(
            client.text_document_hover(uri, line, character)
        )
        return LSPToolObservation.from_hover(file_path, hover_result)

    def close(self) -> None:
        """Cleanup all LSP servers."""
        self._manager.close_all()


class LSPToolDefinition(ToolDefinition[LSPToolAction, LSPToolObservation]):
    """LSP Tool definition that provides code intelligence capabilities.

    This tool exposes LSP operations (definition, references, hover) to agents,
    enabling them to navigate and understand code more effectively.
    """

    name: str = "lsp"

    @classmethod
    def create(
        cls,
        lsp_config: dict[str, Any],
        workspace_root: str,
    ) -> Sequence["LSPToolDefinition"]:
        """Create LSP tool from configuration.

        Args:
            lsp_config: LSP configuration with server definitions
            workspace_root: Root directory of the workspace

        Returns:
            List containing single LSPToolDefinition instance
        """
        manager = LSPServerManager(lsp_config, workspace_root)
        executor = LSPToolExecutor(manager)

        tool = cls(
            description=LSP_TOOL_DESCRIPTION,
            action_type=LSPToolAction,
            observation_type=LSPToolObservation,
            executor=executor,
            annotations=ToolAnnotations(
                title="Language Server Protocol",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )

        return [tool]
