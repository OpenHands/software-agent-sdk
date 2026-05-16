"""Utility functions for MCP integration."""

import re
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation

import mcp.types
from litellm import ChatCompletionToolParam
from pydantic import Field, ValidationError

from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.definition import MCPToolAction, MCPToolObservation
from openhands.sdk.observability.laminar import observe
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)
from openhands.sdk.tool.schema import Schema
from openhands.sdk.utils.models import DiscriminatedUnionMixin


logger = get_logger(__name__)

# Regex pattern for environment variable references:
# - $VAR or ${VAR} - simple variable reference
# - ${VAR:-default} - variable with default value
SECRET_VAR_PATTERN = re.compile(
    r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)(?::-([^}]*))?\}|\$([a-zA-Z_][a-zA-Z0-9_]*)"
)


def expand_secrets_in_data(
    data: dict[str, Any],
    get_secret: Callable[[str], str | None],
) -> dict[str, Any]:
    """Expand secret/environment variable references in MCP tool action data.

    Supports variable expansion similar to shell and MCP config:
    - $VAR - Simple variable reference
    - ${VAR} - Braced variable reference
    - ${VAR:-default} - With default value

    Args:
        data: MCP tool action data dictionary.
        get_secret: Callback to look up a secret by name. Returns value or None.

    Returns:
        Data dictionary with secret references expanded.
    """

    def replace_var(match: re.Match) -> str:
        # Group 1: braced variable name (from ${VAR} or ${VAR:-default})
        # Group 2: default value (from ${VAR:-default})
        # Group 3: unbraced variable name (from $VAR)
        braced_var = match.group(1)
        default_value = match.group(2)
        unbraced_var = match.group(3)

        var_name = braced_var or unbraced_var

        # Look up the secret
        secret_value = get_secret(var_name)
        if secret_value is not None:
            return secret_value

        # Apply default if available (only for braced syntax)
        if default_value is not None:
            return default_value

        # Return original if not found (preserves placeholder)
        return match.group(0)

    def expand_value(value: Any) -> Any:
        if isinstance(value, str):
            return SECRET_VAR_PATTERN.sub(replace_var, value)
        if isinstance(value, dict):
            return {k: expand_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand_value(item) for item in value]
        return value

    return expand_value(data)


# Default timeout for MCP tool execution in seconds
MCP_TOOL_TIMEOUT_SECONDS = 300


# NOTE: We don't define MCPToolAction because it
# will be a pydantic BaseModel dynamically created from the MCP tool schema.
# It will be available as "tool.action_type".


def to_camel_case(s: str) -> str:
    parts = re.split(r"[_\-\s]+", s)
    return "".join(word.capitalize() for word in parts if word)


