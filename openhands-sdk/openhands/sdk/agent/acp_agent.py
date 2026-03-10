"""ACPAgent — an AgentBase subclass that delegates to an ACP server.

The Agent Client Protocol (ACP) lets OpenHands power conversations using
ACP-compatible servers (Claude Code, Gemini CLI, etc.) instead of direct
LLM calls.  The ACP server manages its own LLM, tools, and execution;
the ACPAgent simply relays user messages and collects the response.

See https://agentclientprotocol.com/protocol/overview
"""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

from pydantic import Field, PrivateAttr

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import Tool  # noqa: TC002


if TYPE_CHECKING:
    from openhands.sdk.conversation import (
        ConversationCallbackType,
        ConversationState,
        ConversationTokenCallbackType,
        LocalConversation,
    )


logger = get_logger(__name__)

# Maximum seconds to wait for a UsageUpdate notification after prompt()
# returns.  The ACP server writes UsageUpdate to the wire before the
# PromptResponse, so under normal conditions the notification handler
# finishes almost immediately.  The timeout is a safety net for slow
# or remote servers.  Override via ACP_USAGE_UPDATE_TIMEOUT.
_USAGE_UPDATE_TIMEOUT: float = float(
    os.environ.get("ACP_USAGE_UPDATE_TIMEOUT", "2.0")
)


def _make_sentinel_llm() -> LLM:
    """Create a sentinel LLM that should never be called."""
    return LLM(model="acp-managed")


# ---------------------------------------------------------------------------
# ACP Client implementation
# ---------------------------------------------------------------------------


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
                # Log non-JSON lines at debug level
                try:
                    logger.debug(
                        "ACP stdout (non-JSON): %s",
                        line.decode(errors="replace").rstrip(),
                    )
                except Exception:
                    pass
    except Exception:
        dest.feed_eof()


