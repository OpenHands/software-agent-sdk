"""MCP-related exceptions for OpenHands SDK."""


class MCPError(Exception):
    """Base exception for MCP-related errors."""

    pass


class MCPInitializationError(MCPError):
    """Exception raised when MCP initialization fails.

    This exception is raised during MCP initialization when:
    - MCP config serialization fails (e.g., Pydantic models not converted to dicts)
    - MCP config validation fails
    - Variable expansion in MCP config fails
    - MCP server connection fails

    CLI applications can catch this to offer graceful degradation,
    e.g., retrying without MCP servers.
    """

    def __init__(self, message: str, cause: Exception | None = None):
        self.cause = cause
        super().__init__(message)


class MCPTimeoutError(MCPError):
    """Exception raised when MCP operations timeout."""

    timeout: float
    config: dict | None

    def __init__(self, message: str, timeout: float, config: dict | None = None):
        self.timeout = timeout
        self.config = config
        super().__init__(message)
