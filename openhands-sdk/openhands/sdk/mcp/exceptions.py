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


class MCPConnectionError(MCPError):
    """Exception raised when an MCP server connection fails."""

    server_name: str | None
    url: str | None
    config: dict | None

    def __init__(
        self,
        message: str,
        *,
        server_name: str | None = None,
        url: str | None = None,
        config: dict | None = None,
    ):
        self.server_name = server_name
        self.url = url
        self.config = config
        super().__init__(message)

