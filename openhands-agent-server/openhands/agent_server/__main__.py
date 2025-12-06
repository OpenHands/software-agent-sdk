import argparse
import sys

import uvicorn

from openhands.agent_server.logging_config import LOGGING_CONFIG
from openhands.sdk.logger import DEBUG


def check_browser():
    """Check if browser functionality can render about:blank."""
    try:
        # Register tools to ensure browser tools are available
        from openhands.tools.preset.default import register_default_tools

        register_default_tools(enable_browser=True)

        # Import browser components
        from openhands.tools.browser_use.definition import BrowserNavigateAction
        from openhands.tools.browser_use.impl import BrowserToolExecutor

        # Create executor
        executor = BrowserToolExecutor(headless=True, session_timeout_minutes=1)

        # Try to navigate to about:blank
        action = BrowserNavigateAction(url="about:blank")
        result = executor(action)

        # Clean up
        executor.close()

        # Check if the operation was successful
        if result.is_error:
            print(f"Browser check failed: {result.content}")
            return False

        print("Browser check passed: Successfully rendered about:blank")
        return True

    except Exception as e:
        print(f"Browser check failed: {e}")
        return False


def main():
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

    args = parser.parse_args()

    # Handle browser check
    if args.check_browser:
        if check_browser():
            sys.exit(0)
        else:
            sys.exit(1)

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


if __name__ == "__main__":
    main()
