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
- ``supports_set_session_model``  whether the provider applies its *initial*
                                  model via a protocol call at session creation
                                  (``set_config_option``/``set_session_model``)
- ``supports_runtime_model_switch``  whether the server supports a protocol-level
                                  model switch for runtime, mid-conversation use
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
    """ACP session-mode ID set right after ``session/new``.

    For servers with a permission-suppressing mode that is the value:
    ``bypassPermissions`` (claude-agent-acp), ``full-access`` (codex-acp).
    gemini-cli uses ``default`` (its ``yolo`` mode errors at init); the ACP
    bridge auto-approves permission requests, so the mode doesn't gate prompts.
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

    This governs the **session-creation** path only. ``True`` for all three
    built-in providers, which get a one-shot ``set_session_model`` call right
    after the session is created. claude-agent-acp was ``False`` until 0.30.0
    was found to silently ignore the session-``_meta`` selection it relied on
    (#3654); its ``_meta`` payload is still sent alongside (see
    :attr:`session_meta_key`).

    This is **independent of** runtime switching capability — see
    :attr:`supports_runtime_model_switch`. The original meaning of this flag
    is preserved so external consumers that use it to pick the initial
    selection path keep working.
    """

    session_meta_key: str | None
    """Top-level ``_meta`` key for model selection *at session creation*.

    When non-``None``, the model is additionally advertised via ACP session
    ``_meta`` using the structure
    ``{session_meta_key: {"options": {"model": <model>}}}`` passed to
    ``new_session()``. This is best-effort only: claude-agent-acp ignores it
    (#3654), so the authoritative initial selection is the protocol call gated
    on :attr:`supports_set_session_model`.

    Runtime switches use the mechanism the session advertised
    (``set_config_option`` or ``set_session_model``), gated on
    :attr:`supports_runtime_model_switch`.

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
    yet still support a protocol-level switch for later changes.

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

# Model IDs the Claude Code CLI accepts, mirroring the ``model`` configOptions
# select claude-agent-acp 0.44.0 reports at ``session/new`` (the short aliases
# the CLI's own ``/model`` menu offers, switched via ``set_config_option``).
# ``opus[1m]`` is the SDK-documented version-agnostic 1M-context alias and the
# CLI's own default (``currentValue``); ``default`` is the CLI's recommended
# tier (Opus 4.8 · 1M). The ``/model`` menu is dynamic/account-dependent and the
# CLI validates ``set_config_option(model)`` against the live select — it rejects
# an absent id (e.g. ``sonnet`` on accounts without it), so these are pre-session
# suggestions, not ground truth; a rejected id degrades to the server default.
_CLAUDE_MODELS: tuple[ACPModelOption, ...] = (
    ACPModelOption(id="default", label="Default (recommended)"),
    ACPModelOption(id="opus[1m]", label="Claude Opus 4.8 (1M)"),
    ACPModelOption(id="sonnet", label="Claude Sonnet 4.6"),
    ACPModelOption(id="haiku", label="Claude Haiku 4.5"),
)

# Bare preset ids the ``@zed-industries/codex-acp`` 0.16 ``model`` configOptions
# select reports at ``session/new`` (``set_config_option(configId="model")``
# targets). The reasoning-effort tier is a *separate* ``reasoning_effort``
# configOption on 0.16, not part of the model id, so it is not encoded here.
_CODEX_MODELS: tuple[ACPModelOption, ...] = (
    ACPModelOption(id="gpt-5.5", label="GPT-5.5"),
    ACPModelOption(id="gpt-5.4", label="GPT-5.4"),
    ACPModelOption(id="gpt-5.4-mini", label="GPT-5.4 Mini"),
)

# Model IDs accepted by ``@google/gemini-cli --acp``. Mirrors the
# ``availableModels`` the CLI reports at ``session/new`` on the pinned version
# (gemini-cli 0.46.0). ``auto`` delegates version selection to the CLI's
# router; the explicit ``gemini-*`` entries pin to a specific snapshot. The CLI
# also accepts ids outside this list (it remaps them at generation), so these
# are curated suggestions, not an access check.
_GEMINI_MODELS: tuple[ACPModelOption, ...] = (
    ACPModelOption(id="auto", label="Auto"),
    # gemini-cli 0.46 surfaces the pro-preview as ``gemini-3.1-pro-preview`` once
    # the Gemini 3.1 launch flag is on (``PREVIEW_GEMINI_3_1_MODEL``), falling
    # back to ``gemini-3-pro-preview`` (``PREVIEW_GEMINI_MODEL``) otherwise — keep
    # both so the picker matches either rollout state.
    ACPModelOption(id="gemini-3.1-pro-preview", label="Gemini 3.1 Pro (preview)"),
    ACPModelOption(id="gemini-3-pro-preview", label="Gemini 3 Pro (preview)"),
    ACPModelOption(id="gemini-3-flash-preview", label="Gemini 3 Flash (preview)"),
    ACPModelOption(id="gemini-3.1-flash-lite", label="Gemini 3.1 Flash Lite"),
    ACPModelOption(id="gemini-2.5-pro", label="Gemini 2.5 Pro"),
    ACPModelOption(id="gemini-2.5-flash", label="Gemini 2.5 Flash"),
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


# Pinned npm versions for the built-in ACP launchers. Keep in sync with the
# `npm install -g` line in
# openhands-agent-server/openhands/agent_server/docker/Dockerfile — a bump must
# edit both. The pin constrains the native (no pre-installed binary) path, where
# the bare `npx -y <pkg>` would otherwise resolve npm `latest` at launch under a
# permission-disabling session mode. In the image the binary rewrite in
# `ACPAgentSettings.resolve_acp_command` runs the pinned `binary_name` instead,
# so the `@version` suffix is a no-op there.
#
# claude-agent-acp 0.44+ / codex-acp 0.16+ select the model via a ``model``
# ``configOptions`` entry rather than ``session/set_model``; the SDK detects
# which per session and applies it through the matching call.
CLAUDE_AGENT_ACP_VERSION = "0.44.0"
CODEX_ACP_VERSION = "0.16.0"
GEMINI_CLI_VERSION = "0.46.0"


ACP_PROVIDERS: Mapping[str, ACPProviderInfo] = MappingProxyType(
    {
        "claude-code": ACPProviderInfo(
            key="claude-code",
            display_name="Claude Code",
            default_command=(
                "npx",
                "-y",
                f"@agentclientprotocol/claude-agent-acp@{CLAUDE_AGENT_ACP_VERSION}",
            ),
            api_key_env_var="ANTHROPIC_API_KEY",
            base_url_env_var="ANTHROPIC_BASE_URL",
            default_session_mode="bypassPermissions",
            agent_name_patterns=("claude-agent",),
            # claude-agent-acp ignores the session-_meta model selection (the
            # requested model only becomes a picker option; the session keeps
            # running its default), so the init path must push the model via a
            # protocol call (#3654). On 0.44.0+ that call is
            # ``set_config_option(configId="model")`` rather than
            # ``set_session_model`` (auto-detected from session/new); the _meta
            # payload (session_meta_key below) is still sent — harmless, and
            # picks up the same model if a future CLI honours it.
            supports_set_session_model=True,
            supports_runtime_model_switch=True,
            session_meta_key="claudeCode",
            available_models=_CLAUDE_MODELS,
            # The CLI's own default (model configOptions ``currentValue``).
            default_model="opus[1m]",
            binary_name="claude-agent-acp",
            data_dir_env_var="CLAUDE_CONFIG_DIR",
        ),
        "codex": ACPProviderInfo(
            key="codex",
            display_name="Codex",
            default_command=(
                "npx",
                "-y",
                f"@zed-industries/codex-acp@{CODEX_ACP_VERSION}",
            ),
            api_key_env_var="OPENAI_API_KEY",
            base_url_env_var="OPENAI_BASE_URL",
            default_session_mode="full-access",
            agent_name_patterns=("codex-acp",),
            supports_set_session_model=True,
            supports_runtime_model_switch=True,
            session_meta_key=None,
            available_models=_CODEX_MODELS,
            default_model="gpt-5.5",
            file_secrets=_CODEX_FILE_SECRETS,
            binary_name="codex-acp",
            data_dir_env_var="CODEX_HOME",
        ),
        "gemini-cli": ACPProviderInfo(
            key="gemini-cli",
            display_name="Gemini CLI",
            default_command=(
                "npx",
                "-y",
                f"@google/gemini-cli@{GEMINI_CLI_VERSION}",
                "--acp",
            ),
            api_key_env_var="GEMINI_API_KEY",
            base_url_env_var="GEMINI_BASE_URL",
            # gemini-cli 0.46.0 rejects ``set_session_mode("yolo")`` at session
            # init (-32603), which crashes headless startup; ``default`` is
            # accepted. The ACP bridge auto-approves every request_permission, so
            # prompts never block regardless of mode. See #3772.
            default_session_mode="default",
            agent_name_patterns=("gemini-cli",),
            supports_set_session_model=True,
            supports_runtime_model_switch=True,
            session_meta_key=None,
            available_models=_GEMINI_MODELS,
            # Match the Gemini CLI's own auto-router rather than a manually
            # pinned snapshot. Pinning e.g. ``gemini-2.5-pro`` here would make
            # downstream clients persist a value that bypasses the CLI's
            # auto-routing. ``auto`` is the router id the CLI reports in its
            # 0.46.0 ``availableModels``.
            default_model="auto",
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
    ``@zed-industries/codex-acp``, ``@google/gemini-cli@0.46.0``,
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
