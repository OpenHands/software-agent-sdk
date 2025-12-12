"""
ACPAgent: An Agent-Client Protocol client implementation for OpenHands SDK.

This agent acts as an ACP client that communicates with ACP servers
(like Claude-Code, Gemini CLI) to provide AI agent capabilities through
the Agent-Client Protocol (https://agentclientprotocol.com/).

Uses the official agent-client-protocol Python SDK for protocol communication.
"""

import asyncio
import asyncio.subprocess as aio_subprocess
import os
from typing import TYPE_CHECKING, Any

from pydantic import Field

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from acp import ClientSideConnection  # type: ignore[import-not-found]

    from openhands.sdk.conversation import (
        ConversationCallbackType,
        ConversationState,
    )
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation

logger = get_logger(__name__)

# Lazy import ACP SDK to avoid hard dependency
_ACP_AVAILABLE = False
try:
    from acp import (  # type: ignore[import-not-found]
        PROTOCOL_VERSION,
        Client,
        ClientSideConnection,
        InitializeRequest,
        NewSessionRequest,
        PromptRequest,
        RequestError,
        text_block,
    )
    from acp.schema import (  # type: ignore[import-not-found]
        AgentMessageChunk,
        AudioContentBlock,
        ClientCapabilities,
        EmbeddedResourceContentBlock,
        ImageContentBlock,
        Implementation,
        ResourceContentBlock,
        TextContentBlock,
    )

    _ACP_AVAILABLE = True

    class _OpenHandsACPClient(Client):  # type: ignore[misc]
        """
        OpenHands implementation of ACP Client interface.

        This client implementation accumulates agent responses and provides
        minimal implementations of required ACP client methods.
        """

        def __init__(self) -> None:
            super().__init__()
            self.accumulated_text: list[str] = []

        async def requestPermission(self, params: Any) -> Any:  # type: ignore[override] # noqa: ARG002
            raise RequestError.method_not_found("session/request_permission")

        async def writeTextFile(self, params: Any) -> Any:  # type: ignore[override] # noqa: ARG002
            raise RequestError.method_not_found("fs/write_text_file")

        async def readTextFile(self, params: Any) -> Any:  # type: ignore[override] # noqa: ARG002
            raise RequestError.method_not_found("fs/read_text_file")

        async def createTerminal(self, params: Any) -> Any:  # type: ignore[override] # noqa: ARG002
            raise RequestError.method_not_found("terminal/create")

        async def terminalOutput(self, params: Any) -> Any:  # type: ignore[override] # noqa: ARG002
            raise RequestError.method_not_found("terminal/output")

        async def releaseTerminal(self, params: Any) -> Any:  # type: ignore[override] # noqa: ARG002
            raise RequestError.method_not_found("terminal/release")

        async def waitForTerminalExit(self, params: Any) -> Any:  # type: ignore[override] # noqa: ARG002
            raise RequestError.method_not_found("terminal/wait_for_exit")

        async def killTerminal(self, params: Any) -> Any:  # type: ignore[override] # noqa: ARG002
            raise RequestError.method_not_found("terminal/kill")

        async def sessionUpdate(self, params: Any) -> None:  # type: ignore[override]
            """Handle session updates from the ACP server."""
            update = params.update
            if not isinstance(update, AgentMessageChunk):
                return

            content = update.content
            text: str
            if isinstance(content, TextContentBlock):
                text = content.text
            elif isinstance(content, ImageContentBlock):
                text = "<image>"
            elif isinstance(content, AudioContentBlock):
                text = "<audio>"
            elif isinstance(content, ResourceContentBlock):
                text = content.uri or "<resource>"
            elif isinstance(content, EmbeddedResourceContentBlock):
                text = "<resource>"
            else:
                text = "<content>"

            self.accumulated_text.append(text)

        async def extMethod(self, method: str, params: dict) -> dict:  # noqa: ARG002
            raise RequestError.method_not_found(method)

        async def extNotification(self, method: str, params: dict) -> None:  # noqa: ARG002
            raise RequestError.method_not_found(method)

except ImportError:
    # Define a dummy placeholder class when ACP is not available
    class _OpenHandsACPClient:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.accumulated_text: list[str] = []


