"""Client-defined tools: tools defined via JSON spec, executed by external clients.

These tools allow frontend clients (like Agent Canvas) to register tools purely
via JSON in ``POST /conversations``, with no Python code required. When the agent
calls a client tool, an ActionEvent is emitted over the WebSocket and the client
handles execution. The SDK returns an acknowledgment observation immediately.

This eliminates the need for Python tool code in JavaScript repos and the complex
``tool_module_qualnames`` / ``--import-modules`` plumbing.
"""

import copy
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, Field, field_validator

from openhands.sdk.logger import get_logger
from openhands.sdk.tool.schema import Action, Observation, Schema
from openhands.sdk.tool.tool import (
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState
    from openhands.sdk.tool.spec import Tool


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Cached dynamic action types
#
# ``Action.from_mcp_schema`` creates a *concrete* ``Action`` subclass whose
# ``kind`` is derived from the class name (``ClientAction_<name>``). These
# subclasses register process-globally in the discriminated-union hierarchy,
# so creating two classes with the same name (e.g. when the same client tool
# is registered twice, or re-created on conversation resume) makes
# ``Action.resolve_kind`` raise a duplicate-class error and breaks event
# deserialization. We therefore cache the generated type per tool name and
# reject same-name/different-schema conflicts explicitly.
# ---------------------------------------------------------------------------
_client_action_types: dict[str, type[Action]] = {}
_client_action_schemas: dict[str, dict[str, Any]] = {}
_client_action_lock = threading.RLock()


class ClientToolRegistrationError(ValueError):
    """Raised when client tool registration receives invalid input.

    This is a caller/input error (e.g. a bad ``POST /conversations`` payload),
    so callers such as the agent server map it to a 4xx response rather than a
    500.
    """


class ClientToolSchemaConflictError(ClientToolRegistrationError):
    """Raised when a client tool name is reused with a different schema.

    The generated action ``kind`` (``ClientAction_<name>``) is process-global,
    so a single name cannot represent two different parameter schemas.
    """


def _get_client_action_type(name: str, schema: dict[str, Any]) -> type[Action]:
    """Return a cached ``Action`` subclass for ``name`` built from ``schema``.

    Reuses the previously generated type when the same ``name`` + ``schema``
    is requested again. Raises :class:`ClientToolSchemaConflictError` if ``name``
    was already registered with a *different* schema, since the generated action
    ``kind`` is process-global and cannot represent two schemas at once.
    """
    with _client_action_lock:
        existing = _client_action_types.get(name)
        if existing is not None:
            if _client_action_schemas[name] != schema:
                raise ClientToolSchemaConflictError(
                    f"Client tool '{name}' is already registered with a different "
                    "parameters schema. Client tool names must map to a single, "
                    "stable schema within a process."
                )
            return existing
        action_type = Action.from_mcp_schema(
            model_name=f"ClientAction_{name}",
            schema=schema,
        )
        _client_action_types[name] = action_type
        _client_action_schemas[name] = copy.deepcopy(schema)
        return action_type


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
        description=(
            "Optional MCP-style annotations for the tool. When omitted, the "
            "tool is treated conservatively (not read-only), so the agent is "
            "asked to predict a security risk before calling it."
        ),
    )

    @field_validator("parameters")
    @classmethod
    def _validate_object_schema(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Ensure ``parameters`` is a JSON Schema *object* schema.

        ``ClientTool`` builds a Pydantic action model from this schema via
        ``Action.from_mcp_schema``, which only supports object schemas. Validate
        here so callers get an immediate, clear error at the source instead of a
        confusing failure later during tool creation.
        """
        if v.get("type") != "object":
            raise ValueError(
                "ClientToolSpec.parameters must be an object JSON Schema "
                f"(got type={v.get('type')!r}). Example: "
                '{"type": "object", "properties": {...}}'
            )
        return v


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
    input_schema: dict[str, Any] = Field(
        description=(
            "The original JSON Schema for the tool's parameters, as provided by "
            "the client. Used verbatim when exporting the tool to the LLM so "
            "client-defined constraints (enum, nested objects, bounds, "
            "additionalProperties, ...) are preserved."
        ),
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
            **params: Must include ``spec`` — either a :class:`ClientToolSpec`
                instance or a JSON-serializable dict of one. The dict form is
                used when the spec flows through per-conversation
                ``Tool.params`` on the server.

        Returns:
            A single-element sequence containing the ClientTool.
        """
        spec_param = params.get("spec")
        if spec_param is None:
            raise ValueError(
                "ClientTool.create requires a 'spec' parameter "
                "(a ClientToolSpec or a dict of one)."
            )
        if isinstance(spec_param, ClientToolSpec):
            spec = spec_param
        elif isinstance(spec_param, dict):
            spec = ClientToolSpec.model_validate(spec_param)
        else:
            raise TypeError(
                "ClientTool.create 'spec' must be a ClientToolSpec or dict, "
                f"got {type(spec_param)}."
            )

        action_type = _get_client_action_type(spec.name, spec.parameters)

        return [
            cls(
                client_tool_name=spec.name,
                description=spec.description,
                action_type=action_type,
                observation_type=ClientToolObservation,
                executor=_CLIENT_TOOL_EXECUTOR,
                # Leave annotations unset unless the client explicitly provides
                # them: client tools can trigger arbitrary frontend side effects,
                # so we must not optimistically assume read-only/idempotent.
                annotations=spec.annotations,
                input_schema=spec.parameters,
            )
        ]

    @classmethod
    def from_spec(cls, spec: ClientToolSpec) -> "ClientTool":
        """Convenience factory that creates a ClientTool from a spec.

        Returns a single ClientTool instance (not a sequence).
        """
        tools = cls.create(spec=spec)
        return tools[0]

    def _get_tool_schema(
        self,
        add_security_risk_prediction: bool = False,
        action_type: type[Schema] | None = None,
    ) -> dict[str, Any]:
        """Build the provider-facing schema from the original client schema.

        The base implementation rebuilds the schema from the generated Pydantic
        action model, which drops client-defined JSON Schema constraints
        (``enum``, nested ``properties``, ``additionalProperties``, numeric
        bounds, ...). Here we start from the original ``input_schema`` and only
        overlay the SDK-added meta fields (``summary`` and, when applicable,
        ``security_risk``) so client constraints are preserved exactly.
        """
        if action_type is not None:
            raise ValueError(
                "ClientTool._get_tool_schema does not support overriding action_type"
            )

        # Render the SDK meta fields (summary / security_risk) exactly as the
        # base implementation would, then lift just those properties over.
        sdk_schema = super()._get_tool_schema(
            add_security_risk_prediction=add_security_risk_prediction,
        )
        sdk_props: dict[str, Any] = sdk_schema.get("properties", {})
        sdk_required: list[str] = sdk_schema.get("required", []) or []

        merged = copy.deepcopy(self.input_schema)
        merged.setdefault("type", "object")
        props = merged.setdefault("properties", {})
        for meta in ("security_risk", "summary"):
            if meta in sdk_props:
                props[meta] = sdk_props[meta]
                if meta in sdk_required:
                    required = merged.setdefault("required", [])
                    if meta not in required:
                        required.append(meta)

        from openhands.sdk.tool.tool import _prioritize_schema_fields

        _prioritize_schema_fields(
            schema=merged,
            priority=("security_risk", "summary"),
        )
        return merged

    def to_mcp_tool(
        self,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if input_schema is not None or output_schema is not None:
            raise ValueError(
                "ClientTool.to_mcp_tool does not support overriding schemas"
            )
        return super().to_mcp_tool(
            input_schema=self.input_schema,
            output_schema=self.observation_type.to_mcp_schema()
            if self.observation_type
            else None,
        )


def extract_client_tool_specs(tools: "Sequence[Tool]") -> list[ClientToolSpec]:
    """Recover :class:`ClientToolSpec`s embedded in persisted ``Tool`` specs.

    Client tools carry their full spec under ``Tool.params['spec']`` (see
    :func:`register_client_tools`). When a conversation is resumed in a fresh
    process, that spec is the only place the schema survives, so we use it to
    re-register the dynamic tools. A persisted ``Tool`` is treated as a client
    tool only when its ``params['spec']`` validates as a ``ClientToolSpec`` whose
    ``name`` matches the tool name, which avoids misclassifying ordinary tools
    that happen to use a ``spec`` param.
    """
    from pydantic import ValidationError

    specs: list[ClientToolSpec] = []
    for tool in tools:
        raw = (tool.params or {}).get("spec")
        if not isinstance(raw, dict):
            continue
        try:
            spec = ClientToolSpec.model_validate(raw)
        except ValidationError:
            continue
        if spec.name == tool.name:
            specs.append(spec)
    return specs


def resolve_client_tool(
    tool: "Tool", conv_state: "ConversationState"
) -> Sequence[ToolDefinition] | None:
    """Resolve a registered client tool from its embedded spec."""
    raw = (tool.params or {}).get("spec")
    if not isinstance(raw, dict):
        return None
    try:
        spec = ClientToolSpec.model_validate(raw)
    except ValueError:
        return None
    if spec.name != tool.name:
        return None
    with _client_action_lock:
        if spec.name not in _client_action_types:
            return None
    return ClientTool.create(conv_state=conv_state, spec=spec)


def register_client_tools(
    specs: Sequence[ClientToolSpec],
    *,
    agent_tools: Sequence["Tool"] | None = None,
) -> list["Tool"]:
    """Validate client tools and return per-conversation tool specs."""
    from openhands.sdk.tool.registry import (
        is_tool_registered_as,
        list_registered_tools,
        register_tool,
    )
    from openhands.sdk.tool.spec import Tool

    seen_names: set[str] = set()
    for spec in specs:
        if spec.name in seen_names:
            raise ClientToolRegistrationError(
                f"Duplicate client tool name '{spec.name}' in one registration "
                "request. Client tool names must be unique."
            )
        seen_names.add(spec.name)

    with _client_action_lock:
        tool_specs: list[Tool] = []
        already_registered = set(list_registered_tools())
        if agent_tools is None:
            collisions = [
                spec.name
                for spec in specs
                if spec.name in already_registered
                and not is_tool_registered_as(spec.name, ClientTool)
            ]
        else:
            collisions = []
            for spec in specs:
                existing = [tool for tool in agent_tools if tool.name == spec.name]
                if not existing:
                    continue
                existing_specs = extract_client_tool_specs(existing)
                if len(existing_specs) != len(existing):
                    collisions.append(spec.name)
                elif any(existing_spec != spec for existing_spec in existing_specs):
                    raise ClientToolRegistrationError(
                        f"Client tool '{spec.name}' conflicts with the existing "
                        "client tool spec in this agent."
                    )

        for name in collisions:
            raise ClientToolRegistrationError(
                f"Client tool name '{name}' collides with an existing "
                "non-client tool. Choose a unique client tool name."
            )

        for spec in specs:
            _get_client_action_type(spec.name, spec.parameters)
            if spec.name not in already_registered:
                register_tool(spec.name, ClientTool)
                already_registered.add(spec.name)
            tool_specs.append(Tool(name=spec.name, params={"spec": spec.model_dump()}))
        return tool_specs
