"""ACPAgent — an AgentBase subclass that delegates to an ACP server.

The Agent Client Protocol (ACP) lets OpenHands power conversations using
ACP-compatible servers (Claude Code, Gemini CLI, etc.) instead of direct
LLM calls.  The ACP server manages its own LLM, tools, and execution;
the ACPAgent relays user messages and collects the response. OpenHands
can still append prompt-only context, such as a skill catalog, to the
user message before it is sent to the ACP server.

Unlike the built-in Agent, one ACP ``step()`` maps to one complete remote
assistant turn. ACPAgent therefore emits a terminal ``FinishAction`` at the
end of each step to delimit that completed turn for downstream consumers.

See https://agentclientprotocol.com/protocol/overview
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Generator
from concurrent.futures import Future
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, NamedTuple

from acp.client.connection import ClientSideConnection
from acp.exceptions import RequestError as ACPRequestError
from acp.helpers import image_block, text_block
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    AllowedOutcome,
    EnvVariable,
    HttpHeader,
    HttpMcpServer,
    ImageContentBlock,
    McpServerStdio,
    PromptResponse,
    RequestPermissionResponse,
    SseMcpServer,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
)
from acp.transports import default_environment
from pydantic import (
    Field,
    PrivateAttr,
    SecretStr,
    ValidationInfo,
    field_serializer,
    field_validator,
)

from openhands.sdk.agent.acp_models import ACPModelInfo
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.context import AgentContext
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import (
    ACPToolCallEvent,
    ActionEvent,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import LLM, ImageContent, Message, MessageToolCall, TextContent
from openhands.sdk.logger import get_logger
from openhands.sdk.observability.laminar import maybe_init_laminar, observe
from openhands.sdk.settings.acp_providers import (
    ACPFileSecretSpec,
    build_session_model_meta,
    default_acp_file_secrets,
    detect_acp_provider_by_agent_name,
    detect_acp_provider_by_command,
)
from openhands.sdk.tool import Tool  # noqa: TC002
from openhands.sdk.tool.builtins.finish import FinishAction, FinishObservation
from openhands.sdk.utils import maybe_truncate
from openhands.sdk.utils.deprecation import warn_deprecated
from openhands.sdk.utils.pydantic_secrets import (
    serialize_secret,
    validate_secret,
    validate_secret_dict,
)


logger = get_logger(__name__)
maybe_init_laminar()


if TYPE_CHECKING:
    from openhands.sdk.conversation import (
        ConversationCallbackType,
        ConversationState,
        ConversationTokenCallbackType,
        LocalConversation,
    )


# Maximum seconds to wait for a UsageUpdate notification after prompt()
# returns. The ACP server writes UsageUpdate to the wire before the
# PromptResponse, so under normal conditions the notification handler
# completes almost immediately. This timeout is a safety net for slow
# or remote servers.
_USAGE_UPDATE_TIMEOUT: float = float(os.environ.get("ACP_USAGE_UPDATE_TIMEOUT", "2.0"))

# Retry configuration for transient ACP connection errors.
# These errors can occur when the connection drops mid-conversation but the
# session state is still valid on the server side.
_ACP_PROMPT_MAX_RETRIES: int = int(os.environ.get("ACP_PROMPT_MAX_RETRIES", "3"))

# After a timeout/cancellation, wait briefly for the ACP prompt task to react
# to session/cancel before rewiring callbacks for the next turn.
_ACP_CANCEL_DRAIN_TIMEOUT: float = float(
    os.environ.get("ACP_CANCEL_DRAIN_TIMEOUT", "2.0")
)

_ACP_PROMPT_RETRY_DELAYS: tuple[float, ...] = (5.0, 15.0, 30.0)  # seconds

# Exception types that indicate transient connection issues worth retrying
_RETRIABLE_CONNECTION_ERRORS = (OSError, ConnectionError, BrokenPipeError, EOFError)

# ``npm run`` exports package/lifecycle configuration into descendants. ACP
# providers launched through ``npx`` must not inherit that context, otherwise npm
# can try to resolve against the parent package instead of the requested one.
_INHERITED_NPM_ENV_EXACT: frozenset[str] = frozenset({"INIT_CWD"})
_INHERITED_NPM_ENV_PREFIXES: tuple[str, ...] = ("npm_", "NPM_")

# JSON-RPC error codes from the ACP server that are transient and worth
# retrying.  These map to server-side failures (HTTP 500 equivalents) where
# the session state is still valid but the request failed.
# -32603 = "Internal error" (JSON-RPC spec) — covers ACP server crashes,
#          upstream model 500s, and transient infrastructure errors.
_RETRIABLE_SERVER_ERROR_CODES: frozenset[int] = frozenset({-32603})


# Maximum characters for ACP tool call content — matches MAX_CMD_OUTPUT_SIZE
# used by the terminal tool and the default max_message_chars in LLM config.
MAX_ACP_CONTENT_CHARS: int = 30_000

# Env vars that must be removed from the subprocess environment when a
# particular "dominant" env var is present.
#
# Rationale: Claude Code's subscription auth uses CLAUDE_CODE_OAUTH_TOKEN, a
# bearer validated against api.anthropic.com. A co-present ANTHROPIC_API_KEY
# would take precedence over the token (silently bypassing the subscription),
# and an ANTHROPIC_BASE_URL would route the bearer to a proxy that rejects it —
# either silently breaks the intended OAuth auth. When the OAuth token is the
# active credential we strip both so the subprocess authenticates with the
# token against api.anthropic.com.
#
# Keyed on the credential itself (CLAUDE_CODE_OAUTH_TOKEN), NOT on
# CLAUDE_CONFIG_DIR: the config dir is a *location* lever (data-dir isolation,
# #1019) that is orthogonal to which credential is active. Keying the strip on
# it wrongly fired during API-key isolation and missed the conflict when the
# token arrived via env without isolation (#3588).
_ENV_CONFLICT_MAP: dict[str, frozenset[str]] = {
    "CLAUDE_CODE_OAUTH_TOKEN": frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"}),
}

# Number of trailing characters of an ACP session id retained in log lines
# for correlation.  ACP session ids are server-issued tokens whose possession
# alone is enough to call ``session/load`` — they're effectively bearer
# tokens.  ``model_dump`` / ``model_dump_json`` already redact the
# ``acp_resume_session_id`` field; log aggregators (Datadog, CloudWatch, …)
# are another serialization boundary that retains lines for weeks, so the
# same redaction applies there.  Eight trailing characters give enough
# entropy to correlate across log lines for one conversation but not enough
# to brute-force the full id.
_SESSION_ID_LOG_SUFFIX_LEN: Final[int] = 8


def _fingerprint_session_id(session_id: str | None) -> str:
    """Render an ACP session id as a short, non-reversible fingerprint.

    ACP session ids are effectively bearer tokens (anyone holding one can
    call ``session/load`` against the ACP server), so the full value must
    not appear in logs — see :data:`_SESSION_ID_LOG_SUFFIX_LEN`.

    Returns ``"<none>"`` for ``None``, ``"<short>"`` for ids shorter than
    or equal to the suffix length (e.g. test fixtures), and ``"...<last-N>"``
    otherwise.
    """
    if session_id is None:
        return "<none>"
    if len(session_id) <= _SESSION_ID_LOG_SUFFIX_LEN:
        return "<short>"
    return f"...{session_id[-_SESSION_ID_LOG_SUFFIX_LEN:]}"


def _strip_inherited_npm_env(env: dict[str, str]) -> None:
    """Remove npm lifecycle/config variables inherited from the parent stack."""
    for key in list(env):
        if key in _INHERITED_NPM_ENV_EXACT or key.startswith(
            _INHERITED_NPM_ENV_PREFIXES
        ):
            env.pop(key, None)


# Limit for asyncio.StreamReader buffers used by the ACP subprocess pipes.
# The default (64 KiB) is too small for session_update notifications that
# carry large tool-call outputs (e.g. file contents, test results).  When
# a single JSON-RPC line exceeds the limit, readline() raises
# LimitOverrunError, silently killing the filter/receive pipeline and
# leaving the prompt() future unresolved forever.  100 MiB is a pragmatic
# compatibility limit for current ACP servers, not an endorsement of huge
# JSON-RPC payloads; the long-term fix is protocol-level chunking/streaming
# for large tool output.
_STREAM_READER_LIMIT: int = 100 * 1024 * 1024  # 100 MiB

# Minimum interval between on_activity heartbeat signals (seconds).
# Throttled to avoid excessive calls while still keeping the idle timer
# well below the ~20 min runtime-api kill threshold.
_ACTIVITY_SIGNAL_INTERVAL: float = 30.0

# ACP tool-call statuses that represent a terminal outcome.  Non-terminal
# statuses (``pending``, ``in_progress``) mean the call is still in flight
# and, if the turn aborts before it reaches a terminal state, the live-
# emitted event on state.events will otherwise be orphaned forever.
_TERMINAL_TOOL_CALL_STATUSES: frozenset[str] = frozenset({"completed", "failed"})


class _PromptDrainResult(NamedTuple):
    drained: bool
    completed: bool
    response: PromptResponse | None
    error: BaseException | None


# Stable identifier stamped onto the sentinel LLM so downstream code
# (e.g. title_utils) can detect "this LLM cannot be called" without
# relying on the model name — which we overwrite with the real model
# once ``acp_model`` is known, so logs and serialized state show the
# actual model rather than "acp-managed".
ACP_SENTINEL_USAGE_ID = "acp-managed"


def _make_dummy_llm() -> LLM:
    """Create a dummy LLM that should never be called directly."""
    return LLM(model="acp-managed", usage_id=ACP_SENTINEL_USAGE_ID)


# ---------------------------------------------------------------------------
# ACP Client implementation
# ---------------------------------------------------------------------------


# ACP auth method ID → environment variable that supplies the credential.
# When the server reports auth_methods, we pick the first method whose
# required credential source is present.
# Note: claude-login is intentionally NOT included because Claude Code ACP
# uses bypassPermissions mode instead of API key authentication.
_AUTH_METHOD_ENV_MAP: dict[str, str] = {
    "codex-api-key": "CODEX_API_KEY",
    "openai-api-key": "OPENAI_API_KEY",
    "gemini-api-key": "GEMINI_API_KEY",
}
_CHATGPT_AUTH_PATH = Path(".codex") / "auth.json"
# Gemini CLI personal (Google OAuth) login, cached by ``gemini login`` /
# ``gemini --acp``. Its presence lets us select the server's ``oauth-personal``
# auth method without an API key (mirrors the ChatGPT subscription path).
_GEMINI_OAUTH_PATH = Path(".gemini") / "oauth_creds.json"


def _codex_auth_file(env: dict[str, str]) -> Path:
    """Path to Codex's ChatGPT-subscription ``auth.json``, honoring ``CODEX_HOME``.

    Codex reads ``$CODEX_HOME/auth.json`` when ``CODEX_HOME`` is set — which the
    SDK does after materialising a relocated, per-conversation ``auth.json``
    (see :meth:`ACPAgent._materialise_file_secrets`) — and ``~/.codex/auth.json``
    otherwise. Detection must follow the same relocation or a materialised
    subscription token is never recognised (issue #1020).
    """
    codex_home = env.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "auth.json"
    return Path.home() / _CHATGPT_AUTH_PATH


def _select_auth_method(
    auth_methods: list[Any],
    env: dict[str, str],
) -> str | None:
    """Pick an auth method whose required credentials are present.

    Returns the ``id`` of the first matching method, or ``None`` if no
    supported credential source is available (the server may not require auth).

    File-backed subscription / SA logins are checked first so they take
    precedence over explicit API keys, which serve as the fallback:

    - ``chatgpt`` (codex-acp) — ``$CODEX_HOME/auth.json`` or ``~/.codex/auth.json``
    - ``vertex-ai`` (gemini-cli) — service-account JSON at
      ``GOOGLE_APPLICATION_CREDENTIALS`` (the deployable Gemini path; preferred
      over personal OAuth, which is host-bound and undeployable)
    - ``oauth-personal`` (gemini-cli) — ``~/.gemini/oauth_creds.json``

    In a server image the interactive-login files are absent, so the API-key
    fallback (e.g. ``GEMINI_API_KEY``) is used instead.
    """
    method_ids = {m.id for m in auth_methods}
    # Prefer file-backed subscription / service-account logins when their
    # credential file is present.
    if "chatgpt" in method_ids and _codex_auth_file(env).is_file():
        return "chatgpt"
    gac = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    if "vertex-ai" in method_ids and gac and Path(gac).is_file():
        return "vertex-ai"
    if "oauth-personal" in method_ids and (Path.home() / _GEMINI_OAUTH_PATH).is_file():
        return "oauth-personal"
    # Fall back to explicit API key env vars.
    for method_id, env_var in _AUTH_METHOD_ENV_MAP.items():
        if method_id in method_ids and env_var in env:
            return method_id
    return None


def _codex_base_url_overrides(
    command: str, args: list[str], env: dict[str, str]
) -> list[str]:
    """Translate ``OPENAI_BASE_URL`` into the codex config key that sets it.

    Unlike claude-agent-acp (which honours ``ANTHROPIC_BASE_URL``) and gemini-cli
    (whose base URL is supplied via the ``authenticate`` gateway), **codex does
    not read the ``OPENAI_BASE_URL`` env var** — its supported base-URL config
    lives in ``config.toml`` (see the codex "Advanced configuration" docs). Its
    built-in ``openai`` provider otherwise targets ``https://api.openai.com``, so
    a caller that points codex at a gateway/proxy (eval LiteLLM proxy, a
    corporate egress, etc.) via ``OPENAI_BASE_URL`` alone would have every turn
    hit the real OpenAI API with the wrong key and fail ``401 invalid_api_key``
    — surfaced opaquely as ACP ``-32603 Internal error``. (codex-acp 0.11.1
    happened to honour the env var; 0.15.0 does not, so the eval/canvas/cloud
    codex-via-proxy flows broke on the bump.)

    The documented one-liner is ``openai_base_url`` — it overrides the built-in
    ``openai`` provider's base URL without inventing a separate provider, so the
    provider's defaults (``OPENAI_API_KEY`` env key, Responses ``wire_api``) keep
    applying and per-conversation keys keep working. No-op for non-codex servers,
    when ``OPENAI_BASE_URL`` is unset, or when the caller already pinned a base
    URL / ``model_provider`` (via ``acp_args``/``-c``), which takes precedence.
    """
    if not any("codex-acp" in tok for tok in (command, *args)):
        return []
    base_url = env.get("OPENAI_BASE_URL")
    if not base_url:
        return []
    if any("openai_base_url" in tok or "model_provider" in tok for tok in args):
        return []
    return ["-c", f'openai_base_url="{base_url}"']


def _write_secret_file(path: Path, value: str) -> None:
    """Write ``value`` to ``path`` as a ``0600`` file.

    ``os.open`` creates a *new* file at ``0600``, but ``O_CREAT`` does not
    narrow an existing file's mode. So ``fchmod`` the raw fd to ``0600`` before
    any bytes land — clamping the mode while we still hold the fd guarantees the
    secret content never exists with wider permissions even when the file
    pre-existed (e.g. a ``0644`` empty file from another tool).
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value)


def _extract_session_models(
    response: Any,
) -> tuple[str | None, list[ACPModelInfo] | None]:
    """Extract the model state off a session response.

    Returns a ``(current_model_id, available_models)`` pair, both best-effort.
    ``available_models`` is normalized into our own stable :class:`ACPModelInfo`
    type at this boundary so nothing downstream depends on the vendored
    ``acp.schema`` shape.

    The second element distinguishes **absent** from **empty** — this matters
    for resume persistence (preserve the last-known list when the server didn't
    report one; clear it when the server explicitly says it has none):

    - ``None``  — the (UNSTABLE) ``models`` block was absent from the response
      (older agent, opted out, or ``load_session`` not carrying it).
    - ``[]``    — the server *did* report ``models`` but offers no (usable)
      models this session.
    - ``[...]`` — the reported models, minus any with an unusable ``model_id``.

    ``getattr`` keeps the helper tolerant of agents that emit a partial
    structure.
    """
    if response is None:
        return None, None
    models = getattr(response, "models", None)
    if models is None:
        return None, None
    current = getattr(models, "current_model_id", None)
    current = current if isinstance(current, str) and current else None
    raw = getattr(models, "available_models", None) or []
    # Drop entries without a usable id: an empty/missing ``model_id`` is an
    # invalid picker option and an unusable ``set_session_model`` target, so we
    # filter it out rather than surfacing ``model_id=""``.
    available = [
        info for info in (ACPModelInfo.from_protocol(m) for m in raw) if info.model_id
    ]
    return current, available


# The ACP MCP server union accepted by new_session() / load_session().
_ACPMcpServer = HttpMcpServer | SseMcpServer | McpServerStdio


def _remote_mcp_headers(spec: dict[str, Any], name: str) -> list[HttpHeader]:
    """Convert remote MCP headers/auth into ACP's header-only representation."""
    raw_headers = spec.get("headers") or {}
    headers = [HttpHeader(name=str(k), value=str(v)) for k, v in raw_headers.items()]

    auth = spec.get("auth")
    has_authorization = any(h.name.lower() == "authorization" for h in headers)
    if auth and not has_authorization:
        if isinstance(auth, str) and auth != "oauth":
            headers.append(HttpHeader(name="Authorization", value=f"Bearer {auth}"))
        else:
            logger.warning(
                "ACP MCP server %r uses unsupported remote MCP auth type %r; "
                "only explicit headers or string bearer tokens can be forwarded",
                name,
                type(auth).__name__,
            )
    return headers


