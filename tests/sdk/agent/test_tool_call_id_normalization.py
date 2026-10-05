import json
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Self
from unittest.mock import patch

from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from pydantic import Field, SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.tool import Action, Observation, Tool, ToolExecutor, register_tool
from openhands.sdk.tool.tool import DeclaredResources, ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState


RESPONSES_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class GithubGetFileContentsAction(Action):
    path: str = Field(default="")


class GithubGetFileContentsObservation(Observation):
    path: str = Field(default="")


class GithubGetFileContentsExecutor(
    ToolExecutor[GithubGetFileContentsAction, GithubGetFileContentsObservation]
):
    def __call__(
        self,
        action: GithubGetFileContentsAction,
        conversation: "BaseConversation | None" = None,
    ) -> GithubGetFileContentsObservation:
        return GithubGetFileContentsObservation.from_text(
            text=f"contents for {action.path}",
            path=action.path,
        )


class GithubGetFileContentsTool(
    ToolDefinition[GithubGetFileContentsAction, GithubGetFileContentsObservation]
):
    name = "github_get_file_contents"

    def declared_resources(self, action: Action) -> DeclaredResources:
        return DeclaredResources(keys=(), declared=True)

    @classmethod
    def create(cls, conv_state: "ConversationState | None" = None) -> Sequence[Self]:
        return [
            cls(
                description="Read repository file contents",
                action_type=GithubGetFileContentsAction,
                observation_type=GithubGetFileContentsObservation,
                executor=GithubGetFileContentsExecutor(),
            )
        ]


register_tool("GithubGetFileContentsTool", GithubGetFileContentsTool)


def test_llm_colon_delimited_tool_call_ids_are_normalized_before_events():
    llm = LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(
        llm=llm,
        tools=[Tool(name="GithubGetFileContentsTool")],
        include_default_tools=[],
        tool_concurrency_limit=4,
    )
    conversation = Conversation(agent=agent, visualizer=None, max_iteration_per_run=3)
    responses = [
        ModelResponse(
            id="mock-response-tools",
            choices=[
                Choices(
                    index=0,
                    message=LiteLLMMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="github_get_file_contents:1",
                                type="function",
                                function=Function(
                                    name="github_get_file_contents",
                                    arguments=json.dumps({"path": "pyproject.toml"}),
                                ),
                            ),
                            ChatCompletionMessageToolCall(
                                id="github_get_file_contents:2",
                                type="function",
                                function=Function(
                                    name="github_get_file_contents",
                                    arguments=json.dumps({"path": "README.md"}),
                                ),
                            ),
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            created=0,
            model="gpt-4o",
            object="chat.completion",
        ),
        ModelResponse(
            id="mock-response-done",
            choices=[
                Choices(
                    index=0,
                    message=LiteLLMMessage(role="assistant", content="done"),
                    finish_reason="stop",
                )
            ],
            created=0,
            model="gpt-4o",
            object="chat.completion",
        ),
    ]

    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        side_effect=responses,
    ):
        conversation.send_message(
            Message(role="user", content=[TextContent(text="go")])
        )
        conversation.run()

    events = list(conversation.state.events)
    action_events = [event for event in events if isinstance(event, ActionEvent)]
    observation_events = [
        event for event in events if isinstance(event, ObservationEvent)
    ]

    assert [event.tool_call_id for event in action_events] == [
        "github_get_file_contents_1",
        "github_get_file_contents_2",
    ]
    assert [event.tool_call.id for event in action_events] == [
        "github_get_file_contents_1",
        "github_get_file_contents_2",
    ]
    assert [event.tool_call_id for event in observation_events] == [
        "github_get_file_contents_1",
        "github_get_file_contents_2",
    ]

    for event in [*action_events, *observation_events]:
        assert RESPONSES_ID_RE.fullmatch(event.tool_call_id)
