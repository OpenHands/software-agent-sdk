from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, Field, create_model
from pydantic.json_schema import SkipJsonSchema
from rich.text import Text

from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState


class FinishAction(Action):
    message: str = Field(description="Final message to send to the user.")

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this action."""
        content = Text()
        content.append("Finish with message:\n", style="bold blue")
        content.append(self.message)
        return content


class FinishObservation(Observation):
    """
    Observation returned after finishing a task.
    The FinishAction itself contains the message sent to the user so no
    extra fields are needed here.
    """

    @property
    def visualize(self) -> Text:
        """Return an empty Text representation since the message is in the action."""
        return Text()


TOOL_DESCRIPTION = """Signals the completion of the current task or conversation.

Use this tool when:
- You have successfully completed the user's requested task
- You cannot proceed further due to technical limitations or missing information

The message should include:
- A clear summary of actions taken and their results
- Any next steps for the user
- Explanation if you're unable to complete the task
- Any follow-up questions if more information is needed
"""


def create_structured_finish_action(response_schema: type[BaseModel]) -> type[Action]:
    """Create a FinishAction subclass with fields from the response schema.

    This dynamically creates an Action class that includes all fields from
    the provided Pydantic model, allowing the LLM to return structured output.

    Args:
        response_schema: A Pydantic model class defining the expected structure.

    Returns:
        A new Action subclass with fields from the response schema.
    """
    fields: dict[str, Any] = {}
    for field_name, field_info in response_schema.model_fields.items():
        annotation = response_schema.__annotations__.get(field_name)
        fields[field_name] = (annotation, field_info)

    structured_action: type[Action] = create_model(
        f"Structured{response_schema.__name__}FinishAction",
        __base__=Action,
        **fields,
    )
    return structured_action


class FinishExecutor(ToolExecutor):
    def __call__(
        self,
        action: FinishAction,
        conversation: "BaseConversation | None" = None,  # noqa: ARG002
    ) -> FinishObservation:
        # For structured actions, extract text from any 'message' or 'summary' field
        # or use a JSON representation
        if hasattr(action, "message"):
            return FinishObservation.from_text(text=action.message)
        else:
            # For structured actions without a message field,
            # return the action data as JSON
            import json

            return FinishObservation.from_text(
                text=json.dumps(action.model_dump(exclude={"kind"}), indent=2)
            )


class FinishTool(ToolDefinition[FinishAction, FinishObservation]):
    """Tool for signaling the completion of a task or conversation."""

    # Store response_schema for later retrieval (excluded from serialization)
    response_schema: SkipJsonSchema[type[BaseModel] | None] = Field(
        default=None, repr=False, exclude=True
    )

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        response_schema: type[BaseModel] | None = None,
        **params,
    ) -> Sequence[Self]:
        """Create FinishTool instance with optional structured output.

        Args:
            conv_state: Optional conversation state (not used by FinishTool).
            response_schema: Optional Pydantic model class defining the expected
                structure of the final response. When provided, the agent must
                return data matching this schema instead of a simple message.
            **params: Additional parameters (none supported).

        Returns:
            A sequence containing a single FinishTool instance.

        Raises:
            ValueError: If any unsupported parameters are provided.

        Example:
            ```python
            from pydantic import BaseModel, Field

            class TaskResult(BaseModel):
                success: bool = Field(description="Whether the task succeeded")
                summary: str = Field(description="Summary of what was done")
                files_changed: list[str] = Field(description="Files modified")

            tools = [
                Tool(name="FinishTool", params={"response_schema": TaskResult})
            ]
            ```
        """
        if params:
            raise ValueError(
                f"FinishTool only accepts 'response_schema' parameter, got: {params}"
            )

        if response_schema is not None:
            action_type = create_structured_finish_action(response_schema)
            schema_json = response_schema.model_json_schema()
            description = (
                f"{TOOL_DESCRIPTION}\n\n"
                f"You MUST provide output matching the following schema:\n"
                f"{schema_json}"
            )
        else:
            action_type = FinishAction
            description = TOOL_DESCRIPTION

        return [
            cls(
                action_type=action_type,
                observation_type=FinishObservation,
                description=description,
                executor=FinishExecutor(),
                response_schema=response_schema,
                annotations=ToolAnnotations(
                    title="finish",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]
