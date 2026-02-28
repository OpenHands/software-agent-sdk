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
#
# This implementation uses SELECTIVE FLUSHING:
# - Parse pending stdin data byte-by-byte
# - Discard only recognized escape sequences (CSI, OSC)
# - Preserve all other data (likely user typeahead) in a buffer
# - Provide get_buffered_input() for SDK to retrieve preserved data

_cleanup_registered = False
_preserved_input_buffer: bytes = b""


def _is_csi_final_byte(byte: int) -> bool:
    """Check if byte is a CSI sequence final character (0x40-0x7E).

    Per ECMA-48, CSI sequences end with a byte in the range 0x40-0x7E.
    Common finals: 'R' (0x52) for cursor position, 'n' for device status.
    """
    return 0x40 <= byte <= 0x7E


def _find_csi_end(data: bytes, start: int) -> int:
    """Find end position of a CSI sequence.

    CSI format: ESC [ [params] [intermediates] final
    - Params: 0x30-0x3F (digits, semicolons, etc.)
    - Intermediates: 0x20-0x2F (space, !"#$%&'()*+,-./)
    - Final: 0x40-0x7E (@A-Z[\\]^_`a-z{|}~)

    Args:
        data: The byte buffer to parse.
        start: Position of the ESC byte (start of \\x1b[ sequence).

    Returns:
        Position AFTER the sequence ends. If incomplete, returns start
        (preserving the incomplete sequence as potential user input).
    """
    pos = start + 2  # Skip \x1b[
    while pos < len(data):
        byte = data[pos]
        if _is_csi_final_byte(byte):
            return pos + 1  # Include the final byte
        if byte < 0x20 or byte > 0x3F and byte < 0x40:
            # Invalid byte in CSI sequence - treat as end
            return pos
        pos += 1
    # Incomplete sequence at end of buffer - preserve it (might be user input)
    return start


def _find_osc_end(data: bytes, start: int) -> int:
    """Find end position of an OSC sequence.

    OSC format: ESC ] ... (BEL or ST)
    - BEL terminator: 0x07
    - ST terminator: ESC \\ (0x1b 0x5c)

    Args:
        data: The byte buffer to parse.
        start: Position of the ESC byte (start of \\x1b] sequence).

    Returns:
        Position AFTER the sequence ends. If incomplete, returns start
        (preserving the incomplete sequence as potential user input).
    """
    pos = start + 2  # Skip \x1b]
    while pos < len(data):
        if data[pos] == 0x07:  # BEL terminator
            return pos + 1
        if data[pos] == 0x1B and pos + 1 < len(data) and data[pos + 1] == 0x5C:
            return pos + 2  # ST terminator \x1b\\
        pos += 1
    # Incomplete sequence - preserve it
    return start


def _parse_stdin_data(data: bytes) -> tuple[bytes, int]:
    """Parse stdin data, separating escape sequences from user input.

    This function implements selective flushing: it identifies and discards
    terminal escape sequence responses (CSI and OSC) while preserving any
    other data that may be legitimate user typeahead.

    Args:
        data: Raw bytes read from stdin.

    Returns:
        Tuple of (preserved_user_input, flushed_escape_sequence_bytes).
    """
    preserved = b""
    flushed = 0
    i = 0

    while i < len(data):
        # Check for CSI sequence: \x1b[
        if i + 1 < len(data) and data[i] == 0x1B and data[i + 1] == 0x5B:
            end = _find_csi_end(data, i)
            if end > i:  # Complete sequence found
                flushed += end - i
                i = end
            else:  # Incomplete - preserve as potential user input
                preserved += data[i : i + 1]
                i += 1

        # Check for OSC sequence: \x1b]
        elif i + 1 < len(data) and data[i] == 0x1B and data[i + 1] == 0x5D:
            end = _find_osc_end(data, i)
            if end > i:  # Complete sequence found
                flushed += end - i
                i = end
            else:  # Incomplete - preserve
                preserved += data[i : i + 1]
                i += 1

        # Single ESC followed by another character - could be escape sequence
        # Be conservative: if next byte looks like start of known sequence type,
        # preserve both bytes for next iteration or as user input
        elif data[i] == 0x1B and i + 1 < len(data):
            next_byte = data[i + 1]
            # Known sequence starters we don't fully parse: SS2, SS3, DCS, PM, APC
            # SS2=N, SS3=O, DCS=P, PM=^, APC=_
            if next_byte in (0x4E, 0x4F, 0x50, 0x5E, 0x5F):
                # These are less common; preserve as user input
                preserved += data[i : i + 1]
                i += 1
            else:
                # Unknown escape sequence type - preserve it
                preserved += data[i : i + 1]
                i += 1

        # Regular byte - preserve it (likely user input)
        else:
            preserved += data[i : i + 1]
            i += 1

    return preserved, flushed


