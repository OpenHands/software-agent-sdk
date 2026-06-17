import json
import logging
import os
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from openhands.agent_server.env_parser import (
    MISSING,
    _get_default_parsers,
    from_env,  # noqa: F401 - compatibility re-export
    get_env_parser,
    merge,
)
from openhands.sdk.utils.cipher import Cipher


# Environment variable constants
V0_SESSION_API_KEY_ENV = "SESSION_API_KEY"
V1_SESSION_API_KEY_ENV = "OH_SESSION_API_KEYS_0"
ENVIRONMENT_VARIABLE_PREFIX = "OH"
CONFIG_PATH_ENV = "OPENHANDS_AGENT_SERVER_CONFIG_PATH"
DEFAULT_CONFIG_PATH = Path("workspace/openhands_agent_server_config.json")
_logger = logging.getLogger(__name__)


def _default_session_api_keys():
    """
    This function exists as a fallback to using this old V0 environment
    variable. If new V1_SESSION_API_KEYS_0 environment variable exists,
    it is read automatically by the EnvParser and this function is never
    called.
    """
    result = []
    session_api_key = os.getenv(V0_SESSION_API_KEY_ENV)
    if session_api_key:
        result.append(session_api_key)
    return result


def _default_secret_key() -> SecretStr | None:
    """
    If the OH_SECRET_KEY environment variable is present, it is read by the EnvParser
    and this function is never called. Otherwise, we fall back to using the first
    available session_api_key - which we read from the environment.
    We check both the V0 and V1 variables for this.
    """
    session_api_key = os.getenv(V0_SESSION_API_KEY_ENV)
    if session_api_key:
        return SecretStr(session_api_key)
    session_api_key = os.getenv(V1_SESSION_API_KEY_ENV)
    if session_api_key:
        return SecretStr(session_api_key)
    return None


def _default_web_url() -> str | None:
    web_url = os.getenv("OH_WEB_URL")
    if web_url:
        return web_url

    return None


class WebhookSpec(BaseModel):
    """Spec to create a webhook. All webhook requests use POST method."""

    # General parameters
    event_buffer_size: int = Field(
        default=5,
        ge=1,
        description=(
            "The number of events to buffer locally before posting to the webhook"
        ),
    )
    base_url: str = Field(
        description="The base URL of the webhook service. Events will be sent to "
        "{base_url}/events and conversation info to {base_url}/conversations"
    )
    headers: dict[str, str] = Field(default_factory=dict)
    flush_delay: float = Field(
        default=30.0,
        gt=0,
        description=(
            "The delay in seconds after which buffered events will be flushed to "
            "the webhook, even if the buffer is not full. Timer is reset on each "
            "new event."
        ),
    )

    # Retry parameters
    num_retries: int = Field(
        default=3,
        ge=0,
        description="The number of times to retry if the post operation fails",
    )
    retry_delay: int = Field(default=5, ge=0, description="The delay between retries")

    # Backpressure parameters
    max_queue_size: int = Field(
        default=1000,
        ge=1,
        description=(
            "Upper bound on the number of events buffered for delivery. When the "
            "downstream is failing and events are re-queued for retry, the oldest "
            "events are dropped past this bound to prevent unbounded memory growth."
        ),
    )