class MCPToolExecutor(ToolExecutor):
    """Executor for MCP tools."""

    tool_name: str
    client: MCPClient
    timeout: float

    def __init__(
        self,
        tool_name: str,
        client: MCPClient,
        timeout: float = MCP_TOOL_TIMEOUT_SECONDS,
    ):
        self.tool_name = tool_name
        self.client = client
        self.timeout = timeout

    @observe(name="MCPToolExecutor.call_tool", span_type="TOOL")
    async def call_tool(self, action: MCPToolAction) -> MCPToolObservation:
        """Execute the MCP tool call using the already-connected client."""
        if not self.client.is_connected():
            raise RuntimeError(
                f"MCP client not connected for tool '{self.tool_name}'. "
                "The connection may have been closed or failed to establish."
            )
        try:
            logger.debug(
                f"Calling MCP tool {self.tool_name} with args: {action.model_dump()}"
            )
            result: mcp.types.CallToolResult = await self.client.call_tool_mcp(
                name=self.tool_name, arguments=action.to_mcp_arguments()
            )
            return MCPToolObservation.from_call_tool_result(
                tool_name=self.tool_name, result=result
            )
        except Exception as e:
            error_msg = f"Error calling MCP tool {self.tool_name}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return MCPToolObservation.from_text(
                text=error_msg,
                is_error=True,
                tool_name=self.tool_name,
            )

    def __call__(
        self,
        action: MCPToolAction,
        conversation: "LocalConversation | None" = None,
    ) -> MCPToolObservation:
        """Execute an MCP tool call.

        If a conversation is provided, secret references in the action data
        (e.g., $VAR, ${VAR}, ${VAR:-default}) are expanded using the
        conversation's secret registry before calling the MCP server.
        """
        # Expand secrets in action data if conversation is available
        expanded_action = action
        if conversation is not None:
            try:
                secret_registry = conversation.state.secret_registry
                expanded_data = expand_secrets_in_data(
                    action.data, secret_registry.get_secret_value
                )
                expanded_action = MCPToolAction(data=expanded_data)
            except Exception as e:
                logger.warning(f"Failed to expand secrets in MCP tool action: {e}")
                # Fall back to original action if expansion fails

        try:
            observation = self.client.call_async_from_sync(
                self.call_tool, action=expanded_action, timeout=self.timeout
            )
            # Mask secrets in observation output
            return self._mask_observation(observation, conversation)
        except TimeoutError:
            error_msg = (
                f"MCP tool '{self.tool_name}' timed out after {self.timeout} seconds. "
                "The tool server may be unresponsive or the operation is taking "
                "too long. Consider retrying or using an alternative approach."
            )
            logger.error(error_msg)
            return MCPToolObservation.from_text(
                text=error_msg,
                is_error=True,
                tool_name=self.tool_name,
            )

    def _mask_observation(
        self,
        observation: MCPToolObservation,
        conversation: "LocalConversation | None" = None,
    ) -> MCPToolObservation:
        """Apply automatic secrets masking to observation content."""
        if conversation is None:
            return observation

        try:
            from openhands.sdk.llm import TextContent

            secret_registry = conversation.state.secret_registry
            # Mask secrets in all text content blocks
            masked_content = []
            for block in observation.content:
                if isinstance(block, TextContent) and block.text:
                    masked_text = secret_registry.mask_secrets_in_output(block.text)
                    masked_content.append(TextContent(text=masked_text))
                else:
                    masked_content.append(block)

            # Return new observation with masked content
            return MCPToolObservation(
                content=masked_content,
                is_error=observation.is_error,
                tool_name=observation.tool_name,
            )
        except Exception as e:
            logger.warning(f"Failed to mask secrets in MCP observation: {e}")
            return observation

    def close(self) -> None:
        self.client.sync_close()


_mcp_dynamic_action_type: dict[str, type[Schema]] = {}


def _create_mcp_action_type(action_type: mcp.types.Tool) -> type[Schema]:
    """Dynamically create a Pydantic model for MCP tool action from schema.

    We create from "Schema" instead of:
    - "MCPToolAction" because MCPToolAction has a "data" field that
      wraps all dynamic fields, which we don't want here.
    - "Action" because Action inherits from DiscriminatedUnionMixin,
      which includes `kind` field that is not needed here.

    .from_mcp_schema simply defines a new Pydantic model class
    that inherits from the given base class.
    We may want to use the returned class to convert fields definitions
    to openai tool schema.
    """

    # Tool.name should be unique, so we can cache the created types.
    mcp_action_type = _mcp_dynamic_action_type.get(action_type.name)
    if mcp_action_type:
        return mcp_action_type

    model_name = f"MCP{to_camel_case(action_type.name)}Action"
    mcp_action_type = Schema.from_mcp_schema(model_name, action_type.inputSchema)
    _mcp_dynamic_action_type[action_type.name] = mcp_action_type
    return mcp_action_type


