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


class MCPServerStartError(MCPError):
    """Exception raised when an MCP server added at runtime fails to start.

    ``server_name`` names the server; the original error is the ``__cause__``.
    """

    server_name: str

    def __init__(self, server_name: str, message: str):
        self.server_name = server_name
        super().__init__(message)
