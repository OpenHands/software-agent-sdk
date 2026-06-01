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
- ``supports_set_session_model``  whether the provider selects its *initial*
                                  model via the ``set_session_model`` protocol
                                  call (vs session ``_meta``) at session creation
- ``supports_runtime_model_switch``  whether the server supports the
                                  ``session/set_model`` protocol call for
                                  runtime, mid-conversation model switching
- ``session_meta_key``      top-level ``_meta`` key for model selection (or ``None``)
- ``available_models``      curated list of selectable models for the provider's
                            model picker (``acp_model`` candidates)
- ``default_model``         model preselected when none is configured (or ``None``)

Callers outside the SDK (e.g. ``openhands-agent-server``, the ``OpenHands``
frontend, and the ``@openhands/typescript-client`` mirror) can import
:data:`ACP_PROVIDERS` and :func:`get_acp_provider` instead of maintaining their
own copies of this metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class ACPModelOption:
    """One selectable model for a built-in ACP provider's model picker."""

    id: str
    """Exact model identifier sent to the ACP server as ``acp_model``."""

    label: str
    """Human-readable label shown in the model picker (e.g. ``"Claude Opus 4.7"``)."""


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
    """``True`` if this provider selects its *initial* model via the
    ``set_session_model`` protocol call (rather than session ``_meta``).

    This governs the **session-creation** path only:

    - ``False`` for claude-agent-acp, which selects its initial model via
      session ``_meta`` (see :attr:`session_meta_key`).
    - ``True`` for codex-acp and gemini-cli, which get a one-shot
      ``set_session_model`` call right after the session is created.

    This is **independent of** runtime switching capability — see
    :attr:`supports_runtime_model_switch`. The original meaning of this flag
    is preserved so external consumers that use it to pick the initial
    selection path keep working.
    """

    session_meta_key: str | None
    """Top-level ``_meta`` key for model selection *at session creation*.

    When non-``None``, the provider selects its **initial** model via ACP
    session ``_meta`` using the structure
    ``{session_meta_key: {"options": {"model": <model>}}}`` passed to
    ``new_session()``. When ``None``, the initial model is applied with a
    one-shot ``set_session_model`` call right after the session is created
    (gated on :attr:`supports_set_session_model`).

    This only governs the *initial* selection; runtime switches always use
    ``set_session_model`` (gated on :attr:`supports_runtime_model_switch`).

    - ``"claudeCode"`` — claude-agent-acp
    - ``None``         — codex-acp, gemini-cli
    """

    available_models: tuple[ACPModelOption, ...] = field(default=(), compare=False)
    """Curated list of models surfaced in this provider's ``acp_model`` picker.

    These mirror the runtime picker values for each built-in harness, but are
    suggestions — not authoritative access checks. A user can still configure a
    custom ``acp_model`` the list does not contain, and actual availability
    depends on the account's plan tier. Empty for providers without a curated
    list (e.g. forward-compatible entries).
    """

    default_model: str | None = None
    """Model ID preselected when no ``acp_model`` is configured, or ``None``.

    When set, it must be one of the :attr:`available_models` ids. ``None`` lets
    the ACP server pick its own default.
    """

    supports_runtime_model_switch: bool = False
    """``True`` if the server supports the ``session/set_model`` protocol call
    for **runtime, mid-conversation model switching**.

    The call applies to the live session, so subsequent turns use the new
    model without restarting the subprocess or losing context. All three
    built-in providers support it (verified against claude-agent-acp,
    codex-acp, and gemini-cli).

    Unlike :attr:`supports_set_session_model`, this is about switching the
    model of an *already-running* session, not the initial selection. A
    provider may select its initial model via ``_meta`` (claude-agent-acp)
    yet still support ``set_session_model`` for later switches.

    Defaults to ``False`` so forward-compat providers — and any external
    caller constructing this dataclass positionally — keep working without a
    signature break; the built-in providers set it explicitly.
    """

    supports_auth_status_probe: bool = True
    """``True`` if this provider's login state can be detected via the ACP
    handshake behind ``GET /acp/auth-status`` (see ``ACPAgent.probe_auth``).

    The probe infers login from ``session/new``: an ``auth_required`` (-32000)
    error ⇒ not logged in, success ⇒ logged in. That only holds for servers
    that actually validate credentials at session-creation time (codex-acp,
    gemini-cli — both return -32000 when logged out, verified against the real
    CLIs).

    ``False`` for ``claude-agent-acp``: it does **not** gate auth at
    ``session/new`` (the call always succeeds and auth is enforced later, at
    prompt time), so a probe would false-positive ``authenticated`` for a
    logged-out user. The endpoint returns ``unknown`` for such providers instead
    of guessing. Detecting Claude login needs an out-of-band credential check
    (env / ``~/.claude`` / keychain) — tracked separately, not done here.
    """


