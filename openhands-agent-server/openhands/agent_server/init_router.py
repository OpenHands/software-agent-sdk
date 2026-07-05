"""Deferred-init router for warm-pool agent servers.

When ``Config.deferred_init`` is True the server starts in *dormant* mode:
stateless services (VSCode, desktop, tool preload) come up as usual, but
the conversation, event, and bash routers return 503 until ``POST /api/init``
delivers the runtime configuration. This is intended for warm-pool
deployments where pods are pre-warmed before a user is matched and the
per-user workspace + credentials are attached later.

See: https://github.com/OpenHands/software-agent-sdk/issues/2523
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, ClassVar, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import Config, WebhookSpec
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.server_details_router import mark_initialization_complete
from openhands.sdk.logger import get_logger


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
    if req.secret_key is not None:
        updates["secret_key"] = req.secret_key
    elif req.session_api_keys and base.secret_key is None:
        # Match the Config default: fall back to first session key when no
        # secret_key was provided.
        updates["secret_key"] = SecretStr(req.session_api_keys[0])
    if req.conversations_path is not None:
        updates["conversations_path"] = req.conversations_path
    if req.bash_events_dir is not None:
        updates["bash_events_dir"] = req.bash_events_dir
    if req.webhooks is not None:
        updates["webhooks"] = req.webhooks
    if req.web_url is not None:
        updates["web_url"] = req.web_url
    if req.allow_cors_origins is not None:
        updates["allow_cors_origins"] = req.allow_cors_origins
    if req.max_concurrent_runs is not None:
        updates["max_concurrent_runs"] = req.max_concurrent_runs
    return base.model_copy(update=updates)


class InitService:
    """Tracks dormant→ready transition and serialises /api/init calls.

    A single ``asyncio.Lock`` makes concurrent /api/init posts safe; the second
    one sees ``state != "dormant"`` and gets a 400. On failure mid-init the
    state rolls back to ``dormant`` so the orchestrator can retry.
    """

    def __init__(self, app: FastAPI, base_config: Config) -> None:
        self._app = app
        self._base_config = base_config
        self._state: InitState = "dormant"
        self._error: str | None = None
        self._lock = asyncio.Lock()
        self._entered_service: ConversationService | None = None
        self._entered_bash_service: BashEventService | None = None

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
            self._state = "initializing"
            self._error = None
        try:
            new_config = _build_initialized_config(self._base_config, req)
            if req.env:
                # Setting env vars before services boot lets things like
                # the cipher pick up OH_SECRET_KEY-style overrides, and
                # tools pick up credentials.
                for key, value in req.env.items():
                    os.environ[key] = value

            # Reset the module-level singleton so other call sites that go
            # through ``get_default_conversation_service`` see the new
            # instance built from the merged config.
            from openhands.agent_server import conversation_service as cs_mod

            service = ConversationService.get_instance(new_config)
            cs_mod._conversation_service = service

            bash_svc = BashEventService(bash_events_dir=new_config.bash_events_dir)
            await bash_svc.__aenter__()
            self._entered_bash_service = bash_svc

            await service.__aenter__()
            self._entered_service = service
            self._app.state.config = new_config
            self._app.state.conversation_service = service
            self._app.state.bash_event_service = bash_svc

            # Re-derive root_path from the merged config so Doc URLS are valid
            from openhands.agent_server.api import _get_root_path

            new_root_path = _get_root_path(new_config)
            self._app.root_path = new_root_path

            mark_initialization_complete()
            self._state = "ready"
            logger.info("deferred_init: server transitioned to ready")
            return self.snapshot()
        except Exception as exc:  # pragma: no cover - logged + re-raised
            logger.exception("deferred_init: /api/init failed; rolling back to dormant")
            self._error = f"{type(exc).__name__}: {exc}"
            self._state = "dormant"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=self._error,
            ) from exc

    async def teardown(self) -> None:
        """Tear down the conversation service if /api/init succeeded.

        Called from the FastAPI lifespan's finally clause so dormant pods
        that were never initialized don't need any cleanup.
        """
        if self._entered_service is not None:
            await self._entered_service.__aexit__(None, None, None)
            self._entered_service = None
        if self._entered_bash_service is not None:
            await self._entered_bash_service.__aexit__(None, None, None)
            self._entered_bash_service = None


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
    request: Request,
    init_api_key: str | None = Depends(_INIT_API_KEY_HEADER),
) -> None:
    """Auth gate for /api/init. Uses the dormant server's ``secret_key`` as the
    bootstrap credential — the orchestrator already holds it because it is
    required for encryption. The key is replaced when /api/init delivers the
    per-user runtime config."""
    config: Config | None = getattr(request.app.state, "config", None)
    if config is None or config.secret_key is None:
        # No key configured → endpoint is open. Acceptable for dev.
        return
    expected = config.secret_key.get_secret_value()
    if init_api_key != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


def require_initialized(request: Request) -> None:
    """Dependency that 503s every /api/* route while the server is dormant.

    Returns immediately when ``deferred_init`` is False (the normal path) so
    this has zero cost for non-deferred deployments.
    """
    init_service: InitService | None = getattr(request.app.state, "init_service", None)
    if init_service is None or init_service.state == "ready":
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            f"server is in deferred-init state '{init_service.state}'; "
            "call POST /api/init first"
        ),
    )


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
