"""Deferred-init router for warm-pool agent servers.

When ``Config.deferred_init`` is True the server starts in *dormant* mode:
stateless services (VSCode, tool preload) come up as usual, but
the conversation, event, and bash routers return 503 until ``POST /api/init``
delivers the runtime configuration. This is intended for warm-pool
deployments where pods are pre-warmed before a user is matched and the
per-user workspace + credentials are attached later.

See: https://github.com/OpenHands/software-agent-sdk/issues/2523
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar, Literal

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocketException,
    status,
)
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.requests import HTTPConnection

from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import (
    V1_SESSION_API_KEY_ENV,
    Config,
    TelemetrySpec,
    WebhookSpec,
    export_session_api_key,
)
from openhands.agent_server.conversation_registry import (
    create_conversation_registry,
)
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_config
from openhands.agent_server.persistence import ServerStores, reencrypt_stale_stores
from openhands.agent_server.server_context import (
    ServerContext,
    install_server_context,
    server_context_of,
)
from openhands.agent_server.server_details_router import mark_initialization_complete
from openhands.agent_server.telemetry import (
    build_telemetry_sink,
    emit_server_started,
    shutdown_telemetry_sink,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.observability import maybe_init_laminar


logger = get_logger(__name__)


# The init endpoint uses its own header (distinct from X-Session-API-Key)
# because session keys aren't known to the pool at warm-up time — they
# arrive *inside* the /api/init body. The value is checked against the
# dormant server's ``secret_key``, which the orchestrator already holds
# for encryption purposes and which will be overwritten by the init payload.
_INIT_API_KEY_HEADER = APIKeyHeader(name="X-Init-API-Key", auto_error=False)


InitState = Literal["dormant", "initializing", "ready"]


class InitRequest(BaseModel):
    """Runtime configuration delivered at /api/init time.

    Each field is optional and overrides the equivalent field on the dormant
    ``Config``. Fields not provided keep the value the server was constructed
    with (typically from env vars at pod startup). The set of overridable
    fields is intentionally narrow — it covers the values that today are
    "env-var shaped" and must change per-user, not image-build-time
    configuration (Python deps, plugin set, etc.) which stays bound to the
    warm-pool flavor.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    session_api_keys: list[str] | None = Field(
        default=None,
        description=(
            "Per-user session API keys. If provided, all subsequent /api/* "
            "requests must authenticate with one of these keys via the "
            "X-Session-API-Key header."
        ),
    )
    secret_key: SecretStr | None = Field(
        default=None,
        description=(
            "Symmetric secret used to encrypt persisted secrets. If not "
            "provided, falls back to the first session_api_key (matching the "
            "default Config behavior)."
        ),
    )
    previous_secret_keys: list[SecretStr] | None = Field(
        default=None,
        description=(
            "Earlier values of secret_key (or of the first session_api_key it "
            "falls back to), used only to decrypt values saved under them; "
            "they are re-encrypted under the current key when next saved."
        ),
    )
    conversations_path: Path | None = Field(
        default=None,
        description=(
            "Directory where conversations are persisted. Override this to "
            "point at the mounted user workspace."
        ),
    )
    bash_events_dir: Path | None = Field(
        default=None,
        description=(
            "Directory where bash events are persisted. Typically located "
            "inside the mounted user workspace."
        ),
    )
    conversation_worktree_root: Path | None = Field(
        default=None,
        description=(
            "Root directory for conversation git worktrees. Override this to "
            "point at the mounted user workspace."
        ),
    )
    webhooks: list[WebhookSpec] | None = Field(
        default=None,
        description="Per-user webhooks (e.g. for streaming events back).",
    )
    web_url: str | None = Field(
        default=None,
        description=(
            "External URL where this server is reachable, used for root-path "
            "calculation. Only honored when not already set in dormant config."
        ),
    )
    allow_cors_origins: list[str] | None = Field(
        default=None,
        description="CORS origins to add to the existing localhost allowlist.",
    )
    max_concurrent_runs: int | None = Field(
        default=None,
        ge=1,
        description="Override the conversation-step concurrency limit.",
    )
    env: dict[str, str] | None = Field(
        default=None,
        description=(
            "Process environment variables to set before conversation services "
            "start. Useful for credentials consumed by tools (e.g. GITHUB_TOKEN). "
            "These are applied with ``os.environ.update``; existing values are "
            "overwritten."
        ),
    )
    telemetry: TelemetrySpec | None = Field(
        default=None,
        description=(
            "Product-analytics policy for this pod. Without this, a warm-pool "
            "pod keeps whatever mode it booted with (normally 'disabled'), so "
            "a deployment that expects telemetry must supply it here."
        ),
    )