class ACPAgent(AgentBase):
    """
    ACPAgent is an Agent-Client Protocol client implementation.

    This agent communicates with ACP servers (like Claude-Code or Gemini CLI)
    to provide AI agent capabilities using the official agent-client-protocol
    Python SDK.

    Requirements:
        pip install agent-client-protocol

    Note:
        ACP servers manage their own LLM configuration internally, so the `llm`
        parameter is optional and not used by the ACP protocol.

    Example:
        >>> from openhands.sdk.agent import ACPAgent
        >>> from openhands.sdk.conversation import LocalConversation
        >>> agent = ACPAgent(acp_command=["npx", "-y", "claude-code-acp"])
        >>> conversation = LocalConversation(agent=agent, workspace="/workspace")
        >>> conversation.send_message("Hello!")
        >>> conversation.run()
    """

    llm: Any = Field(  # type: ignore[assignment]
        default=None,
        description=(
            "LLM configuration (not used by ACP - ACP servers manage their "
            "own LLM). Kept for AgentBase compatibility."
        ),
    )

    acp_command: list[str] = Field(
        ...,
        description=(
            "Command to start the ACP server subprocess. "
            "Example: ['npx', '-y', 'claude-code-acp']"
        ),
    )
    acp_args: list[str] = Field(
        default_factory=list,
        description="Additional arguments to pass to the ACP server command.",
    )
    acp_cwd: str | None = Field(
        default=None,
        description=(
            "Working directory for the ACP server process. "
            "If None, uses the conversation workspace."
        ),
    )

    def _check_unsupported_features(self) -> None:
        """Check for unsupported features and raise errors."""
        if self.tools and len(self.tools) > 0:
            raise NotImplementedError(
                "ACPAgent does not yet support custom tools. "
                "The ACP server manages its own tools."
            )

        if self.mcp_config:
            raise NotImplementedError(
                "ACPAgent does not yet support MCP configuration. "
                "MCP integration should be configured at the ACP server level."
            )

        if self.agent_context:
            raise NotImplementedError(
                "ACPAgent does not yet support AgentContext (microagents). "
                "This is a known limitation and will be addressed in future versions."
            )

        if self.security_analyzer:
            raise NotImplementedError(
                "ACPAgent does not yet support security analyzers. "
                "Security policies should be managed at the ACP server level."
            )

        if self.condenser:
            raise NotImplementedError(
                "ACPAgent does not yet support context condensers. "
                "Context management should be handled at the ACP server level."
            )

    def init_state(
        self,
        state: "ConversationState",  # noqa: ARG002
        on_event: "ConversationCallbackType",  # noqa: ARG002
    ) -> None:
        """Initialize the agent state and check for unsupported features."""
        if not _ACP_AVAILABLE:
            raise ImportError(
                "agent-client-protocol package is required for ACPAgent. "
                "Install it with: pip install agent-client-protocol"
            )

        self._check_unsupported_features()

    async def _run_acp_interaction(
        self,
        user_message: str,
        workspace: str,
    ) -> str:
        """
        Run an ACP interaction using the official SDK.

        Args:
            user_message: The user's message to send to the ACP server
            workspace: The workspace directory path

        Returns:
            The accumulated response text from the ACP server
        """
        # Start ACP server process
        proc = await asyncio.create_subprocess_exec(
            *self.acp_command,
            *self.acp_args,
            stdin=aio_subprocess.PIPE,
            stdout=aio_subprocess.PIPE,
            cwd=self.acp_cwd or workspace,
        )

        if proc.stdin is None or proc.stdout is None:
            raise RuntimeError("ACP server process does not expose stdio pipes")

        # Create ACP client
        client_impl = _OpenHandsACPClient()
        conn: ClientSideConnection = ClientSideConnection(
            lambda _agent: client_impl, proc.stdin, proc.stdout
        )

        try:
            # Initialize protocol
            await conn.initialize(
                InitializeRequest(  # type: ignore[possibly-unbound]
                    protocolVersion=PROTOCOL_VERSION,  # type: ignore[possibly-unbound]
                    clientCapabilities=ClientCapabilities(),  # type: ignore[possibly-unbound]
                    clientInfo=Implementation(  # type: ignore[possibly-unbound]
                        name="openhands-sdk",
                        title="OpenHands SDK",
                        version="1.0.0",
                    ),
                )
            )

            # Create session
            session = await conn.newSession(
                NewSessionRequest(  # type: ignore[possibly-unbound]
                    mcpServers=[],
                    cwd=workspace,
                )
            )

            # Send prompt
            await conn.prompt(
                PromptRequest(  # type: ignore[possibly-unbound]
                    sessionId=session.sessionId,
                    prompt=[text_block(user_message)],  # type: ignore[possibly-unbound]
                )
            )

            # Return accumulated response
            return "".join(client_impl.accumulated_text)

        finally:
            # Cleanup process
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()

    def step(
        self,
        conversation: "LocalConversation",
        on_event: "ConversationCallbackType",
    ) -> None:
        """
        Execute one step of the agent's interaction with the ACP server.

        Args:
            conversation: The conversation object
            on_event: Callback for emitting events
        """
        state = conversation.state
        # Get the last user message
        last_user_message = None
        for event in reversed(list(state.events)):
            if isinstance(event, MessageEvent) and event.source == "user":
                if event.llm_message and event.llm_message.content:
                    for content in event.llm_message.content:
                        if isinstance(content, TextContent):
                            last_user_message = content.text
                            break
                if last_user_message:
                    break

        if not last_user_message:
            logger.warning("No user message found in conversation state")
            state.execution_status = ConversationExecutionStatus.FINISHED
            return

        # Get workspace path
        workspace = getattr(state, "workspace", os.getcwd())

        # Run ACP interaction
        try:
            response_text = asyncio.run(
                self._run_acp_interaction(last_user_message, workspace)
            )

            # Create agent response event
            agent_message = Message(
                role="assistant",
                content=[TextContent(type="text", text=response_text)],
            )

            event = MessageEvent(
                source="agent",
                llm_message=agent_message,
            )

            on_event(event)

            # Mark conversation as finished
            state.execution_status = ConversationExecutionStatus.FINISHED

        except Exception as e:
            logger.error(f"ACP interaction failed: {e}")
            # Create error message event
            error_message = Message(
                role="assistant",
                content=[TextContent(type="text", text=f"Error: {e}")],
            )

            event = MessageEvent(
                source="agent",
                llm_message=error_message,
            )

            on_event(event)

            state.execution_status = ConversationExecutionStatus.ERROR