class _OpenHandsACPClient:
    """ACP Client that accumulates session updates and emits OpenHands events.

    Implements the ``Client`` protocol from ``agent_client_protocol``.
    """

    def __init__(self) -> None:
        self.accumulated_text: list[str] = []
        self.accumulated_thoughts: list[str] = []
        self.on_token: Any = None  # ConversationTokenCallbackType | None
        # Telemetry state (persists across turns)
        self._last_cost: float = 0.0  # last cumulative cost seen from ACP
        self._context_window: int = 0  # context window size from ACP
        # Per-turn synchronization for UsageUpdate notifications.
        # session_update() stores the data and signals the event;
        # step() awaits the event and records all telemetry in one place.
        self._turn_usage_update: Any = None  # latest UsageUpdate for current turn
        self._usage_received: Any = None  # asyncio.Event, set when UsageUpdate arrives

    def reset(self) -> None:
        self.accumulated_text.clear()
        self.accumulated_thoughts.clear()
        self.on_token = None
        # Per-turn usage update is cleared each turn.
        self._turn_usage_update = None
        # Note: _last_cost and _context_window are intentionally NOT
        # cleared — they accumulate across turns.

    # -- Client protocol methods ------------------------------------------

    async def session_update(
        self,
        session_id: str,  # noqa: ARG002
        update: Any,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        from acp.schema import (
            AgentMessageChunk,
            AgentThoughtChunk,
            TextContentBlock,
            ToolCallProgress,
            ToolCallStart,
            UsageUpdate,
        )

        if isinstance(update, AgentMessageChunk):
            if isinstance(update.content, TextContentBlock):
                text = update.content.text
                self.accumulated_text.append(text)
                if self.on_token is not None:
                    try:
                        self.on_token(text)
                    except Exception:
                        pass
        elif isinstance(update, AgentThoughtChunk):
            if isinstance(update.content, TextContentBlock):
                self.accumulated_thoughts.append(update.content.text)
        elif isinstance(update, UsageUpdate):
            # Store the update for step() to process — no metrics
            # side-effects here.  step() is the single place where
            # all telemetry (cost, tokens, latency) is recorded.
            self._context_window = update.size
            self._turn_usage_update = update
            if self._usage_received is not None:
                self._usage_received.set()
        elif isinstance(update, (ToolCallStart, ToolCallProgress)):
            logger.debug("ACP tool call event: %s", type(update).__name__)
        else:
            logger.debug("ACP session update: %s", type(update).__name__)

    async def request_permission(
        self,
        options: list[Any],
        session_id: str,  # noqa: ARG002
        tool_call: Any,
        **kwargs: Any,  # noqa: ARG002
    ) -> Any:
        """Auto-approve all permission requests from the ACP server."""
        from acp.schema import (
            AllowedOutcome,
            RequestPermissionResponse,
        )

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
    """Agent that delegates to an ACP (Agent Client Protocol) server.

    Instead of calling an LLM directly, this agent spawns an ACP-compatible
    server (e.g. ``claude-code-acp``) as a subprocess and communicates with
    it via the ACP protocol.  The server manages its own LLM, tools, and
    execution lifecycle.

    Example::

        from openhands.sdk.agent import ACPAgent
        from openhands.sdk.conversation import Conversation

        agent = ACPAgent(acp_command=["npx", "-y", "claude-code-acp"])
        conversation = Conversation(agent=agent, workspace="./workspace")
        conversation.send_message("Hello! What is 2+2?")
        conversation.run()
    """

    # Override required fields with ACP-appropriate defaults
    llm: LLM = Field(default_factory=_make_sentinel_llm)
    tools: list[Tool] = Field(default_factory=list)
    include_default_tools: list[str] = Field(default_factory=list)

    # ACP-specific configuration
    acp_command: list[str] = Field(
        ...,
        description=(
            "Command to start the ACP server, e.g. ['npx', '-y', 'claude-code-acp']"
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
    acp_model: str | None = Field(
        default=None,
        description="Model for the ACP server to use (e.g. 'claude-opus-4-6'). "
        "Passed via session _meta. If None, the server picks its default.",
    )

    # Private runtime state
    _executor: Any = PrivateAttr(default=None)
    _conn: Any = PrivateAttr(default=None)  # ClientSideConnection
    _session_id: str | None = PrivateAttr(default=None)
    _process: Any = PrivateAttr(default=None)  # asyncio subprocess
    _client: Any = PrivateAttr(default=None)  # _OpenHandsACPClient
    _filtered_reader: Any = PrivateAttr(default=None)  # StreamReader
    _closed: bool = PrivateAttr(default=False)

    # -- Override base properties to be no-ops for ACP ---------------------

    @property
    def system_message(self) -> str:
        return "ACP-managed agent"

    def get_all_llms(self) -> Generator[LLM, None, None]:
        yield self.llm

    # -- Lifecycle ---------------------------------------------------------

    def init_state(
        self,
        state: ConversationState,
        on_event: ConversationCallbackType,
    ) -> None:
        """Spawn the ACP server and initialize a session."""
        # Validate no unsupported features
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
        if self.critic is not None:
            raise NotImplementedError(
                "ACPAgent does not support critic; "
                "the ACP server manages its own evaluation"
            )
        if self.agent_context is not None:
            raise NotImplementedError(
                "ACPAgent does not support agent_context; "
                "configure the ACP server directly"
            )

        from openhands.sdk.utils.async_executor import AsyncExecutor

        self._executor = AsyncExecutor()

        try:
            self._start_acp_server(state)
        except Exception as e:
            logger.error("Failed to start ACP server: %s", e)
            self._cleanup()
            raise

        # Emit a minimal SystemPromptEvent
        event = SystemPromptEvent(
            source="agent",
            system_prompt=TextContent(text="ACP-managed agent"),
            tools=[],
        )
        on_event(event)
        self._initialized = True

    def _start_acp_server(self, state: ConversationState) -> None:
        """Start the ACP subprocess and initialize the session."""
        import asyncio

        from acp.client.connection import (
            ClientSideConnection,
        )
        from acp.transports import (
            default_environment,
        )

        client = _OpenHandsACPClient()
        self._client = client

        # Build environment: inherit current env + ACP extras
        env = default_environment()
        env.update(os.environ)
        env.update(self.acp_env)

        command = self.acp_command[0]
        args = list(self.acp_command[1:]) + list(self.acp_args)

        working_dir = str(state.workspace.working_dir)

        async def _init() -> tuple[Any, Any, Any, str]:
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
            )
            assert process.stdin is not None
            assert process.stdout is not None

            # Wrap the subprocess stdout in a filtering reader that
            # only passes lines starting with '{' (JSON-RPC messages).
            filtered_reader = asyncio.StreamReader()
            asyncio.get_event_loop().create_task(
                _filter_jsonrpc_lines(process.stdout, filtered_reader)
            )

            conn = ClientSideConnection(
                client,
                process.stdin,  # write to subprocess
                filtered_reader,  # read filtered output
            )

            # Initialize the protocol
            await conn.initialize(protocol_version=1)

            # Build _meta for session options (e.g. model selection)
            meta: dict[str, Any] | None = None
            if self.acp_model:
                meta = {"claudeCode": {"options": {"model": self.acp_model}}}

            # Create a new session
            response = await conn.new_session(cwd=working_dir, _meta=meta)
            session_id = response.session_id

            return conn, process, filtered_reader, session_id

        result = self._executor.run_async(_init)
        self._conn, self._process, self._filtered_reader, self._session_id = result

    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        """Send the latest user message to the ACP server and emit the response."""
        state = conversation.state

        # Find the latest user message
        user_message = None
        for event in reversed(list(state.events)):
            if isinstance(event, MessageEvent) and event.source == "user":
                # Extract text from the message
                for content in event.llm_message.content:
                    if isinstance(content, TextContent) and content.text.strip():
                        user_message = content.text
                        break
                if user_message:
                    break

        if user_message is None:
            logger.warning("No user message found; finishing conversation")
            state.execution_status = ConversationExecutionStatus.FINISHED
            return

        # Reset client accumulators
        self._client.reset()
        self._client.on_token = on_token

        try:
            import asyncio

            from acp.helpers import text_block

            async def _prompt() -> tuple[Any, float]:
                # Prepare synchronization: the ACP server writes UsageUpdate
                # to the wire *before* the PromptResponse, so the notification
                # is enqueued before the response future resolves.  We use an
                # asyncio.Event to wait for the handler to finish processing
                # it instead of a blind sleep.
                self._client._usage_received = asyncio.Event()
                self._client._turn_usage_update = None

                t0 = time.monotonic()
                response = await self._conn.prompt(
                    [text_block(user_message)],
                    self._session_id,
                )
                latency = time.monotonic() - t0

                # Wait for UsageUpdate if it hasn't arrived yet.
                if self._client._turn_usage_update is None:
                    try:
                        await asyncio.wait_for(
                            self._client._usage_received.wait(),
                            timeout=_USAGE_UPDATE_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "UsageUpdate not received within %.1fs timeout",
                            _USAGE_UPDATE_TIMEOUT,
                        )

                return response, latency

            # Send prompt to ACP server
            response, latency = self._executor.run_async(_prompt)

            # --- Single telemetry recording point ---
            # All cost, token, and latency data is recorded here after
            # both PromptResponse and UsageUpdate are available.

            usage_update = self._client._turn_usage_update

            # 1. Cost (from UsageUpdate)
            if usage_update is not None and usage_update.cost is not None:
                delta = usage_update.cost.amount - self._client._last_cost
                if delta > 0:
                    self.llm.metrics.add_cost(delta)
                self._client._last_cost = usage_update.cost.amount

            # 2. Tokens (from PromptResponse.usage)
            if (
                response is not None
                and hasattr(response, "usage")
                and response.usage is not None
            ):
                usage = response.usage
                ctx_window = (
                    usage_update.size
                    if usage_update is not None
                    else self._client._context_window
                )
                self.llm.metrics.add_token_usage(
                    prompt_tokens=usage.input_tokens,
                    completion_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cached_read_tokens or 0,
                    cache_write_tokens=usage.cached_write_tokens or 0,
                    reasoning_tokens=usage.thought_tokens or 0,
                    context_window=ctx_window,
                    response_id=self._session_id or "",
                )

            # 3. Latency
            self.llm.metrics.add_response_latency(
                latency, self._session_id or ""
            )

            # 4. Stats callback
            if self.llm.telemetry._stats_update_callback is not None:
                try:
                    self.llm.telemetry._stats_update_callback()
                except Exception:
                    pass

            # Build response message
            response_text = "".join(self._client.accumulated_text)
            thought_text = "".join(self._client.accumulated_thoughts)

            if not response_text:
                response_text = "(No response from ACP server)"

            message = Message(
                role="assistant",
                content=[TextContent(text=response_text)],
                reasoning_content=thought_text if thought_text else None,
            )

            msg_event = MessageEvent(
                source="agent",
                llm_message=message,
            )
            on_event(msg_event)
            state.execution_status = ConversationExecutionStatus.FINISHED

        except Exception as e:
            logger.error("ACP prompt failed: %s", e, exc_info=True)
            # Emit error as an agent message since AgentErrorEvent requires
            # tool context we don't have
            error_message = Message(
                role="assistant",
                content=[TextContent(text=f"ACP error: {e}")],
            )
            error_event = MessageEvent(
                source="agent",
                llm_message=error_message,
            )
            on_event(error_event)
            state.execution_status = ConversationExecutionStatus.ERROR

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
