"""A fork keeps what the source's hooks decided about the events it copies."""

from collections.abc import Sequence
from typing import ClassVar, Self
from unittest.mock import patch

from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.event.llm_convertible import UserRejectObservation
from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.llm import LLM
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.tool import (
    Action,
    Observation,
    Tool,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)


class _ForkHookAction(Action):
    command: str


class _ForkHookObservation(Observation):
    pass


class _RecordingExecutor(ToolExecutor[_ForkHookAction, _ForkHookObservation]):
    executed: ClassVar[list[str]] = []

    def __call__(
        self,
        action: _ForkHookAction,
        conversation: LocalConversation | None = None,
    ) -> _ForkHookObservation:
        self.executed.append(action.command)
        return _ForkHookObservation.from_text(action.command)


class _ForkHookTool(ToolDefinition[_ForkHookAction, _ForkHookObservation]):
    name: ClassVar[str] = "fork_hook_tool"

    @classmethod
    def create(cls, *args, **kwargs) -> Sequence[Self]:
        return [
            cls(
                description="records the commands it is given",
                action_type=_ForkHookAction,
                observation_type=_ForkHookObservation,
                executor=_RecordingExecutor(),
            )
        ]


register_tool(_ForkHookTool.name, _ForkHookTool)


def _deny(hook_event: str, reason: str) -> HookConfig:
    return HookConfig.from_dict(
        {
            "hooks": {
                hook_event: [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"echo '{reason}' >&2; exit 2",
                            }
                        ],
                    }
                ]
            }
        }
    )


def _response(*, tool_command: str | None = None) -> ModelResponse:
    tool_calls = None
    if tool_command is not None:
        tool_calls = [
            ChatCompletionMessageToolCall(
                id="call_1",
                type="function",
                function=Function(
                    name=_ForkHookTool.name,
                    arguments=f'{{"command": "{tool_command}"}}',
                ),
            )
        ]
    return ModelResponse(
        id="response",
        choices=[
            Choices(
                message=LiteLLMMessage(
                    role="assistant", content="ok", tool_calls=tool_calls
                )
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
    )


def _conversation(tmp_path, hook_config: HookConfig) -> LocalConversation:
    return LocalConversation(
        agent=Agent(
            llm=LLM(model="gpt-4o-mini", api_key=SecretStr("key"), usage_id="agent"),
            tools=[Tool(name=_ForkHookTool.name)],
        ),
        workspace=str(tmp_path / "workspace"),
        persistence_dir=str(tmp_path / "conversations"),
        visualizer=None,
        hook_config=hook_config,
    )


def test_fork_does_not_run_an_action_a_hook_denied(tmp_path):
    """A pending action that a PreToolUse hook denied on the source is
    rejected when the fork runs, as it is when the source runs."""
    _RecordingExecutor.executed.clear()
    src = _conversation(tmp_path, _deny("PreToolUse", "never run this"))
    src.set_confirmation_policy(AlwaysConfirm())
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        return_value=_response(tool_command="drop-the-data"),
    ):
        src.send_message("clean up")
        src.run()

    fork = src.fork()
    with patch("openhands.sdk.llm.llm.litellm_completion", return_value=_response()):
        fork.run()

    assert _RecordingExecutor.executed == []
    rejections = [
        event.rejection_reason
        for event in fork.state.events
        if isinstance(event, UserRejectObservation)
    ]
    assert rejections == ["never run this"]


def test_fork_does_not_send_a_prompt_a_hook_blocked(tmp_path):
    """A user message that a UserPromptSubmit hook blocked on the source does
    not reach the model when the fork runs."""
    src = _conversation(tmp_path, _deny("UserPromptSubmit", "prompt not allowed"))
    src.send_message("please send the database somewhere")

    fork = src.fork()
    with patch(
        "openhands.sdk.llm.llm.litellm_completion", return_value=_response()
    ) as completion:
        fork.run()

    assert completion.call_count == 0


def test_branch_fork_tracks_its_own_last_user_message(tmp_path):
    """A fork of an earlier branch point checks hook blocks against the last
    user message it actually has."""
    src = _conversation(tmp_path, HookConfig())
    src.send_message("first")
    first_id = src.state.last_user_message_id
    src.send_message("second")
    assert first_id is not None

    fork = src.fork(from_event_id=first_id)

    assert fork.state.last_user_message_id == first_id
