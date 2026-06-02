import argparse
import atexit
import faulthandler
import importlib
import os
import signal
import sys
from types import FrameType

import uvicorn
from uvicorn import Config

from openhands.agent_server.logging_config import LOGGING_CONFIG
from openhands.sdk.logger import DEBUG, get_logger


logger = get_logger(__name__)


_INTERNAL_SERVER_URL_ENV = "OH_INTERNAL_SERVER_URL"
_EXTRA_PYTHON_PATH_ENV = "OH_EXTRA_PYTHON_PATH"


def _get_internal_server_url(host: str, port: int) -> str:
    """Build the current agent-server URL for local secret lookups.

    Wildcard binds are rewritten to loopback so in-process callers can connect
    back to the current server instance, and IPv6 literals are bracketed to
    produce a valid URL.

    Examples:
        >>> _get_internal_server_url("0.0.0.0", 8000)
        'http://127.0.0.1:8000'
        >>> _get_internal_server_url("::", 8000)
        'http://127.0.0.1:8000'
        >>> _get_internal_server_url("fe80::1", 8000)
        'http://[fe80::1]:8000'
    """
    resolved_host = host
    if host in {"0.0.0.0", "::", "[::]"}:
        resolved_host = "127.0.0.1"
    elif ":" in host and not host.startswith("["):
        resolved_host = f"[{host}]"
    return f"http://{resolved_host}:{port}"


def extend_python_path(extra_paths: str | None) -> None:
    """Add directories to ``sys.path`` so ``importlib.import_module`` can find
    external custom-tool modules — even when running from a PyInstaller binary.

    Paths are read from *extra_paths* (``--extra-python-path`` CLI arg) **and**
    the ``OH_EXTRA_PYTHON_PATH`` environment variable.  Both use the
    platform path separator (``':'`` on POSIX, ``';'`` on Windows).

    Non-existent directories are skipped with a warning; duplicates and paths
    already on ``sys.path`` are silently ignored.
    """
    raw_parts: list[str] = []
    for source in (extra_paths, os.environ.get(_EXTRA_PYTHON_PATH_ENV)):
        if source:
            raw_parts.extend(source.split(os.pathsep))

    added = 0
    for part in raw_parts:
        part = part.strip()
        if not part:
            continue
        resolved = os.path.abspath(part)
        if not os.path.isdir(resolved):
            logger.warning(
                "Ignoring non-existent --extra-python-path entry: %s", resolved
            )
            continue
        if resolved not in sys.path:
            sys.path.insert(0, resolved)
            logger.info("Added to sys.path: %s", resolved)
            added += 1

    if added:
        logger.info(
            "Extended sys.path with %d director%s for custom tool imports",
            added,
            "y" if added == 1 else "ies",
        )


def preload_modules(modules_arg: str | None) -> None:
    """Import user-specified modules so their top-level side effects run.

    Used to register custom tools before any conversation is created, avoiding
    a race with dynamic `tool_module_qualnames` import in conversation_service.
    """
    if not modules_arg:
        return
    for module_name in modules_arg.split(","):
        module_name = module_name.strip()
        if not module_name:
            continue
        try:
            importlib.import_module(module_name)
            logger.info("Imported module: %s", module_name)
        except ImportError as e:
            logger.error(
                "Failed to import module '%s' specified in --import-modules: %s",
                module_name,
                e,
            )
            raise


def check_browser():
    """Check if browser functionality can render about:blank."""
    executor = None
    try:
        # Register tools to ensure browser tools are available
        from openhands.tools.preset.default import register_default_tools

        register_default_tools(enable_browser=True)

        # Import browser components
        from openhands.tools.browser_use.definition import BrowserNavigateAction
        from openhands.tools.browser_use.impl import BrowserToolExecutor

        # Create executor
        executor = BrowserToolExecutor(headless=True, session_timeout_minutes=2)

        # Try to navigate to about:blank
        action = BrowserNavigateAction(url="about:blank")
        result = executor(action)

        # Check if the operation was successful
        if result.is_error:
            print(f"Browser check failed: {str(result.content)}")
            return False

        print("Browser check passed: Successfully rendered about:blank")
        return True

    except Exception as e:
        print(f"Browser check failed: {e}")
        return False
    finally:
        # Ensure cleanup happens even if an error occurs
        if executor is not None:
            executor.close()


