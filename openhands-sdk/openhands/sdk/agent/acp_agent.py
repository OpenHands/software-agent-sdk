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
from collections.abc import Generator
from concurrent.futures import Future
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from acp.client.connection import ClientSideConnection
from acp.exceptions import RequestError as ACPRequestError
from acp.helpers import image_block, text_block
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    AllowedOutcome,
    ImageContentBlock,
    PromptResponse,
    RequestPermissionResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
)
from acp.transports import default_environment
from pydantic import (
    BaseModel,
    ConfigDict,
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
from openhands.sdk.secret import SecretSource
from openhands.sdk.settings.acp_providers import (
    build_session_model_meta,
    detect_acp_provider_by_agent_name,
)
from openhands.sdk.tool import Tool  # noqa: TC002
from openhands.sdk.tool.builtins.finish import FinishAction, FinishObservation
from openhands.sdk.utils import maybe_truncate
from openhands.sdk.utils.pydantic_secrets import (
    serialize_secret,
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

# JSON-RPC error codes from the ACP server that are transient and worth
# retrying.  These map to server-side failures (HTTP 500 equivalents) where
# the session state is still valid but the request failed.
# -32603 = "Internal error" (JSON-RPC spec) — covers ACP server crashes,
#          upstream model 500s, and transient infrastructure errors.
_RETRIABLE_SERVER_ERROR_CODES: frozenset[int] = frozenset({-32603})

# JSON-RPC error code an ACP server returns from ``session/new`` when the
# underlying CLI has no working credentials — ``RequestError.auth_required``
# (acp.exceptions, code -32000, "Authentication required").  This is the one
# error ``ACPAgent.probe_auth`` classifies as "not logged in" rather than a
# failure: any other error means the probe could not determine login state.
_ACP_AUTH_REQUIRED_CODE: int = -32000

# Default hard cap (seconds) for ``ACPAgent.probe_auth``.  Generous because a
# cold ``npx -y`` first run can take 10-30s to download the ACP CLI; deployed
# images that pre-bake the CLI complete in a few seconds.
_ACP_AUTH_PROBE_TIMEOUT: float = float(os.environ.get("ACP_AUTH_PROBE_TIMEOUT", "60.0"))

# Cap (seconds) on the graceful connection-close during a probe teardown.
# Bounded so a wedged ACP subprocess can't hang teardown — after this we fall
# through to terminate()/kill() regardless.
_ACP_TEARDOWN_TIMEOUT: float = 5.0


# Maximum characters for ACP tool call content — matches MAX_CMD_OUTPUT_SIZE
# used by the terminal tool and the default max_message_chars in LLM config.
MAX_ACP_CONTENT_CHARS: int = 30_000

# Env vars that must be removed from the subprocess environment when a
# particular "dominant" env var is present.
#
# Rationale: some auth mechanisms are mutually exclusive and their env vars
# conflict.  For example, CLAUDE_CONFIG_DIR activates Claude Code's OAuth
# credential-file flow.  If ANTHROPIC_API_KEY or ANTHROPIC_BASE_URL are
# also present they redirect requests to a different endpoint (e.g. a proxy)
# that doesn't support OAuth bearer tokens, breaking authentication silently.
# When CLAUDE_CONFIG_DIR is detected we strip the conflicting vars so the
# subprocess can reach api.anthropic.com with its own OAuth token.
_ENV_CONFLICT_MAP: dict[str, frozenset[str]] = {
    "CLAUDE_CONFIG_DIR": frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"}),
}


def _apply_env_conflict_rules(env: dict[str, str]) -> None:
    """Strip env vars that would break the active auth mechanism, in place.

    Drops ``CLAUDECODE`` so a nested Claude Code instance does not refuse to
    start, then applies :data:`_ENV_CONFLICT_MAP` (e.g. removing
    ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_BASE_URL`` when ``CLAUDE_CONFIG_DIR``
    selects Claude Code's OAuth credential-file flow).  Shared by
    :meth:`ACPAgent._start_acp_server` and :meth:`ACPAgent.probe_auth` so both
    paths build the subprocess environment with identical rules.
    """
    env.pop("CLAUDECODE", None)
    for dominant, conflicts in _ENV_CONFLICT_MAP.items():
        if dominant in env:
            for conflict in conflicts:
                env.pop(conflict, None)


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


def _select_auth_method(
    auth_methods: list[Any],
    env: dict[str, str],
) -> str | None:
    """Pick an auth method whose required credentials are present.

    Returns the ``id`` of the first matching method, or ``None`` if no
    supported credential source is available (the server may not require auth).

    Subscription / OAuth logins (whose cached credential file is present) are
    checked first so they take precedence over explicit API keys, which serve
    as the fallback:

    - ``chatgpt`` (codex-acp) — ``~/.codex/auth.json``
    - ``oauth-personal`` (gemini-cli) — ``~/.gemini/oauth_creds.json``

    In a server image these files are absent (no interactive login), so the
    API-key fallback (e.g. ``GEMINI_API_KEY``) is used instead.
    """
    method_ids = {m.id for m in auth_methods}
    # Prefer subscription / OAuth logins when their cached credential file is
    # present.
    if "chatgpt" in method_ids and (Path.home() / _CHATGPT_AUTH_PATH).is_file():
        return "chatgpt"
    if "oauth-personal" in method_ids and (Path.home() / _GEMINI_OAUTH_PATH).is_file():
        return "oauth-personal"
    # Fall back to explicit API key env vars.
    for method_id, env_var in _AUTH_METHOD_ENV_MAP.items():
        if method_id in method_ids and env_var in env:
            return method_id
    return None


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


async def _maybe_set_session_model(
    conn: ClientSideConnection,
    agent_name: str,
    session_id: str,
    acp_model: str | None,
) -> bool:
    """Apply the *initial* session model right after session creation.

    This is the session-creation path only, gated on
    :attr:`~openhands.sdk.settings.acp_providers.ACPProviderInfo.supports_set_session_model`.
    Providers that select their initial model via session ``_meta``
    (claude-agent-acp, ``supports_set_session_model=False``) already received
    the model in ``new_session()``, so this is a no-op for them. Providers that
    use the protocol call for initial selection (codex-acp, gemini-cli) get a
    one-shot ``set_session_model`` call here.

    Runtime, mid-conversation switches go through
    :meth:`ACPAgent.set_acp_model` instead, which always uses
    ``set_session_model`` and is gated on the separate
    ``supports_runtime_model_switch`` capability flag.

    Returns ``True`` only when this issued a ``set_session_model`` call — i.e.
    the override was actually pushed to the server via *this* path. ``False``
    when there is nothing to apply (no ``acp_model``) or the provider selects
    its model another way (``_meta``) or not at all (unknown/custom server), so
    the caller can tell whether the live session is really running ``acp_model``.
    """
    if not acp_model:
        return False
    provider = detect_acp_provider_by_agent_name(agent_name)
    if provider is not None and provider.supports_set_session_model:
        await conn.set_session_model(model_id=acp_model, session_id=session_id)
        return True
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

    The gating mirrors :meth:`ACPAgent.set_acp_model` (attempt for custom/unknown
    servers and known providers that support runtime switching; skip only known
    providers that don't), deliberately differing from the initial-selection
    gate: claude-agent-acp selects its initial model via ``_meta`` yet supports
    ``set_session_model`` for later switches. A server that rejects the call is
    tolerated (logged) — like the ``load_session`` fallback above — so resume
    can't break; the session keeps the server default until the next switch.

    Returns ``True`` only when ``set_session_model`` was issued and accepted, so
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
        await conn.set_session_model(model_id=acp_model, session_id=session_id)
        return True
    except ACPRequestError as e:
        logger.warning(
            "Could not reapply model %r on resumed session %s (%s); the live "
            "session may run on the server default until the next switch",
            acp_model,
            session_id,
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
        self._last_activity_signal: float = float("-inf")
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

    # -- Client protocol methods ------------------------------------------

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        logger.debug("ACP session_update: type=%s", type(update).__name__)

        # Route fork session updates to the fork accumulator
        if self._fork_session_id is not None and session_id == self._fork_session_id:
            if isinstance(update, AgentMessageChunk):
                if isinstance(update.content, TextContentBlock):
                    self._fork_accumulated_text.append(update.content.text)
            return

        if isinstance(update, AgentMessageChunk):
            if isinstance(update.content, TextContentBlock):
                text = update.content.text
                self.accumulated_text.append(text)
                if self.on_token is not None:
                    try:
                        self.on_token(text)
                    except Exception:
                        logger.debug("on_token callback failed", exc_info=True)
            self._maybe_signal_activity()
        elif isinstance(update, AgentThoughtChunk):
            if isinstance(update.content, TextContentBlock):
                self.accumulated_thoughts.append(update.content.text)
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
            self.accumulated_tool_calls.append(entry)
            logger.debug("ACP tool call start: %s", update.tool_call_id)
            self._emit_tool_call_event(entry)
            self._maybe_signal_activity()
        elif isinstance(update, ToolCallProgress):
            # Find the existing tool call entry and merge updates
            target: dict[str, Any] | None = None
            for tc in self.accumulated_tool_calls:
                if tc["tool_call_id"] == update.tool_call_id:
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
            if target is not None:
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
# Shared ACP handshake helpers (used by _start_acp_server and probe_auth)
# ---------------------------------------------------------------------------


async def _spawn_acp_connection(
    client: _OpenHandsACPBridge,
    command: str,
    args: list[str],
    env: dict[str, str],
) -> tuple[Any, ClientSideConnection, asyncio.StreamReader, asyncio.Task[None]]:
    """Spawn the ACP subprocess and wrap it in a JSON-RPC connection.

    Spawns ``command`` directly (rather than via the ACP helper) so a
    filtering reader can drop the non-JSON-RPC lines some servers (e.g.
    ``claude-code-acp`` v0.1.x) write to stdout. Returns the live
    ``(process, connection, filtered_reader, reader_task)`` tuple; the **caller
    owns teardown** (see :func:`_teardown_acp_connection`), including cancelling
    ``reader_task`` — otherwise a short-lived caller whose loop closes right
    after (e.g. :meth:`ACPAgent.probe_auth`) leaks it as a destroyed-pending-task.
    """
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

    # Wrap subprocess stdout in a filtering reader that only forwards
    # JSON-RPC lines (see _filter_jsonrpc_lines).
    filtered_reader = asyncio.StreamReader(limit=_STREAM_READER_LIMIT)
    reader_task = asyncio.get_running_loop().create_task(
        _filter_jsonrpc_lines(process.stdout, filtered_reader)
    )

    conn = ClientSideConnection(
        client,
        process.stdin,  # write to subprocess
        filtered_reader,  # read filtered output
    )
    return process, conn, filtered_reader, reader_task


async def _acp_initialize(
    conn: ClientSideConnection,
) -> tuple[str, str, list[Any]]:
    """Run the ACP ``initialize`` handshake and report what the server advertised.

    Returns ``(agent_name, agent_version, auth_methods)`` from
    ``InitializeResponse`` — the *raw* advertised auth-method objects, to be
    passed to :func:`_acp_authenticate`. The metadata is captured here, before
    any authenticate step, so a caller can preserve it even when a later
    ``authenticate`` / ``session/new`` fails (see :meth:`ACPAgent.probe_auth`,
    which still wants the agent name/version + offered methods on the
    not-logged-in path). Shared by :meth:`ACPAgent._start_acp_server` and
    :meth:`ACPAgent.probe_auth`.
    """
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
    return agent_name, agent_version, list(init_response.auth_methods or [])


async def _acp_authenticate(
    conn: ClientSideConnection,
    env: dict[str, str],
    agent_name: str,
    auth_methods: list[Any],
) -> None:
    """Authenticate against the ACP server when it advertised auth methods.

    Some servers (e.g. ``codex-acp``) require an explicit ``authenticate`` call
    before session creation; the method is auto-selected from the credentials
    present in ``env`` and the cached subscription/OAuth files (see
    :func:`_select_auth_method`). When no method matches this is a no-op — session
    creation may still succeed via the CLI's own login. Split out from
    :func:`_acp_initialize` so callers capture the initialize metadata *before* an
    auth failure here can discard it.
    """
    if not auth_methods:
        return
    method_id = _select_auth_method(auth_methods, env)
    if method_id is None:
        logger.warning(
            "ACP server offers auth methods %s but no matching "
            "env var is set — session creation may fail",
            [m.id for m in auth_methods],
        )
        return
    logger.info("Authenticating with ACP method: %s", method_id)
    auth_kwargs: dict[str, Any] = {}
    # gemini-cli: pass gateway baseUrl to route API calls through the LiteLLM
    # proxy. claude-agent-acp and codex-acp read their provider base URL from
    # env vars directly.
    if method_id == "gemini-api-key":
        provider = detect_acp_provider_by_agent_name(agent_name)
        base_url_var = provider.base_url_env_var if provider is not None else None
        if base_url_var:
            base_url = env.get(base_url_var)
            if base_url:
                auth_kwargs["gateway"] = {"baseUrl": base_url}
    await conn.authenticate(method_id=method_id, **auth_kwargs)


async def _teardown_acp_connection(
    conn: Any, process: Any, reader_task: Any = None
) -> None:
    """Best-effort teardown of a probe's ACP connection and subprocess.

    Cancels the stdout filter task, closes the JSON-RPC connection (bounded by
    :data:`_ACP_TEARDOWN_TIMEOUT` so a wedged subprocess can't hang here), then
    gracefully terminates the subprocess (SIGTERM → reap → escalate to SIGKILL
    only if it doesn't exit in time). Every step is guarded so a failure in one
    does not skip the others; used by :meth:`ACPAgent.probe_auth`, which owns a
    short-lived connection rather than the instance-level resources
    :meth:`ACPAgent._cleanup` manages.
    """
    # Cancel the stdout filter task first. It only ends naturally on stdout EOF
    # (which races with killing the subprocess); without cancelling, a caller
    # whose event loop closes right after teardown (probe_auth) leaks it as a
    # "Task was destroyed but it is pending" warning.
    if reader_task is not None:
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Probe ACP reader task error on teardown", exc_info=True)
    try:
        await asyncio.wait_for(conn.close(), timeout=_ACP_TEARDOWN_TIMEOUT)
    except Exception:
        logger.debug("Error closing probe ACP connection", exc_info=True)
    if process is not None:
        # SIGTERM, then reap (process.wait); only escalate to SIGKILL if it
        # doesn't exit within the timeout. Reaping clears asyncio's child watcher
        # so a standalone SDK/CLI caller (whose event loop closes right after
        # probe_auth returns) doesn't see a "Loop ... that handles pid ... is
        # closed" warning — the agent-server path avoids it only because its loop
        # stays alive.
        for stop in (process.terminate, process.kill):
            try:
                stop()
            except Exception:
                logger.debug("Error stopping probe ACP process", exc_info=True)
            try:
                await asyncio.wait_for(process.wait(), timeout=_ACP_TEARDOWN_TIMEOUT)
                break  # exited; no need to escalate to SIGKILL
            except Exception:
                logger.debug(
                    "Probe ACP process did not exit after signal", exc_info=True
                )


class ACPAuthProbeResult(BaseModel):
    """Result of a prompt-free ACP authentication probe.

    Produced by :meth:`ACPAgent.probe_auth`. ``authenticated`` is the headline
    signal: ``True`` when ``session/new`` succeeded (the CLI found working
    credentials — a subscription login *or* an API key), ``False`` when the
    server returned ``auth_required``. The remaining fields echo what the
    ``initialize`` handshake reported, for display/diagnostics.
    """

    model_config = ConfigDict(frozen=True)

    authenticated: bool = Field(
        description=(
            "True when session/new succeeded ⇒ the ACP CLI is logged in "
            "(by subscription or API key). False on an auth_required error."
        )
    )
    auth_methods: list[str] = Field(
        default_factory=list,
        description=(
            "Auth method ids from initialize.authMethods. Informational only: "
            "the menu of how to log in, not a reliable logged-in signal."
        ),
    )
    agent_name: str = Field(
        default="", description="ACP server name from InitializeResponse.agent_info."
    )
    agent_version: str = Field(
        default="",
        description="ACP server version from InitializeResponse.agent_info.",
    )


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
        description="Additional environment variables for the ACP server process",
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
            "Timeout in seconds for a single ACP prompt() call. "
            "Prevents indefinite hangs when the ACP server fails to respond."
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
        """``False`` — MCP configuration is owned by the ACP subprocess."""
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
        Kept in lockstep with :meth:`set_acp_model`, which refuses the switch
        only for a *known* provider that declares no support and otherwise
        attempts it optimistically — so a custom/unknown ACP server that does
        support ``session/set_model`` isn't needlessly blocked from the picker.
        ``False`` before a session exists (nothing to switch yet).

        See
        :meth:`~openhands.sdk.conversation.impl.local_conversation.LocalConversation.switch_acp_model`.
        """
        if self._session_id is None:
            return False
        provider = detect_acp_provider_by_agent_name(self._agent_name)
        return provider is None or provider.supports_runtime_model_switch

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
        # server tools, MCP configuration, and context-window management remain
        # owned by the server.
        if self.tools:
            raise NotImplementedError(
                "ACPAgent does not support custom tools; "
                "the ACP server manages its own tools"
            )
        if self.mcp_config:
            raise NotImplementedError(
                "ACPAgent does not support mcp_config; "
                "configure MCP on the ACP server instead"
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
        # A prior session id in agent_state means we may be resuming; used by
        # ``truly_resumed`` below to decide whether the model state reported
        # for this launch describes the resumed session or a fresh one.
        prior_session_id = state.agent_state.get("acp_session_id")
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
        """
        secret_infos = state.secret_registry.get_secret_infos()
        if self.agent_context is None:
            # No caller-supplied context. Only synthesize an empty one for the
            # renderer if we actually have a registry-secret advertisement to
            # emit — otherwise return None so we don't start injecting other
            # parts of the empty AgentContext's defaults (current_datetime, …)
            # that the old "agent_context is None ⇒ no suffix" rule used to
            # suppress.
            if not secret_infos:
                return None
            return AgentContext(current_datetime=None).to_acp_prompt_context(
                additional_secret_infos=secret_infos
            )
        return self.agent_context.to_acp_prompt_context(
            additional_secret_infos=secret_infos
        )

    def _start_acp_server(self, state: ConversationState) -> None:
        """Start the ACP subprocess and initialize the session."""
        client = _OpenHandsACPBridge()
        self._client = client

        # Build the subprocess environment top-down, highest precedence first:
        #   acp_env > os.environ > default_environment >
        #   state.secret_registry > agent_context.secrets
        #
        # Secret tiers fill-if-absent. The ``name in env`` guard does double
        # duty: it preserves higher-precedence values and avoids calling
        # SecretSource.get_value() for keys already satisfied — important
        # because LookupSecret can make an HTTP request.
        env = default_environment()
        env.update(os.environ)
        env.update(self.acp_env)
        for name in state.secret_registry.secret_sources:
            if name in env:
                continue
            value = state.secret_registry.get_secret_value(name)
            if value:
                env[name] = value
        if self.agent_context and self.agent_context.secrets:
            for name, secret in self.agent_context.secrets.items():
                if name in env:
                    continue
                value = (
                    secret.get_value()
                    if isinstance(secret, SecretSource)
                    else str(secret)
                )
                if value:
                    env[name] = value
        # Drop CLAUDECODE (nested Claude Code) and strip env vars that conflict
        # with an active auth mechanism (e.g. CLAUDE_CONFIG_DIR's OAuth
        # credential file vs ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL).
        _apply_env_conflict_rules(env)

        command = self.acp_command[0]
        args = list(self.acp_command[1:]) + list(self.acp_args)

        working_dir = str(state.workspace.working_dir)

        # Prior ACP session id — survives agent-server restarts via
        # ConversationState.agent_state (serialized into base_state.json).
        # Its presence is the signal to resume; its absence means fresh start.
        # ACP servers key persistence by ``cwd``; if the workspace moved we
        # drop the id so we don't accidentally resume (or silently load) a
        # session the server associates with a different directory.
        prior_session_id: str | None = state.agent_state.get("acp_session_id")
        prior_session_cwd: str | None = state.agent_state.get("acp_session_cwd")
        if prior_session_id is not None and prior_session_cwd not in (
            None,
            working_dir,
        ):
            logger.warning(
                "ACP session %s was created with cwd=%s; current cwd=%s differs, "
                "starting a fresh session instead of resuming",
                prior_session_id,
                prior_session_cwd,
                working_dir,
            )
            prior_session_id = None

        async def _init() -> tuple[
            str, str, str, str | None, list[ACPModelInfo] | None, bool
        ]:
            # Spawn the subprocess and install a JSON-RPC filtering reader
            # (some servers, e.g. claude-code-acp v0.1.x, write non-protocol
            # lines to stdout).
            # The reader task here lives on the instance's long-lived executor
            # loop and ends on stdout EOF when _cleanup() terminates the
            # subprocess, so (unlike the probe) it doesn't orphan; left untracked.
            process, conn, filtered_reader, _reader_task = await _spawn_acp_connection(
                client, command, args, env
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

            # Initialize the protocol, discover server identity, and
            # authenticate if the server requires it (auto-selected from the
            # credentials available in env / cached subscription files).
            agent_name, agent_version, _auth_methods = await _acp_initialize(conn)
            await _acp_authenticate(conn, env, agent_name, _auth_methods)

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
                        mcp_servers=[],
                    )
                    session_id = prior_session_id
                    reported_model_id, available_models = _extract_session_models(
                        load_response
                    )
                    logger.info(
                        "Resumed ACP session: %s (cwd=%s)",
                        session_id,
                        working_dir,
                    )
                except ACPRequestError as e:
                    logger.warning(
                        "ACP load_session(%s) failed (%s); starting a fresh session",
                        prior_session_id,
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
                response = await conn.new_session(cwd=working_dir, **session_meta)
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

    @staticmethod
    def probe_auth(
        command: list[str],
        *,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout: float = _ACP_AUTH_PROBE_TIMEOUT,
    ) -> ACPAuthProbeResult:
        """Probe whether an ACP server is already authenticated — prompt-free.

        Runs the same lightweight handshake as :meth:`_start_acp_server`
        (spawn → ``initialize`` → ``authenticate`` if the server asks for it →
        ``session/new``) and then tears the subprocess down. **No prompt is
        sent, so no model tokens are spent.** The signal is an ``auth_required``
        (JSON-RPC ``-32000``) error from *either* the ``authenticate`` call or
        ``session/new``:

        - neither raises it ⇒ the CLI found working credentials (a subscription
          login *or* an API key) ⇒ ``authenticated=True``;
        - either raises it ⇒ not logged in ⇒ ``authenticated=False``.

        ``initialize.authMethods`` alone is *not* a reliable logged-in signal
        (Claude omits it once authed; Gemini lists it even when authed), so it
        is returned for display only.

        Reliability caveat: this assumes every supported CLI reports missing
        credentials via the ``-32000`` ``auth_required`` code. A provider that
        instead uses a different error code, or lets ``session/new`` succeed and
        only fails at first prompt, would be misreported (``unknown`` is the safe
        outcome the router falls back on; a false ``authenticated`` is the risky
        one). Confirmed against the real CLIs for ``claude-code`` and
        ``gemini-cli`` 0.43.0 (logged-out ``session/new`` → ``-32000``, and the
        logged-in path → success); ``codex``'s explicit-``authenticate`` →
        ``-32000`` path is covered by tests against a simulated server.

        This is the agent-server-side detection behind the ACP onboarding
        "you're already logged in" banner: the browser can't read the keychain
        or credential files, but the ACP CLI — running wherever the agent-server
        runs — reports the truth through the protocol handshake, keeping the
        caller OS- and topology-agnostic.

        Args:
            command: Full launch command for the ACP server, e.g. the output of
                ``ACPAgentSettings.resolve_acp_command()``. ``command[0]`` is the
                executable; the remainder are arguments.
            args: Extra arguments appended after ``command[1:]`` (the ``acp_args``
                equivalent).
            env: Extra environment variables overlaid on the inherited process
                environment (the ``acp_env`` equivalent — typically the provider
                API key / base URL resolved from settings). The same
                CLAUDECODE / auth-conflict stripping as ``_start_acp_server`` is
                applied (see :func:`_apply_env_conflict_rules`).
            cwd: Working directory for ``session/new``. Login state does not
                depend on it, so a throwaway directory is fine; defaults to the
                current process directory.
            timeout: Hard cap in seconds for the whole probe.

        Returns:
            An :class:`ACPAuthProbeResult`.

        Raises:
            ValueError: if ``command`` is empty.
            TimeoutError: if the probe exceeds ``timeout``.
            Exception: any transport/protocol error other than ``auth_required``
                (e.g. the subprocess failing to start) propagates — callers
                should treat it as "unknown", not "not logged in".
        """
        from openhands.sdk.utils.async_executor import AsyncExecutor

        if not command:
            raise ValueError("probe_auth() requires a non-empty command")
        executable = command[0]
        full_args = list(command[1:]) + list(args or [])

        # Mirror _start_acp_server's environment construction: inherit the
        # process env, overlay the caller-supplied extras, then strip the
        # CLAUDECODE / auth-conflicting vars so the probe authenticates as a real
        # conversation would. Caveat: here every key in ``env`` overrides
        # os.environ. That matches _start_acp_server for ``acp_env``, but there
        # secret-registry / agent_context secrets are applied *fill-missing*
        # (os.environ wins). So if the caller folds stored secrets into ``env``
        # and one shadows a process-env var of the same name (e.g.
        # ANTHROPIC_API_KEY), the probe may authenticate with a different value
        # than the real run — an edge case, but a real precedence divergence.
        full_env = default_environment()
        full_env.update(os.environ)
        full_env.update(env or {})
        _apply_env_conflict_rules(full_env)

        working_dir = cwd if cwd is not None else os.getcwd()

        async def _handshake(conn: ClientSideConnection) -> ACPAuthProbeResult:
            authenticated = True
            agent_name = ""
            agent_version = ""
            auth_method_ids: list[str] = []
            # Capture the initialize metadata first and *outside* the auth_required
            # guard, so the not-logged-in result still reports the agent
            # name/version and offered methods. A single guard then wraps both
            # authenticate and session/new: some servers (e.g. codex-acp) surface
            # "not logged in" from the explicit ``authenticate`` call rather than
            # from ``session/new`` — catching it in either place classifies as
            # unauthenticated instead of letting it bubble up to "unknown".
            agent_name, agent_version, auth_methods = await _acp_initialize(conn)
            auth_method_ids = [m.id for m in auth_methods]
            try:
                await _acp_authenticate(conn, full_env, agent_name, auth_methods)
                await conn.new_session(cwd=working_dir, mcp_servers=[])
            except ACPRequestError as e:
                if e.code != _ACP_AUTH_REQUIRED_CODE:
                    raise
                authenticated = False
                logger.info(
                    "ACP auth probe: %s is not logged in (auth_required)",
                    agent_name or executable,
                )
            return ACPAuthProbeResult(
                authenticated=authenticated,
                auth_methods=auth_method_ids,
                agent_name=agent_name,
                agent_version=agent_version,
            )

        async def _probe() -> ACPAuthProbeResult:
            # Bound spawn + handshake under ``timeout`` together (a hung spawn
            # shouldn't escape the cap), while guaranteeing teardown runs on this
            # same event loop in ``finally`` — even on timeout — so the
            # subprocess and reader task are never leaked.
            client = _OpenHandsACPBridge()
            process: Any = None
            conn: Any = None
            reader_task: Any = None

            async def _spawn_and_handshake() -> ACPAuthProbeResult:
                nonlocal process, conn, reader_task
                process, conn, _reader, reader_task = await _spawn_acp_connection(
                    client, executable, full_args, full_env
                )
                return await _handshake(conn)

            try:
                return await asyncio.wait_for(_spawn_and_handshake(), timeout=timeout)
            finally:
                await _teardown_acp_connection(conn, process, reader_task)

        executor = AsyncExecutor()
        try:
            return executor.run_async(_probe)
        finally:
            executor.close()

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
        a single end-of-turn burst.
        """
        self._client.reset()
        self._client.on_token = on_token
        self._client.on_event = on_event
        self._client.on_activity = self._on_activity

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
                    self._session_id,
                )
        return response

    async def _await_prompt_response_with_timeout(
        self,
        prompt_future: Future[PromptResponse | None],
    ) -> PromptResponse | None:
        """Await an ACP prompt with a hard turn deadline.

        The terminal tool reports hard command timeouts back to the agent
        instead of waiting forever for active commands. ACP prompts follow the
        same rule: activity heartbeats keep the server alive, but they do not
        extend this prompt deadline. The timeout handler sends ``session/cancel``
        and closes any in-flight tool cards.
        """
        try:
            return await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(prompt_future)),
                timeout=self.acp_prompt_timeout,
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"ACP prompt timed out after {self.acp_prompt_timeout:.0f}s"
            ) from exc

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

        # ACPToolCallEvents were already emitted live from
        # _OpenHandsACPBridge.session_update as each ToolCallStart /
        # ToolCallProgress notification arrived — no end-of-turn fan-out
        # here. FinishAction closes out the turn below.
        response_text = "".join(self._client.accumulated_text)
        thought_text = "".join(self._client.accumulated_thoughts)
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
        """Error path when ``conn.prompt`` exceeded ``acp_prompt_timeout``."""
        logger.error(
            "ACP prompt timed out after %.1fs (limit=%.0fs). "
            "The ACP server may have completed its work but failed to "
            "send the JSON-RPC response. Accumulated %d text chunks, "
            "%d tool calls.",
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
                        f"ACP prompt timed out after {elapsed:.0f}s. "
                        "The agent may have completed its work but "
                        "the response was not received."
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
                "Sending ACP prompt (timeout=%.0fs, blocks=%d)",
                self.acp_prompt_timeout,
                len(prompt_blocks),
            )
            response: PromptResponse | None = None
            max_retries = _ACP_PROMPT_MAX_RETRIES

            async def _prompt() -> PromptResponse | None:
                # Thin closure so existing mocks of ``_executor.run_async``
                # that take a single positional callable keep working.
                return await self._do_acp_prompt(prompt_blocks)

            for attempt in range(max_retries + 1):
                try:
                    response = self._executor.run_async(
                        _prompt, timeout=self.acp_prompt_timeout
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
                "Sending ACP prompt (timeout=%.0fs, blocks=%d, async)",
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
                            fork_session_id,
                        )
                fork_elapsed = time.monotonic() - fork_t0

                result = "".join(client._fork_accumulated_text)
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
            self._session_id,
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
