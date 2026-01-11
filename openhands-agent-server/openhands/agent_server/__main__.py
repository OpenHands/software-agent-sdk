import argparse
import atexit
import faulthandler
import signal
import sys

import uvicorn

from openhands.agent_server.logging_config import LOGGING_CONFIG
from openhands.sdk.logger import DEBUG, get_logger


logger = get_logger(__name__)


def _setup_signal_handlers() -> None:
    """Set up signal handlers to log termination signals."""

    def signal_handler(signum: int, frame) -> None:
        sig_name = signal.Signals(signum).name
        logger.info(
            "Received signal %s (%d), shutting down...",
            sig_name,
            signum,
        )
        sys.exit(128 + signum)

    # Register handlers for common termination signals
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(sig, signal_handler)
        except (OSError, ValueError):
            # Some signals may not be available on all platforms
            pass


def _setup_crash_diagnostics() -> None:
    """Enable crash diagnostics for debugging unexpected terminations."""
    # Enable faulthandler to print tracebacks on SIGSEGV, SIGFPE, SIGABRT, SIGBUS, SIGILL
    faulthandler.enable()

    # Register atexit handler to log normal exits
    @atexit.register
    def _log_exit() -> None:
        logger.info("Process exiting via atexit handler")


def main():
    # Set up crash diagnostics early, before any other initialization
    _setup_crash_diagnostics()
    _setup_signal_handlers()

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

    args = parser.parse_args()

    print(f"🙌 Starting OpenHands Agent Server on {args.host}:{args.port}")
    print(f"📖 API docs will be available at http://{args.host}:{args.port}/docs")
    print(f"🔄 Auto-reload: {'enabled' if args.reload else 'disabled'}")

    # Show debug mode status
    if DEBUG:
        print("🐛 DEBUG mode: ENABLED (stack traces will be shown)")
    else:
        print("🔒 DEBUG mode: DISABLED")
    print()

    # Configure uvicorn logging based on DEBUG environment variable
    log_level = "debug" if DEBUG else "info"

    try:
        uvicorn.run(
            "openhands.agent_server.api:api",
            host=args.host,
            port=args.port,
            reload=args.reload,
            reload_includes=["openhands-agent-server", "openhands-sdk", "openhands-tools"],
            log_level=log_level,
            log_config=LOGGING_CONFIG,
            ws="wsproto",  # Use wsproto instead of deprecated websockets implementation
        )
    except Exception:
        logger.error("Server crashed with unexpected exception", exc_info=True)
        raise
    except BaseException as e:
        # Catch SystemExit, KeyboardInterrupt, etc. - these are normal termination paths
        logger.info("Server terminated: %s: %s", type(e).__name__, e)
        raise


if __name__ == "__main__":
    main()
