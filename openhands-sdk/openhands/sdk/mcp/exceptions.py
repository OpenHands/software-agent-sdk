"""MCP-related exceptions for OpenHands SDK."""


class MCPError(Exception):
    """Base exception for MCP-related errors."""

    pass


class MCPTimeoutError(MCPError):
    """Exception raised when MCP operations timeout."""

    timeout: float
    config: dict | None

    def __init__(self, message: str, timeout: float, config: dict | None = None):
        self.timeout = timeout
        self.config = config
        super().__init__(message)


class MCPServerError(MCPError):
    """Exception raised when an individual MCP server fails to initialize.

    Contains details about which server failed and the underlying cause.
    """

    server_name: str
    cause: Exception | None

    def __init__(self, message: str, server_name: str, cause: Exception | None = None):
        self.server_name = server_name
        self.cause = cause
        super().__init__(message)
