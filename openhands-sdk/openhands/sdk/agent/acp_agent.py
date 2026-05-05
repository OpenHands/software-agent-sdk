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
import json
import os
import threading
import time
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
from pydantic import Field, PrivateAttr, field_serializer

from openhands.sdk.agent.base import AgentBase
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
from openhands.sdk.tool import Tool  # noqa: TC002
from openhands.sdk.tool.builtins.finish import FinishAction, FinishObservation
from openhands.sdk.utils import maybe_truncate


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
_ACP_PROMPT_RETRY_DELAYS: tuple[float, ...] = (5.0, 15.0, 30.0)  # seconds

# Exception types that indicate transient connection issues worth retrying
_RETRIABLE_CONNECTION_ERRORS = (OSError, ConnectionError, BrokenPipeError, EOFError)

# JSON-RPC error codes from the ACP server that are transient and worth
# retrying.  These map to server-side failures (HTTP 500 equivalents) where
# the session state is still valid but the request failed.
# -32603 = "Internal error" (JSON-RPC spec) — covers ACP server crashes,
#          upstream model 500s, and transient infrastructure errors.
_RETRIABLE_SERVER_ERROR_CODES: frozenset[int] = frozenset({-32603})

# Maximum characters for ACP tool call content — matches MAX_CMD_OUTPUT_SIZE
# used by the terminal tool and the default max_message_chars in LLM config.
MAX_ACP_CONTENT_CHARS: int = 30_000

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


# Known ACP server name → bypass-permissions mode ID mappings.
_BYPASS_MODE_MAP: dict[str, str] = {
    "claude-agent": "bypassPermissions",
    "codex-acp": "full-access",
    "gemini-cli": "yolo",
}
_DEFAULT_BYPASS_MODE = "full-access"

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


def _select_auth_method(
    auth_methods: list[Any],
    env: dict[str, str],
) -> str | None:
    """Pick an auth method whose required credentials are present.

    Returns the ``id`` of the first matching method, or ``None`` if no
    supported credential source is available (the server may not require auth).

    ChatGPT subscription login (device-code flow stored in
    ``~/.codex/auth.json``) is checked first so it takes precedence over
    explicit API keys, which serve as the fallback.
    """
    method_ids = {m.id for m in auth_methods}
    # Prefer ChatGPT subscription login when the auth file is present.
    if "chatgpt" in method_ids:
        if (Path.home() / _CHATGPT_AUTH_PATH).is_file():
            return "chatgpt"
    # Fall back to explicit API key env vars.
    for method_id, env_var in _AUTH_METHOD_ENV_MAP.items():
        if method_id in method_ids and env_var in env:
            return method_id
    return None


def _resolve_bypass_mode(agent_name: str) -> str:
    """Return the session mode ID that bypasses all permission prompts.

    Different ACP servers use different mode IDs for the same concept:
    - claude-agent-acp → ``bypassPermissions``
    - codex-acp        → ``full-access``
    - gemini-cli       → ``yolo``

    Falls back to ``full-access`` for unknown servers.
    """
    for key, mode in _BYPASS_MODE_MAP.items():
        if key in agent_name.lower():
            return mode
    return _DEFAULT_BYPASS_MODE


def _build_session_meta(agent_name: str, acp_model: str | None) -> dict[str, Any]:
    """Build ACP session metadata for server-specific model selection."""
    if not acp_model:
        return {}
    # claude-agent-acp: model selection via session _meta (claudeCode.options.model)
    if "claude" in agent_name.lower():
        return {"claudeCode": {"options": {"model": acp_model}}}
    # codex-acp, gemini-cli: use protocol-level set_session_model instead (see below)
    return {}


