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

Callers outside the SDK (e.g. ``openhands-agent-server``, the ``OpenHands``
frontend) can import :data:`ACP_PROVIDERS` and :func:`get_acp_provider` instead
of maintaining their own copies of this metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


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
        ),
    }
)
"""Read-only registry of built-in ACP providers keyed by ``acp_server`` value."""


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
