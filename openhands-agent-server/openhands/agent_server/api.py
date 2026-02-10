import asyncio
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from openhands.agent_server.bash_router import bash_router
from openhands.agent_server.config import (
    Config,
    get_default_config,
)
from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import (
    get_default_conversation_service,
)
from openhands.agent_server.dependencies import create_session_api_key_dependency
from openhands.agent_server.desktop_router import desktop_router
from openhands.agent_server.desktop_service import get_desktop_service
from openhands.agent_server.event_router import event_router
from openhands.agent_server.file_router import file_router
from openhands.agent_server.git_router import git_router
from openhands.agent_server.hooks_router import hooks_router
from openhands.agent_server.middleware import LocalhostCORSMiddleware
from openhands.agent_server.server_details_router import (
    get_server_info,
    mark_initialization_complete,
    server_details_router,
)
from openhands.agent_server.skills_router import skills_router
from openhands.agent_server.sockets import sockets_router
from openhands.agent_server.tool_preload_service import get_tool_preload_service
from openhands.agent_server.tool_router import tool_router
from openhands.agent_server.vscode_router import vscode_router
from openhands.agent_server.vscode_service import get_vscode_service
from openhands.sdk.logger import DEBUG, get_logger


logger = get_logger(__name__)


@asynccontextmanager
async def api_lifespan(api: FastAPI) -> AsyncIterator[None]:
    service = get_default_conversation_service()
    vscode_service = get_vscode_service()
    desktop_service = get_desktop_service()
    tool_preload_service = get_tool_preload_service()

    # Define async functions for starting each service
    async def start_vscode_service():
        if vscode_service is not None:
            vscode_started = await vscode_service.start()
            if vscode_started:
                logger.info("VSCode service started successfully")
            else:
                logger.warning(
                    "VSCode service failed to start, continuing without VSCode"
                )
        else:
            logger.info("VSCode service is disabled")

    async def start_desktop_service():
        if desktop_service is not None:
            desktop_started = await desktop_service.start()
            if desktop_started:
                logger.info("Desktop service started successfully")
            else:
                logger.warning(
                    "Desktop service failed to start, continuing without desktop"
                )
        else:
            logger.info("Desktop service is disabled")

    async def start_tool_preload_service():
        if tool_preload_service is not None:
            tool_preload_started = await tool_preload_service.start()
            if tool_preload_started:
                logger.info("Tool preload service started successfully")
            else:
                logger.warning("Tool preload service failed to start - skipping")
        else:
            logger.info("Tool preload service is disabled")

    # Start all services concurrently
    results = await asyncio.gather(
        start_vscode_service(),
        start_desktop_service(),
        start_tool_preload_service(),
        return_exceptions=True,
    )

    # Check for any exceptions during initialization
    exceptions = [r for r in results if isinstance(r, Exception)]
    if exceptions:
        logger.error(
            "Service initialization failed with %d exception(s): %s",
            len(exceptions),
            exceptions,
        )
        # Re-raise the first exception to prevent server from starting
        raise RuntimeError(
            f"Server initialization failed with {len(exceptions)} exception(s)"
        ) from exceptions[0]

    # Mark initialization as complete - now the /ready endpoint will return 200
    # and Kubernetes readiness probes will pass
    mark_initialization_complete()
    logger.info("Server initialization complete - ready to serve requests")

    async with service:
        # Store the initialized service in app state for dependency injection
        api.state.conversation_service = service
        try:
            yield
        finally:
            # Define async functions for stopping each service
            async def stop_vscode_service():
                if vscode_service is not None:
                    await vscode_service.stop()

            async def stop_desktop_service():
                if desktop_service is not None:
                    await desktop_service.stop()

            async def stop_tool_preload_service():
                if tool_preload_service is not None:
                    await tool_preload_service.stop()

            # Stop all services concurrently
            await asyncio.gather(
                stop_vscode_service(),
                stop_desktop_service(),
                stop_tool_preload_service(),
                return_exceptions=True,
            )


def _create_fastapi_instance() -> FastAPI:
    """Create the basic FastAPI application instance.

    Returns:
        Basic FastAPI application with title, description, and lifespan.
    """
    return FastAPI(
        title="OpenHands Agent Server",
        description=(
            "OpenHands Agent Server - REST/WebSocket interface for OpenHands AI Agent"
        ),
        lifespan=api_lifespan,
    )


def _find_http_exception(exc: BaseExceptionGroup) -> HTTPException | None:
    """Helper function to find HTTPException in ExceptionGroup.

    Args:
        exc: BaseExceptionGroup to search for HTTPException.

    Returns:
        HTTPException if found, None otherwise.
    """
    for inner_exc in exc.exceptions:
        if isinstance(inner_exc, HTTPException):
            return inner_exc
        # Recursively search nested ExceptionGroups
        if isinstance(inner_exc, BaseExceptionGroup):
            found = _find_http_exception(inner_exc)
            if found:
                return found
    return None