def _mcp_config_to_acp_servers(
    mcp_config: dict[str, Any],
    mcp_capabilities: Any,
) -> list[_ACPMcpServer]:
    """Translate an OpenHands ``mcp_config`` dict into ACP MCP server objects.

    Reads the standard ``{"mcpServers": {name: {...}}}`` shape (the same shape
    :attr:`AgentBase.mcp_config` carries for the built-in Agent) and returns the
    list to pass to ``new_session()`` / ``load_session()`` so the ACP
    subprocess connects to those servers itself.  Unlike the built-in Agent
    these are *not* turned into in-process OpenHands MCP tools
    (:attr:`ACPAgent.supports_openhands_mcp` stays ``False``) — the ACP server
    owns the MCP connection and exposes the tools through its own turn.

    Each entry maps by transport:

    - ``command`` present → :class:`McpServerStdio` (always forwarded; the
      protocol gates only the remote transports behind a capability flag).
    - ``url`` present, transport ``sse`` → :class:`SseMcpServer`, forwarded only
      when the server advertises ``mcp_capabilities.sse``.
    - ``url`` present, any other / absent transport → :class:`HttpMcpServer`
      (covers ``http`` and ``streamable-http``), forwarded only when the server
      advertises ``mcp_capabilities.http``.

    A remote server whose transport the ACP server does not advertise is dropped
    with a warning rather than failing init — one misconfigured server should
    not sink the whole conversation.  ``env`` / ``headers`` maps are converted
    to the protocol's ``[{name, value}]`` list form; string ``auth`` values are
    forwarded as bearer ``Authorization`` headers when no explicit
    ``Authorization`` header is already present.  Their values were already
    decrypted by :class:`AgentBase`'s ``mcp_config`` validator.
    """
    servers = mcp_config.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    http_ok = bool(getattr(mcp_capabilities, "http", False))
    sse_ok = bool(getattr(mcp_capabilities, "sse", False))
    result: list[_ACPMcpServer] = []
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            logger.warning("Skipping malformed ACP MCP server %r", name)
            continue
        command = spec.get("command")
        url = spec.get("url")
        if command:
            env = [
                EnvVariable(name=str(k), value=str(v))
                for k, v in (spec.get("env") or {}).items()
            ]
            result.append(
                McpServerStdio(
                    name=str(name),
                    command=str(command),
                    args=[str(a) for a in (spec.get("args") or [])],
                    env=env,
                )
            )
        elif url:
            headers = _remote_mcp_headers(spec, str(name))
            is_sse = str(spec.get("transport") or "http").lower() == "sse"
            if not (sse_ok if is_sse else http_ok):
                logger.warning(
                    "ACP server does not advertise %s MCP support; "
                    "dropping MCP server %r (%s)",
                    "SSE" if is_sse else "HTTP",
                    name,
                    url,
                )
                continue
            # Construct each transport explicitly so the ``type`` literal stays
            # narrow (the union's two arms require distinct ``Literal``s).
            if is_sse:
                result.append(
                    SseMcpServer(
                        type="sse", name=str(name), url=str(url), headers=headers
                    )
                )
            else:
                result.append(
                    HttpMcpServer(
                        type="http", name=str(name), url=str(url), headers=headers
                    )
                )
        else:
            logger.warning(
                "Skipping ACP MCP server %r: needs a 'command' (stdio) or "
                "'url' (http/sse)",
                name,
            )
    return result


async def _maybe_set_session_model(
    conn: ClientSideConnection,
    agent_name: str,
    session_id: str,
    acp_model: str | None,
) -> bool:
    """Apply the *initial* session model right after session creation.

    This is the session-creation path only, gated on
    :attr:`~openhands.sdk.settings.acp_providers.ACPProviderInfo.supports_set_session_model`.
    All built-in providers get a one-shot ``set_session_model`` call here.
    claude-agent-acp used to be skipped on the assumption that it already
    received the model via ``new_session()`` ``_meta``, but 0.30.0 silently
    ignores that payload (#3654) — the protocol call is the only path that
    both validates and applies the model.

    For unknown/custom providers (e.g. Devin CLI), we fall back to the generic
    ``set_config_option`` method with configId="model", which is a standard ACP
    method that many custom ACP servers support.

    Runtime, mid-conversation switches go through
    :meth:`ACPAgent.set_acp_model` instead, which always uses
    ``set_session_model`` and is gated on the separate
    ``supports_runtime_model_switch`` capability flag.

    Returns ``True`` only when this issued a model-setting call that succeeded — i.e.
    the override was actually pushed to the server via *this* path. ``False``
    when there is nothing to apply (no ``acp_model``) or the provider selects
    its model another way (``_meta``) or the server rejected the call, so
    the caller can tell whether the live session is really running ``acp_model``.
    """
    if not acp_model:
        return False
    provider = detect_acp_provider_by_agent_name(agent_name)
    if provider is not None and provider.supports_set_session_model:
        await conn.set_session_model(model_id=acp_model, session_id=session_id)
        return True
    # For unknown/custom providers, try the generic set_config_option method
    # which is a standard ACP protocol method for setting configuration options
    if provider is None:
        try:
            await conn.set_config_option(
                config_id="model",
                value=acp_model,
                session_id=session_id,
            )
            logger.info(
                "Set model %r on unknown/custom ACP server %s via set_config_option",
                acp_model,
                agent_name,
            )
            return True
        except ACPRequestError as e:
            logger.warning(
                "Could not set model %r on unknown/custom ACP server %s via "
                "set_config_option (%s); the session will use the server default",
                acp_model,
                agent_name,
                e,
            )
    return False


async def _reapply_session_model_on_resume(
    conn: ClientSideConnection,
    agent_name: str,
    session_id: str,
    acp_model: str | None,
) -> bool:
    """Reapply the persisted model to a *resumed* session.

    ``load_session()`` carries no model ``_meta``, so a session resumed after a
    runtime switch (or with any persisted ``acp_model``) would otherwise run on
    the ACP server's default. This issues ``set_session_model`` so the resumed
    live session matches the serialized ``acp_model``.

    For unknown/custom providers (e.g. Devin CLI), we fall back to the generic
    ``set_config_option`` method with configId="model", which is a standard ACP
    method that many custom ACP servers support.

    The gating mirrors :meth:`ACPAgent.set_acp_model` (attempt for custom/unknown
    servers and known providers that support runtime switching; skip only known
    providers that don't), deliberately differing from the initial-selection
    gate: claude-agent-acp selects its initial model via ``_meta`` yet supports
    ``set_session_model`` for later switches. A server that rejects the call is
    tolerated (logged) — like the ``load_session`` fallback above — so resume
    can't break; the session keeps the server default until the next switch.

    Returns ``True`` only when a model-setting call was issued and accepted, so
    the caller knows the resumed live session is actually running ``acp_model``.
    ``False`` when there is nothing to reapply, the provider doesn't support the
    switch, or the server rejected the call (swallowed) — in those cases the
    session keeps the server default and the override must not be surfaced as
    the current model.
    """
    if not acp_model:
        return False
    provider = detect_acp_provider_by_agent_name(agent_name)
    if provider is not None and not provider.supports_runtime_model_switch:
        return False
    try:
        if provider is not None:
            # Known provider: use set_session_model
            await conn.set_session_model(model_id=acp_model, session_id=session_id)
        else:
            # Unknown/custom provider: try set_config_option as fallback
            await conn.set_config_option(
                config_id="model",
                value=acp_model,
                session_id=session_id,
            )
            logger.info(
                "Reapplied model %r on unknown/custom ACP server %s "
                "via set_config_option",
                acp_model,
                agent_name,
            )
        return True
    except ACPRequestError as e:
        logger.warning(
            "Could not reapply model %r on resumed session %s (%s); the live "
            "session may run on the server default until the next switch",
            acp_model,
            _fingerprint_session_id(session_id),
            e,
        )
        return False


def _extract_token_usage(
    response: Any,
) -> tuple[int, int, int, int, int]:
    """Extract token usage from an ACP PromptResponse.

    Returns (input_tokens, output_tokens, cache_read, cache_write, reasoning).

    Checks two locations:
    - claude-agent-acp, codex-acp: ``response.usage`` (standard ACP field)
    - gemini-cli: ``response._meta.quota.token_count`` (non-standard)
    """
    if response is not None and response.usage is not None:
        u = response.usage
        return (
            u.input_tokens,
            u.output_tokens,
            u.cached_read_tokens or 0,
            u.cached_write_tokens or 0,
            u.thought_tokens or 0,
        )
    if response is not None and response.field_meta is not None:
        quota = response.field_meta.get("quota", {})
        tc = quota.get("token_count", {})
        return (tc.get("input_tokens", 0), tc.get("output_tokens", 0), 0, 0, 0)
    return (0, 0, 0, 0, 0)


def _estimate_cost_from_tokens(
    model: str, input_tokens: int, output_tokens: int
) -> float:
    """Estimate cost from token counts using LiteLLM's pricing database.

    Returns 0.0 if pricing is unavailable for the model.
    """
    try:
        import litellm

        cost_map = litellm.model_cost
        info = cost_map.get(model, {})
        input_cost = info.get("input_cost_per_token", 0) or 0
        output_cost = info.get("output_cost_per_token", 0) or 0
        return input_tokens * input_cost + output_tokens * output_cost
    except Exception:
        return 0.0


def _image_url_to_acp_block(url: str) -> ImageContentBlock | None:
    """Convert an image URL (data URI or plain URL) to an ACP ImageContentBlock.

    Data URIs (``data:<mime>;base64,<data>``) are parsed directly.
    Plain URLs are passed via the ``uri`` field with a generic MIME type.
    Returns ``None`` if the URL cannot be converted.
    """
    if url.startswith("data:"):
        # Parse data URI: data:<mime>;base64,<data>
        try:
            header, data = url.split(",", 1)
            mime_type = header.split(":", 1)[1].split(";", 1)[0]
            return image_block(data=data, mime_type=mime_type)
        except (ValueError, IndexError):
            logger.warning("Failed to parse data URI for ACP image block")
            return None
    # Plain URL — pass as uri with a generic MIME type; the ACP server
    # can fetch and detect the actual type.
    return image_block(data="", mime_type="image/png", uri=url)


def _mask_json_value(value: Any, mask: Callable[[str], str]) -> Any:
    """Recursively apply *mask* to every string leaf of a JSON-like value.

    ACP tool-call ``raw_input`` / ``raw_output`` / ``content`` blocks are
    arbitrary JSON (a bare string, a dict of params, a list of content
    blocks). ``SecretRegistry.mask_secrets_in_output`` is a pure string op,
    so walk the structure and mask each leaf string; non-string leaves
    (ints, bools, ``None``) pass through unchanged.
    """
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, dict):
        return {k: _mask_json_value(v, mask) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_json_value(v, mask) for v in value]
    return value


def _serialize_tool_content(content: list[Any] | None) -> list[dict[str, Any]] | None:
    """Serialize ACP tool call content blocks to plain dicts for JSON storage."""
    if not content:
        return None
    result = []
    for content_block in content:
        block_dict = (
            content_block.model_dump(mode="json")
            if hasattr(content_block, "model_dump")
            else content_block
        )
        if (
            isinstance(block_dict, dict)
            and block_dict.get("type") == "text"
            and isinstance(block_dict.get("text"), str)
        ):
            block_dict = {
                **block_dict,
                "text": maybe_truncate(
                    block_dict["text"], truncate_after=MAX_ACP_CONTENT_CHARS
                ),
            }
        result.append(block_dict)
    return result


async def _filter_jsonrpc_lines(source: Any, dest: Any) -> None:
    """Read lines from *source* and forward only JSON-RPC lines to *dest*.

    Some ACP servers (e.g. ``claude-code-acp`` v0.1.x) emit log messages
    like ``[ACP] ...`` to stdout alongside JSON-RPC traffic.  This coroutine
    strips those non-protocol lines so the JSON-RPC connection is not confused.
    """
    try:
        while True:
            line = await source.readline()
            if not line:
                dest.feed_eof()
                break
            # JSON-RPC messages are single-line JSON objects containing
            # "jsonrpc". Filter out multi-line pretty-printed JSON from
            # debug logs that also start with '{'.
            stripped = line.lstrip()
            if stripped.startswith(b"{") and b'"jsonrpc"' in line:
                dest.feed_data(line)
            else:
                logger.debug(
                    "ACP stdout (non-JSON): %s",
                    line.decode(errors="replace").rstrip(),
                )
    except Exception:
        logger.debug("_filter_jsonrpc_lines stopped", exc_info=True)
        dest.feed_eof()


def _classify_acp_init_error(exc: BaseException) -> str:
    """Map a cold-start failure to a structured ``ConversationErrorEvent`` code.

    ACP's spawn + auth + ``session/new`` runs in :meth:`ACPAgent.init_state`,
    which ``LocalConversation.run()``/``arun()`` invoke *before* their try-block
    (via ``_ensure_agent_ready()``).  These cold-start failures — far more common
    on cloud than locally — therefore bypass the run loop's error emission, so
    ``init_state`` surfaces them itself.  The code tells clients *which* failure
    occurred so they can react (e.g. prompt re-auth vs. report a missing binary):

    - ``ACPAuthRequired``: the ACP server reported a JSON-RPC auth-required error
      (code ``-32000``) from ``authenticate``/``new_session`` — missing, expired,
      or rejected credentials.  The most actionable cloud failure, so it gets its
      own code.
    - ``ACPSpawnError``: the subprocess could not be launched — the CLI binary is
      missing or not executable (``FileNotFoundError`` / ``PermissionError`` from
      ``create_subprocess_exec``).
    - ``ACPInitError``: anything else during the protocol handshake or session
      creation (timeouts, transport drops, unexpected protocol errors, cwd
      mismatch surfaced by the server).
    """
    if isinstance(exc, ACPRequestError) and getattr(exc, "code", None) == -32000:
        return "ACPAuthRequired"
    if isinstance(exc, (FileNotFoundError, PermissionError)):
        return "ACPSpawnError"
    return "ACPInitError"


