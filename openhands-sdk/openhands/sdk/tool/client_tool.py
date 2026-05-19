"""Client-defined tools: tools defined via JSON spec, executed by external clients.

These tools allow frontend clients (like Agent Canvas) to register tools purely
via JSON in ``POST /conversations``, with no Python code required. When the agent
calls a client tool, an ActionEvent is emitted over the WebSocket and the client
handles execution. The SDK returns an acknowledgment observation immediately.

This eliminates the need for Python tool code in JavaScript repos and the complex
``tool_module_qualnames`` / ``--import-modules`` plumbing.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, Field

from openhands.sdk.tool.schema import Action, Observation
from openhands.sdk.tool.tool import (
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState


class ClientToolSpec(BaseModel):
    """A tool defined by the client, executed externally (not by the SDK).

    Clients pass these specs in ``POST /conversations`` to register tools
    whose execution is handled outside the SDK (e.g., by a frontend
    listening for ActionEvents over WebSocket).
    """

    name: str = Field(
        ...,
        description="Unique tool name the agent will use to call this tool.",
    )
    description: str = Field(
        ...,
        description=(
            "Description shown to the LLM explaining when and how to use this tool."
        ),
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        description=(
            "JSON Schema describing the tool's input parameters. "
            "Must be an object schema."
        ),
    )
    annotations: ToolAnnotations | None = Field(
        default=None,
        description="Optional MCP-style annotations for the tool.",
    )


class ClientToolObservation(Observation):
    """Observation returned when a client tool is called.

    The actual execution happens on the client side; the SDK returns
    this acknowledgment so the agent loop can continue.
    """


class ClientToolExecutor(ToolExecutor):
    """No-op executor that returns an acknowledgment observation.

    The real execution happens on the client (frontend) which listens
    for the ActionEvent over WebSocket.
    """

    def __call__(
        self,
        action: Action,  # noqa: ARG002
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> ClientToolObservation:
        return ClientToolObservation.from_text(text="Tool call dispatched to client.")


# Shared executor instance — stateless, so one is enough.
_CLIENT_TOOL_EXECUTOR = ClientToolExecutor()


class ClientTool(ToolDefinition[Action, ClientToolObservation]):
    """A tool whose execution is deferred to the external client.

    Created from a :class:`ClientToolSpec` at conversation start. The agent
    sees it as a normal tool and can call it; the ActionEvent is emitted
    over WebSocket for the client to handle.
    """

    client_tool_name: str = Field(
        description="Per-instance tool name from the ClientToolSpec.",
    )

    @property
    def name(self) -> str:  # type: ignore[override]
        """Return the client-defined tool name."""
        return self.client_tool_name

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        **params: Any,
    ) -> Sequence[Self]:
        """Create a ClientTool from a :class:`ClientToolSpec`.

        Args:
            conv_state: Conversation state (not used).
            **params: Must include ``spec`` (:class:`ClientToolSpec`).

        Returns:
            A single-element sequence containing the ClientTool.
        """
        spec: ClientToolSpec = params["spec"]
        action_type = Action.from_mcp_schema(
            model_name=f"ClientAction_{spec.name}",
            schema=spec.parameters,
        )

        annotations = spec.annotations or ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )

        return [
            cls(
                client_tool_name=spec.name,
                description=spec.description,
                action_type=action_type,
                observation_type=ClientToolObservation,
                executor=_CLIENT_TOOL_EXECUTOR,
                annotations=annotations,
            )
        ]

    @classmethod
    def from_spec(cls, spec: ClientToolSpec) -> "ClientTool":
        """Convenience factory that creates a ClientTool from a spec.

        Returns a single ClientTool instance (not a sequence).
        """
        tools = cls.create(spec=spec)
        return tools[0]
