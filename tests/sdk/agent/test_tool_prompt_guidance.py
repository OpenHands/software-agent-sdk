from collections.abc import Sequence
from typing import ClassVar
from uuid import uuid4

from openhands.sdk import LLM
from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import Event, SystemPromptEvent
from openhands.sdk.llm.message import ImageContent, TextContent
from openhands.sdk.tool import ToolDefinition
from openhands.sdk.tool.registry import register_tool
from openhands.sdk.tool.spec import Tool
from openhands.sdk.tool.tool import Action, Observation, ToolExecutor
from openhands.sdk.workspace import LocalWorkspace


SHARED = "<SHARED_GUIDANCE>\n* shared rule\n</SHARED_GUIDANCE>"
OTHER = "<OTHER_GUIDANCE>\n* other rule\n</OTHER_GUIDANCE>"


class _GuidanceAction(Action):
    text: str = ""


class _GuidanceObs(Observation):
    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text="ok")]


class _GuidanceExec(ToolExecutor[_GuidanceAction, _GuidanceObs]):
    def __call__(self, action: _GuidanceAction, conversation=None) -> _GuidanceObs:
        return _GuidanceObs()


class _GuidedTool(ToolDefinition[_GuidanceAction, _GuidanceObs]):
    guidance: ClassVar[str | None] = None

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["_GuidedTool"]:
        return [
            cls(
                description=f"{cls.name} tool",
                action_type=_GuidanceAction,
                observation_type=_GuidanceObs,
                executor=_GuidanceExec(),
                prompt_guidance=cls.guidance,
            )
        ]


class _SharedGuidanceATool(_GuidedTool):
    name: ClassVar[str] = "guided_a"
    guidance: ClassVar[str | None] = SHARED


class _SharedGuidanceBTool(_GuidedTool):
    name: ClassVar[str] = "guided_b"
    guidance: ClassVar[str | None] = SHARED


class _OtherGuidanceTool(_GuidedTool):
    name: ClassVar[str] = "guided_c"
    guidance: ClassVar[str | None] = OTHER


class _UnguidedTool(_GuidedTool):
    name: ClassVar[str] = "unguided"


for _tool in (
    _SharedGuidanceATool,
    _SharedGuidanceBTool,
    _OtherGuidanceTool,
    _UnguidedTool,
):
    register_tool(_tool.name, _tool)


def _system_prompt(tmp_path, tool_names: list[str], **agent_kwargs) -> str:
    agent = Agent(
        llm=LLM(model="test-model", usage_id="test-llm"),
        tools=[Tool(name=name) for name in tool_names],
        **agent_kwargs,
    )
    state = ConversationState.create(
        id=uuid4(), agent=agent, workspace=LocalWorkspace(working_dir=str(tmp_path))
    )
    events: list[Event] = []
    agent.init_state(state, on_event=events.append)
    (event,) = [e for e in events if isinstance(e, SystemPromptEvent)]
    return event.system_prompt.text


def test_guidance_renders_once_per_text_before_external_services(tmp_path):
    with_guidance = _system_prompt(
        tmp_path, ["guided_a", "guided_b", "guided_c", "unguided"]
    )
    without = _system_prompt(tmp_path, ["unguided"])

    assert with_guidance == without.replace(
        "<EXTERNAL_SERVICES>", f"{SHARED}\n\n{OTHER}\n\n<EXTERNAL_SERVICES>"
    )


def test_inline_system_prompt_is_followed_by_tool_guidance(tmp_path):
    prompt = _system_prompt(
        tmp_path, ["guided_a", "guided_c"], system_prompt="CUSTOM PROMPT"
    )

    assert prompt == f"CUSTOM PROMPT\n\n{SHARED}\n\n{OTHER}"


def test_inline_system_prompt_is_verbatim_without_tool_guidance(tmp_path):
    prompt = _system_prompt(tmp_path, ["unguided"], system_prompt="CUSTOM PROMPT")

    assert prompt == "CUSTOM PROMPT"
