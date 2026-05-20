"""ACP provider registry — single source of truth for built-in provider metadata.

Each record captures the static properties that are known at configuration time
(before any subprocess is launched):

- ``key``                   settings discriminator (``ACPAgentSettings.acp_server``)
- ``display_name``          human-readable label for UI display
- ``default_command``       default ``npx``-based launch command
- ``api_key_env_var``       env var the subprocess expects for its API key
- ``base_url_env_var``      env var for proxy/base-URL routing (or ``None``)
- ``default_session_mode``  ACP mode ID that disables permission prompts
- ``agent_name_patterns``   lowercase substrings in the runtime agent name;
                            used by ``ACPAgent`` to auto-detect mode / protocol
- ``supports_set_session_model``  whether to use the ``set_session_model``
                                  protocol call (vs ``_meta``) for model selection
- ``file_secrets``          provider-default credential files to materialise
                            from ``AgentContext.secrets``
- ``subscription_auth_secret`` UX metadata that explains which custom secret
                               enables subscription-style auth for the provider

Callers outside the SDK (e.g. ``openhands-agent-server``, the ``OpenHands``
frontend) can import :data:`ACP_PROVIDERS` and :func:`get_acp_provider` instead
of maintaining their own copies of this metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ACPFileSecretSpec(BaseModel):
    """Declarative mapping from an ``AgentContext`` secret to a credential file.

    The SDK writes ``secret_name`` into ``relative_path`` under a private
    per-agent temp directory, then expands ``{file}``, ``{dir}``, and ``{root}``
    placeholders in ``env`` and ``args`` so the ACP subprocess can find it.
    """

    model_config = ConfigDict(frozen=True)

    secret_name: str = Field(
        ...,
        min_length=1,
        description="Name of the AgentContext secret whose value should be written.",
    )
    relative_path: str = Field(
        ...,
        min_length=1,
        description=(
            "Relative path for the generated credential file inside an "
            "SDK-owned temp directory."
        ),
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Environment variables to set on the ACP subprocess. Values may use "
            "{file}, {dir}, and {root} placeholders."
        ),
    )
    args: list[str] = Field(
        default_factory=list,
        description=(
            "Extra command arguments to append when this secret is materialised. "
            "Values may use {file}, {dir}, and {root} placeholders."
        ),
    )
    overwrite_env: bool = Field(
        default=True,
        description=(
            "Whether generated env vars may replace values already present in "
            "the ACP subprocess environment."
        ),
    )

    @field_validator("relative_path")
    @classmethod
    def _validate_relative_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or path == Path("."):
            raise ValueError(
                "relative_path must be a non-empty relative path under the "
                "SDK-created temp directory"
            )
        return value


class ACPSubscriptionAuthSecretInfo(BaseModel):
    """UX-facing instructions for configuring subscription-style ACP auth.

    Clients can render this metadata to tell users which custom secret to
    create, what value it should contain, and how the SDK will use it.
    """

    model_config = ConfigDict(frozen=True)

    secret_name: str = Field(
        ...,
        min_length=1,
        description="Name of the custom secret users should create.",
    )
    label: str = Field(
        ...,
        min_length=1,
        description="Human-readable label for the secret.",
    )
    description: str = Field(
        ...,
        min_length=1,
        description="Short explanation suitable for settings UIs.",
    )
    value_description: str = Field(
        ...,
        min_length=1,
        description="Description of the expected secret value format.",
    )
    setup_steps: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Ordered setup instructions for users.",
    )
    extract_commands: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Optional local commands that print or locate the secret value.",
    )
    file_secret: ACPFileSecretSpec | None = Field(
        default=None,
        description=(
            "File-secret mapping the SDK uses when this custom secret is present."
        ),
    )


@dataclass(frozen=True)
class ACPProviderInfo:
    """Immutable metadata record for one built-in ACP provider."""

    key: str
    """Settings discriminator value (``ACPAgentSettings.acp_server``)."""

    display_name: str
    """Human-readable name suitable for UI labels."""

    default_command: tuple[str, ...] = field(compare=False)
    """Default subprocess command used when no explicit ``acp_command`` is set."""

    api_key_env_var: str | None
    """Env var the ACP subprocess expects for its primary API credential.

    ``None`` for providers that authenticate via browser login rather than
    an API key (e.g. Claude Code's ``claude-login`` flow).
    """

    base_url_env_var: str | None
    """Env var the ACP subprocess reads for a custom API base URL.

    Allows routing provider calls through a proxy such as LiteLLM.
    ``None`` if the provider does not support env-based base-URL override.
    """

    default_session_mode: str
    """ACP session-mode ID that suppresses all permission prompts.

    Different servers use different IDs for the same concept:

    - ``bypassPermissions`` — claude-agent-acp
    - ``full-access``       — codex-acp
    - ``yolo``              — gemini-cli
    """

    agent_name_patterns: tuple[str, ...]
    """Lowercase substring fragments present in the runtime ``agent_name``.

    ``ACPAgent`` checks these against the name returned by the ACP server's
    ``InitializeResponse`` to auto-select the correct session mode and
    determine which model-selection protocol to use.
    """

    supports_set_session_model: bool
    """``True`` if this provider uses the ``set_session_model`` protocol call.

    - ``False`` for claude-agent-acp, which uses session ``_meta`` instead.
    - ``True`` for codex-acp and gemini-cli.
    """

    session_meta_key: str | None
    """Top-level ``_meta`` key for model selection, or ``None``.

    When non-``None``, the provider selects its model via ACP session ``_meta``
    using the structure ``{session_meta_key: {"options": {"model": <model>}}}``.
    ``None`` means the provider uses the ``set_session_model`` protocol call
    instead (see :attr:`supports_set_session_model`).

    - ``"claudeCode"`` — claude-agent-acp
    - ``None``         — codex-acp, gemini-cli
    """

    file_secrets: tuple[ACPFileSecretSpec, ...] = field(
        default_factory=tuple,
        compare=False,
    )
    """Credential file mappings enabled by default for this built-in provider."""

    subscription_auth_secret: ACPSubscriptionAuthSecretInfo | None = field(
        default=None,
        compare=False,
    )
    """UX-facing custom-secret instructions for subscription-style auth."""


_CODEX_AUTH_JSON_FILE_SECRET = ACPFileSecretSpec(
    secret_name="CODEX_AUTH_JSON",
    relative_path="auth.json",
    env={"CODEX_HOME": "{dir}"},
)

_GEMINI_APPLICATION_CREDENTIALS_FILE_SECRET = ACPFileSecretSpec(
    secret_name="GOOGLE_APPLICATION_CREDENTIALS_JSON",
    relative_path="gcloud-credentials.json",
    env={"GOOGLE_APPLICATION_CREDENTIALS": "{file}"},
)


ACP_PROVIDERS: Mapping[str, ACPProviderInfo] = MappingProxyType(
    {
        "claude-code": ACPProviderInfo(
            key="claude-code",
            display_name="Claude Code",
            default_command=("npx", "-y", "@agentclientprotocol/claude-agent-acp"),
            api_key_env_var="ANTHROPIC_API_KEY",
            base_url_env_var="ANTHROPIC_BASE_URL",
            default_session_mode="bypassPermissions",
            agent_name_patterns=("claude-agent",),
            supports_set_session_model=False,
            session_meta_key="claudeCode",
        ),
        "codex": ACPProviderInfo(
            key="codex",
            display_name="Codex",
            default_command=("npx", "-y", "@zed-industries/codex-acp"),
            api_key_env_var="OPENAI_API_KEY",
            base_url_env_var="OPENAI_BASE_URL",
            default_session_mode="full-access",
            agent_name_patterns=("codex-acp",),
            supports_set_session_model=True,
            session_meta_key=None,
            file_secrets=(_CODEX_AUTH_JSON_FILE_SECRET,),
            subscription_auth_secret=ACPSubscriptionAuthSecretInfo(
                secret_name="CODEX_AUTH_JSON",
                label="Codex ChatGPT auth.json",
                description=(
                    "Create this custom secret to let codex-acp use a ChatGPT "
                    "Plus/Pro/Team subscription login instead of an API key."
                ),
                value_description=(
                    "The complete JSON contents of the Codex CLI auth file."
                ),
                setup_steps=(
                    "Run the Codex CLI login flow on a trusted machine.",
                    "Create a custom secret named CODEX_AUTH_JSON.",
                    "Paste the complete contents of ~/.codex/auth.json as the value.",
                ),
                extract_commands=("cat ~/.codex/auth.json",),
                file_secret=_CODEX_AUTH_JSON_FILE_SECRET,
            ),
        ),
        "gemini-cli": ACPProviderInfo(
            key="gemini-cli",
            display_name="Gemini CLI",
            default_command=("npx", "-y", "@google/gemini-cli", "--acp"),
            api_key_env_var="GEMINI_API_KEY",
            base_url_env_var="GEMINI_BASE_URL",
            default_session_mode="yolo",
            agent_name_patterns=("gemini-cli",),
            supports_set_session_model=True,
            session_meta_key=None,
            file_secrets=(_GEMINI_APPLICATION_CREDENTIALS_FILE_SECRET,),
            subscription_auth_secret=ACPSubscriptionAuthSecretInfo(
                secret_name="GOOGLE_APPLICATION_CREDENTIALS_JSON",
                label="Gemini CLI Google credentials JSON",
                description=(
                    "Create this custom secret to let gemini-cli authenticate "
                    "non-interactively with Google credentials that the SDK "
                    "writes to a file before starting ACP."
                ),
                value_description=(
                    "A Google Application Default Credentials or service-account "
                    "JSON document."
                ),
                setup_steps=(
                    "Create or locate a Google credentials JSON file usable by "
                    "Gemini CLI.",
                    "Create a custom secret named GOOGLE_APPLICATION_CREDENTIALS_JSON.",
                    "Paste the complete JSON file contents as the value.",
                    "Set any required Gemini CLI env vars such as "
                    "GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_CLOUD_PROJECT, and "
                    "GOOGLE_CLOUD_LOCATION in ACP environment variables.",
                ),
                extract_commands=("cat /path/to/google-credentials.json",),
                file_secret=_GEMINI_APPLICATION_CREDENTIALS_FILE_SECRET,
            ),
        ),
    }
)
"""Read-only registry of built-in ACP providers keyed by ``acp_server`` value."""


ACP_SUBSCRIPTION_AUTH_SECRETS: Mapping[str, ACPSubscriptionAuthSecretInfo] = (
    MappingProxyType(
        {
            key: info.subscription_auth_secret
            for key, info in ACP_PROVIDERS.items()
            if info.subscription_auth_secret is not None
        }
    )
)
"""Read-only subscription-auth secret instructions keyed by ACP provider."""

ACP_CODEX_SUBSCRIPTION_AUTH_SECRET = ACP_SUBSCRIPTION_AUTH_SECRETS["codex"]
"""Custom-secret instructions for Codex ChatGPT subscription auth."""

ACP_GEMINI_CLI_SUBSCRIPTION_AUTH_SECRET = ACP_SUBSCRIPTION_AUTH_SECRETS["gemini-cli"]
"""Custom-secret instructions for Gemini CLI Google credential auth."""


def get_acp_provider(key: str) -> ACPProviderInfo | None:
    """Return the :class:`ACPProviderInfo` for ``key``, or ``None`` if unknown."""
    return ACP_PROVIDERS.get(key)


def detect_acp_provider_by_agent_name(agent_name: str) -> ACPProviderInfo | None:
    """Identify a provider from the runtime ``agent_name`` string.

    Iterates :data:`ACP_PROVIDERS` in insertion order and returns the first
    entry whose :attr:`~ACPProviderInfo.agent_name_patterns` contains a
    substring of ``agent_name.lower()``.

    Returns ``None`` when no pattern matches (e.g. a ``'custom'`` server or
    an unrecognised third-party ACP implementation).
    """
    lower = agent_name.lower()
    for info in ACP_PROVIDERS.values():
        if any(pat in lower for pat in info.agent_name_patterns):
            return info
    return None


def build_session_model_meta(agent_name: str, acp_model: str | None) -> dict[str, Any]:
    """Build ACP session ``_meta`` content for model selection.

    Returns the dict to spread into ``new_session()`` kwargs for providers
    that select their model via ``_meta`` (i.e. those whose
    :attr:`~ACPProviderInfo.session_meta_key` is not ``None``).

    Returns an empty dict when *acp_model* is ``None`` or when the detected
    provider uses the ``set_session_model`` protocol call instead.
    """
    if not acp_model:
        return {}
    provider = detect_acp_provider_by_agent_name(agent_name)
    if provider is None or provider.session_meta_key is None:
        return {}
    return {provider.session_meta_key: {"options": {"model": acp_model}}}
