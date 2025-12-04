"""The browser_use server reconfigures logging for ALL loggers on import,
overwriting any custom configuration we may have applied.

We have submitted a patch which should allow us to circumvent this problematic
behavior: https://github.com/browser-use/browser-use/pull/3717

In the meantime, using this script rather than a direct import means that
logging will still work in the agent server."""

import logging

import browser_use.mcp.server


def _noop(*args, **kwargs):
    """No-op replacement for functions"""


# Monkey patch
browser_use.mcp.server._configure_mcp_server_logging = _noop
browser_use.mcp.server._ensure_all_loggers_use_stderr = _noop
_orig_disable = logging.disable
_orig_basic_config = logging.basicConfig
logging.disable = _noop
logging.basicConfig = _noop
try:
    from browser_use.mcp.server import BrowserUseServer  # noqa: E402
finally:
    # Restore logging after import
    logging.disable = _orig_disable
    logging.basicConfig = _orig_basic_config


LogSafeBrowserUseServer = BrowserUseServer