class InitStatus(BaseModel):
    state: InitState = Field(
        description=(
            "``dormant`` — server is up but waiting for /api/init. "
            "``initializing`` — /api/init has been received and services are "
            "starting. "
            "``ready`` — initialization complete; all /api/* routes are live."
        )
    )
    error: str | None = Field(
        default=None,
        description=(
            "If a previous /api/init attempt failed, the error message. The state "
            "rolls back to ``dormant`` so /api/init can be retried."
        ),
    )


def _build_initialized_config(base: Config, req: InitRequest) -> Config:
    """Merge dormant ``base`` config with ``req`` and clear ``deferred_init``."""
    updates: dict[str, Any] = {"deferred_init": False}
    if req.session_api_keys is not None:
        updates["session_api_keys"] = req.session_api_keys
    # A blank secret_key is treated as unset, like a blank OH_SECRET_KEY.
    if req.secret_key is not None and req.secret_key.get_secret_value().strip():
        updates["secret_key"] = req.secret_key
    elif req.session_api_keys and base.secret_key is None:
        # Match the Config default: fall back to first session key when no
        # secret_key was provided.
        updates["secret_key"] = SecretStr(req.session_api_keys[0])
    if req.previous_secret_keys is not None:
        updates["previous_secret_keys"] = req.previous_secret_keys
    if req.conversations_path is not None:
        updates["conversations_path"] = req.conversations_path
    if req.bash_events_dir is not None:
        updates["bash_events_dir"] = req.bash_events_dir
    if req.conversation_worktree_root is not None:
        updates["conversation_worktree_root"] = req.conversation_worktree_root
    if req.webhooks is not None:
        updates["webhooks"] = req.webhooks
    if req.web_url is not None:
        updates["web_url"] = req.web_url
    if req.allow_cors_origins is not None:
        updates["allow_cors_origins"] = req.allow_cors_origins
    if req.max_concurrent_runs is not None:
        updates["max_concurrent_runs"] = req.max_concurrent_runs
    if req.telemetry is not None:
        updates["telemetry"] = req.telemetry
    return base.model_copy(update=updates)


def _restore_environ(previous: dict[str, str | None]) -> None:
    """Put variables back to the recorded values; ``None`` means it was unset."""
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@dataclass(frozen=True, slots=True)
class _PreparedInit:
    """An initialisation with every fallible step done and nothing published."""

    context: ServerContext
    root_path: str
    started: AsyncExitStack
    """Stops the services of ``context`` and restores the environment."""


