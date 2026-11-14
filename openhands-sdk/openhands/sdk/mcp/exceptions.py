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


class MCPServerHTTPError(MCPError):
    """An MCP server answered with an HTTP error status.

    The message names the status, the server's origin and the reason the
    server gave, which the MCP client otherwise reduces to a stand-in such as
    ``Server returned an error response`` or ``Session terminated``.
    """

    status_code: int
    url: str
    reason: str | None

    def __init__(
        self, status_code: int, reason_phrase: str, url: str, reason: str | None
    ):
        self.status_code = status_code
        self.url = url
        self.reason = reason
        message = f"HTTP {status_code} {reason_phrase} from {url}"
        super().__init__(f"{message}: {reason}" if reason else message)
