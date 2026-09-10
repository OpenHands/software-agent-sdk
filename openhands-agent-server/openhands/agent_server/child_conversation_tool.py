"""Trusted server-side child conversation launch tool."""

from typing import Literal

from pydantic import Field

from openhands.sdk import TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)
from openhands.sdk.tool.client_tool import register_reserved_native_tool_name


class LaunchChildConversationAction(Action):
    """Request a child conversation on the current agent server."""

    task: str = Field(
        min_length=1, max_length=32768, description="Task brief for the child agent."
    )
    title: str | None = Field(
        default=None, max_length=256, description="Optional child title."
    )
    isolation: Literal["shared", "worktree"] = Field(
        default="shared",
        description="Use the parent workspace or a dedicated git worktree.",
    )


class LaunchChildConversationObservation(Observation):
    """Identity and initial status of a launched child."""

    conversation_id: str
    execution_status: str
    workspace: str
    url_path: str

    @property
    def to_llm_content(self) -> list[TextContent]:
        return [
            TextContent(
                text=(
                    f"Child conversation {self.conversation_id} launched with status "
                    f"{self.execution_status}. Workspace: {self.workspace}. "
                    f"Path: {self.url_path}."
                )
            )
        ]


class LaunchChildConversationExecutor(ToolExecutor):
    def __call__(
        self,
        action: LaunchChildConversationAction,
        conversation=None,
    ) -> LaunchChildConversationObservation:
        context = getattr(conversation, "server_tool_context", None)
        if context is None or not hasattr(context, "launch_child_conversation"):
            raise RuntimeError("Child conversations are not supported in this context")
        return context.launch_child_conversation(action)

    def close(self) -> None:
        pass


class LaunchChildConversationTool(
    ToolDefinition[LaunchChildConversationAction, LaunchChildConversationObservation]
):
    @classmethod
    def create(cls, conv_state, **params):  # noqa: ARG003
        return [
            cls(
                action_type=LaunchChildConversationAction,
                observation_type=LaunchChildConversationObservation,
                description=(
                    "Launch a child conversation on this agent server. The child "
                    "inherits the parent agent and workspace relationship."
                ),
                annotations=ToolAnnotations(
                    title="launch_child_conversation",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
                executor=LaunchChildConversationExecutor(),
            )
        ]


register_tool(LaunchChildConversationTool.name, LaunchChildConversationTool)
register_reserved_native_tool_name(LaunchChildConversationTool.name)