class InitService:
    """Tracks dormant→ready transition and serialises /api/init calls.

    A single ``asyncio.Lock`` makes concurrent /api/init posts safe; the second
    one sees ``state != "dormant"`` and gets a 400. On failure mid-init the
    state rolls back to ``dormant`` so the orchestrator can retry.

    An initialisation has two phases. ``_prepare`` does everything that can
    fail beside the dormant :class:`ServerContext` and undoes all of it if it
    does fail; ``_commit`` then publishes the result with assignments alone.
    So an attempt either becomes the running server in one piece or leaves
    nothing behind.
    """

    def __init__(self, app: FastAPI, base_config: Config) -> None:
        self._app = app
        self._base_config = base_config
        self._state: InitState = "dormant"
        self._error: str | None = None
        self._lock = asyncio.Lock()
        # What the committed initialisation started, for ``teardown`` to stop.
        self._started: AsyncExitStack | None = None

    @property
    def state(self) -> InitState:
        return self._state

    def snapshot(self) -> InitStatus:
        return InitStatus(state=self._state, error=self._error)

    async def initialize(self, req: InitRequest) -> InitStatus:
        async with self._lock:
            if self._state != "dormant":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"server already in state: {self._state}",
                )
            if req.session_api_keys is not None and any(
                not key for key in req.session_api_keys
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="session_api_keys cannot contain empty keys",
                )
            self._state = "initializing"
            self._error = None
        try:
            prepared = await self._prepare(req)
        except Exception as exc:  # pragma: no cover - logged + re-raised
            logger.exception("deferred_init: /api/init failed; rolling back to dormant")
            self._error = f"{type(exc).__name__}: {exc}"
            self._state = "dormant"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=self._error,
            ) from exc
        except BaseException:
            # Cancelled: the attempt is unwound like a failed one, so it can
            # be retried rather than leaving the server "initializing".
            self._state = "dormant"
            raise
        self._commit(prepared)
        logger.info("deferred_init: server transitioned to ready")
        return self.snapshot()

    async def _prepare(self, req: InitRequest) -> _PreparedInit:
        """Do every step of an initialisation that can fail; publish nothing.

        If a step fails, the two exit stacks undo the earlier ones: the
        services are stopped, the environment and the telemetry sink are the
        dormant server's again, and the dormant context was never replaced.
        """
        # Circular: api.py builds the app this router is mounted on.
        from openhands.agent_server.api import _get_root_path

        dormant = server_context_of(self._app)
        new_config = _build_initialized_config(self._base_config, req)
        root_path = _get_root_path(new_config)

        # ``started`` is undone on failure and otherwise kept for ``teardown``;
        # ``on_failure`` only if the attempt fails, after ``started`` has
        # stopped the services.
        async with AsyncExitStack() as on_failure, AsyncExitStack() as started:
            # ``req.env`` lets the stores resolve ``OH_PERSISTENCE_DIR`` and
            # tools find credentials. The session key must also reach the
            # in-process callers that read it from the environment (the
            # start_child_conversation tool, the managed LLM key refresh).
            exported = {*(req.env or {}), V1_SESSION_API_KEY_ENV}
            started.callback(
                _restore_environ, {key: os.environ.get(key) for key in exported}
            )
            os.environ.update(req.env or {})
            export_session_api_key(new_config)
            maybe_init_laminar()

            stores = ServerStores.from_config(new_config)
            await asyncio.to_thread(reencrypt_stale_stores, stores)

            # The conversation service reads the sink while it starts, so the
            # delivered one has to be in place first. ``server_started`` is
            # emitted by ``_commit``: an attempt that fails never produces one.
            on_failure.push_async_callback(
                self._use_telemetry_of, dormant.config, dormant.stores
            )
            await self._use_telemetry_of(new_config, stores)

            service = ConversationService.get_instance(new_config, stores)
            conversation_registry = create_conversation_registry(new_config, stores)
            conversation_registry.configure_service(service)
            bash_svc = BashEventService(
                bash_events_dir=new_config.bash_events_dir, cipher=new_config.cipher
            )
            await started.enter_async_context(bash_svc)
            await started.enter_async_context(service)
            started.push_async_callback(conversation_registry.shutdown)
            await conversation_registry.start()

            # VSCode's token is the first session key, as on a normal boot.
            # A dormant server that booted without one gave VSCode a random
            # token, so switch it to the key delivered here. Last, because a
            # restarted editor cannot be put back if a later step failed.
            vscode_service = dormant.vscode_service
            if vscode_service is not None and new_config.session_api_keys:
                await vscode_service.set_connection_token(
                    new_config.session_api_keys[0]
                )

            context = replace(
                dormant,
                config=new_config,
                stores=stores,
                conversation_registry=conversation_registry,
                conversation_service=service,
                bash_event_service=bash_svc,
            )
            on_failure.pop_all()
            return _PreparedInit(
                context=context, root_path=root_path, started=started.pop_all()
            )

    def _commit(self, prepared: _PreparedInit) -> None:
        """Make a prepared initialisation the running server.

        Assignments only, with no ``await`` between them: it cannot fail or be
        interrupted half-way, so every request sees the delivered config with
        its own stores and services, or none of them.
        """
        self._started = prepared.started
        install_server_context(self._app, prepared.context)
        # Re-derived from the merged config so Doc URLs are valid.
        self._app.root_path = prepared.root_path
        mark_initialization_complete()
        self._state = "ready"
        emit_server_started()

    async def _use_telemetry_of(self, config: Config, stores: ServerStores) -> None:
        """Replace the telemetry sink with the one ``config`` asks for."""
        await shutdown_telemetry_sink()
        self._app.state.telemetry_sink = await build_telemetry_sink(
            config, stores.settings
        )

    async def teardown(self) -> None:
        """Stop what /api/init started and restore the environment it changed.

        Called from the FastAPI lifespan's finally clause; a dormant pod that
        was never initialized has nothing to undo.
        """
        started, self._started = self._started, None
        if started is not None:
            # Registry, then the conversation service, then the bash service,
            # then the environment.
            await started.aclose()


