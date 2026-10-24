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


class MCPCapabilityNotSupportedError(MCPError):
    """Exception raised when a server does not implement a requested capability.

    Raised when an MCP server answers a resources or prompts request with a
    method-not-found error, meaning it does not advertise that capability.
    """

    server: str
    capability: str

    def __init__(self, server: str, capability: str):
        self.server = server
        self.capability = capability
        super().__init__(
            f"MCP server '{server}' does not support the '{capability}' capability."
        )