async def _maybe_set_session_model(
    conn: ClientSideConnection,
    agent_name: str,
    session_id: str,
    acp_model: str | None,
) -> None:
    """Apply a protocol-level session model override when the server supports it."""
    if not acp_model:
        return
    # codex-acp, gemini-cli: model selection via set_session_model protocol method
    # claude-agent-acp: uses session _meta instead (see _build_session_meta)
    if "codex-acp" in agent_name.lower() or "gemini-cli" in agent_name.lower():
        await conn.set_session_model(model_id=acp_model, session_id=session_id)


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

    @field_serializer("acp_env", when_used="always")
    def _serialize_acp_env(self, value: dict[str, str], info) -> dict[str, str]:
        """Mask ``acp_env`` values during serialization to prevent secret leakage.

        ``acp_env`` carries provider credentials (e.g. ``ANTHROPIC_API_KEY``,
        ``OPENAI_API_KEY``, ``GEMINI_API_KEY``) into the ACP subprocess. Without
        masking, these values land in any persisted agent state — including the
        conversation history dumps consumed by trace tooling — exposing live
        proxy keys.

        Pass ``context={"expose_secrets": True}`` to ``model_dump`` /
        ``model_dump_json`` to recover the real values (e.g. when shipping the
        agent to a remote agent-server). The runtime path
        (:meth:`_start_acp_server`) reads ``self.acp_env`` directly and is not
        affected by this serializer.
        """
        if info.context and info.context.get("expose_secrets"):
            return dict(value)
        return dict.fromkeys(value, "<redacted>")

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
    # Callback to signal that the ACP subprocess is actively working.
    # Injected by the agent-server to call update_last_execution_time().
    _on_activity: Any = PrivateAttr(default=None)  # Callable[[], None] | None

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

    # -- Override base properties to be no-ops for ACP ---------------------

    @property
    def agent_name(self) -> str:
        """Name of the ACP server (from InitializeResponse.agent_info)."""
        return self._agent_name

    @property
    def agent_version(self) -> str:
        """Version of the ACP server (from InitializeResponse.agent_info)."""
        return self._agent_version

    def get_all_llms(self) -> Generator[LLM]:
        yield self.llm

    # -- Lifecycle ---------------------------------------------------------

    def init_state(
        self,
        state: ConversationState,
        on_event: ConversationCallbackType,
    ) -> None:
        """Spawn the ACP server and initialize a session."""
        # Emit a placeholder system prompt so the visualizer shows a section
        # even though the real system prompt is managed by the ACP server.
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
                tools=[],
            )
        )

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

        try:
            self._start_acp_server(state)
        except Exception as e:
            logger.error("Failed to start ACP server: %s", e)
            self._cleanup()
            raise

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
        state.agent_state = {
            **state.agent_state,
            "acp_agent_name": self._agent_name,
            "acp_agent_version": self._agent_version,
            "acp_session_id": self._session_id,
            "acp_session_cwd": self._working_dir,
        }

    def _start_acp_server(self, state: ConversationState) -> None:
        """Start the ACP subprocess and initialize the session."""
        client = _OpenHandsACPBridge()
        self._client = client

        # Build environment: inherit current env + ACP extras
        env = default_environment()
        env.update(os.environ)
        env.update(self.acp_env)
        # Inject secrets from agent_context. acp_env entries take precedence
        # (already set above), so we only fill keys not already present.
        # SecretSource.get_value() is synchronous; calling it here is safe
        # because _start_acp_server is a regular (non-async) method.
        if self.agent_context and self.agent_context.secrets:
            for name, secret in self.agent_context.secrets.items():
                if name not in env:
                    value = (
                        secret.get_value()
                        if isinstance(secret, SecretSource)
                        else str(secret)
                    )
                    if value:
                        env[name] = value
        # Strip CLAUDECODE so nested Claude Code instances don't refuse to start
        env.pop("CLAUDECODE", None)

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

        async def _init() -> tuple[Any, Any, Any, str, str, str]:
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
                        gemini_base_url = env.get("GEMINI_BASE_URL")
                        if gemini_base_url:
                            auth_kwargs["gateway"] = {"baseUrl": gemini_base_url}
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
            if prior_session_id is not None:
                try:
                    await conn.load_session(
                        cwd=working_dir,
                        session_id=prior_session_id,
                        mcp_servers=[],
                    )
                    session_id = prior_session_id
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

            if session_id is None:
                # Build _meta content for session options (e.g. model selection).
                # Extra kwargs to new_session() become the _meta dict in the
                # JSON-RPC request — do NOT wrap in _meta= (that double-nests).
                session_meta = _build_session_meta(agent_name, self.acp_model)
                response = await conn.new_session(cwd=working_dir, **session_meta)
                session_id = response.session_id
            await _maybe_set_session_model(
                conn,
                agent_name,
                session_id,
                self.acp_model,
            )

            # Resolve the permission mode to use.  Different ACP servers
            # use different mode IDs for the same concept (no-prompts):
            #   - claude-agent-acp → "bypassPermissions"
            #   - codex-acp        → "full-access"
            mode_id = self.acp_session_mode
            if mode_id is None:
                mode_id = _resolve_bypass_mode(agent_name)
            logger.info("Setting ACP session mode: %s", mode_id)
            await conn.set_session_mode(mode_id=mode_id, session_id=session_id)

            return conn, process, filtered_reader, session_id, agent_name, agent_version

        result = self._executor.run_async(_init)
        (
            self._conn,
            self._process,
            self._filtered_reader,
            self._session_id,
            self._agent_name,
            self._agent_version,
        ) = result
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

        Uses the bridge's ``on_event`` directly (the same callback driving
        live emissions); call this *before* ``_reset_client_for_turn`` so
        the callback is still wired up.  No-op if ``on_event`` was never
        set (e.g. during tests exercising the bridge in isolation).
        """
        on_event = self._client.on_event
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
        if self.agent_context:
            acp_prompt_context = self.agent_context.to_acp_prompt_context()
            if acp_prompt_context:
                blocks.append(text_block(acp_prompt_context))
        if not blocks:
            return None
        return blocks

    @observe(name="acp_agent.step", ignore_inputs=["conversation", "on_event"])
    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        """Send the latest user message to the ACP server and emit the response."""
        state = conversation.state

        # Find the latest user message. Conversation implementations already
        # attach per-turn AgentContext extensions to MessageEvent.extended_content;
        # MessageEvent.to_llm_message() merges those extensions with the user text.
        prompt_blocks = None
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

            async def _prompt() -> PromptResponse:
                usage_sync = self._client.prepare_usage_sync(self._session_id or "")
                response = await self._conn.prompt(
                    prompt_blocks,
                    self._session_id,
                )
                if self._client.get_turn_usage_update(self._session_id or "") is None:
                    try:
                        await asyncio.wait_for(
                            usage_sync.wait(), timeout=_USAGE_UPDATE_TIMEOUT
                        )
                    except TimeoutError:
                        logger.warning(
                            "UsageUpdate not received within %.1fs for session %s",
                            _USAGE_UPDATE_TIMEOUT,
                            self._session_id,
                        )
                return response

            # Send prompt to ACP server with retry logic for connection errors.
            # Transient connection failures (network blips, server restarts) are
            # retried to preserve session state and avoid losing progress.
            logger.info(
                "Sending ACP prompt (timeout=%.0fs, blocks=%d)",
                self.acp_prompt_timeout,
                len(prompt_blocks),
            )

            response: PromptResponse | None = None
            max_retries = _ACP_PROMPT_MAX_RETRIES

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
                            "ACP prompt failed with retriable error (attempt %d/%d), "
                            "retrying in %.0fs: %s",
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
                    # Error" from Gemini).  These are JSON-RPC -32603 errors
                    # that indicate a server-side failure, not a client bug.
                    if (
                        e.code in _RETRIABLE_SERVER_ERROR_CODES
                        and attempt < max_retries
                    ):
                        delay = _ACP_PROMPT_RETRY_DELAYS[
                            min(attempt, len(_ACP_PROMPT_RETRY_DELAYS) - 1)
                        ]
                        logger.warning(
                            "ACP prompt failed with server error (attempt %d/%d), "
                            "retrying in %.0fs: [%d] %s",
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

            # Build response message
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

        except TimeoutError:
            elapsed = time.monotonic() - t0
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
        except Exception as e:
            logger.error("ACP prompt failed: %s", e, exc_info=True)
            error_str = str(e)

            # Close any tool cards left in flight before surfacing the error.
            self._cancel_inflight_tool_calls()

            # Emit error as an agent message (existing behavior, preserved for
            # consumers that inspect MessageEvents)
            error_message = Message(
                role="assistant",
                content=[TextContent(text=f"ACP error: {e}")],
            )
            on_event(MessageEvent(source="agent", llm_message=error_message))

            # Emit typed ConversationErrorEvent so RemoteConversation can
            # report the actual error detail via _get_last_error_detail()
            # instead of falling back to "Remote conversation ended with error"
            is_aup = (
                "usage policy" in error_str.lower()
                or "content policy" in error_str.lower()
            )
            on_event(
                ConversationErrorEvent(
                    source="agent",
                    code="UsagePolicyRefusal" if is_aup else "ACPPromptError",
                    detail=error_str[:500],
                )
            )

            state.execution_status = ConversationExecutionStatus.ERROR

            # Re-raise so LocalConversation.run()'s outer except handler
            # breaks the loop, emits ConversationErrorEvent, and raises
            # ConversationRunError — matching how the regular Agent works
            raise
        finally:
            # Unwire the per-turn callbacks now that this step has finished
            # emitting everything it's going to emit.  If the ACP subprocess
            # later dispatches a trailing ``session_update`` (e.g. between
            # turns), it fires on the portal thread with no FIFOLock held
            # by anyone — firing a stale ``on_event`` there would race
            # with other threads mutating ``state.events``.  Clearing the
            # callbacks turns any such late update into a no-op emit.
            self._client.on_event = None
            self._client.on_token = None
            self._client.on_activity = None

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

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