# ---------------------------------------------------------------------------
# Curated ``acp_model`` candidate lists for the built-in providers.
#
# These are suggestions for the model picker, mirroring each harness's own
# runtime ``/model`` options. They are not authoritative access checks —
# availability ultimately depends on the user's plan tier, and a custom
# ``acp_model`` outside these lists is always allowed.
# ---------------------------------------------------------------------------

# Canonical model IDs the Claude Code CLI accepts. ``opus[1m]`` / ``sonnet[1m]``
# are the SDK-documented version-agnostic 1M-context aliases (so they auto-track
# the newest 1M-capable model — keep their labels version-less to match).
# ``opusplan`` routes planning to Opus and execution to Sonnet.
_CLAUDE_MODELS: tuple[ACPModelOption, ...] = (
    ACPModelOption(id="claude-opus-4-7", label="Claude Opus 4.7"),
    ACPModelOption(id="claude-opus-4-6", label="Claude Opus 4.6"),
    ACPModelOption(id="opus[1m]", label="Claude Opus (1M)"),
    ACPModelOption(id="claude-opus-4-5", label="Claude Opus 4.5"),
    ACPModelOption(id="claude-opus-4-1-20250805", label="Claude Opus 4.1"),
    ACPModelOption(id="claude-sonnet-4-6", label="Claude Sonnet 4.6"),
    ACPModelOption(id="sonnet[1m]", label="Claude Sonnet (1M)"),
    ACPModelOption(id="claude-sonnet-4-5", label="Claude Sonnet 4.5"),
    ACPModelOption(id="claude-haiku-4-5", label="Claude Haiku 4.5"),
    ACPModelOption(id="opusplan", label="Opus (plan) + Sonnet (execute)"),
)

# Model IDs accepted by ``@zed-industries/codex-acp``, mirroring the Codex CLI's
# ``/model`` picker. Format is ``<base-model>/<effort>`` where the trailing tier
# (``low``/``medium``/``high``/``xhigh``) hints the reasoning effort for the turn.
_CODEX_MODELS: tuple[ACPModelOption, ...] = (
    ACPModelOption(id="gpt-5.5/low", label="GPT-5.5 (low)"),
    ACPModelOption(id="gpt-5.5/medium", label="GPT-5.5 (medium)"),
    ACPModelOption(id="gpt-5.5/high", label="GPT-5.5 (high)"),
    ACPModelOption(id="gpt-5.5/xhigh", label="GPT-5.5 (xhigh)"),
    ACPModelOption(id="gpt-5.4/low", label="GPT-5.4 (low)"),
    ACPModelOption(id="gpt-5.4/medium", label="GPT-5.4 (medium)"),
    ACPModelOption(id="gpt-5.4/high", label="GPT-5.4 (high)"),
    ACPModelOption(id="gpt-5.4/xhigh", label="GPT-5.4 (xhigh)"),
    ACPModelOption(id="gpt-5.4-mini/low", label="GPT-5.4 Mini (low)"),
    ACPModelOption(id="gpt-5.4-mini/medium", label="GPT-5.4 Mini (medium)"),
    ACPModelOption(id="gpt-5.4-mini/high", label="GPT-5.4 Mini (high)"),
    ACPModelOption(id="gpt-5.4-mini/xhigh", label="GPT-5.4 Mini (xhigh)"),
    ACPModelOption(id="gpt-5.3-codex/low", label="GPT-5.3 Codex (low)"),
    ACPModelOption(id="gpt-5.3-codex/medium", label="GPT-5.3 Codex (medium)"),
    ACPModelOption(id="gpt-5.3-codex/high", label="GPT-5.3 Codex (high)"),
    ACPModelOption(id="gpt-5.3-codex/xhigh", label="GPT-5.3 Codex (xhigh)"),
    ACPModelOption(id="gpt-5.2/low", label="GPT-5.2 (low)"),
    ACPModelOption(id="gpt-5.2/medium", label="GPT-5.2 (medium)"),
    ACPModelOption(id="gpt-5.2/high", label="GPT-5.2 (high)"),
    ACPModelOption(id="gpt-5.2/xhigh", label="GPT-5.2 (xhigh)"),
)