class Config(BaseModel):
    """
    Immutable configuration for a server running in local mode.
    (Typically inside a sandbox).
    """

    session_api_keys: list[str] = Field(
        default_factory=_default_session_api_keys,
        description=(
            "List of valid session API keys used to authenticate incoming requests. "
            "Empty list implies the server will be unsecured. Any key in this list "
            "will be accepted for authentication. Multiple keys are supported to "
            "enable key rotation without service disruption - new keys can be added "
            "to the list, then clients are updated with the new key, and finally the "
            "old key is removed from the list. "
        ),
    )
    allow_cors_origins: list[str] = Field(
        default_factory=list,
        description=(
            "CORS origins permitted by this server. Localhost / 127.0.0.1 "
            "and ``DOCKER_HOST_ADDR`` are always allowed. Does not apply to "
            "the workspace cookie routes, which accept any origin — see "
            "``middleware.py``."
        ),
    )
    allow_cors_origin_regex: str | None = Field(
        default=None,
        description=(
            "Regular expression matching additional CORS origins permitted by "
            "this server. Localhost / 127.0.0.1 and ``DOCKER_HOST_ADDR`` are "
            "always allowed. Does not apply to the workspace cookie routes, "
            "which accept any origin — see ``middleware.py``."
        ),
    )
    conversations_path: Path = Field(
        default=Path("workspace/conversations"),
        description=(
            "The location of the directory where conversations and events are stored."
        ),
    )
    workspace_path: Path = Field(
        default=Path("workspace/project"),
        description=(
            "Default workspace directory for conversations created by the server."
        ),
    )
    bash_events_dir: Path = Field(
        default=Path("workspace/bash_events"),
        description=(
            "The location of the directory where bash events are stored as files. "
            "Defaults to 'workspace/bash_events'."
        ),
    )
    bash_events_retention_seconds: int | None = Field(
        default=None,
        gt=0,
        description=(
            "How long bash event files are retained on disk, in seconds. "
            "A background task purges events older than this window on a "
            "rolling basis. None (default) retains events indefinitely. "
            "Should be set higher than the longest expected command timeout: "
            "a command whose BashCommand file is purged mid-execution will "
            "complete normally, but its on-disk event history will be "
            "incomplete. A value >= 2x max command timeout avoids this."
        ),
    )
    static_files_path: Path | None = Field(
        default=None,
        description=(
            "The location of the directory containing static files to serve. "
            "If specified and the directory exists, static files will be served "
            "at the /static/ endpoint."
        ),
    )
    webhooks: list[WebhookSpec] = Field(
        default_factory=list,
        description="Webhooks to invoke in response to events",
    )
    enable_vscode: bool = Field(
        default=True,
        description="Whether to enable VSCode server functionality",
    )
    vscode_port: int = Field(
        default=8001,
        ge=1,
        le=65535,
        description="Port on which VSCode server should run",
    )
    vscode_base_path: str | None = Field(
        default=None,
        description=(
            "Base path for VSCode server (used in path-based routing). "
            "For example, '/{runtime_id}/vscode' when using path-based routing."
        ),
    )
    enable_vnc: bool = Field(
        default=False,
        description="Whether to enable VNC desktop functionality",
    )
    preload_tools: bool = Field(
        default=True,
        description="Whether to preload tools",
    )
    max_concurrent_runs: int = Field(
        default=10,
        ge=1,
        description=(
            "Maximum number of conversations that can execute agent steps "
            "concurrently.  Controls the size of the dedicated thread pool "
            "used for conversation.run() calls."
        ),
    )
    secret_key: SecretStr | None = Field(
        default_factory=_default_secret_key,
        description=(
            "Secret key used for encrypting sensitive values in all serialized data. "
            "If missing, any sensitive data is redacted, meaning full state cannot"
            "be restored between restarts."
        ),
    )
    web_url: str | None = Field(
        default_factory=_default_web_url,
        description=(
            "The URL where this agent server instance is available externally"
        ),
    )
    deferred_init: bool = Field(
        default=False,
        description=(
            "When True, the server starts in dormant mode. Stateless services "
            "(VSCode, tool preload, etc.) start as usual, but the conversation, "
            "event, and bash routers return 503 until POST /api/init is called with "
            "the runtime configuration. This is intended for warm-pool deployments "
            "where pods are pre-warmed before a user is matched and per-user "
            "configuration is delivered later."
        ),
    )
    model_config: ClassVar[ConfigDict] = {"frozen": True}

    @property
    def cipher(self) -> Cipher | None:
        cipher = getattr(self, "_cipher", None)
        if cipher is None:
            if self.secret_key is None:
                _logger.warning(
                    "⚠️ OH_SECRET_KEY was not defined. Secrets will not "
                    "be persisted between restarts."
                )
                cipher = None
            else:
                cipher = Cipher(self.secret_key.get_secret_value())
            setattr(self, "_cipher", cipher)
        return cipher


_default_config: Config | None = None


def _read_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a JSON object: {path}")
    return data


def load_config(config_path: Path | None = None) -> Config:
    """Load agent-server config from JSON file and environment variables.

    Values from ``OH_*`` environment variables override values from the JSON
    config file so deployment-specific environment overrides keep working.
    """
    resolved_path = config_path
    if resolved_path is None:
        resolved_path = Path(os.getenv(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH))

    file_data = _read_config_file(resolved_path)
    parser = get_env_parser(Config, _get_default_parsers())
    env_data = parser.from_env(ENVIRONMENT_VARIABLE_PREFIX)

    if env_data is MISSING:
        data = file_data
    else:
        data = merge(file_data, env_data)

    if not data:
        return Config()
    return Config.model_validate(data)


def get_default_config() -> Config:
    """Get the default local server config shared across the server"""
    global _default_config
    if _default_config is None:
        _default_config = load_config()
        assert _default_config is not None
    return _default_config
