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
- ``file_secrets``          reserved "file-content" credential secrets the
                            provider authenticates from (Codex ``auth.json``,
                            Gemini Vertex SA JSON); see :class:`ACPFileSecretSpec`

Callers outside the SDK (e.g. ``openhands-agent-server``, the ``OpenHands``
frontend, and the ``@openhands/typescript-client`` mirror) can import
:data:`ACP_PROVIDERS` and :func:`get_acp_provider` instead of maintaining their
own copies of this metadata.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


@dataclass(frozen=True)
class ACPModelOption:
    """One selectable model for a built-in ACP provider's model picker."""

    id: str
    """Exact model identifier sent to the ACP server as ``acp_model``."""

    label: str
    """Human-readable label shown in the model picker (e.g. ``"Claude Opus 4.7"``)."""


class ACPFileSecretSpec(BaseModel):
    """Declarative mapping from a reserved "file-content" secret to a credential
    file the ACP subprocess authenticates from.

    Some providers read their credential from a *file on disk* rather than an
    env var: Codex reads ``$CODEX_HOME/auth.json``; Gemini (Vertex AI) reads a
    service-account JSON pointed at by ``GOOGLE_APPLICATION_CREDENTIALS``. The
    user supplies that credential as a pasted blob — a reserved secret named
    :attr:`secret_name` — and :class:`~openhands.sdk.agent.ACPAgent` materialises
    it to :attr:`filename` under the conversation's durable per-conversation root
    (seed-if-absent), then sets :attr:`env_var` so the CLI can find it.

    Materialisation is keyed off :attr:`secret_name` (not the launch command),
    so a custom or aliased ``acp_command`` still works as long as the reserved
    secret is supplied.

    The SDK owns the *mechanism* (writing the file in the runtime pod, setting
    the env var, seed-if-absent, permissions); the *policy* — which secrets map
    to which files for which CLIs — lives in these specs. Built-in defaults
    cover the supported providers, but downstream applications can override
    :attr:`~openhands.sdk.agent.ACPAgent.acp_file_secrets` to support other ACP
    servers with different file-auth schemes without an SDK change.
    """

    model_config = ConfigDict(frozen=True)

    secret_name: str = Field(min_length=1)
    """Reserved secret whose value is the credential file's contents (looked up
    in ``state.secret_registry`` / ``agent_context.secrets``)."""

    filename: str = Field(min_length=1)
    """Basename of the materialised file (e.g. ``auth.json``)."""

    env_var: str = Field(min_length=1)
    """Env var the CLI reads to locate the materialised credential."""

    subdir: str = Field(min_length=1)
    """Folder under the per-conversation ``<conversations>/{id.hex}/acp/`` root
    where the file is written — the provider key for built-ins (``codex`` /
    ``gemini-cli``), or any stable folder name for a custom spec. Keeps
    concurrent providers' credential files isolated within one sandbox."""

    env_points_to: Literal["dir", "file"] = "file"
    """Whether :attr:`env_var` is set to the file's parent *directory* (Codex's
    ``CODEX_HOME``) or to the *file* path itself (Gemini's
    ``GOOGLE_APPLICATION_CREDENTIALS``)."""

    warn_if_unset: tuple[str, ...] = ()
    """Companion env vars to warn about when this secret is materialised but
    they are missing (e.g. ``GOOGLE_CLOUD_PROJECT`` / ``GOOGLE_CLOUD_LOCATION``
    for Vertex AI). Advisory only — materialisation still proceeds."""

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        """``filename`` must be a bare basename, never a path or traversal."""
        if "/" in value or "\\" in value or value in (".", ".."):
            raise ValueError("filename must be a bare basename, not a path")
        return value

    @field_validator("subdir")
    @classmethod
    def _validate_subdir(cls, value: str) -> str:
        """``subdir`` must be a real relative folder (no traversal, root escape,
        or the ``.`` identity path that would drop the credential straight into
        the shared ``acp/`` root where two specs could collide)."""
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or value.strip() in ("", "."):
            raise ValueError(
                "subdir must be a non-empty relative path without '.'/'..' segments"
            )
        return value


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

    file_secrets: tuple[ACPFileSecretSpec, ...] = field(default=(), compare=False)
    """Reserved file-content credential secrets this provider authenticates from.

    Each entry maps a reserved secret name to the on-disk file (and the env var
    pointing at it) that :class:`~openhands.sdk.agent.ACPAgent` materialises
    before launching the subprocess. Empty for providers that authenticate
    purely via env vars (e.g. Claude Code). Defaults to ``()`` so external
    callers constructing this dataclass positionally keep working.
    """

    binary_name: str | None = field(default=None, compare=False)
    """Pinned, pre-installed CLI binary for this provider (e.g. ``codex-acp``).

    The agent-server image installs the ACP CLIs at a fixed version as ``PATH``
    wrappers. When this binary resolves via :func:`shutil.which`,
    :meth:`~openhands.sdk.settings.model.ACPAgentSettings.resolve_acp_command`
    rewrites the ``npx -y <pkg>`` launch command to run it directly (preserving
    trailing args like gemini's ``--acp``); otherwise the ``npx`` command is
    used unchanged. ``None`` for providers with no pinned binary (and ``custom``
    servers); defaulted so positional construction keeps working.
    """

    session_subtrees: tuple[str, ...] = field(default=(), compare=False)
    """Top-level subdirectories of the data root that hold session transcripts.

    The allowlist for session-blob export/import (see
    :mod:`openhands.sdk.settings.acp_session_blob`): only files under these
    subtrees of the per-conversation data root (``<persistence_dir>/acp/<key>``)
    are ever packed into — or restored from — a session snapshot. Everything
    else at the data root (``auth.json``, ``.credentials.json``,
    ``history.jsonl``, config, caches) is excluded by construction, so live
    credentials never ride along with a snapshot; auth is re-established on
    every launch by file-secret materialisation / env-var secrets.

    - ``("sessions",)`` — codex-acp (``$CODEX_HOME/sessions/**`` rollout JSONLs;
      what ``session/load`` reads)
    - ``("projects",)`` — claude-agent-acp (``$CLAUDE_CONFIG_DIR/projects/
      <cwd-slug>/<session-id>.jsonl``; the filename is the ``session/load`` id)
    - ``()``            — gemini-cli (no reliable ``session/load``; its only
      data-dir lever is ``HOME``, far too broad to snapshot)

    Empty means the provider does not support session snapshots.
    """

    data_dir_env_var: str | None = None
    """Env var that relocates this CLI's per-user data/config root.

    Set it to a per-conversation directory to isolate the CLI's on-disk state
    (config, transcripts, caches, lockfiles) when several of a user's
    conversations share one sandbox (``SandboxGroupingStrategy != NO_GROUPING``)
    — otherwise they race on a single shared ``HOME`` (see #1019). Each CLI
    exposes a different lever:

    - ``CODEX_HOME``        — codex-acp (relocates ``~/.codex`` wholesale)
    - ``CLAUDE_CONFIG_DIR`` — claude-agent-acp (relocates ``~/.claude*``)
    - ``HOME``              — gemini-cli (no dedicated var; it hard-codes
      ``~/.gemini`` and ignores ``XDG``, so only ``HOME`` moves it)

    ``None`` for providers with no known relocation lever, which then skip
    isolation. Consumed by
    :attr:`~openhands.sdk.agent.ACPAgent.acp_isolate_data_dir`.
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


# ---------------------------------------------------------------------------
# Reserved file-content credential secrets for the built-in providers.
#
# Codex's ChatGPT-subscription ``auth.json`` relocates with ``CODEX_HOME`` (and
# is rewritten in place on token refresh, so it must live on durable, writable
# storage). Gemini's Vertex AI service-account JSON is pointed at directly by
# ``GOOGLE_APPLICATION_CREDENTIALS``; Vertex also needs a project/location, so
# warn when those are unset.
# ---------------------------------------------------------------------------
_CODEX_FILE_SECRETS: tuple[ACPFileSecretSpec, ...] = (
    ACPFileSecretSpec(
        secret_name="CODEX_AUTH_JSON",
        filename="auth.json",
        env_var="CODEX_HOME",
        subdir="codex",
        env_points_to="dir",
    ),
)
_GEMINI_FILE_SECRETS: tuple[ACPFileSecretSpec, ...] = (
    ACPFileSecretSpec(
        secret_name="GOOGLE_APPLICATION_CREDENTIALS_JSON",
        filename="gcloud-credentials.json",
        env_var="GOOGLE_APPLICATION_CREDENTIALS",
        subdir="gemini-cli",
        env_points_to="file",
        warn_if_unset=("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"),
    ),
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
            binary_name="claude-agent-acp",
            data_dir_env_var="CLAUDE_CONFIG_DIR",
            session_subtrees=("projects",),
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
            file_secrets=_CODEX_FILE_SECRETS,
            binary_name="codex-acp",
            data_dir_env_var="CODEX_HOME",
            session_subtrees=("sessions",),
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
            file_secrets=_GEMINI_FILE_SECRETS,
            binary_name="gemini",
            # Gemini CLI has no dedicated config-dir var; it hard-codes
            # ``~/.gemini`` (ignoring XDG), so only HOME relocates its state.
            data_dir_env_var="HOME",
        ),
    }
)
"""Read-only registry of built-in ACP providers keyed by ``acp_server`` value."""


def default_acp_file_secrets() -> tuple[ACPFileSecretSpec, ...]:
    """Built-in file-content credential specs across all supported providers.

    The union of every :attr:`ACPProviderInfo.file_secrets` (Codex ``auth.json``,
    Gemini Vertex SA). Used as the default for
    :attr:`~openhands.sdk.agent.ACPAgent.acp_file_secrets`, which a downstream
    application may override or extend to support other ACP servers without an
    SDK change.
    """
    return tuple(spec for info in ACP_PROVIDERS.values() for spec in info.file_secrets)


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


def detect_acp_provider_by_command(
    command: Sequence[str],
) -> ACPProviderInfo | None:
    """Identify a provider from its launch ``command``, before the subprocess runs.

    Each provider's :attr:`~ACPProviderInfo.agent_name_patterns` fragments
    (``"codex-acp"``, ``"claude-agent"``, ``"gemini-cli"``) are prefixes of its
    npm-package / binary basename, so we can pick the provider *before* the server
    starts and reports its name (when the subprocess environment, e.g. a relocated
    data dir, must already be set).

    Matching is deliberately stricter than
    :func:`detect_acp_provider_by_agent_name` because the launch command is
    *caller-controlled*: each token is reduced to its basename (last path segment,
    minus a trailing ``@version`` pin) and a provider matches only when that
    basename *starts with* one of its patterns. This accepts the real forms —
    ``@zed-industries/codex-acp``, ``@google/gemini-cli@0.43.0``,
    ``/opt/node_modules/.bin/codex-acp`` — while rejecting incidental substrings
    like ``my-codex-acp-wrapper`` or ``/opt/shims/not-codex-acp`` that a plain
    substring test would misattribute.

    Returns ``None`` for a custom/unrecognised command, so callers that require a
    known provider (e.g. data-dir isolation) safely no-op.
    """
    bases: list[str] = []
    for token in command:
        base = token.rsplit("/", 1)[-1].lower()
        at = base.rfind("@")
        if at > 0:  # strip a trailing @version pin (not a leading @scope)
            base = base[:at]
        bases.append(base)
    for info in ACP_PROVIDERS.values():
        if any(
            base.startswith(pat) for base in bases for pat in info.agent_name_patterns
        ):
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