class MCPToolDefinition(ToolDefinition[MCPToolAction, MCPToolObservation]):
    """MCP Tool that wraps an MCP client and provides tool functionality."""

    mcp_tool: mcp.types.Tool = Field(description="The MCP tool definition.")

    @property
    def name(self) -> str:  # type: ignore[override]
        """Return the MCP tool name instead of the class name."""
        return self.mcp_tool.name

    def __call__(
        self,
        action: Action,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> Observation:
        """Execute the tool action using the MCP client.

        We dynamically create a new MCPToolAction class with
        the tool's input schema to validate the action.

        Args:
            action: The action to execute.

        Returns:
            The observation result from executing the action.
        """
        if not isinstance(action, MCPToolAction):
            raise ValueError(
                f"MCPTool can only execute MCPToolAction actions, got {type(action)}",
            )
        assert self.name == self.mcp_tool.name
        mcp_action_type = _create_mcp_action_type(self.mcp_tool)
        try:
            mcp_action_type.model_validate(action.data)
        except ValidationError as e:
            # Surface validation errors as an observation instead of crashing
            error_msg = f"Validation error for MCP tool '{self.name}' args: {e}"
            logger.error(error_msg, exc_info=True)
            return MCPToolObservation.from_text(
                text=error_msg,
                is_error=True,
                tool_name=self.name,
            )

        return super().__call__(action, conversation)

    def action_from_arguments(self, arguments: dict[str, Any]) -> MCPToolAction:
        """Create an MCPToolAction from parsed arguments with early validation.

        We validate the raw arguments against the MCP tool's input schema here so
        Agent._get_action_event can catch ValidationError and surface an
        AgentErrorEvent back to the model instead of crashing later during tool
        execution. On success, we return MCPToolAction with sanitized arguments.

        Args:
            arguments: The parsed arguments from the tool call.

        Returns:
            The MCPToolAction instance with data populated from the arguments.

        Raises:
            ValidationError: If the arguments do not conform to the tool schema.
        """
        # Drop None-valued keys before validation to avoid type errors
        # on optional fields
        prefiltered_args = {k: v for k, v in (arguments or {}).items() if v is not None}
        # Validate against the dynamically created action type (from MCP schema)
        mcp_action_type = _create_mcp_action_type(self.mcp_tool)
        validated = mcp_action_type.model_validate(prefiltered_args)
        # Use exclude_none to avoid injecting nulls back to the call
        # Exclude DiscriminatedUnionMixin fields (e.g., 'kind') as they're
        # internal to OpenHands and not part of the MCP tool schema
        exclude_fields = set(DiscriminatedUnionMixin.model_fields.keys()) | set(
            DiscriminatedUnionMixin.model_computed_fields.keys()
        )
        sanitized = validated.model_dump(exclude_none=True, exclude=exclude_fields)
        return MCPToolAction(data=sanitized)

    @classmethod
    def create(
        cls,
        mcp_tool: mcp.types.Tool,
        mcp_client: MCPClient,
    ) -> Sequence["MCPToolDefinition"]:
        try:
            annotations = (
                ToolAnnotations.model_validate(
                    mcp_tool.annotations.model_dump(exclude_none=True)
                )
                if mcp_tool.annotations
                else None
            )

            tool_instance = cls(
                description=mcp_tool.description or "No description provided",
                action_type=MCPToolAction,
                observation_type=MCPToolObservation,
                annotations=annotations,
                meta=mcp_tool.meta,
                executor=MCPToolExecutor(tool_name=mcp_tool.name, client=mcp_client),
                # pass-through fields (enabled by **extra in Tool.create)
                mcp_tool=mcp_tool,
            )
            return [tool_instance]
        except ValidationError as e:
            logger.error(
                f"Validation error creating MCPTool for {mcp_tool.name}: "
                f"{e.json(indent=2)}",
                exc_info=True,
            )
            raise e

    def to_mcp_tool(
        self,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if input_schema is not None or output_schema is not None:
            raise ValueError("MCPTool.to_mcp_tool does not support overriding schemas")

        return super().to_mcp_tool(
            input_schema=self.mcp_tool.inputSchema,
            output_schema=self.observation_type.to_mcp_schema()
            if self.observation_type
            else None,
        )

    def to_openai_tool(
        self,
        add_security_risk_prediction: bool = False,
        action_type: type[Schema] | None = None,
    ) -> ChatCompletionToolParam:
        """Convert a Tool to an OpenAI tool.

        For MCP, we dynamically create the action_type (type: Schema)
        from the MCP tool input schema, and pass it to the parent method.
        It will use the .model_fields from this pydantic model to
        generate the OpenAI-compatible tool schema.

        Args:
            add_security_risk_prediction: Whether to add a `security_risk` field
                to the action schema for LLM to predict. This is useful for
                tools that may have safety risks, so the LLM can reason about
                the risk level before calling the tool.
        """
        if action_type is not None:
            raise ValueError(
                "MCPTool.to_openai_tool does not support overriding action_type"
            )

        assert self.name == self.mcp_tool.name
        mcp_action_type = _create_mcp_action_type(self.mcp_tool)
        return super().to_openai_tool(
            add_security_risk_prediction=add_security_risk_prediction,
            action_type=mcp_action_type,
        )