def _add_api_routes(app: FastAPI, config: Config) -> None:
    """Add all API routes to the FastAPI application.

    Args:
        app: FastAPI application instance to add routes to.
    """
    app.include_router(server_details_router)

    dependencies = []
    if config.session_api_keys:
        dependencies.append(Depends(create_session_api_key_dependency(config)))

    api_router = APIRouter(prefix="/api", dependencies=dependencies)
    api_router.include_router(event_router)
    api_router.include_router(conversation_router)
    api_router.include_router(tool_router)
    api_router.include_router(bash_router)
    api_router.include_router(git_router)
    api_router.include_router(file_router)
    api_router.include_router(vscode_router)
    api_router.include_router(desktop_router)
    api_router.include_router(skills_router)
    api_router.include_router(hooks_router)
    app.include_router(api_router)
    app.include_router(sockets_router)


def _setup_static_files(app: FastAPI, config: Config) -> None:
    """Set up static file serving and root redirect if configured.

    Args:
        app: FastAPI application instance.
        config: Configuration object containing static files settings.
    """
    # Only proceed if static files are configured and directory exists
    if not (
        config.static_files_path
        and config.static_files_path.exists()
        and config.static_files_path.is_dir()
    ):
        # Map the root path to server info if there are no static files
        app.get("/")(get_server_info)
        return

    # Mount static files directory
    app.mount(
        "/static",
        StaticFiles(directory=str(config.static_files_path)),
        name="static",
    )

    # Add root redirect to static files
    @app.get("/", tags=["Server Details"])
    async def root_redirect():
        """Redirect root endpoint to static files directory."""
        # Check if index.html exists in the static directory
        # We know static_files_path is not None here due to the outer condition
        assert config.static_files_path is not None
        index_path = config.static_files_path / "index.html"
        if index_path.exists():
            return RedirectResponse(url="/static/index.html", status_code=302)
        else:
            return RedirectResponse(url="/static/", status_code=302)


def _add_exception_handlers(api: FastAPI) -> None:
    """Add exception handlers to the FastAPI application."""

    @api.exception_handler(Exception)
    async def _unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Handle unhandled exceptions."""
        # Always log that we're in the exception handler for debugging
        logger.debug(
            "Exception handler called for %s %s with %s: %s",
            request.method,
            request.url.path,
            type(exc).__name__,
            str(exc),
        )

        content = {
            "detail": "Internal Server Error",
            "exception": str(exc),
        }
        # In DEBUG mode, include stack trace in response
        if DEBUG:
            content["traceback"] = traceback.format_exc()
        # Check if this is an HTTPException that should be handled directly
        if isinstance(exc, HTTPException):
            return await _http_exception_handler(request, exc)

        # Check if this is a BaseExceptionGroup with HTTPExceptions
        if isinstance(exc, BaseExceptionGroup):
            http_exc = _find_http_exception(exc)
            if http_exc:
                return await _http_exception_handler(request, http_exc)
            # If no HTTPException found, treat as unhandled exception
            logger.error(
                "Unhandled ExceptionGroup on %s %s",
                request.method,
                request.url.path,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            return JSONResponse(status_code=500, content=content)

        # Logs full stack trace for any unhandled error that FastAPI would
        # turn into a 500
        logger.error(
            "Unhandled exception on %s %s",
            request.method,
            request.url.path,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(status_code=500, content=content)

    @api.exception_handler(HTTPException)
    async def _http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        """Handle HTTPExceptions with appropriate logging."""
        # Log 4xx errors at info level (expected client errors like auth failures)
        if 400 <= exc.status_code < 500:
            logger.info(
                "HTTPException %d on %s %s: %s",
                exc.status_code,
                request.method,
                request.url.path,
                exc.detail,
            )
        # Log 5xx errors at error level with full traceback (server errors)
        elif exc.status_code >= 500:
            logger.error(
                "HTTPException %d on %s %s: %s",
                exc.status_code,
                request.method,
                request.url.path,
                exc.detail,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            content = {
                "detail": "Internal Server Error",
                "exception": str(exc),
            }
            if DEBUG:
                content["traceback"] = traceback.format_exc()
            # Don't leak internal details to clients for 5xx errors in production
            return JSONResponse(
                status_code=exc.status_code,
                content=content,
            )

        # Return clean JSON response for all non-5xx HTTP exceptions
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def create_app(config: Config | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Configuration object. If None, uses default config.

    Returns:
        Configured FastAPI application.
    """
    if config is None:
        config = get_default_config()
    app = _create_fastapi_instance()
    _add_api_routes(app, config)
    _setup_static_files(app, config)
    app.add_middleware(LocalhostCORSMiddleware, allow_origins=config.allow_cors_origins)
    _add_exception_handlers(app)

    return app


# Create the default app instance
api = create_app()
