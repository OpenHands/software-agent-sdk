# simple_logger.py
"""
Minimal logger setup that encourages per-module loggers,
with Rich for humans and JSON for machines.

Usage:
    from openhands.sdk.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Hello from this module!")
"""

import atexit
import logging
import os
import select
from logging.handlers import TimedRotatingFileHandler

import litellm
from pythonjsonlogger.json import JsonFormatter
from rich.console import Console
from rich.logging import RichHandler


# ========= ENV (loaded at import) =========
LEVEL_MAP = (
    logging.getLevelNamesMapping()
    if hasattr(logging, "getLevelNamesMapping")
    else logging._nameToLevel
)

DEBUG = os.environ.get("DEBUG", "false").lower() in {"1", "true", "yes"}
ENV_LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()
ENV_LOG_LEVEL = LEVEL_MAP.get(ENV_LOG_LEVEL_STR, logging.INFO)
if DEBUG:
    ENV_LOG_LEVEL = logging.DEBUG

ENV_LOG_TO_FILE = os.getenv("LOG_TO_FILE", "false").lower() in {"1", "true", "yes"}
ENV_LOG_DIR = os.getenv("LOG_DIR", "logs")
ENV_ROTATE_WHEN = os.getenv("LOG_ROTATE_WHEN", "midnight")
ENV_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "7"))

# Rich vs JSON
ENV_JSON = os.getenv("LOG_JSON", "false").lower() in {"1", "true", "yes"}
IN_CI = os.getenv("CI", "false").lower() in {"1", "true", "yes"} or bool(
    os.environ.get("GITHUB_ACTIONS")
)
ENV_RICH_TRACEBACKS = os.getenv("LOG_RICH_TRACEBACKS", "true").lower() in {
    "1",
    "true",
    "yes",
}


ENV_AUTO_CONFIG = os.getenv("LOG_AUTO_CONFIG", "true").lower() in {"1", "true", "yes"}
ENV_DEBUG_LLM = os.getenv("DEBUG_LLM", "false").lower() in {"1", "true", "yes"}


# ========= LiteLLM controls =========
_ENABLE_LITELLM_DEBUG = False
if ENV_DEBUG_LLM:
    confirmation = input(
        "\n⚠️ WARNING: You are enabling DEBUG_LLM which may expose sensitive "
        "information like API keys.\nThis should NEVER be enabled in production.\n"
        "Type 'y' to confirm you understand the risks: "
    )
    if confirmation.lower() == "y":
        _ENABLE_LITELLM_DEBUG = True
        litellm.suppress_debug_info = False
        litellm.set_verbose = True  # type: ignore
    else:
        print("DEBUG_LLM disabled due to lack of confirmation")
        litellm.suppress_debug_info = True
        litellm.set_verbose = False  # type: ignore
else:
    litellm.suppress_debug_info = True
    litellm.set_verbose = False  # type: ignore