class _OpenHandsACPBridge:
    """Bridge between OpenHands and ACP that accumulates session updates.

    Implements the ``Client`` protocol from ``agent_client_protocol``.

    Concurrency model — ``on_event`` / ``on_token`` / ``on_activity`` are
    fired synchronously from ``session_update``, which runs on the
    ``AsyncExecutor`` portal thread.  The guarantees that keep callbacks
    serialized within a single turn rely on the combination of two things,
    not the GIL alone:

    1. ``LocalConversation.run()`` calls ``agent.step(...)`` while holding
       the reentrant ``ConversationState`` lock (a ``FIFOLock``) — see
       ``local_conversation.py`` where ``self.agent.step(...)`` sits inside
       ``with self._state:``.  The caller thread owns that lock for the
       entire duration of ``step()``, so no other thread can append to
       ``state.events`` during the turn.
    2. ``portal.call(_prompt)`` blocks the caller thread until ``prompt()``
       returns.  Live ``on_event`` calls happen on the portal thread while
       the caller thread is parked inside ``portal.call()`` still owning
       the state lock; the final ``MessageEvent`` / ``FinishAction`` run
       on the caller thread after ``prompt()`` returns.  The two phases
       never overlap in time.

    The caller's state-lock ownership is what excludes *other* threads
    (hook workers, remote-conversation push layers, visualizers spawned
    elsewhere) from racing with either phase.  The ordering between the
    two phases is what keeps a single consumer's cross-callback state
    (e.g. hook processors that read-then-write) consistent.

    Two invariants callers rely on:

    * ``on_event`` handlers MUST NOT acquire the conversation state lock
      (``with conversation.state:``).  The bridge fires them on the portal
      thread while the caller thread is parked inside ``portal.call()``
      owning that lock, and ``FIFOLock`` is thread-bound — a lock-acquire
      on the portal thread would deadlock rather than re-enter.
    * Tool-call → final-message ordering depends on the ACP server
      draining every ``session_update`` notification for a turn *before*
      the prompt response returns.  Verified against
      ``claude-agent-acp@0.29.0``; servers that interleave trailing
      ``ToolCallProgress`` after the prompt response would invert the
      order a consumer sees, and dedupe-by-id+"last-seen wins" would
      treat the post-message event as authoritative.
    """

    def __init__(self) -> None:
        self.accumulated_text: list[str] = []
        self.accumulated_thoughts: list[str] = []
        self.accumulated_tool_calls: list[dict[str, Any]] = []
        self.on_token: Any = None  # ConversationTokenCallbackType | None
        # Live event sink — fired from session_update as ACP tool-call
        # updates arrive, so the event stream reflects real subprocess
        # progress instead of a single end-of-turn burst. Set by
        # ACPAgent.step() for the duration of one prompt() round-trip.
        self.on_event: ConversationCallbackType | None = None
        # Activity heartbeat — called (throttled) during session_update to
        # signal that the ACP subprocess is still actively working.  Set by
        # ACPAgent.step() to keep the agent-server's idle timer alive.
        self.on_activity: Any = None  # Callable[[], None] | None
        # Secret masker — set per turn by ACPAgent to
        # ``state.secret_registry.mask_secrets_in_output``. Applied to streamed
        # text chunks and tool-call raw_input/raw_output/content before they
        # reach ``on_token`` / ``on_event`` so a subprocess that echoes an
        # injected credential never lands in the (persisted, network-relayed)
        # event stream in cleartext. ``None`` ⇒ no-op (bridge used standalone).
        self.mask: Callable[[str], str] | None = None
        self._last_activity_signal: float = float("-inf")
        # Monotonic timestamp of the most recent ``session_update``. Unlike the
        # throttled ``_last_activity_signal``, updated on *every* update so the
        # prompt idle-timeout watchdog sees real progress. Armed per turn via
        # ``arm_activity_clock``.
        self._last_activity_monotonic: float = float("-inf")
        # Telemetry state from UsageUpdate (persists across turns)
        self._last_cost: float = 0.0  # last cumulative cost seen
        self._last_cost_by_session: dict[str, float] = {}
        self._context_window: int = 0  # last context window seen
        self._context_window_by_session: dict[str, int] = {}
        # Per-turn synchronization for UsageUpdate notifications.
        self._turn_usage_updates: dict[str, Any] = {}
        self._usage_received: dict[str, asyncio.Event] = {}
        # Fork session state for ask_agent() — guarded by _fork_lock to
        # prevent concurrent ask_agent() calls from colliding.
        self._fork_lock = threading.Lock()
        self._fork_session_id: str | None = None
        self._fork_accumulated_text: list[str] = []

    def reset(self) -> None:
        self.accumulated_text.clear()
        self.accumulated_thoughts.clear()
        self.accumulated_tool_calls.clear()
        self.on_token = None
        self.on_event = None
        self.on_activity = None
        self._turn_usage_updates.clear()
        self._usage_received.clear()
        # Note: telemetry state (_last_cost, _context_window, _last_activity_signal,
        # etc.) is intentionally NOT cleared — it accumulates across turns.

    def arm_activity_clock(self) -> None:
        """Mark "now" as the last activity for the idle-timeout watchdog.

        Called at the start of each prompt (and each retry) so the idle
        window is measured from the moment the prompt is sent rather than
        from a stale value — a server that legitimately takes a while before
        its first ``session_update`` must not be killed prematurely.
        """
        self._last_activity_monotonic = time.monotonic()

    def seconds_since_last_activity(self) -> float:
        """Seconds since the last ``session_update`` (or ``arm_activity_clock``).

        Drives the prompt idle-timeout: any streamed token, thought, tool-call
        start/progress, or usage update from the ACP server resets the clock,
        so a steadily-progressing agent never trips the deadline while a
        genuinely silent (hung) server still does.
        """
        return time.monotonic() - self._last_activity_monotonic

    def prepare_usage_sync(self, session_id: str) -> asyncio.Event:
        """Prepare per-turn UsageUpdate synchronization for a session."""
        event = asyncio.Event()
        self._usage_received[session_id] = event
        self._turn_usage_updates.pop(session_id, None)
        return event

    def get_turn_usage_update(self, session_id: str) -> Any:
        """Return the latest UsageUpdate observed for the current turn."""
        return self._turn_usage_updates.get(session_id)

    def pop_turn_usage_update(self, session_id: str) -> Any:
        """Consume per-turn UsageUpdate synchronization state for a session."""
        self._usage_received.pop(session_id, None)
        return self._turn_usage_updates.pop(session_id, None)

    def _mask_value(self, value: Any) -> Any:
        """Mask injected secrets in *value* (string or JSON-like), no-op if unset.

        Defensive: on mask failure, returns the original value unchanged and
        logs at DEBUG — this may transiently leak the credential but prevents a
        crash, matching the regular terminal tool's masking contract. (Masking
        is a pure ``str.replace`` and should never raise in practice.)
        """
        if self.mask is None:
            return value
        try:
            return _mask_json_value(value, self.mask)
        except Exception:
            logger.debug("secret masking failed", exc_info=True)
            return value

    def _mask_tool_call_entry(self, entry: dict[str, Any]) -> None:
        """Mask title / raw_input / raw_output / content of a tool-call entry.

        Applied in place at ingestion (``session_update``) so the accumulator
        itself never holds plaintext secrets, and every downstream emitter
        (``_emit_tool_call_event`` and the supersede path in
        ``_cancel_inflight_tool_calls``) carries masked values for free.
        ``title`` is normally a benign server-set label, but a misbehaving ACP
        server could echo a credential there (e.g. ``Running: curl -H
        'Authorization: Bearer <token>'``), so it is masked too.
        """
        for key in ("title", "raw_input", "raw_output", "content"):
            if entry.get(key) is not None:
                entry[key] = self._mask_value(entry[key])

    # -- Client protocol methods ------------------------------------------

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        logger.debug("ACP session_update: type=%s", type(update).__name__)

        # Any update — token, thought, tool-call start/progress, usage — is
        # progress: reset the idle clock so the prompt's inactivity watchdog
        # keeps a steadily-working agent alive (unthrottled, unlike the
        # heartbeat in ``_maybe_signal_activity``).
        self._last_activity_monotonic = time.monotonic()

        # Route fork session updates to the fork accumulator. ask_agent() joins
        # and returns this text to the caller (a UI/network sink), so mask it
        # like the main-turn path — a secret echoed in a fork session must not
        # leak in cleartext.
        if self._fork_session_id is not None and session_id == self._fork_session_id:
            if isinstance(update, AgentMessageChunk):
                if isinstance(update.content, TextContentBlock):
                    self._fork_accumulated_text.append(
                        self._mask_value(update.content.text)
                    )
            return

        if isinstance(update, AgentMessageChunk):
            if isinstance(update.content, TextContentBlock):
                # Mask once, then use the masked chunk for both the persisted
                # accumulation and the live ``on_token`` relay. A secret split
                # across two chunks slips through here (each piece alone won't
                # match); the joined response is re-masked at the persistence
                # boundary in ``_finalize_successful_turn`` to catch that.
                text = self._mask_value(update.content.text)
                self.accumulated_text.append(text)
                if self.on_token is not None:
                    try:
                        self.on_token(text)
                    except Exception:
                        logger.debug("on_token callback failed", exc_info=True)
            self._maybe_signal_activity()
        elif isinstance(update, AgentThoughtChunk):
            if isinstance(update.content, TextContentBlock):
                self.accumulated_thoughts.append(self._mask_value(update.content.text))
        elif isinstance(update, UsageUpdate):
            # Store the update for step()/ask_agent() to process in one place.
            self._context_window = update.size
            self._context_window_by_session[session_id] = update.size
            self._turn_usage_updates[session_id] = update
            event = self._usage_received.get(session_id)
            if event is not None:
                event.set()
        elif isinstance(update, ToolCallStart):
            entry = {
                "tool_call_id": update.tool_call_id,
                "title": update.title,
                "tool_kind": update.kind,
                "status": update.status,
                "raw_input": update.raw_input,
                "raw_output": update.raw_output,
                "content": _serialize_tool_content(update.content),
            }
            self._mask_tool_call_entry(entry)
            self.accumulated_tool_calls.append(entry)
            logger.debug("ACP tool call start: %s", update.tool_call_id)
            # Emit one early "started" event — the action half of the
            # action->observation pair. (If the server reports a terminal
            # status on the very first notification, this single event is
            # also the observation; the matching terminal-transition guard
            # below then suppresses any redundant re-emission.)
            self._emit_tool_call_event(entry)
            self._maybe_signal_activity()
        elif isinstance(update, ToolCallProgress):
            # Find the existing tool call entry and merge updates. Track the
            # status seen *before* this frame so we can detect the single
            # transition into a terminal state.
            target: dict[str, Any] | None = None
            prev_status: str | None = None
            for tc in self.accumulated_tool_calls:
                if tc["tool_call_id"] == update.tool_call_id:
                    prev_status = tc.get("status")
                    if update.title is not None:
                        tc["title"] = update.title
                    if update.kind is not None:
                        tc["tool_kind"] = update.kind
                    if update.status is not None:
                        tc["status"] = update.status
                    if update.raw_input is not None:
                        tc["raw_input"] = update.raw_input
                    if update.raw_output is not None:
                        tc["raw_output"] = update.raw_output
                    if update.content is not None:
                        tc["content"] = _serialize_tool_content(update.content)
                    target = tc
                    break
            logger.debug("ACP tool call progress: %s", update.tool_call_id)
            # Mask the merged entry on every frame so the accumulator (and thus
            # the terminal event and any _cancel_inflight_tool_calls supersede)
            # never carries plaintext secrets. ``status`` is left untouched, so
            # the terminal-transition check below is unaffected.
            if target is not None:
                self._mask_tool_call_entry(target)
            # Persist exactly one terminal event per tool call. Intermediate
            # progress frames each carry the *full cumulative* output; emitting
            # one per frame is O(n^2) storage + WebSocket relay (the bug this
            # method fixes). We accumulate them into ``target`` silently and
            # emit only on the first transition into a terminal status, so the
            # terminal event still carries the complete final output. This is
            # the observation half of the action->observation pair.
            became_terminal = (
                target is not None
                and target.get("status") in _TERMINAL_TOOL_CALL_STATUSES
                and prev_status not in _TERMINAL_TOOL_CALL_STATUSES
            )
            if target is not None and became_terminal:
                self._emit_tool_call_event(target)
            self._maybe_signal_activity()
        else:
            logger.debug("ACP session update: %s", type(update).__name__)

    def _emit_tool_call_event(self, tc: dict[str, Any]) -> None:
        """Emit an ACPToolCallEvent reflecting the current state of ``tc``.

        Called from ``session_update`` on each ``ToolCallStart`` /
        ``ToolCallProgress`` so downstream consumers see tool cards appear
        and update as the subprocess runs.  The same ``tool_call_id`` is
        reused on every emission — consumers should dedupe by id and treat
        the last-seen event as authoritative.
        """
        if self.on_event is None:
            return
        try:
            raw_output = tc.get("raw_output")
            if isinstance(raw_output, str):
                raw_output = maybe_truncate(
                    raw_output, truncate_after=MAX_ACP_CONTENT_CHARS
                )
            event = ACPToolCallEvent(
                tool_call_id=tc["tool_call_id"],
                title=tc["title"],
                status=tc.get("status"),
                tool_kind=tc.get("tool_kind"),
                raw_input=tc.get("raw_input"),
                raw_output=raw_output,
                content=tc.get("content"),
                is_error=tc.get("status") == "failed",
            )
            self.on_event(event)
        except Exception:
            logger.debug("on_event callback failed", exc_info=True)

    def _maybe_signal_activity(self) -> None:
        """Signal activity to the agent-server's idle tracker (throttled).

        During conn.prompt(), ACP tool calls run inside the subprocess and
        never hit the agent-server's HTTP endpoints.  Without this heartbeat
        the server's idle_time grows unboundedly and the runtime-api kills
        the pod (default idle threshold ~20 min).

        Throttled to at most once per _ACTIVITY_SIGNAL_INTERVAL seconds to
        avoid excessive overhead on chatty ACP servers.
        """
        if self.on_activity is None:
            return
        now = time.monotonic()
        if now - self._last_activity_signal >= _ACTIVITY_SIGNAL_INTERVAL:
            self._last_activity_signal = now
            try:
                self.on_activity()
            except Exception:
                logger.debug("on_activity callback failed", exc_info=True)

    async def request_permission(
        self,
        options: list[Any],
        session_id: str,  # noqa: ARG002
        tool_call: Any,
        **kwargs: Any,  # noqa: ARG002
    ) -> Any:
        """Auto-approve all permission requests from the ACP server."""
        # Pick the first option (usually "allow once")
        option_id = options[0].option_id if options else "allow_once"
        logger.info(
            "ACP auto-approving permission: %s (option: %s)",
            tool_call,
            option_id,
        )
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id=option_id),
        )

    # fs/terminal methods — raise NotImplementedError; ACP server handles its own
    async def write_text_file(
        self, content: str, path: str, session_id: str, **kwargs: Any
    ) -> None:
        raise NotImplementedError("ACP server handles file operations")

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError("ACP server handles file operations")

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: Any = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError("ACP server handles terminal operations")

    async def terminal_output(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> Any:
        raise NotImplementedError("ACP server handles terminal operations")

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> None:
        raise NotImplementedError("ACP server handles terminal operations")

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> Any:
        raise NotImplementedError("ACP server handles terminal operations")

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> None:
        raise NotImplementedError("ACP server handles terminal operations")

    async def ext_method(
        self,
        method: str,  # noqa: ARG002
        params: dict[str, Any],  # noqa: ARG002
    ) -> dict[str, Any]:
        return {}

    async def ext_notification(
        self,
        method: str,  # noqa: ARG002
        params: dict[str, Any],  # noqa: ARG002
    ) -> None:
        pass

    def on_connect(self, conn: Any) -> None:  # noqa: ARG002
        pass


# ---------------------------------------------------------------------------
# ACPAgent
# ---------------------------------------------------------------------------


class ACPAgent(AgentBase):
    """Agent that delegates to an ACP-compatible subprocess server."""

    # Override required fields with ACP-appropriate defaults
    llm: LLM = Field(default_factory=_make_dummy_llm)
    tools: list[Tool] = Field(default_factory=list)
    include_default_tools: list[str] = Field(default_factory=list)

    # ACP-specific configuration
    acp_command: list[str] = Field(
        ...,
        description=(
            "Command to start the ACP server, e.g."
            " ['npx', '-y', '@agentclientprotocol/claude-agent-acp']"
        ),
    )
    acp_args: list[str] = Field(
        default_factory=list,
        description="Additional arguments for the ACP server command",
    )
    acp_env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "DEPRECATED (removed in 1.29.0): additional environment variables for "
            "the ACP server process. Route subprocess env/credentials through "
            "state.secret_registry (e.g. agent_context.secrets / "
            "StartConversationRequest.secrets) instead."
        ),
    )

    @field_validator("acp_env", mode="before")
    @classmethod
    def _decrypt_acp_env_values(cls, value: Any, info: ValidationInfo) -> Any:
        """Decrypt persisted ACP environment values when a cipher is available.

        Mirrors the settings-side ``_decrypt_acp_env_values`` on
        :class:`openhands.sdk.settings.model.ACPAgentSettings`. The
        settings variant handles the on-disk → memory round-trip,
        but the conversation-start path goes
        :class:`StartConversationRequest.agent_settings` → the request's
        ``_populate_agent_from_settings`` (a ``mode='before'``
        model_validator that runs *without* cipher context) →
        ``settings.create_agent()`` → :class:`ACPAgent`. By the time
        ``conversation_service.start_conversation`` re-validates the full
        :class:`StoredConversation` with the server's cipher in context,
        the agent has already been constructed and its ``acp_env`` field
        still holds ciphertext. Without a validator here, that ciphertext
        survives the re-validation step and reaches the subprocess as the
        env-var value — breaking any provider call that interprets the
        variable (e.g. an Anthropic request reading a Fernet token in
        place of ``ANTHROPIC_BASE_URL``).

        Legacy plaintext values pass through unchanged so first writes
        from clients that haven't gone through the encryption pipeline
        still validate cleanly.
        """
        return validate_secret_dict(value, info, description="ACP env")

    @field_serializer("acp_env", when_used="always")
    def _serialize_acp_env(self, value: dict[str, str], info):
        """Mask ``acp_env`` values via :func:`serialize_secret`."""
        return {k: serialize_secret(SecretStr(v), info) for k, v in value.items()}

    acp_session_mode: str | None = Field(
        default=None,
        description=(
            "Session mode ID to set after creating a session. "
            "If None (default), auto-detected from the ACP server type: "
            "'bypassPermissions' for claude-agent-acp, 'full-access' for codex-acp."
        ),
    )
    acp_prompt_timeout: float = Field(
        default=1800.0,
        description=(
            "Inactivity timeout in seconds for a single ACP prompt() call. "
            "The deadline resets on every update from the ACP server (token, "
            "thought, tool-call progress, usage), so a steadily-progressing "
            "agent runs as long as it keeps making progress; the prompt is "
            "only aborted after this many seconds with no activity at all. "
            "Prevents indefinite hangs when the ACP server stops responding "
            "without killing legitimately long-running work."
        ),
    )
    acp_model: str | None = Field(
        default=None,
        description=(
            "Model for the ACP server to use (e.g. 'claude-opus-4-6' or "
            "'gpt-5.4'). For Claude ACP, passed via session _meta. For Codex "
            "ACP, applied via the protocol-level set_session_model call. "
            "If None, the server picks its default."
        ),
    )
    acp_resume_session_id: str | None = Field(
        default=None,
        description=(
            "Optional explicit ACP session id to resume. When set, takes "
            "precedence over the id persisted in ``state.agent_state`` and "
            "is used to call ``session/load`` on the ACP server. Designed "
            "for environments where the per-conversation filesystem (and "
            "therefore ``base_state.json``) does not survive across restarts "
            "(e.g. cloud sandbox recycles), but the id has been mirrored "
            "into durable storage elsewhere. Falls back to a fresh session "
            "if the server cannot load the id. Treated as a secret on the "
            "wire — possession of the id is enough to resume the underlying "
            "ACP session, so default serialization redacts it; pass "
            "``expose_secrets='plaintext'`` (trusted backend) or "
            "``expose_secrets='encrypted'`` plus a cipher (frontend round-"
            "trip) when the value must cross a serialization boundary."
        ),
    )

    @field_serializer("acp_resume_session_id", when_used="always")
    def _serialize_acp_resume_session_id(self, value: str | None, info):
        """Mask ``acp_resume_session_id`` via :func:`serialize_secret`.

        Default ``model_dump`` / ``model_dump_json`` redacts the id so it
        cannot leak into logs, trace exports, or PR review attachments.
        Trusted backend callers opt into plaintext via Pydantic's context
        (``expose_secrets='plaintext'``); frontend round-trips use the
        ``"encrypted"`` mode with a cipher in the context.
        """
        if value is None:
            return None
        return serialize_secret(SecretStr(value), info)

    @field_validator("acp_resume_session_id", mode="before")
    @classmethod
    def _validate_acp_resume_session_id(
        cls, value: Any, info: ValidationInfo
    ) -> str | None:
        """Reverse :meth:`_serialize_acp_resume_session_id` on load.

        Without this validator, ``model_validate_json`` of a previously
        serialized agent would put garbage in ``acp_resume_session_id``:

        - Default-redacted dumps would reload as the literal ``"**********"``
          sentinel — calling ``session/load`` with that fails server-side and
          we fall back to a fresh session every time, defeating the whole
          point of the durable mirror.
        - Encrypted dumps (frontend round-trip) would reload as the raw
          Fernet ciphertext — same failure mode, plus the ciphertext sits
          in ``state.agent_state`` on disk.

        :func:`validate_secret` returns ``None`` for empty/redacted values
        and decrypts when a cipher is present in the validation context.
        We unwrap the returned ``SecretStr`` so the field's runtime type
        stays ``str | None`` (the rest of the SDK reads it directly).
        """
        secret = validate_secret(value, info)
        return secret.get_secret_value() if secret is not None else None

    acp_file_secrets: list[ACPFileSecretSpec] = Field(
        default_factory=lambda: list(default_acp_file_secrets()),
        description=(
            "Reserved 'file-content' credential secrets to materialise to disk "
            "before launching the subprocess (e.g. Codex auth.json, Gemini "
            "Vertex SA JSON). The SDK owns the mechanism (write the file in the "
            "runtime pod, set the env var, seed-if-absent); these specs are the "
            "policy. Defaults to the built-in supported providers; a downstream "
            "application may override or extend this to support other ACP "
            "servers with different file-auth schemes."
        ),
    )
    acp_isolate_data_dir: bool = Field(
        default=False,
        description=(
            "Give the ACP subprocess a per-conversation CLI data/config root "
            "instead of the shared user ``HOME``. When True and the provider is "
            "recognised, point its data-dir env var "
            "(``CODEX_HOME`` / ``CLAUDE_CONFIG_DIR`` / ``HOME``; see "
            "``ACPProviderInfo.data_dir_env_var``) at "
            "``<persistence_dir>/acp/<provider>`` — the same per-conversation "
            "tree materialised file-secrets use. Required for correctness when "
            "several of a user's conversations share one sandbox "
            "(``SandboxGroupingStrategy != NO_GROUPING``), where they would "
            "otherwise race on one set of CLI auth/config/cache/lock files "
            "(see #1019). Off by default: with one sandbox per conversation the "
            "shared HOME is already private, and relocating it would hide a "
            "pre-existing interactive login. Downstream policy decides when to "
            "enable it; the SDK owns where the root lives."
        ),
    )

    def model_post_init(self, __context: object) -> None:
        super().model_post_init(__context)
        # Propagate the actual model name to the sentinel LLM and its
        # metrics so that logs, serialized state, and cost/token entries
        # show the real model instead of the "acp-managed" placeholder.
        # The ACP-sentinel marker lives on ``llm.usage_id`` and is
        # independent of the model name.
        if self.acp_model:
            self.llm.model = self.acp_model
            self.llm.metrics.model_name = self.acp_model
            if self.llm.metrics.accumulated_token_usage is not None:
                self.llm.metrics.accumulated_token_usage.model = self.acp_model

    # Private runtime state
    _executor: Any = PrivateAttr(default=None)
    _conn: Any = PrivateAttr(default=None)  # ClientSideConnection
    _session_id: str | None = PrivateAttr(default=None)
    _process: Any = PrivateAttr(default=None)  # asyncio subprocess
    _client: Any = PrivateAttr(default=None)  # _OpenHandsACPBridge
    _filtered_reader: Any = PrivateAttr(default=None)  # StreamReader
    _closed: bool = PrivateAttr(default=False)
    _working_dir: str = PrivateAttr(default="")
    _agent_name: str = PrivateAttr(
        default=""
    )  # ACP server name from InitializeResponse
    _agent_version: str = PrivateAttr(
        default=""
    )  # ACP server version from InitializeResponse
    # The model the ACP server reported as active for this session, captured
    # from ``models.currentModelId`` on the new_session / load_session
    # response.  Overridden by ``self.acp_model`` when the caller explicitly
    # chose one (either via ``set_session_model`` or via session ``_meta``).
    # ``None`` when the server doesn't surface model state — the field is
    # marked UNSTABLE in the ACP spec, so older agents may omit it.
    #
    # Kept as a PrivateAttr (not a Pydantic field) because ``AgentBase`` is
    # frozen and this is per-session runtime state, not config.  The
    # agent-server lifts it onto ``ConversationInfo`` so the value can cross
    # the API boundary even though the agent itself doesn't serialize it.
    _current_model_id: str | None = PrivateAttr(default=None)
    # ``models.availableModels`` from the same session response, normalized
    # to our stable ``ACPModelInfo`` type.  Surfaced verbatim via the
    # ``available_models`` property (and ``ConversationInfo.available_models``)
    # so clients can render a picker and resolve ``current_model_id`` to a
    # display label themselves — the SDK does no name curation.
    # ``None`` encodes "the server didn't report a ``models`` block this launch"
    # (distinct from ``[]`` = "reported, but no models"); the persistence logic
    # in ``init_state`` uses that distinction to preserve vs clear the stored
    # list on resume. The public ``available_models`` property coerces to ``[]``.
    _available_models: list[ACPModelInfo] | None = PrivateAttr(default=None)
    # Whether the caller's ``acp_model`` was actually pushed to the server in
    # the most recent session init (via session ``_meta`` or ``set_session_model``).
    # ``False`` when there's no override, the provider can't apply it (unknown
    # server on a fresh session), or the server rejected the call on resume — in
    # those cases the live session runs its own default, so neither
    # ``_current_model_id`` nor the ``ConversationInfo`` fallback may surface
    # ``acp_model`` as the active model. Read by ``init_state`` to decide whether
    # a stale persisted ``acp_current_model_id`` must be cleared.
    _model_override_applied: bool = PrivateAttr(default=False)
    # Callback to signal that the ACP subprocess is actively working.
    # Injected by the agent-server to call update_last_execution_time().
    _on_activity: Any = PrivateAttr(default=None)  # Callable[[], None] | None
    # Suffix rendered once at session start from agent_context + secret_registry.
    # "unused"               — no agent_context or empty suffix
    # "pending_first_prompt" — new session; inject into first user message
    # "installed"            — already in subprocess history; skip further injection
    _suffix_install_state: str = PrivateAttr(default="unused")
    _installed_suffix: str | None = PrivateAttr(default=None)
    _restart_session_on_next_turn: bool = PrivateAttr(default=False)
    _resumed_existing_session: bool = PrivateAttr(default=False)

    # -- Helpers -----------------------------------------------------------

    def _record_usage(
        self,
        response: PromptResponse | None,
        session_id: str,
        elapsed: float | None = None,
        usage_update: UsageUpdate | None = None,
    ) -> None:
        """Record cost, token usage, latency, and notify stats callback once.

        Args:
            response: The ACP PromptResponse (may carry a ``usage`` field).
            session_id: Session identifier used as the response_id for metrics.
            elapsed: Wall-clock seconds for this prompt round-trip (optional).
            usage_update: The synchronized ACP UsageUpdate for this turn, if any.
        """
        # -- Cost recording ---------------------------------------------------
        # claude-agent-acp, codex-acp: report cost via UsageUpdate notification
        # gemini-cli: does not send UsageUpdate (cost derived from tokens below)
        cost_recorded = False
        if usage_update is not None and usage_update.cost is not None:
            last_cost = self._client._last_cost_by_session.get(session_id, 0.0)
            delta = usage_update.cost.amount - last_cost
            if delta > 0:
                self.llm.metrics.add_cost(delta)
                cost_recorded = True
            self._client._last_cost_by_session[session_id] = usage_update.cost.amount
            self._client._last_cost = usage_update.cost.amount

        # -- Token usage recording --------------------------------------------
        input_tokens, output_tokens, cache_read, cache_write, reasoning = (
            _extract_token_usage(response)
        )
        if input_tokens or output_tokens:
            self.llm.metrics.add_token_usage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                reasoning_tokens=reasoning,
                context_window=self._client._context_window_by_session.get(
                    session_id, self._client._context_window
                ),
                response_id=session_id,
            )

        # -- Cost derivation from tokens --------------------------------------
        # gemini-cli: no UsageUpdate cost, so derive from token counts using
        # LiteLLM's model pricing database (same source the proxy uses).
        # claude-agent-acp, codex-acp: skipped since cost_recorded is True.
        if not cost_recorded and (input_tokens or output_tokens) and self.acp_model:
            cost = _estimate_cost_from_tokens(
                self.acp_model, input_tokens, output_tokens
            )
            if cost > 0:
                self.llm.metrics.add_cost(cost)

        if not cost_recorded and not input_tokens and not output_tokens:
            # gemini-cli currently returns response.usage=None and
            # response.field_meta=None (ACP SDK strips _meta during
            # serialization). Tracked in google-gemini/gemini-cli#24280.
            logger.debug(
                "No usage data from ACP server %s — token/cost tracking unavailable",
                self._agent_name or "unknown",
            )

        if elapsed is not None:
            self.llm.metrics.add_response_latency(elapsed, session_id)

        if self.llm.telemetry._stats_update_callback is not None:
            try:
                self.llm.telemetry._stats_update_callback()
            except Exception:
                logger.debug("Stats update callback failed", exc_info=True)

    # -- Capability helpers ------------------------------------------------

    @property
    def supports_openhands_tools(self) -> bool:
        """``False`` — the ACP server manages its own toolset."""
        return False

    @property
    def supports_openhands_mcp(self) -> bool:
        """``False`` — OpenHands does not create in-process MCP *tools* here.

        This stays ``False`` even though ``mcp_config`` is honored: any
        configured MCP servers are forwarded to the ACP subprocess at session
        creation (see :func:`_mcp_config_to_acp_servers`) rather than connected
        in-process. The ACP server owns the MCP connection and surfaces the
        tools through its own turn.
        """
        return False

    @property
    def supports_condenser(self) -> bool:
        """``False`` — the ACP server manages its own context window."""
        return False

    @property
    def agent_kind(self) -> Literal["acp"]:
        """ACP agents have ``agent_kind == "acp"``."""
        return "acp"

    # -- ACP-specific runtime properties -----------------------------------

    @property
    def agent_name(self) -> str:
        """Name of the ACP server (from InitializeResponse.agent_info)."""
        return self._agent_name

    @property
    def agent_version(self) -> str:
        """Version of the ACP server (from InitializeResponse.agent_info)."""
        return self._agent_version

    @property
    def current_model_id(self) -> str | None:
        """The model the ACP server is currently using for this session.

        Captured from ``models.currentModelId`` on the
        ``new_session`` / ``load_session`` response when the server surfaces
        it (UNSTABLE ACP capability), or ``self.acp_model`` when the caller
        explicitly chose one.  ``None`` for older servers that don't report
        model state and when no override was set — callers should treat the
        value as best-effort.

        Note: this is in-process runtime state; it does not round-trip
        through ``model_dump()``.  Consumers that need to read it across the
        API boundary should look at ``ConversationInfo.current_model_id``,
        which the agent-server lifts off the agent into the response.
        """
        return self._current_model_id

    @property
    def available_models(self) -> list[ACPModelInfo]:
        """Models the ACP server offers for this session.

        Captured verbatim from ``models.availableModels`` on the
        ``new_session`` / ``load_session`` response (UNSTABLE ACP capability);
        empty for servers that don't surface it.  Each entry carries the
        server's ``model_id`` plus an optional ``name``/``description`` —
        enough for a client to render a model picker and resolve
        ``current_model_id`` to a display label without any server-side
        curation.  ``current_model_id`` is the value to pass to
        ``set_session_model`` to switch.

        Same lifecycle and serialization caveats as ``current_model_id``:
        in-process runtime state, lifted onto
        ``ConversationInfo.available_models`` by the agent-server for
        cross-process consumers. Always a list (the internal ``None``
        "not-reported" sentinel is coerced to ``[]`` here).
        """
        return list(self._available_models or [])

    @property
    def supports_runtime_model_switch(self) -> bool:
        """Whether a live, mid-conversation model switch will be attempted.

        Tells a client whether to offer the inline picker's live-switch control.
        ``True`` only for known providers that explicitly declare support for
        ``session/set_model``. Unknown/custom providers use ``set_config_option``
        for *initial* model selection but that RPC is a generic config write, not
        a guaranteed live-switch primitive, so the picker is hidden for them.
        ``False`` before a session exists (nothing to switch yet).

        See
        :meth:`~openhands.sdk.conversation.impl.local_conversation.LocalConversation.switch_acp_model`.
        """
        if self._session_id is None:
            return False
        provider = detect_acp_provider_by_agent_name(self._agent_name)
        return provider is not None and provider.supports_runtime_model_switch

    def get_all_llms(self) -> Generator[LLM]:
        yield self.llm

    # -- Lifecycle ---------------------------------------------------------

    def init_state(
        self,
        state: ConversationState,
        on_event: ConversationCallbackType,
    ) -> None:
        """Spawn the ACP server and initialize a session."""
        # Validate unsupported execution features. agent_context is allowed
        # because it contributes prompt-only extensions to user messages; ACP
        # server tools and context-window management remain owned by the server.
        # mcp_config IS supported: its servers are forwarded to the subprocess at
        # session creation (see _mcp_config_to_acp_servers) rather than turned
        # into in-process OpenHands MCP tools.
        if self.tools:
            raise NotImplementedError(
                "ACPAgent does not support custom tools; "
                "the ACP server manages its own tools"
            )
        if self.condenser is not None:
            raise NotImplementedError(
                "ACPAgent does not support condenser; "
                "the ACP server manages its own context"
            )
        if self.agent_context:
            self.agent_context.validate_acp_compatibility()

        from openhands.sdk.utils.async_executor import AsyncExecutor

        self._executor = AsyncExecutor()

        # Render the suffix once, pulling secrets from the conversation's
        # secret_registry to match the regular Agent's get_dynamic_context().
        self._installed_suffix = self._render_suffix(state)
        # A prior session id means we may be resuming; used by ``truly_resumed``
        # below to decide whether the model state reported for this launch
        # describes the resumed session or a fresh one. An explicit
        # ``acp_resume_session_id`` (e.g. a cloud session-id mirror feeding the
        # id back after base_state.json was wiped) takes precedence over the
        # FS-persisted id, matching the precedence in ``_start_acp_server`` — so
        # ``truly_resumed`` (``self._session_id == prior_session_id``) stays
        # correct whether the resumed id came from the FS or the explicit field.
        prior_session_id = self.acp_resume_session_id or state.agent_state.get(
            "acp_session_id"
        )
        # ``acp_suffix_installed`` is persisted by
        # ``_commit_suffix_installation`` only after the first prompt has
        # actually returned successfully, so on resume we know whether the
        # ACP subprocess received the suffix.  ``acp_session_id`` alone is
        # not a reliable signal — it is persisted at session-creation time
        # regardless of whether the first prompt succeeded, so inferring
        # "installed" from session id presence would skip suffix injection
        # for sessions whose first turn was cancelled mid-prompt.  Older
        # persisted state (from before this PR introduced the marker)
        # will re-inject the suffix on the first turn after upgrade, which
        # is benign — the suffix is additive LLM-context guidance.
        suffix_already_installed = bool(state.agent_state.get("acp_suffix_installed"))
        # Best-effort initial value for _resumed_existing_session; the real
        # start path overwrites it once _start_acp_server returns.
        self._resumed_existing_session = bool(prior_session_id)

        try:
            self._start_acp_server(state)
        except Exception as e:
            logger.error("Failed to start ACP server: %s", e)
            self._cleanup()
            # init_state runs *outside* run()/arun()'s try-block (it is reached
            # via _ensure_agent_ready() before the loop starts), so a cold-start
            # failure — bad/expired auth, missing CLI binary, cwd mismatch — would
            # otherwise bypass error emission and reach the client as a generic
            # "remote conversation ended with error".  Emit a typed
            # ConversationErrorEvent and flip the status to ERROR here, mirroring
            # what the regular Agent (and ACPAgent.astep) do from inside the run
            # loop, so clients render their existing error banner instead.
            # Best-effort: surfacing the error must never mask the original
            # exception, which still propagates to preserve the existing
            # cleanup/re-raise contract that run()/arun() rely on.
            try:
                state.execution_status = ConversationExecutionStatus.ERROR
                on_event(
                    ConversationErrorEvent(
                        source="agent",
                        code=_classify_acp_init_error(e),
                        detail=str(e)[:500],
                    )
                )
            except Exception:
                logger.exception("Failed to surface ACP init error to client")
            raise

        # A successful resume keeps the prior id; cwd mismatch and load_session
        # failure both fall back to ``new_session``, which mints a fresh one.
        # The session-id comparison is the only authoritative signal — the
        # decision happens inside ``_start_acp_server`` and isn't otherwise
        # observable here.
        #
        # When _start_acp_server is patched out in tests, self._session_id
        # stays None (never set by the mock).  In that case, keep the
        # best-effort _resumed_existing_session value set above; otherwise
        # override with the authoritative comparison result.
        if self._session_id is not None:
            truly_resumed = (
                prior_session_id is not None and self._session_id == prior_session_id
            )
            self._resumed_existing_session = truly_resumed
        else:
            truly_resumed = self._resumed_existing_session

        self._initialized = True

        # Persist agent info + the ACP session id + its cwd in agent_state.
        # Keeping these here (rather than on the frozen ACPAgent model) means
        # ConversationState's existing base_state.json persistence carries
        # them across agent-server restarts, and ``_start_acp_server`` on the
        # next launch reads them back to call ``load_session`` instead of
        # starting from scratch.  We record ``acp_session_cwd`` alongside the
        # id because ACP servers key their persistence by ``cwd``: resuming
        # in a different working directory would at best silently miss the
        # prior session and at worst load a different session that happens to
        # exist at the new cwd.
        new_agent_state = {
            **state.agent_state,
            "acp_agent_name": self._agent_name,
            "acp_agent_version": self._agent_version,
            "acp_session_id": self._session_id,
            "acp_session_cwd": self._working_dir,
            # Static provider capability — persisted so cold reads of the
            # conversation list can tell the picker whether to offer live
            # switching without re-detecting the provider server-side.
            "acp_supports_runtime_model_switch": self.supports_runtime_model_switch,
        }
        # When starting a fresh session, clear stale suffix marker so the next
        # launch knows to re-inject it (PR behavior: suffix state is per-session).
        if not self._resumed_existing_session:
            new_agent_state.pop("acp_suffix_installed", None)
        # Model state tracking (from main): persist current model id and
        # available models list for cold reads of the conversation list.
        override_attempted_not_applied = bool(self.acp_model) and (
            not self._model_override_applied
        )
        if self._current_model_id is not None:
            new_agent_state["acp_current_model_id"] = self._current_model_id
        elif (
            not truly_resumed
            or self._available_models is not None
            or override_attempted_not_applied
        ):
            new_agent_state.pop("acp_current_model_id", None)
        if self._available_models is not None:
            new_agent_state["acp_available_models"] = [
                m.model_dump() for m in self._available_models
            ]
        elif not truly_resumed:
            new_agent_state.pop("acp_available_models", None)
        state.agent_state = new_agent_state

        if self._installed_suffix:
            self._suffix_install_state = (
                "installed"
                if suffix_already_installed and self._resumed_existing_session
                else "pending_first_prompt"
            )

        # Emit a placeholder system prompt so the visualizer shows a section
        # even though the real system prompt is managed by the ACP server.
        # dynamic_context mirrors agent.py's SystemPromptEvent so that tooling
        # (UI, tests) can inspect what suffix was installed.
        on_event(
            SystemPromptEvent(
                source="agent",
                system_prompt=TextContent(
                    text=(
                        "This conversation is powered by an ACP server. "
                        "The system prompt and tools are managed by the "
                        "ACP server and are not available for display."
                    )
                ),
                dynamic_context=TextContent(text=self._installed_suffix)
                if self._installed_suffix
                else None,
                tools=[],
            )
        )

    def _render_suffix(self, state: ConversationState) -> str | None:
        """Render the system suffix once, including secrets from the registry.

        The ``<CUSTOM_SECRETS>`` block lists every secret the ACP subprocess
        will receive, so the agent knows which env vars are available without
        them being inlined in the prompt. We render it from
        ``state.secret_registry`` even when ``agent_context`` is absent —
        otherwise a conversation that only ships secrets through the
        ``StartConversationRequest.secrets`` channel (the canonical path)
        would silently drop the advertisement, leaving the agent ignorant of
        secrets that are nonetheless about to land in its env via
        ``_start_acp_server``.

        Reserved file-content secrets (Codex ``auth.json``, Gemini Vertex SA —
        see :meth:`_materialise_file_secrets`) are dropped from the
        advertisement: their values are written to disk, not injected as env
        vars, so advertising them as available env vars would mislead the agent.
        """
        # Advertise from state.secret_registry alone — it now holds
        # agent_context.secrets too (seeded at conversation init, with their
        # descriptions), so it is the single source for the <CUSTOM_SECRETS>
        # block. Reserved file-content secrets are written to disk, not injected
        # as env vars, so drop them from the advertisement.
        file_secret_names = self._present_file_secret_names(state)
        secret_infos = [
            info
            for info in state.secret_registry.get_secret_infos()
            if info.get("name") not in file_secret_names
        ]
        agent_context = self.agent_context
        if agent_context is None:
            # No caller-supplied context. Only synthesize an empty one for the
            # renderer if we actually have a registry-secret advertisement to
            # emit — otherwise return None so we don't start injecting other
            # parts of the empty AgentContext's defaults (current_datetime, …)
            # that the old "agent_context is None ⇒ no suffix" rule used to
            # suppress.
            if not secret_infos:
                return None
            agent_context = AgentContext(current_datetime=None)
        elif agent_context.secrets:
            # The registry already carries these (and their descriptions), so
            # clear the agent_context copy to advertise from the registry alone
            # rather than re-merging a redundant second source.
            agent_context = agent_context.model_copy(update={"secrets": {}})
        return agent_context.to_acp_prompt_context(additional_secret_infos=secret_infos)

    def _present_file_secret_names(self, state: ConversationState) -> set[str]:
        """Reserved file-content secret names supplied for this conversation.

        A name counts as present if it is configured in
        :attr:`acp_file_secrets` *and* registered in ``state.secret_registry``
        (which holds ``agent_context.secrets`` too, seeded at conversation
        init). These names are materialised to disk and therefore excluded from
        the plain env-var injection and the ``<CUSTOM_SECRETS>`` advertisement
        (their values are file blobs, not env vars the subprocess can reference
        by name).
        """
        configured = {spec.secret_name for spec in self.acp_file_secrets}
        if not configured:
            return set()
        return set(state.secret_registry.secret_sources) & configured

    def _acp_file_secret_dir(self, state: ConversationState, subdir: str) -> Path:
        """Durable per-conversation directory for a credential file.

        ``<persistence_dir>/acp/{subdir}`` — the same per-conversation tree the
        regular agent persists ``base_state.json`` / events to, so a token the
        CLI refreshes on disk survives a pod recycle (``/workspace`` persists
        across pause/resume; see #1018/#1019). Falls back to a per-conversation
        directory under the workspace when the conversation is not persisted
        (e.g. in-memory tests) — still seed-if-absent, still no
        ``TemporaryDirectory``. Returned absolute so the subprocess (which
        inherits the agent-server's cwd) resolves it unambiguously.
        """
        if state.persistence_dir:
            root = Path(state.persistence_dir) / "acp" / subdir
        else:
            root = Path(state.workspace.working_dir) / ".openhands" / "acp" / subdir
        return Path(os.path.abspath(root))

    def _isolate_acp_data_dir(
        self, state: ConversationState, env: dict[str, str]
    ) -> None:
        """Relocate the CLI's data/config root to a per-conversation directory.

        When :attr:`acp_isolate_data_dir` is set, point the recognised provider's
        data-dir env var (``CODEX_HOME`` / ``CLAUDE_CONFIG_DIR`` / ``HOME``) at
        ``<persistence_dir>/acp/<provider>`` — the same per-conversation tree
        :meth:`_materialise_file_secrets` seeds auth into, so a relocated
        ``CODEX_HOME`` and a materialised ``auth.json`` always agree on one
        directory. This stops conversations that share a sandbox
        (``SandboxGroupingStrategy != NO_GROUPING``) from racing on a single
        shared HOME's CLI auth/config/cache/lock files (#1019). The override
        replaces an ambient value (e.g. the agent-server's own ``CODEX_HOME``):
        the per-conversation root is what isolation is for.

        No-ops for an unrecognised command or a provider without a relocation
        lever. An explicit ``acp_env`` pin of the data-dir var wins (it has the
        highest precedence and is honoured as the materialisation target too), so
        leave it untouched.

        Claude note: relocating ``CLAUDE_CONFIG_DIR`` is safe under either auth
        mode. :data:`_ENV_CONFLICT_MAP` is keyed on the OAuth token
        (``CLAUDE_CODE_OAUTH_TOKEN``), not on ``CLAUDE_CONFIG_DIR``, so setting
        the config dir for isolation no longer strips a working
        ``ANTHROPIC_API_KEY`` — API-key Claude gets the same per-conversation
        isolation (and pause/resume continuity) as OAuth Claude (#3588).

        ``HOME`` (gemini-cli's only lever — it hard-codes ``~/.gemini`` and
        ignores ``XDG``) has a wider blast radius than the surgical
        ``CODEX_HOME`` / ``CLAUDE_CONFIG_DIR``: it also relocates the home dir
        seen by anything the CLI subprocess itself spawns (``git``, ``npm``,
        ``node``, shells — e.g. ``~/.gitconfig``, ``~/.npmrc``, the npm cache).
        That is accepted as the cost of isolating Gemini at all; callers that
        need a narrower scope can pin ``HOME`` via ``acp_env`` (honoured below)
        or leave isolation off for Gemini.

        Ordering: this runs *after* the ``secret_registry`` injection and the
        ``acp_env`` update in :meth:`_start_acp_server` so an ``acp_env`` pin of
        the data-dir var is visible and wins. Relocation is now credential-blind
        (the auth-conflict strip is keyed on ``CLAUDE_CODE_OAUTH_TOKEN``, not on
        the config dir), so the data-dir var it sets never affects auth.
        """
        provider = detect_acp_provider_by_command(self.acp_command)
        if provider is None or provider.data_dir_env_var is None:
            return
        env_var = provider.data_dir_env_var
        if env_var in self.acp_env:
            return
        data_dir = self._acp_file_secret_dir(state, provider.key)
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        env[env_var] = str(data_dir)

    def _materialise_file_secrets(
        self, state: ConversationState, env: dict[str, str]
    ) -> None:
        """Seed reserved file-content credentials onto disk and point the CLI at them.

        For each spec in :attr:`acp_file_secrets` whose secret is registered in
        ``state.secret_registry``, write its value to the spec's durable
        per-conversation directory (:meth:`_acp_file_secret_dir`) and set the
        controlling env var (``CODEX_HOME`` / ``GOOGLE_APPLICATION_CREDENTIALS``)
        unless the caller pinned it via ``acp_env``.

        Seed-if-absent: a non-empty existing file is preserved, never clobbered
        — so a token the CLI rewrites on refresh (Codex) survives a recycle, and
        a stale pasted blob can't overwrite the live one. Files are ``0600`` in
        ``0700`` directories. The blob secret itself is not exported as an env
        var (callers exclude it via :meth:`_present_file_secret_names`); only
        the path env var is set.

        If the caller pinned the data-dir env var via the (deprecated)
        ``acp_env``, the credential is seeded *where that pin points* so the file
        and env stay consistent — and ``acp_env`` keeps its precedence over the
        env var.
        """
        for spec in self.acp_file_secrets:
            name = spec.secret_name
            value = state.secret_registry.get_secret_value(name)
            if not value:
                continue
            # Seed where the data-dir env var will actually point: an explicit
            # acp_env pin (which wins in env precedence) overrides the default
            # per-conversation root, so honor it as the write target too.
            pinned = self.acp_env.get(spec.env_var)
            if pinned and spec.env_points_to == "dir":
                directory = Path(pinned)
                target = directory / spec.filename
            elif pinned:  # env_points_to == "file"
                target = Path(pinned)
                directory = target.parent
            else:
                directory = self._acp_file_secret_dir(state, spec.subdir)
                target = directory / spec.filename
            try:
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                # Tighten the SDK-owned per-conversation dir in case it
                # pre-existed or umask widened mkdir's mode. Skip for an
                # externally-pinned acp_env dir (e.g. a deliberately
                # group-readable shared mount) so we don't silently narrow
                # permissions the user chose.
                if not pinned:
                    directory.chmod(0o700)
                    # Also clamp the shared SDK-owned `acp/` parent, which
                    # parents=True may have created under the process umask
                    # (e.g. 0o755); the leaf chmod above only covers <subdir>.
                    # Stop at `acp/` — its parent is the persistence layer's.
                    directory.parent.chmod(0o700)
                if target.is_file() and target.stat().st_size > 0:
                    # Seed-if-absent: keep the (possibly CLI-refreshed) contents,
                    # but still clamp perms — a pre-existing credential file may
                    # be world-readable (e.g. 0644 from another tool/restore).
                    target.chmod(0o600)
                    logger.info(
                        "ACP file-secret %r already present at %s; preserving "
                        "(seed-if-absent)",
                        name,
                        target,
                    )
                else:
                    _write_secret_file(target, value)
                    logger.info("Materialised ACP file-secret %r -> %s", name, target)
            except OSError:
                # Fail fast rather than swallowing: if the credential the caller
                # supplied can't be written (read-only/full workspace mount, etc.)
                # its data-dir env var would never be set and the subprocess would
                # fail at auth time with a cryptic CLI error and no SDK breadcrumb.
                # Re-raising lets init_state surface a typed ConversationErrorEvent
                # (ACPInitError) that names the materialisation failure.
                logger.exception(
                    "Failed to materialise ACP file-secret %r under %s",
                    name,
                    directory,
                )
                raise
            # acp_env (applied last in _start_acp_server) keeps precedence; only
            # set the env var here when the caller did not pin it.
            if spec.env_var not in self.acp_env:
                env[spec.env_var] = str(
                    directory if spec.env_points_to == "dir" else target
                )
            for companion in spec.warn_if_unset:
                if not env.get(companion) and companion not in self.acp_env:
                    logger.warning(
                        "ACP file-secret %r materialised but %s is unset; the "
                        "provider may fail to authenticate until it is configured",
                        name,
                        companion,
                    )

    def _start_acp_server(self, state: ConversationState) -> None:
        """Start the ACP subprocess and initialize the session."""
        client = _OpenHandsACPBridge()
        self._client = client
        # Bind the secret masker for the conversation's lifetime. It's derived
        # from state.secret_registry (stable for the conversation) and is a pure
        # read of _exported_values, so it has none of the cross-thread/state-lock
        # hazards that make on_event/on_token strictly per-turn. Binding it here
        # (rather than per-turn in _reset_client_for_turn) keeps it available for
        # session updates AND for ask_agent() forks, which run on the shared
        # client and may fire while no step()/astep() turn is active.
        client.mask = state.secret_registry.mask_secrets_in_output

        # Build the subprocess environment. Precedence, highest first:
        #   acp_env > state.secret_registry > os.environ > default_environment
        #
        # Conversation credentials intentionally OVERRIDE ambient os.environ: an
        # explicit per-conversation / provider secret must win over a same-named
        # variable in the agent-server's own environment. acp_env (deprecated)
        # stays highest.
        #
        # agent_context.secrets are seeded into secret_registry at
        # LocalConversation.__init__ (lower priority than request.secrets), so
        # the registry is now the single channel for all secrets including
        # provider credentials (keyed by the provider's env var name).
        env = default_environment()
        env.update(os.environ)
        _strip_inherited_npm_env(env)
        if self.acp_env:
            warn_deprecated(
                "ACPAgent.acp_env",
                deprecated_in="1.24.0",
                removed_in="1.29.0",
                details=(
                    "Route ACP subprocess env/credentials through "
                    "state.secret_registry (e.g. agent_context.secrets / "
                    "StartConversationRequest.secrets) instead."
                ),
            )
        # Reserved file-content credential secrets (Codex auth.json, Gemini
        # Vertex SA — see _materialise_file_secrets) are written to disk, not
        # injected as env vars, so exclude their (large blob) names from the
        # plain env-injection below; materialisation sets only the path env var.
        file_secret_names = self._present_file_secret_names(state)
        # Inject the whole registry: an ACP CLI is a black box we can't
        # name-scan per command (unlike the regular agent's bash tool), so
        # credentials must be delivered upfront. Registry values override
        # ambient os.environ. Skip keys acp_env will set last (avoids a
        # redundant LookupSecret.get_value()) and file secrets (materialised to
        # disk below).
        env.update(
            state.secret_registry.get_all_secrets_as_env_vars(
                exclude=set(self.acp_env) | file_secret_names
            )
        )
        # Materialise reserved file-content secrets to disk and point their
        # data-dir env vars (CODEX_HOME / GOOGLE_APPLICATION_CREDENTIALS) at the
        # written files. Done before acp_env so an explicit acp_env override of
        # those vars still wins.
        self._materialise_file_secrets(state, env)
        # acp_env (deprecated) has highest precedence.
        env.update(self.acp_env)
        # Strip CLAUDECODE so nested Claude Code instances don't refuse to start
        env.pop("CLAUDECODE", None)

        # Relocate the CLI's data/config root to a per-conversation directory so
        # sandbox-sharing conversations don't race on a shared HOME (#1019).
        # Runs after the registry injection and the acp_env update above so an
        # acp_env pin of the data-dir var wins. Independent of the strip below
        # (keyed on the OAuth token, not the data-dir var), so ordering relative
        # to it no longer matters for correctness.
        if self.acp_isolate_data_dir:
            self._isolate_acp_data_dir(state, env)

        # Strip env vars that conflict with an active auth mechanism: an active
        # CLAUDE_CODE_OAUTH_TOKEN must not coexist with ANTHROPIC_API_KEY (which
        # takes precedence) or ANTHROPIC_BASE_URL (proxies the bearer). See #3588.
        for dominant, conflicts in _ENV_CONFLICT_MAP.items():
            if dominant in env:
                for conflict in conflicts:
                    env.pop(conflict, None)

        command = self.acp_command[0]
        args = list(self.acp_command[1:]) + list(self.acp_args)
        # codex ignores OPENAI_BASE_URL; translate it into the config key it
        # reads. Reads the *fully assembled* env above, so it fires regardless of
        # which channel delivered OPENAI_BASE_URL (agent_context.secrets,
        # state.secret_registry / StartConversationRequest.secrets, acp_env,
        # os.environ) — i.e. eval, canvas, and cloud all route the same way.
        args += _codex_base_url_overrides(command, args, env)

        working_dir = str(state.workspace.working_dir)

        # Prior ACP session id — typically survives agent-server restarts via
        # ConversationState.agent_state (serialized into base_state.json).
        # Its presence is the signal to resume; its absence means fresh start.
        # ACP servers key persistence by ``cwd``; if the workspace moved we
        # drop the id so we don't accidentally resume (or silently load) a
        # session the server associates with a different directory.
        #
        # ``acp_resume_session_id`` (set on the agent config) takes precedence
        # over the FS-persisted id. This lets cloud deployments mirror the id
        # into a durable store and pass it back on the first launch of a fresh
        # sandbox, even when ``base_state.json`` was wiped along with the
        # previous sandbox filesystem.
        #
        # Note the asymmetry on the cwd guard: for an FS-persisted id we have
        # the cwd it was created under and can refuse to load when it differs,
        # because resuming the wrong session silently would be catastrophic.
        # For an explicit ``acp_resume_session_id`` we do *not* have that
        # recorded cwd — the contract is "the caller knows what they're doing"
        # (the app-server only mirrors ids for conversations whose sandbox
        # always lands in the same ``working_dir``). We therefore assume
        # cwd-compatibility and let the ACP server's own ``session/load``
        # validation be the last line of defence: a server-side cwd mismatch
        # returns an ``ACPRequestError``, already caught below and falling back
        # to ``new_session`` — the same recovery path as a forgotten id.
        fs_session_id: str | None = state.agent_state.get("acp_session_id")
        fs_session_cwd: str | None = state.agent_state.get("acp_session_cwd")
        if self.acp_resume_session_id and self.acp_resume_session_id != fs_session_id:
            logger.info(
                "Using explicit acp_resume_session_id (%s); "
                "filesystem agent_state had id=%s",
                _fingerprint_session_id(self.acp_resume_session_id),
                _fingerprint_session_id(fs_session_id),
            )
            prior_session_id: str | None = self.acp_resume_session_id
            prior_session_cwd: str | None = working_dir
        else:
            prior_session_id = fs_session_id
            prior_session_cwd = fs_session_cwd
        if prior_session_id is not None and prior_session_cwd not in (
            None,
            working_dir,
        ):
            logger.warning(
                "ACP session %s was created with cwd=%s; current cwd=%s differs, "
                "starting a fresh session instead of resuming",
                _fingerprint_session_id(prior_session_id),
                prior_session_cwd,
                working_dir,
            )
            prior_session_id = None

        async def _init() -> tuple[
            str, str, str, str | None, list[ACPModelInfo] | None, bool
        ]:
            # Spawn the subprocess directly so we can install a
            # filtering reader that skips non-JSON-RPC lines some
            # ACP servers (e.g. claude-code-acp v0.1.x) write to
            # stdout.
            process = await asyncio.create_subprocess_exec(
                command,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                limit=_STREAM_READER_LIMIT,
            )
            assert process.stdin is not None
            assert process.stdout is not None

            # Wrap the subprocess stdout in a filtering reader that
            # only passes lines starting with '{' (JSON-RPC messages).
            filtered_reader = asyncio.StreamReader(limit=_STREAM_READER_LIMIT)
            asyncio.get_event_loop().create_task(
                _filter_jsonrpc_lines(process.stdout, filtered_reader)
            )

            conn = ClientSideConnection(
                client,
                process.stdin,  # write to subprocess
                filtered_reader,  # read filtered output
            )

            # Track the subprocess/connection on self as soon as they exist, so
            # that if a *later* init step fails (e.g. the resume model reapply
            # times out or the server errors), init_state()'s _cleanup() can
            # still tear them down instead of leaking the subprocess/connection.
            # The "session initialized" gating keys off _session_id (assigned
            # last, on full success), so an early _conn here does not make the
            # agent look ready before _init completes.
            self._process = process
            self._conn = conn
            self._filtered_reader = filtered_reader

            # Initialize the protocol and discover server identity
            init_response = await conn.initialize(protocol_version=1)
            agent_name = ""
            agent_version = ""
            if init_response.agent_info is not None:
                agent_name = init_response.agent_info.name or ""
                agent_version = init_response.agent_info.version or ""
            logger.info(
                "ACP server initialized: agent_name=%r, agent_version=%r",
                agent_name,
                agent_version,
            )

            # Translate any configured MCP servers into ACP protocol objects,
            # gating remote (http/sse) transports on what this server advertised
            # in its initialize response. The same list is passed to both
            # new_session and load_session: load_session does not persist the
            # prior MCP set server-side, so a resume must re-send it or the
            # restored session would silently lose its MCP servers.
            mcp_caps = (
                init_response.agent_capabilities.mcp_capabilities
                if init_response.agent_capabilities is not None
                else None
            )
            acp_mcp_servers = _mcp_config_to_acp_servers(self.mcp_config, mcp_caps)
            if acp_mcp_servers:
                logger.info(
                    "Forwarding %d MCP server(s) to ACP session: %s",
                    len(acp_mcp_servers),
                    [s.name for s in acp_mcp_servers],
                )

            # Authenticate if the server requires it.  Some ACP servers
            # (e.g. codex-acp) require an explicit authenticate call
            # before session creation.  We auto-detect the method from
            # the env vars that are available to the process.
            auth_methods = init_response.auth_methods or []
            if auth_methods:
                method_id = _select_auth_method(auth_methods, env)
                if method_id is not None:
                    logger.info("Authenticating with ACP method: %s", method_id)
                    auth_kwargs: dict[str, Any] = {}
                    # gemini-cli: pass gateway baseUrl to route API calls
                    # through LiteLLM proxy. claude-agent-acp and codex-acp
                    # read their provider base URL from env vars directly.
                    if method_id == "gemini-api-key":
                        provider = detect_acp_provider_by_agent_name(agent_name)
                        base_url_var = (
                            provider.base_url_env_var if provider is not None else None
                        )
                        if base_url_var:
                            base_url = env.get(base_url_var)
                            if base_url:
                                auth_kwargs["gateway"] = {"baseUrl": base_url}
                    await conn.authenticate(method_id=method_id, **auth_kwargs)
                else:
                    logger.warning(
                        "ACP server offers auth methods %s but no matching "
                        "env var is set — session creation may fail",
                        [m.id for m in auth_methods],
                    )

            # Resume the prior ACP session if we have its id.  If the server
            # has forgotten it (state wiped, new host, etc.) fall through to
            # new_session so the conversation still starts cleanly.
            #
            # We only swallow ACPRequestError here: that is the protocol-level
            # "I don't know this session" signal and is recoverable by
            # starting fresh.  Transport failures (broken pipe, EOF, timeout,
            # subprocess crash) propagate — there is no working connection to
            # fall back on, and the outer init_state handler cleans up.
            session_id: str | None = None
            reported_model_id: str | None = None
            available_models: list[ACPModelInfo] | None = None
            if prior_session_id is not None:
                try:
                    load_response = await conn.load_session(
                        cwd=working_dir,
                        session_id=prior_session_id,
                        mcp_servers=acp_mcp_servers,
                    )
                    session_id = prior_session_id
                    reported_model_id, available_models = _extract_session_models(
                        load_response
                    )
                    logger.info(
                        "Resumed ACP session %s (cwd=%s)",
                        _fingerprint_session_id(session_id),
                        working_dir,
                    )
                except ACPRequestError as e:
                    logger.warning(
                        "ACP load_session(%s) failed (%s); starting a fresh session",
                        _fingerprint_session_id(prior_session_id),
                        e,
                    )

            # Track whether ``acp_model`` was actually pushed to the server so
            # ``current_model_id`` below can stay honest: a caller override that
            # never reached the server (unknown provider on a fresh session, or
            # a resume whose ``set_session_model`` the server rejected) must not
            # be surfaced as the live model.
            override_applied = False
            if session_id is None:
                # Fresh session. Build _meta content for session options (e.g.
                # model selection). Extra kwargs to new_session() become the
                # _meta dict in the JSON-RPC request — do NOT wrap in _meta=
                # (that double-nests).
                session_meta = build_session_model_meta(agent_name, self.acp_model)
                response = await conn.new_session(
                    cwd=working_dir,
                    mcp_servers=acp_mcp_servers,
                    **session_meta,
                )
                session_id = response.session_id
                reported_model_id, available_models = _extract_session_models(response)
                # Initial-selection protocol call for providers that use it
                # (codex-acp, gemini-cli); no-op for claude, which selected its
                # model via the _meta above.
                applied_via_call = await _maybe_set_session_model(
                    conn,
                    agent_name,
                    session_id,
                    self.acp_model,
                )
                override_applied = bool(session_meta) or applied_via_call
            else:
                # Resumed session. load_session() does not carry model _meta, so
                # reapply the persisted (possibly runtime-switched) acp_model via
                # the runtime-switch capability — otherwise the resumed live
                # session would run on the server default while serialized state
                # claims the switched model.
                override_applied = await _reapply_session_model_on_resume(
                    conn,
                    agent_name,
                    session_id,
                    self.acp_model,
                )

            # Resolve the model the agent will actually use.
            current_model_id = (
                self.acp_model
                if (self.acp_model and override_applied)
                else reported_model_id
            )

            # Resolve the permission mode.  Known providers each have their
            # own mode ID (bypassPermissions, full-access, yolo …).
            # Unknown/custom servers get None — skip the call rather than
            # sending a provider-specific string they won't recognise.
            provider = detect_acp_provider_by_agent_name(agent_name)
            mode_id = self.acp_session_mode or (
                provider.default_session_mode if provider else None
            )
            if mode_id is not None:
                logger.info("Setting ACP session mode: %s", mode_id)
                await conn.set_session_mode(mode_id=mode_id, session_id=session_id)

            return (
                session_id,
                agent_name,
                agent_version,
                current_model_id,
                available_models,
                override_applied,
            )

        # _conn / _process / _filtered_reader are assigned to the instance inside
        # _init() so a mid-init failure can be cleaned up; only the
        # success-only fields (including the resolved model state) are returned.
        (
            self._session_id,
            self._agent_name,
            self._agent_version,
            self._current_model_id,
            self._available_models,
            self._model_override_applied,
        ) = self._executor.run_async(_init)
        self._working_dir = working_dir

    def _reset_client_for_turn(
        self,
        on_token: ConversationTokenCallbackType | None,
        on_event: ConversationCallbackType,
    ) -> None:
        """Reset per-turn client state and (re)wire live callbacks.

        Called at the start of ``step()`` and again on each retry inside the
        prompt loop so that the three callbacks (``on_token``, ``on_event``,
        ``on_activity``) stay in sync with the fresh turn after ``reset()``
        clears them.  ``on_event`` is fired from inside
        ``_OpenHandsACPBridge.session_update`` as tool-call notifications
        arrive, so consumers see ACPToolCallEvents streamed live instead of
        a single end-of-turn burst.  The secret masker is bound once in
        ``_start_acp_server`` (conversation-stable), not here.
        """
        self._client.reset()
        self._client.on_token = on_token
        self._client.on_event = on_event
        self._client.on_activity = self._on_activity
        # Start the idle-timeout clock fresh for this attempt so the deadline
        # is measured from the send (or retry), not from a stale value.
        self._client.arm_activity_clock()

    def _cancel_inflight_tool_calls(self) -> None:
        """Emit a terminal ``failed`` ACPToolCallEvent for every tool call
        in the accumulator that has not reached a terminal status yet.

        ACP servers mint fresh ``tool_call_id``s on a retried turn, so any
        ``pending`` / ``in_progress`` events already streamed during the
        failed attempt would otherwise be orphaned on ``state.events`` —
        no later notification reuses their id, and consumers that dedupe
        by ``tool_call_id`` + "last-seen status wins" would keep them
        spinning forever.  This method closes those cards before we wipe
        the in-memory accumulator on retry / turn abort.

        Captures the bridge's ``on_event`` callback, then unwires the bridge
        before emitting synthetic terminal events so trailing updates from the
        abandoned portal prompt cannot land after these failures.  No-op if
        ``on_event`` was never set (e.g. tests exercising the bridge alone).
        """
        on_event = self._client.on_event
        self._clear_turn_callbacks()
        if on_event is None:
            return
        for tc in self._client.accumulated_tool_calls:
            status = tc.get("status")
            if status in _TERMINAL_TOOL_CALL_STATUSES:
                continue
            try:
                on_event(
                    ACPToolCallEvent(
                        tool_call_id=tc["tool_call_id"],
                        title=tc["title"],
                        status="failed",
                        tool_kind=tc.get("tool_kind"),
                        raw_input=tc.get("raw_input"),
                        raw_output=tc.get("raw_output"),
                        content=tc.get("content"),
                        is_error=True,
                    )
                )
            except Exception:
                logger.debug(
                    "Failed to emit supersede event for %s",
                    tc.get("tool_call_id"),
                    exc_info=True,
                )

    def _flush_inflight_tool_calls_as_completed(self) -> None:
        """Emit a terminal ``completed`` ACPToolCallEvent for every accumulated
        tool call still sitting at a non-terminal status.

        The prompt returned successfully, so a tool card the server opened but
        never closed (it sent ``ToolCallStart`` but no terminal
        ``ToolCallProgress``) is treated as completed. Since we now persist
        exactly one early ``started`` event and one terminal event per call,
        this guarantees the action->observation pairing holds for *every*
        call — without it, a server that omits the closing frame would leave
        the early ``started`` event as the last word, and the relaxed canvas
        render gate would show that card spinning forever. Reuses
        ``_emit_tool_call_event`` so truncation and error-swallowing match the
        live terminal path. No-op once every call is already terminal (the
        common case, since ``conn.prompt`` only returns after its tools run).
        """
        for tc in self._client.accumulated_tool_calls:
            if tc.get("status") in _TERMINAL_TOOL_CALL_STATUSES:
                continue
            tc["status"] = "completed"
            self._client._emit_tool_call_event(tc)

    async def _arequest_session_cancel(self) -> None:
        """Async variant of _request_session_cancel that waits for cancel send."""
        if self._conn is None or self._executor is None or self._session_id is None:
            return
        session_id = self._session_id

        async def _cancel() -> None:
            result = self._conn.cancel(session_id)
            if inspect.isawaitable(result):
                await result

        try:
            future = self._executor.portal.start_task_soon(_cancel)
            await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)),
                timeout=_ACP_CANCEL_DRAIN_TIMEOUT,
            )
        except TimeoutError:
            logger.warning(
                "Timed out sending ACP session cancel; restarting ACP session"
            )
            self._restart_session_on_next_turn = True
        except Exception:
            logger.warning("Failed to send ACP session cancel", exc_info=True)

    async def _drain_cancelled_prompt(
        self,
        future: Future[PromptResponse | None] | None,
    ) -> _PromptDrainResult:
        """Let a cancelled/timed-out portal prompt quiesce before rewiring."""
        if future is None:
            return _PromptDrainResult(
                drained=True, completed=False, response=None, error=None
            )
        if future.cancelled():
            return _PromptDrainResult(
                drained=True, completed=False, response=None, error=None
            )
        if future.done():
            try:
                return _PromptDrainResult(
                    drained=True,
                    completed=True,
                    response=future.result(),
                    error=None,
                )
            except BaseException as exc:
                return _PromptDrainResult(
                    drained=True, completed=True, response=None, error=exc
                )
        try:
            response = await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)),
                timeout=_ACP_CANCEL_DRAIN_TIMEOUT,
            )
            return _PromptDrainResult(
                drained=True, completed=True, response=response, error=None
            )
        except asyncio.CancelledError:
            if future.cancelled():
                return _PromptDrainResult(
                    drained=False, completed=False, response=None, error=None
                )
            raise
        except TimeoutError:
            logger.warning(
                "Timed out waiting for cancelled ACP prompt to drain; "
                "the ACP session will be restarted before the next turn"
            )
            return _PromptDrainResult(
                drained=False, completed=False, response=None, error=None
            )
        except BaseException as exc:
            return _PromptDrainResult(
                drained=future.done(), completed=True, response=None, error=exc
            )

    def _restart_session_after_drain_timeout(
        self,
        state: ConversationState,
        on_event: ConversationCallbackType,
    ) -> None:
        """Restart ACP after a prompt failed to quiesce post-cancel."""
        logger.warning("Restarting ACP session after cancelled prompt drain timeout")
        self._clear_turn_callbacks()
        self._cleanup()
        self._initialized = False
        # A local drain timeout means the cancelled prompt did not quiesce
        # within our short grace window; it does not prove the ACP server lost
        # its persisted session. Preserve the session id so the restarted
        # subprocess can load_session() and retain conversation memory.
        self.init_state(state, on_event=on_event)
        self._restart_session_on_next_turn = False

    def _request_session_cancel(self) -> None:
        """Ask the ACP server to cancel the active session prompt."""
        if self._conn is None or self._executor is None or self._session_id is None:
            return
        session_id = self._session_id

        async def _cancel() -> None:
            result = self._conn.cancel(session_id)
            if inspect.isawaitable(result):
                await result

        try:
            self._executor.portal.start_task_soon(_cancel)
        except Exception:
            logger.warning("Failed to send ACP session cancel", exc_info=True)

    def _build_acp_prompt(
        self, event: MessageEvent
    ) -> list[TextContentBlock | ImageContentBlock] | None:
        """Build the ACP content blocks for one user turn."""
        message = event.to_llm_message()
        blocks: list[TextContentBlock | ImageContentBlock] = []
        for content in message.content:
            if isinstance(content, TextContent) and content.text.strip():
                blocks.append(text_block(content.text))
            elif isinstance(content, ImageContent):
                for url in content.image_urls:
                    acp_block = _image_url_to_acp_block(url)
                    if acp_block is not None:
                        blocks.append(acp_block)
        if (
            self._suffix_install_state == "pending_first_prompt"
            and self._installed_suffix
        ):
            blocks.append(text_block(self._installed_suffix))
            # NOTE: do NOT flip ``_suffix_install_state`` here.  If the
            # caller (step/astep) is cancelled or fails before the ACP
            # server persists this first turn (more likely on the async
            # path, where ``asyncio.wait_for`` / ``task.cancel()`` can
            # land between block construction and the await), the local
            # state would say "installed" while the server never received
            # the suffix — and the next turn would skip it.  The actual
            # transition happens in ``_commit_suffix_installation``,
            # called from ``_finalize_successful_turn`` once the prompt
            # has returned successfully.
        if not blocks:
            return None
        return blocks

    def _commit_suffix_installation(self, state: ConversationState) -> None:
        """Mark the suffix as installed once a turn has completed.

        Called from ``_finalize_successful_turn`` so the transition only
        happens after the ACP server has actually received the suffix.
        Persists ``acp_suffix_installed=True`` into ``state.agent_state``
        so a subsequent agent-server restart, reading back the same
        ``ConversationState``, can tell whether the suffix was actually
        installed (rather than inferring it from the mere presence of
        ``acp_session_id``, which is persisted at session-creation time
        regardless of whether the first prompt succeeded).  Idempotent:
        safe to call when already ``installed`` or when there is no
        suffix to install.
        """
        if self._suffix_install_state == "pending_first_prompt":
            self._suffix_install_state = "installed"
            state.agent_state = {
                **state.agent_state,
                "acp_suffix_installed": True,
            }

    async def _do_acp_prompt(self, prompt_blocks: list[Any]) -> PromptResponse | None:
        """One ACP ``conn.prompt`` round-trip + UsageUpdate sync.

        Always runs on the portal loop (where ``self._conn`` lives).  No
        retry / timeout — callers wrap with their own per-attempt
        strategy so they can pick ``time.sleep`` (sync) or
        ``asyncio.sleep`` (async).

        Return type allows ``None`` because the ACP server is permitted
        to return an empty body (and test mocks do); downstream
        ``_finalize_successful_turn`` already accepts ``PromptResponse | None``.
        """
        usage_sync = self._client.prepare_usage_sync(self._session_id or "")
        response = await self._conn.prompt(prompt_blocks, self._session_id)
        if self._client.get_turn_usage_update(self._session_id or "") is None:
            try:
                await asyncio.wait_for(usage_sync.wait(), timeout=_USAGE_UPDATE_TIMEOUT)
            except TimeoutError:
                logger.warning(
                    "UsageUpdate not received within %.1fs for session %s",
                    _USAGE_UPDATE_TIMEOUT,
                    _fingerprint_session_id(self._session_id),
                )
        return response

    def _idle_timeout_message(self) -> str:
        return (
            f"ACP prompt timed out after {self.acp_prompt_timeout:.0f}s "
            "with no activity from the ACP server"
        )

    async def _await_with_idle_deadline(
        self,
        awaitable: Awaitable[PromptResponse | None],
        *,
        cancel_on_exit: bool,
    ) -> PromptResponse | None:
        """Await *awaitable*, aborting only after a stretch of inactivity.

        The deadline is an *idle* timeout, not a hard turn deadline: any
        ``session_update`` from the ACP server (token, thought, tool-call
        start/progress, usage) resets ``acp_prompt_timeout``, so a steadily-
        progressing agent runs as long as it keeps making progress while a
        genuinely silent (hung) server is still cut off after the idle window.
        This is what keeps long-running ACP commands alive (issue
        agent-canvas#1245).

        ``asyncio.wait`` (not ``wait_for``) drives the polling so an idle-check
        slice elapsing never cancels the underlying prompt — only a true idle
        period raises ``TimeoutError``. ``cancel_on_exit`` controls cleanup:
        the sync path passes ``True`` to cancel the prompt coroutine it owns;
        the async path passes ``False`` because the portal task must survive
        for ``astep``'s ``session/cancel`` + drain handler.
        """
        idle_limit = self.acp_prompt_timeout
        fut = asyncio.ensure_future(awaitable)
        try:
            while True:
                remaining = idle_limit - self._client.seconds_since_last_activity()
                if remaining <= 0:
                    raise TimeoutError(self._idle_timeout_message())
                # wait() returns when the prompt finishes or the slice elapses,
                # leaving fut untouched either way.
                await asyncio.wait({fut}, timeout=remaining)
                if fut.done():
                    return fut.result()
                # Slice elapsed: only give up if the server produced nothing in
                # the meantime, otherwise the loop re-arms with a fresh window.
                if self._client.seconds_since_last_activity() >= idle_limit:
                    raise TimeoutError(self._idle_timeout_message())
        finally:
            if cancel_on_exit and not fut.done():
                fut.cancel()

    async def _await_prompt_response_with_timeout(
        self,
        prompt_future: Future[PromptResponse | None],
    ) -> PromptResponse | None:
        """Await an ACP prompt with an idle (inactivity) turn deadline.

        Wraps the portal-side prompt future in :meth:`_await_with_idle_deadline`
        so the prompt is only abandoned after ``acp_prompt_timeout`` seconds
        with no ACP activity. The timeout handler in ``astep`` sends
        ``session/cancel`` and closes any in-flight tool cards.
        """
        # cancel_on_exit=False: the portal task behind ``prompt_future`` must
        # outlive an idle timeout / cancellation so astep's drain can observe it.
        return await self._await_with_idle_deadline(
            asyncio.wrap_future(prompt_future), cancel_on_exit=False
        )

    @staticmethod
    def _prompt_response_was_cancelled(response: PromptResponse | None) -> bool:
        return response is not None and response.stop_reason == "cancelled"

    def _finalize_successful_turn(
        self,
        response: PromptResponse | None,
        elapsed: float,
        state: ConversationState,
        on_event: ConversationCallbackType,
    ) -> None:
        """Post-prompt bookkeeping + FinishAction/Observation emission."""
        # ACP server has acknowledged the prompt; commit any pending
        # first-turn suffix install so a subsequent turn doesn't try to
        # re-send it (and so a future cancellation can't unmark it).
        self._commit_suffix_installation(state)

        session_id = self._session_id or ""
        usage_update = self._client.pop_turn_usage_update(session_id)
        self._record_usage(
            response,
            session_id,
            elapsed=elapsed,
            usage_update=usage_update,
        )

        # Tool cards were already streamed live from
        # _OpenHandsACPBridge.session_update: one early ``started`` event per
        # ToolCallStart and one terminal event per call. Close out any card the
        # server opened but never terminated so every ``started`` has its
        # matching terminal observation before the turn's FinishAction lands.
        self._flush_inflight_tool_calls_as_completed()

        # Re-mask the joined text at this persistence boundary: the chunks were
        # already masked individually as they streamed, but a secret split
        # across two chunks only reassembles in the join, so this is where it
        # gets caught before landing in the persisted event stream.
        mask = state.secret_registry.mask_secrets_in_output
        response_text = mask("".join(self._client.accumulated_text))
        thought_text = mask("".join(self._client.accumulated_thoughts))
        if not response_text:
            response_text = "(No response from ACP server)"

        # ACP step() boundaries are full remote assistant turns, not
        # partial planning steps. Emit FinishAction to delimit that
        # completed turn for eval/remote consumers, matching #2190.
        finish_action = FinishAction(message=response_text)
        tc_id = str(uuid.uuid4())
        action_event = ActionEvent(
            source="agent",
            thought=[],
            reasoning_content=thought_text or None,
            action=finish_action,
            tool_name="finish",
            tool_call_id=tc_id,
            tool_call=MessageToolCall(
                id=tc_id,
                name="finish",
                arguments=json.dumps({"message": response_text}),
                origin="completion",
            ),
            llm_response_id=str(uuid.uuid4()),
        )
        on_event(action_event)
        on_event(
            ObservationEvent(
                observation=FinishObservation.from_text(text=response_text),
                action_id=action_event.id,
                tool_name="finish",
                tool_call_id=tc_id,
            )
        )
        state.execution_status = ConversationExecutionStatus.FINISHED

    def _emit_turn_timeout(
        self,
        elapsed: float,
        state: ConversationState,
        on_event: ConversationCallbackType,
    ) -> None:
        """Error path when ``conn.prompt`` went idle past ``acp_prompt_timeout``."""
        logger.error(
            "ACP prompt timed out after %.1fs with no activity for the last "
            "%.0fs. The ACP server may have stalled or failed to send the "
            "JSON-RPC response. Accumulated %d text chunks, %d tool calls.",
            elapsed,
            self.acp_prompt_timeout,
            len(self._client.accumulated_text),
            len(self._client.accumulated_tool_calls),
        )
        error_message = Message(
            role="assistant",
            content=[
                TextContent(
                    text=(
                        "ACP prompt timed out after "
                        f"{self.acp_prompt_timeout:.0f}s with no activity from "
                        "the agent. The agent may have stalled, or it may have "
                        "completed its work but the response was not received."
                    )
                )
            ],
        )
        # Close any tool cards left in flight from the timed-out attempt.
        self._cancel_inflight_tool_calls()
        on_event(MessageEvent(source="agent", llm_message=error_message))
        state.execution_status = ConversationExecutionStatus.ERROR

    def _emit_turn_error(
        self,
        exc: BaseException,
        state: ConversationState,
        on_event: ConversationCallbackType,
    ) -> None:
        """Error path for non-timeout exceptions raised out of the prompt."""
        logger.error("ACP prompt failed: %s", exc, exc_info=True)
        error_str = str(exc)
        # Close any tool cards left in flight before surfacing the error.
        self._cancel_inflight_tool_calls()
        # Emit error as an agent message (preserved for consumers that
        # inspect MessageEvents).
        on_event(
            MessageEvent(
                source="agent",
                llm_message=Message(
                    role="assistant",
                    content=[TextContent(text=f"ACP error: {exc}")],
                ),
            )
        )
        # Emit typed ConversationErrorEvent so RemoteConversation surfaces
        # the actual detail instead of falling back to
        # "Remote conversation ended with error".
        is_aup = (
            "usage policy" in error_str.lower() or "content policy" in error_str.lower()
        )
        on_event(
            ConversationErrorEvent(
                source="agent",
                code="UsagePolicyRefusal" if is_aup else "ACPPromptError",
                detail=error_str[:500],
            )
        )
        state.execution_status = ConversationExecutionStatus.ERROR

    def _handle_cancelled_cleanup_interruption(
        self,
        prompt_future: Future[PromptResponse | None] | None,
        elapsed: float,
        state: ConversationState,
        on_event: ConversationCallbackType,
    ) -> None:
        """Repair state when cancellation interrupts cancel/drain cleanup."""
        if prompt_future is not None and prompt_future.done():
            try:
                response = prompt_future.result()
            except BaseException:
                self._cancel_inflight_tool_calls()
                self._restart_session_on_next_turn = True
            else:
                if self._prompt_response_was_cancelled(response):
                    self._cancel_inflight_tool_calls()
                    self._restart_session_on_next_turn = True
                else:
                    self._finalize_successful_turn(response, elapsed, state, on_event)
            return

        self._cancel_inflight_tool_calls()
        if prompt_future is not None:
            self._restart_session_on_next_turn = True

    def _clear_turn_callbacks(self) -> None:
        """Unwire per-turn bridge callbacks so trailing ``session_update``
        between turns is a no-op (fires on the portal thread with no
        FIFOLock held by anyone — without unwiring, a stale ``on_event``
        there would race with other threads mutating ``state.events``).
        """
        if self._client is None:
            return
        self._client.on_event = None
        self._client.on_token = None
        self._client.on_activity = None

    @observe(name="acp_agent.step", ignore_inputs=["conversation", "on_event"])
    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        """Send the latest user message to the ACP server and emit the response.

        Sync entry point — used by ``LocalConversation.run`` (sync path),
        the CLI, and the eval harness.  The async path
        (``LocalConversation.arun``) goes through :meth:`astep`, which
        avoids the cross-thread state-lock deadlock described in #3348.
        """
        state = conversation.state

        if self._restart_session_on_next_turn:
            # If restart initialization fails, let the conversation transition
            # to ERROR rather than reusing an ambiguous ACP session.
            self._restart_session_after_drain_timeout(state, on_event)

        # Conversation implementations already attach per-turn AgentContext
        # extensions to MessageEvent.extended_content; MessageEvent.to_llm_message()
        # merges those extensions with the user text.
        prompt_blocks: list[Any] | None = None
        for event in reversed(list(state.events)):
            if isinstance(event, MessageEvent) and event.source == "user":
                prompt_blocks = self._build_acp_prompt(event)
                if prompt_blocks:
                    break
        if prompt_blocks is None:
            logger.warning("No user message found; finishing conversation")
            state.execution_status = ConversationExecutionStatus.FINISHED
            return

        self._reset_client_for_turn(on_token, on_event)

        t0 = time.monotonic()
        try:
            logger.info(
                "Sending ACP prompt (idle_timeout=%.0fs, blocks=%d)",
                self.acp_prompt_timeout,
                len(prompt_blocks),
            )
            response: PromptResponse | None = None
            max_retries = _ACP_PROMPT_MAX_RETRIES

            async def _prompt() -> PromptResponse | None:
                # Thin closure so existing mocks of ``_executor.run_async``
                # that take a single positional callable keep working. The idle
                # deadline is enforced inside (cancel_on_exit=True: this path
                # owns the coroutine) rather than as a hard run_async timeout.
                return await self._await_with_idle_deadline(
                    self._do_acp_prompt(prompt_blocks), cancel_on_exit=True
                )

            for attempt in range(max_retries + 1):
                try:
                    response = self._executor.run_async(_prompt)
                    break
                except TimeoutError:
                    raise
                except _RETRIABLE_CONNECTION_ERRORS as e:
                    if attempt < max_retries:
                        delay = _ACP_PROMPT_RETRY_DELAYS[
                            min(attempt, len(_ACP_PROMPT_RETRY_DELAYS) - 1)
                        ]
                        logger.warning(
                            "ACP prompt failed with retriable error "
                            "(attempt %d/%d), retrying in %.0fs: %s",
                            attempt + 1,
                            max_retries + 1,
                            delay,
                            e,
                        )
                        time.sleep(delay)
                        self._cancel_inflight_tool_calls()
                        self._reset_client_for_turn(on_token, on_event)
                    else:
                        raise
                except ACPRequestError as e:
                    # Retry transient server errors (e.g. "Internal Server
                    # Error" from Gemini).  JSON-RPC -32603 = server-side
                    # failure, not a client bug.
                    if (
                        e.code in _RETRIABLE_SERVER_ERROR_CODES
                        and attempt < max_retries
                    ):
                        delay = _ACP_PROMPT_RETRY_DELAYS[
                            min(attempt, len(_ACP_PROMPT_RETRY_DELAYS) - 1)
                        ]
                        logger.warning(
                            "ACP prompt failed with server error "
                            "(attempt %d/%d), retrying in %.0fs: [%d] %s",
                            attempt + 1,
                            max_retries + 1,
                            delay,
                            e.code,
                            e,
                        )
                        time.sleep(delay)
                        self._cancel_inflight_tool_calls()
                        self._reset_client_for_turn(on_token, on_event)
                    else:
                        raise

            elapsed = time.monotonic() - t0
            logger.info("ACP prompt returned in %.1fs", elapsed)
            self._finalize_successful_turn(response, elapsed, state, on_event)
        except TimeoutError:
            self._request_session_cancel()
            self._emit_turn_timeout(time.monotonic() - t0, state, on_event)
        except Exception as e:
            self._emit_turn_error(e, state, on_event)
            # Re-raise so LocalConversation.run()'s outer except handler
            # breaks the loop, emits ConversationErrorEvent, and raises
            # ConversationRunError — matching how the regular Agent works.
            raise
        finally:
            self._clear_turn_callbacks()

    @observe(name="acp_agent.astep", ignore_inputs=["conversation", "on_event"])
    async def astep(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
        prompt_message: MessageEvent | None = None,
    ) -> None:
        """Native-async variant of :meth:`step`.

        Schedules the ACP ``conn.prompt`` round-trip on the portal loop
        (where ``self._conn`` lives) via ``BlockingPortal.start_task_soon``
        and awaits the result back on the caller's loop via
        ``asyncio.wrap_future``.  Post-prompt work — ``_record_usage``
        (and the ``stats_callback`` it triggers), ``on_event(action)``,
        ``on_event(observation)``, ``state.execution_status`` — runs
        entirely on the caller's thread.

        Why this matters: ``LocalConversation.arun`` deliberately does
        not hold the conversation state's reentrant ``FIFOLock`` across
        long ACP prompt awaits, so remote user messages can be persisted
        while the subprocess is still working. The default
        ``AgentBase.astep`` would wrap sync ``step`` in
        ``loop.run_in_executor(None, self.step, ...)``, moving post-prompt
        callbacks and state updates to a worker thread. Keeping this path
        native-async leaves finalization on the caller's loop task, where
        ``LocalConversation`` can serialize each emitted event with a
        short state-lock acquire and avoid the cross-thread deadlocks
        diagnosed in #3348 / #3350.

        Bridge ``session_update`` notifications continue to fire on the
        portal thread (no marshalling here). The ``on_event`` callback
        supplied by ``LocalConversation.arun`` is responsible for taking
        the state lock around each individual event.
        """
        state = conversation.state

        if self._restart_session_on_next_turn:
            # If restart initialization fails, let the conversation transition
            # to ERROR rather than reusing an ambiguous ACP session.
            self._restart_session_after_drain_timeout(state, on_event)

        prompt_blocks: list[Any] | None = None
        if prompt_message is not None:
            prompt_blocks = self._build_acp_prompt(prompt_message)
        else:
            for event in reversed(list(state.events)):
                if isinstance(event, MessageEvent) and event.source == "user":
                    prompt_blocks = self._build_acp_prompt(event)
                    if prompt_blocks:
                        break
        if prompt_blocks is None:
            logger.warning("No user message found; finishing conversation")
            state.execution_status = ConversationExecutionStatus.FINISHED
            return

        self._reset_client_for_turn(on_token, on_event)

        t0 = time.monotonic()
        prompt_future: Future[PromptResponse | None] | None = None
        try:
            logger.info(
                "Sending ACP prompt (idle_timeout=%.0fs, blocks=%d, async)",
                self.acp_prompt_timeout,
                len(prompt_blocks),
            )
            portal = self._executor.portal

            response: PromptResponse | None = None
            max_retries = _ACP_PROMPT_MAX_RETRIES
            for attempt in range(max_retries + 1):
                try:
                    # Schedule the ACP prompt on the portal loop (where the
                    # connection lives); await the future back on the caller
                    # loop.  Shield the portal task from wait_for timeout so
                    # the timeout/cancellation handlers can send session/cancel
                    # and briefly drain the task before the next turn rewires
                    # callbacks.
                    current_prompt_future: Future[PromptResponse | None] = (
                        portal.start_task_soon(
                            self._do_acp_prompt,
                            prompt_blocks,
                        )
                    )
                    prompt_future = current_prompt_future
                    response = await self._await_prompt_response_with_timeout(
                        current_prompt_future
                    )
                    break
                except TimeoutError:
                    raise
                except _RETRIABLE_CONNECTION_ERRORS as e:
                    if attempt < max_retries:
                        delay = _ACP_PROMPT_RETRY_DELAYS[
                            min(attempt, len(_ACP_PROMPT_RETRY_DELAYS) - 1)
                        ]
                        logger.warning(
                            "ACP prompt failed with retriable error "
                            "(attempt %d/%d), retrying in %.0fs: %s",
                            attempt + 1,
                            max_retries + 1,
                            delay,
                            e,
                        )
                        await asyncio.sleep(delay)
                        self._cancel_inflight_tool_calls()
                        self._reset_client_for_turn(on_token, on_event)
                    else:
                        raise
                except ACPRequestError as e:
                    if (
                        e.code in _RETRIABLE_SERVER_ERROR_CODES
                        and attempt < max_retries
                    ):
                        delay = _ACP_PROMPT_RETRY_DELAYS[
                            min(attempt, len(_ACP_PROMPT_RETRY_DELAYS) - 1)
                        ]
                        logger.warning(
                            "ACP prompt failed with server error "
                            "(attempt %d/%d), retrying in %.0fs: [%d] %s",
                            attempt + 1,
                            max_retries + 1,
                            delay,
                            e.code,
                            e,
                        )
                        await asyncio.sleep(delay)
                        self._cancel_inflight_tool_calls()
                        self._reset_client_for_turn(on_token, on_event)
                    else:
                        raise

            elapsed = time.monotonic() - t0
            logger.info("ACP prompt returned in %.1fs (async)", elapsed)
            # ``on_event`` may be LocalConversation._on_event_with_state_lock,
            # which re-acquires this same FIFOLock. This is safe because astep()
            # finalization runs on the event-loop thread and FIFOLock is
            # reentrant for the owning thread.
            with state:
                self._finalize_successful_turn(response, elapsed, state, on_event)
        except asyncio.CancelledError:
            # ``asyncio.CancelledError`` inherits from ``BaseException``, not
            # ``Exception`` — so it would otherwise bypass the generic handler
            # and only run ``finally``, where ``_clear_turn_callbacks`` unwires
            # the bridge.  Without closing in-flight tool cards here, any
            # ``pending`` / ``in_progress`` ``ACPToolCallEvent`` streamed
            # before cancellation stays live in the event log forever
            # (``LocalConversation._emit_orphaned_action_errors`` only patches
            # ``ActionEvent``s, not ``ACPToolCallEvent``s).  Cancel-emit on
            # the caller thread after the portal prompt has observed
            # session/cancel, so late cancelled-turn updates cannot overwrite
            # the terminal synthetic failures.
            try:
                await self._arequest_session_cancel()
                drain_result = await self._drain_cancelled_prompt(prompt_future)
            except asyncio.CancelledError:
                with state:
                    elapsed = time.monotonic() - t0
                    self._handle_cancelled_cleanup_interruption(
                        prompt_future, elapsed, state, on_event
                    )
                raise
            with state:
                elapsed = time.monotonic() - t0
                if drain_result.completed and drain_result.error is None:
                    if self._prompt_response_was_cancelled(drain_result.response):
                        self._cancel_inflight_tool_calls()
                        self._restart_session_on_next_turn = True
                    else:
                        self._finalize_successful_turn(
                            drain_result.response, elapsed, state, on_event
                        )
                    raise
                if drain_result.completed and drain_result.error is not None:
                    self._cancel_inflight_tool_calls()
                    self._restart_session_on_next_turn = True
                    raise
                self._cancel_inflight_tool_calls()
            if not drain_result.drained:
                self._restart_session_on_next_turn = True
            raise
        except TimeoutError:
            try:
                await self._arequest_session_cancel()
                drain_result = await self._drain_cancelled_prompt(prompt_future)
            except asyncio.CancelledError:
                with state:
                    elapsed = time.monotonic() - t0
                    self._handle_cancelled_cleanup_interruption(
                        prompt_future, elapsed, state, on_event
                    )
                raise
            with state:
                elapsed = time.monotonic() - t0
                if drain_result.completed and drain_result.error is None:
                    if self._prompt_response_was_cancelled(drain_result.response):
                        self._emit_turn_timeout(elapsed, state, on_event)
                        self._restart_session_on_next_turn = True
                    else:
                        self._finalize_successful_turn(
                            drain_result.response, elapsed, state, on_event
                        )
                elif drain_result.completed and drain_result.error is not None:
                    self._emit_turn_error(drain_result.error, state, on_event)
                    self._restart_session_on_next_turn = True
                else:
                    self._emit_turn_timeout(elapsed, state, on_event)
                    self._restart_session_on_next_turn = True
        except Exception as e:
            with state:
                self._emit_turn_error(e, state, on_event)
            raise
        finally:
            self._clear_turn_callbacks()

    def ask_agent(self, question: str) -> str | None:
        """Fork the ACP session, prompt the fork, and return the response."""
        if self._conn is None:
            msg = "ACPAgent has no ACP connection; call init_state() first"
            raise RuntimeError(msg)
        if self._session_id is None:
            msg = "ACPAgent has no session ID; call init_state() first"
            raise RuntimeError(msg)

        client = self._client

        async def _fork_and_prompt() -> str:
            fork_response = await self._conn.fork_session(
                cwd=self._working_dir,
                session_id=self._session_id,
            )
            fork_session_id = fork_response.session_id

            client._fork_session_id = fork_session_id
            client._fork_accumulated_text.clear()
            try:
                fork_t0 = time.monotonic()
                usage_sync = client.prepare_usage_sync(fork_session_id)
                response = await self._conn.prompt(
                    [text_block(question)],
                    fork_session_id,
                )
                if client.get_turn_usage_update(fork_session_id) is None:
                    try:
                        await asyncio.wait_for(
                            usage_sync.wait(), timeout=_USAGE_UPDATE_TIMEOUT
                        )
                    except TimeoutError:
                        logger.warning(
                            "UsageUpdate not received within %.1fs for fork session %s",
                            _USAGE_UPDATE_TIMEOUT,
                            _fingerprint_session_id(fork_session_id),
                        )
                fork_elapsed = time.monotonic() - fork_t0

                # Re-mask the joined fork text at this return boundary — mirrors
                # _finalize_successful_turn, catching a secret split across fork
                # chunks that per-chunk masking can't match.
                result = client._mask_value("".join(client._fork_accumulated_text))
                usage_update = client.pop_turn_usage_update(fork_session_id)
                self._record_usage(
                    response,
                    fork_session_id,
                    elapsed=fork_elapsed,
                    usage_update=usage_update,
                )
                return result
            finally:
                client._fork_session_id = None
                client._fork_accumulated_text.clear()

        with client._fork_lock:
            return self._executor.run_async(_fork_and_prompt)

    def set_acp_model(self, model: str) -> None:
        """Switch the model on the running ACP session (mid-conversation).

        Issues a protocol-level ``session/set_model`` call on the live
        connection so the new model takes effect for subsequent turns in the
        *same* session — no subprocess restart, no loss of conversation
        context. Verified against claude-agent-acp and codex-acp.

        This is the low-level agent primitive; prefer
        :meth:`LocalConversation.switch_acp_model` as the entry point. That
        wrapper (a) holds the state lock so the switch cannot race a running
        ``step()``, and (b) persists the new value by swapping in an agent
        ``model_copy`` — ``acp_model`` is frozen, so this method updates only
        the live session and the sentinel ``llm.model``/metrics, **not**
        ``self.acp_model``. A direct caller therefore leaves ``acp_model``
        (which ``_record_usage`` reads for cost attribution) stale and the
        switch unpersisted; go through ``switch_acp_model`` instead.

        Args:
            model: Provider-specific model id to switch to (e.g.
                ``"claude-haiku-4-5-20251001"`` or ``"gpt-5.4/low"``).

        Raises:
            ValueError: If ``model`` is empty or whitespace-only, if the
                detected provider does not support runtime model switching, or
                if the ACP server rejects the ``session/set_model`` call (e.g.
                method-not-found on a custom server, or an invalid model id).
            RuntimeError: If the ACP session has not been initialized yet
                (i.e. before the first ``run()``).
            TimeoutError: If the server does not answer within
                ``acp_prompt_timeout`` seconds.

        Note:
            A timeout means the client stopped waiting, not that the switch was
            rejected: the ``session/set_model`` request may already have been
            written and could still be applied server-side. The connection and
            session stay alive and the local sentinel model is intentionally
            left unchanged, so a timed-out switch leaves the server-side model
            indeterminate. The conservative choice (treat it as failed locally)
            keeps cost/token accounting on the previously-known model and
            self-heals on the next successful switch; the agent itself always
            runs whatever model the live ACP session holds.
        """
        if not model or not model.strip():
            raise ValueError("model must be a non-empty string")
        if self._conn is None or self._session_id is None or self._executor is None:
            raise RuntimeError(
                "ACP session is not initialized; the model can only be switched "
                "after the conversation has started (first run())."
            )
        provider = detect_acp_provider_by_agent_name(self._agent_name)
        if provider is not None and not provider.supports_runtime_model_switch:
            raise ValueError(
                f"ACP provider '{provider.key}' does not support runtime model "
                "switching via set_session_model."
            )
        # Bounded round-trip: this runs while LocalConversation.switch_acp_model
        # holds the state lock, so a server that accepts the call but never
        # answers must not wedge the lock indefinitely. On timeout / protocol
        # error we propagate *before* mutating any local state, so the sentinel
        # LLM is only updated once the live session has actually switched.
        try:
            self._executor.run_async(
                self._conn.set_session_model(
                    model_id=model, session_id=self._session_id
                ),
                timeout=self.acp_prompt_timeout,
            )
        except ACPRequestError as e:
            # Server-internal failures (JSON-RPC -32603) are not the caller's
            # fault, and the prompt path already treats them as retriable. Let
            # them propagate (-> 5xx) instead of mislabeling them as a 400
            # client error.
            if e.code in _RETRIABLE_SERVER_ERROR_CODES:
                raise
            # acp.exceptions.RequestError derives from Exception (not
            # RuntimeError); surface a true client/protocol rejection (e.g.
            # method-not-found, invalid model id) as a ValueError so callers —
            # and the agent-server route — treat it as a 400-class client error
            # rather than an opaque 500.
            raise ValueError(
                f"ACP server rejected set_session_model(model={model!r}): {e}"
            ) from e
        # Reflect the live model on the sentinel LLM + metrics so cost/token
        # accounting and serialized state show the model actually in use
        # (mirrors model_post_init). The ``acp_model`` field is frozen, so the
        # authoritative current model is persisted by
        # :meth:`LocalConversation.switch_acp_model` via an agent ``model_copy``.
        self.llm.model = model
        self.llm.metrics.model_name = model
        if self.llm.metrics.accumulated_token_usage is not None:
            self.llm.metrics.accumulated_token_usage.model = model
        # Refresh the surfaced model state so the chip/picker
        # (``ConversationInfo.current_model_id``) reflects the switch instead
        # of the stale session-start value. ``_current_model_id`` is a
        # PrivateAttr, so ``switch_acp_model``'s shallow ``model_copy`` carries
        # this updated value onto the persisted agent. ``available_models`` is
        # unchanged by a model switch, so it is intentionally left alone.
        self._current_model_id = model
        logger.info(
            "Switched ACP session model to %s (provider=%s, session=%s)",
            model,
            provider.key if provider else "unknown",
            _fingerprint_session_id(self._session_id),
        )

    def close(self) -> None:
        """Terminate the ACP subprocess and clean up resources."""
        if self._closed:
            return
        self._closed = True
        self._cleanup()

    def _cleanup(self) -> None:
        """Internal cleanup of ACP resources."""
        # Close the connection first
        if self._conn is not None and self._executor is not None:
            try:
                self._executor.run_async(self._conn.close())
            except Exception as e:
                logger.debug("Error closing ACP connection: %s", e)
            self._conn = None

        # Terminate the subprocess
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception as e:
                logger.debug("Error terminating ACP process: %s", e)
            try:
                self._process.kill()
            except Exception as e:
                logger.debug("Error killing ACP process: %s", e)
            self._process = None

        if self._executor is not None:
            try:
                self._executor.close()
            except Exception as e:
                logger.debug("Error closing executor: %s", e)
            self._executor = None

    def release_runtime(self) -> None:
        """Disarm this agent's finalizer after handing its live ACP runtime to a
        shallow :meth:`~pydantic.BaseModel.model_copy`.

        The copy shares this agent's ``_conn`` / ``_executor`` / ``_process``
        references (``model_copy`` is shallow). Marking this now-stale instance
        closed makes its ``__del__`` -> :meth:`close` a no-op, so dropping it
        cannot tear down the runtime the copy now owns.

        The runtime references are intentionally left intact: an in-flight
        :meth:`ask_agent` fork — which is thread-safe and may still hold this
        pre-switch agent — keeps a valid connection until it finishes. Sole
        ownership for teardown passes to the copy (the live ``self.agent``
        going forward), which is closed on conversation shutdown.

        See :meth:`LocalConversation.switch_acp_model`.
        """
        self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