# Model IDs accepted by ``@google/gemini-cli --acp``. The ``auto-gemini-*``
# entries delegate version selection to the CLI's router; the explicit
# ``gemini-3.1-*`` / ``gemini-2.5-*`` entries pin to a specific snapshot.
_GEMINI_MODELS: tuple[ACPModelOption, ...] = (
    ACPModelOption(id="auto-gemini-3", label="Auto (Gemini 3)"),
    ACPModelOption(id="auto-gemini-2.5", label="Auto (Gemini 2.5)"),
    ACPModelOption(id="gemini-3.1-pro-preview", label="Gemini 3.1 Pro (preview)"),
    ACPModelOption(id="gemini-3-flash-preview", label="Gemini 3 Flash (preview)"),
    ACPModelOption(
        id="gemini-3.1-flash-lite-preview", label="Gemini 3.1 Flash Lite (preview)"
    ),
    ACPModelOption(id="gemini-2.5-pro", label="Gemini 2.5 Pro"),
    ACPModelOption(id="gemini-2.5-flash", label="Gemini 2.5 Flash"),
    ACPModelOption(id="gemini-2.5-flash-lite", label="Gemini 2.5 Flash Lite"),
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
            # claude-agent-acp selects its *initial* model via session _meta
            # (session_meta_key below), so the init path does NOT use
            # set_session_model. It DOES, however, support session/set_model
            # for mid-conversation switches.
            supports_set_session_model=False,
            supports_runtime_model_switch=True,
            session_meta_key="claudeCode",
            available_models=_CLAUDE_MODELS,
            default_model="claude-opus-4-7",
            # claude-agent-acp accepts session/new without validating creds
            # (auth is enforced at prompt time), so the auth-status probe would
            # false-positive — the endpoint returns "unknown" for it instead.
            supports_auth_status_probe=False,
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
            supports_runtime_model_switch=True,
            session_meta_key=None,
            available_models=_CODEX_MODELS,
            default_model="gpt-5.5/medium",
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
            supports_runtime_model_switch=True,
            session_meta_key=None,
            available_models=_GEMINI_MODELS,
            # Match the Gemini CLI's own no-model-configured default
            # (``DEFAULT_GEMINI_MODEL_AUTO``), i.e. the auto-router — not a
            # manually-pinned snapshot. Pinning ``gemini-2.5-pro`` here would
            # make downstream clients persist a value that bypasses the CLI's
            # auto-routing.
            default_model="auto-gemini-2.5",
        ),
    }
)
"""Read-only registry of built-in ACP providers keyed by ``acp_server`` value."""


# Per-provider CLI auth-status fallback for servers whose ACP handshake can't
# report login state (``supports_auth_status_probe=False``). These args are
# appended to the provider's launch command to invoke the CLI's own auth-status
# subcommand, which prints JSON containing a ``loggedIn`` boolean
# (see ``ACPAgent.probe_cli_auth_status``). For ``claude-agent-acp`` the ``--cli``
# flag re-exposes the bundled Claude CLI's ``auth status`` so no separate
# ``claude`` binary need be installed.
#
# Kept as a module constant rather than an ``ACPProviderInfo`` field so the
# dataclass — and its exact mirror in typescript-client's ``acp-providers.json``
# (enforced by the drift check) — stays unchanged.
ACP_CLI_AUTH_STATUS_ARGS: dict[str, tuple[str, ...]] = {
    "claude-code": ("--cli", "auth", "status", "--json"),
}


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
