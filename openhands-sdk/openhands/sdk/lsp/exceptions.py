"""Exceptions for LSP-related errors."""

from typing import Any


class LSPError(Exception):
    """Base exception for LSP-related errors."""

    pass


class LSPTimeoutError(LSPError):
    """Exception raised when LSP operations timeout."""

    def __init__(
        self,
        message: str,
        timeout: float,
        server_name: str | None = None,
        operation: str | None = None,
    ):
        self.timeout = timeout
        self.server_name = server_name
        self.operation = operation
        super().__init__(message)


class LSPServerError(LSPError):
    """Exception raised when LSP server returns an error response."""

    def __init__(
        self,
        message: str,
        code: int,
        data: Any | None = None,
    ):
        self.code = code
        self.data = data
        super().__init__(message)


class LSPServerNotFoundError(LSPError):
    """Exception raised when no LSP server is configured for a file type."""

    def __init__(self, file_path: str, extension: str):
        self.file_path = file_path
        self.extension = extension
        super().__init__(
            f"No LSP server configured for file extension '{extension}': {file_path}"
        )


class LSPConnectionError(LSPError):
    """Exception raised when LSP server connection fails."""

    def __init__(self, message: str, server_name: str | None = None):
        self.server_name = server_name
        super().__init__(message)