def disable_logger(name: str, level: int = logging.CRITICAL) -> None:
    """Disable or quiet down a specific logger by name."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False


# Quiet chatty third-party loggers
for name in ["litellm", "LiteLLM", "openai"]:
    disable_logger(name, logging.DEBUG if _ENABLE_LITELLM_DEBUG else logging.ERROR)
for name in ["httpcore", "httpx", "libtmux"]:
    disable_logger(name, logging.WARNING)


# ========= SETUP =========
def setup_logging(
    level: int | None = None,
    log_to_file: bool | None = None,
    log_dir: str | None = None,
    fmt: str | None = None,
    when: str | None = None,
    backup_count: int | None = None,
) -> None:
    """Configure the root logger. All child loggers inherit this setup."""
    lvl = ENV_LOG_LEVEL if level is None else level
    to_file = ENV_LOG_TO_FILE if log_to_file is None else log_to_file
    directory = ENV_LOG_DIR if log_dir is None else log_dir
    rotate_when = ENV_ROTATE_WHEN if when is None else when
    keep = ENV_BACKUP_COUNT if backup_count is None else backup_count

    root = logging.getLogger()
    old_level = root.level
    root.setLevel(lvl)

    # Set the level for any existing logger with the same intial level
    for logger in logging.root.manager.loggerDict.values():
        if isinstance(logger, logging.Logger) and logger.level == old_level:
            logger.setLevel(lvl)

    # Do NOT clear existing handlers; Uvicorn installs these before importing the app.
    # Only add ours if there isn't already a comparable stream handler.
    has_stream = any(isinstance(h, logging.StreamHandler) for h in root.handlers)

    if not has_stream:
        if ENV_JSON or IN_CI:
            # JSON console handler
            ch = logging.StreamHandler()
            ch.setLevel(lvl)
            ch.setFormatter(
                JsonFormatter(
                    fmt="%(asctime)s %(levelname)s %(name)s "
                    "%(filename)s %(lineno)d %(message)s"
                )
            )
            root.addHandler(ch)
        else:
            # Rich console handler
            rich_handler = RichHandler(
                console=Console(stderr=True),
                omit_repeated_times=False,
                rich_tracebacks=ENV_RICH_TRACEBACKS,
            )
            rich_handler.setFormatter(logging.Formatter("%(message)s"))
            rich_handler.setLevel(lvl)
            root.addHandler(rich_handler)

    if to_file:
        os.makedirs(directory, exist_ok=True)
        fh = TimedRotatingFileHandler(
            os.path.join(directory, "app.log"),
            when=rotate_when,
            backupCount=keep,
            encoding="utf-8",
        )
        fh.setLevel(lvl)
        if ENV_JSON:
            fh.setFormatter(
                JsonFormatter(
                    fmt="%(asctime)s %(levelname)s %(name)s "
                    "%(filename)s %(lineno)d %(message)s"
                )
            )
        else:
            log_fmt = (
                fmt
                or "%(asctime)s - %(levelname)s - %(name)s "
                "- %(filename)s:%(lineno)d - %(message)s"
            )
            fh.setFormatter(logging.Formatter(log_fmt))
        root.addHandler(fh)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for the specified module.

    This function returns a configured logger that inherits from the root logger
    setup. The logger supports both Rich formatting for human-readable output
    and JSON formatting for machine processing, depending on environment configuration.

    Args:
        name: The name of the module, typically __name__.

    Returns:
        A configured Logger instance.

    Example:
        >>> from openhands.sdk.logger import get_logger
        >>> logger = get_logger(__name__)
        >>> logger.info("This is an info message")
        >>> logger.error("This is an error message")
    """
    logger = logging.getLogger(name)
    logger.propagate = True
    return logger


# Auto-configure if desired
if ENV_AUTO_CONFIG:
    setup_logging()


# ========= TERMINAL CLEANUP =========
# Prevents ANSI escape code leaks during operation and at exit.
# See: https://github.com/OpenHands/software-agent-sdk/issues/2244
#
# The issue: Terminal queries (like DSR for cursor position) get responses
# written to stdin. If not consumed, these leak as garbage to the shell or
# corrupt the next input() call in CLI applications.

_cleanup_registered = False


def flush_stdin() -> int:
    """Flush any pending terminal query responses from stdin.

    On macOS (and some Linux terminals), terminal query responses can leak
    to stdin. If not consumed before exit or between conversation turns,
    they corrupt input or appear as garbage in the shell.

    This function is called automatically:
    1. At exit (registered via atexit)
    2. After each agent step in LocalConversation.run()
    3. Before rendering events in DefaultConversationVisualizer

    It can also be called manually if needed.

    Returns:
        Number of bytes flushed from stdin.
    """
    import sys as _sys  # Import locally to avoid issues at atexit time

    if not _sys.stdin.isatty():
        return 0

    try:
        import termios
    except ImportError:
        return 0  # Windows

    flushed = 0
    old = None
    try:
        old = termios.tcgetattr(_sys.stdin)
        # Deep copy required: old[6] is a list (cc), and list(old) only
        # does a shallow copy. Without deep copy, modifying new[6][VMIN]
        # would also modify old[6][VMIN], corrupting the restore.
        new = [item[:] if isinstance(item, list) else item for item in old]
        new[3] &= ~(termios.ICANON | termios.ECHO)
        new[6][termios.VMIN] = 0
        new[6][termios.VTIME] = 0
        termios.tcsetattr(_sys.stdin, termios.TCSANOW, new)
        while select.select([_sys.stdin], [], [], 0)[0]:
            data = os.read(_sys.stdin.fileno(), 4096)
            if not data:
                break
            flushed += len(data)
    except (OSError, termios.error):
        pass
    finally:
        if old is not None:
            try:
                termios.tcsetattr(_sys.stdin, termios.TCSANOW, old)
            except (OSError, termios.error):
                pass
    return flushed


# Register cleanup at module load time
if not _cleanup_registered:
    atexit.register(flush_stdin)
    _cleanup_registered = True