def get_init_service(request: Request) -> InitService:
    init_service = getattr(request.app.state, "init_service", None)
    if init_service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "server is not running with deferred_init=True; the /api/init "
                "endpoint is not available"
            ),
        )
    return init_service


def check_init_api_key(
    config: Config = Depends(get_config),
    init_api_key: str | None = Depends(_INIT_API_KEY_HEADER),
) -> None:
    """Auth gate for /api/init. Uses the dormant server's ``secret_key`` as the
    bootstrap credential — the orchestrator already holds it because it is
    required for encryption. The key is replaced when /api/init delivers the
    per-user runtime config."""
    if config.secret_key is None:
        if config.secret_key_was_blank:
            # A blank key was configured but no usable key remains → fail
            # closed, since no client can present a blank key.
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        # No key configured → endpoint is open. Acceptable for dev.
        return
    expected = config.secret_key.get_secret_value()
    if init_api_key != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


def require_initialized(connection: HTTPConnection) -> None:
    """Dependency that rejects requests and WebSockets while the server is dormant.

    HTTP requests get a 503; WebSockets are closed with 1013 (Try Again Later)
    before the handshake is accepted. Returns immediately when
    ``deferred_init`` is False (the normal path) so this has zero cost for
    non-deferred deployments.
    """
    init_service: InitService | None = getattr(
        connection.app.state, "init_service", None
    )
    if init_service is None or init_service.state == "ready":
        return
    reason = (
        f"server is in deferred-init state '{init_service.state}'; "
        "call POST /api/init first"
    )
    if connection.scope["type"] == "websocket":
        raise WebSocketException(code=status.WS_1013_TRY_AGAIN_LATER, reason=reason)
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=reason)


init_router = APIRouter(prefix="/init", tags=["Init"])


@init_router.get("", response_model=InitStatus)
async def get_init_status(
    init_service: InitService = Depends(get_init_service),
) -> InitStatus:
    """Report the current init state.

    Authentication is intentionally not required on this endpoint so a warm
    pool controller can poll it without holding the init key. The payload
    contains no sensitive data.
    """
    return init_service.snapshot()


@init_router.post(
    "",
    response_model=InitStatus,
    dependencies=[Depends(check_init_api_key)],
)
async def initialize_server(
    req: InitRequest,
    init_service: InitService = Depends(get_init_service),
) -> InitStatus:
    """Initialize a dormant server with runtime configuration.

    Returns 400 if the server has already been initialized (state != dormant).
    Returns 500 if initialization fails; in that case the state rolls back to
    ``dormant`` so the orchestrator can retry.
    """
    return await init_service.initialize(req)