def flush_stdin() -> int:
    """Flush terminal escape sequences from stdin, preserving user input.

    On macOS (and some Linux terminals), terminal query responses can leak
    to stdin. If not consumed before exit or between conversation turns,
    they corrupt input or appear as garbage in the shell.

    This function uses SELECTIVE FLUSHING:
    - Only discards recognized escape sequences (CSI `\\x1b[...`, OSC `\\x1b]...`)
    - Preserves all other data in an internal buffer
    - Use get_buffered_input() to retrieve preserved user input

    This function is called automatically:
    1. At exit (registered via atexit)
    2. After each agent step in LocalConversation.run()
    3. Before rendering events in DefaultConversationVisualizer

    It can also be called manually if needed.

    Returns:
        Number of escape sequence bytes flushed from stdin.
    """  # noqa: E501
    global _preserved_input_buffer
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
        # termios attrs: [iflag, oflag, cflag, lflag, ispeed, ospeed, cc]
        # Index 3 is lflag (int), index 6 is cc (list)
        lflag = new[3]
        assert isinstance(lflag, int)  # Help type checker
        new[3] = lflag & ~(termios.ICANON | termios.ECHO)
        new[6][termios.VMIN] = 0
        new[6][termios.VTIME] = 0
        termios.tcsetattr(_sys.stdin, termios.TCSANOW, new)

        while select.select([_sys.stdin], [], [], 0)[0]:
            data = os.read(_sys.stdin.fileno(), 4096)
            if not data:
                break
            # Parse data: discard escape sequences, preserve user input
            preserved, seq_flushed = _parse_stdin_data(data)
            flushed += seq_flushed
            _preserved_input_buffer += preserved

    except (OSError, termios.error):
        pass
    finally:
        if old is not None:
            try:
                termios.tcsetattr(_sys.stdin, termios.TCSANOW, old)
            except (OSError, termios.error):
                pass
    return flushed


def get_buffered_input() -> bytes:
    """Get any user input that was preserved during flush_stdin calls.

    When flush_stdin() discards escape sequences, it preserves any other
    data that might be legitimate user typeahead. This function retrieves
    and clears that buffered input.

    SDK components that read user input should call this function to
    prepend any buffered data to their input.

    Returns:
        Bytes that were preserved from stdin during flush operations.
        The internal buffer is cleared after this call.

    Example:
        >>> # In code that reads user input:
        >>> buffered = get_buffered_input()
        >>> user_input = buffered.decode('utf-8', errors='replace') + input()
    """
    global _preserved_input_buffer
    data = _preserved_input_buffer
    _preserved_input_buffer = b""
    return data


def clear_buffered_input() -> None:
    """Clear any buffered input without returning it.

    Use this when you want to discard any preserved input, for example
    at the start of a new conversation or after a timeout.
    """
    global _preserved_input_buffer
    _preserved_input_buffer = b""


# Register cleanup at module load time
if not _cleanup_registered:
    atexit.register(flush_stdin)
    _cleanup_registered = True