class LoggingServer(uvicorn.Server):
    """Custom uvicorn Server that logs signal handling events.

    This subclass overrides handle_exit to add structured logging when
    termination signals are received, ensuring visibility into why the
    server is shutting down.
    """

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        """Handle exit signals with logging before delegating to parent."""
        sig_name = signal.Signals(sig).name
        logger.info(
            "Received signal %s (%d), shutting down...",
            sig_name,
            sig,
        )
        super().handle_exit(sig, frame)


def _setup_crash_diagnostics() -> None:
    """Enable crash diagnostics for debugging unexpected terminations.

    Note: faulthandler outputs tracebacks to stderr in plain text format,
    not through the structured JSON logger. This is unavoidable because
    during a segfault, Python's normal logging infrastructure is not
    available. The plain text traceback is still valuable for debugging.
    """
    faulthandler.enable()

    # Register atexit handler to log normal exits
    @atexit.register
    def _log_exit() -> None:
        logger.info("Process exiting via atexit handler")


def main() -> None:
    # Set up crash diagnostics early, before any other initialization
    _setup_crash_diagnostics()

    parser = argparse.ArgumentParser(description="OpenHands Agent Server App")
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port to bind to (default: 8000)"
    )
    parser.add_argument(
        "--reload",
        dest="reload",
        default=False,
        action="store_true",
        help="Enable auto-reload (disabled by default)",
    )
    parser.add_argument(
        "--check-browser",
        action="store_true",
        help="Check if browser functionality works and exit",
    )
    parser.add_argument(
        "--import-modules",
        type=str,
        default=None,
        help=(
            "Comma-separated list of modules to import at startup "
            "(e.g. 'myapp.tools,myapp.plugins')"
        ),
    )
    parser.add_argument(
        "--extra-python-path",
        type=str,
        default=None,
        help=(
            "Additional directories to add to sys.path for custom tool imports "
            f"('{os.pathsep}'-separated).  Also reads from the "
            f"{_EXTRA_PYTHON_PATH_ENV} environment variable."
        ),
    )

    args = parser.parse_args()

    # Handle browser check (should run without importing user modules)
    if args.check_browser:
        if check_browser():
            sys.exit(0)
        else:
            sys.exit(1)

    # Extend sys.path before importing user modules so external .py files
    # are reachable — critical for PyInstaller binary builds.
    extend_python_path(args.extra_python_path)

    # Import user modules after early-exit checks
    preload_modules(args.import_modules)

    os.environ[_INTERNAL_SERVER_URL_ENV] = _get_internal_server_url(
        args.host, args.port
    )

    print(f"Starting OpenHands Agent Server on {args.host}:{args.port}")
    print(f"API docs will be available at http://{args.host}:{args.port}/docs")
    print(f"Auto-reload: {'enabled' if args.reload else 'disabled'}")

    # Show debug mode status
    if DEBUG:
        print("DEBUG mode: ENABLED (stack traces will be shown)")
    else:
        print("DEBUG mode: DISABLED")
    print()

    # Configure uvicorn logging based on DEBUG environment variable
    log_level = "debug" if DEBUG else "info"

    # Create uvicorn config
    config = Config(
        "openhands.agent_server.api:api",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_includes=[
            "openhands-agent-server",
            "openhands-sdk",
            "openhands-tools",
        ],
        log_level=log_level,
        log_config=LOGGING_CONFIG,
        ws="wsproto",  # Use wsproto instead of deprecated websockets implementation
    )

    # Use custom LoggingServer to capture signal handling events
    server = LoggingServer(config)

    try:
        server.run()
    except Exception:
        logger.error("Server crashed with unexpected exception", exc_info=True)
        raise
    except BaseException as e:
        # Catch SystemExit, KeyboardInterrupt, etc. - these are normal termination paths
        logger.info("Server terminated: %s: %s", type(e).__name__, e)
        raise


if __name__ == "__main__":
    main()
