"""The browser_use server reconfigures logging for ALL loggers on import,
overwriting any custom configuration we may have applied.

We have submitted a patch which should allow us to circumvent this problematic
behavior: https://github.com/browser-use/browser-use/pull/3717

In the meantime, using this script rather than a direct import means that
logging will still work in the agent server."""

import logging
from dataclasses import dataclass, field

from openhands.sdk.utils.deprecation import warn_cleanup


warn_cleanup(
    "Monkey patching to prevent browser_use logging interference",
    cleanup_by="1.8.0",
    details=(
        "This workaround should be removed once browser_use PR #3717 "
        "(https://github.com/browser-use/browser-use/pull/3717) is merged "
        "and released. The upstream fix will allow bypassing the "
        "problematic logging configuration code."
    ),
)


def _noop(*args, **kwargs):
    """No-op replacement for functions"""


@dataclass
class _MockManager:
    loggerDict: dict[str, logging.Logger] = field(default_factory=dict)


@dataclass
class _MockRoot:
    handlers: list[logging.Handler] = field(default_factory=list)
    manager: _MockManager = field(default_factory=_MockManager)

    def __getattr__(self, name: str):
        return _noop


# Monkey patch before import
_orig_disable = logging.disable
_orig_basic_config = logging.basicConfig
_orig_root = logging.root
logging.disable = _noop
logging.basicConfig = _noop
logging.root = _MockRoot()
try:
    from browser_use.mcp import server  # noqa: E402
finally:
    # Restore logging after import
    logging.disable = _orig_disable
    logging.basicConfig = _orig_basic_config
    logging.root = _orig_root


# This gets called on each init - so make sure it's a noop
server._ensure_all_loggers_use_stderr = _noop

LogSafeBrowserUseServer = server.BrowserUseServer
