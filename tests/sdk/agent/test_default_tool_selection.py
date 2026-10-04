import json
import uuid

import pytest

from openhands.sdk import LLM, Agent, AgentContext, OpenHandsAgentSettings
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.skills import Skill
from openhands.sdk.tool.builtins.vision_inspect import VISION_INSPECT_TOOL_NAME
from openhands.sdk.workspace.local import LocalWorkspace


def _agent_context() -> AgentContext:
    return AgentContext(
        skills=[
            Skill(
                name="frontend-design",
                content="# Frontend design",
                description="Design frontend interfaces.",
                source="/skills/frontend-design/SKILL.md",
                is_agentskills_format=True,
            )
        ]
    )


def _make_agent(include_default_tools: list[str] | None = None) -> Agent:
    kwargs = {}
    if include_default_tools is not None:
        kwargs["include_default_tools"] = include_default_tools
    return Agent(
        llm=LLM(
            model="openai/gpt-4o-mini",
            api_key="dummy",
            disable_vision=True,
        ),
        tools=[],
        agent_context=_agent_context(),
        **kwargs,
    )


def _initialize(agent: AgentBase, tmp_path) -> set[str]:
    state = ConversationState.create(
        id=uuid.uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )
    agent._initialize(state)
    return set(agent.tools_map)


def _mock_vision_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "openhands.sdk.agent.base.has_vision_profile_available", lambda: True
    )
    monkeypatch.setattr(
        "openhands.sdk.tool.builtins.vision_inspect._candidate_vision_profiles",
        lambda: ["saved-vision"],
    )


@pytest.mark.parametrize(
    ("selection", "expected_tools"),
    [
        pytest.param([], set(), id="empty-selection"),
        pytest.param(["FinishTool"], {"finish"}, id="explicit-subset"),
        pytest.param(
            ["FinishTool", "ThinkTool"],
            {"finish", "think"},
            id="explicit-default-set",
        ),
        pytest.param(
            ["FinishTool", "ThinkTool", "SwitchLLMTool"],
            {"finish", "think", "switch_llm"},
            id="explicit-settings-default-set",
        ),
        pytest.param(
            ["InvokeSkillTool", "VisionInspectTool"],
            {"invoke_skill", VISION_INSPECT_TOOL_NAME},
            id="explicit-optional-tools",
        ),
    ],
)
@pytest.mark.parametrize("round_trip", [False, True], ids=["direct", "serialized"])
def test_explicit_default_tool_selection_suppresses_auto_attached_builtins(
    selection: list[str],
    expected_tools: set[str],
    round_trip: bool,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_vision_profile(monkeypatch)
    agent = _make_agent(selection)
    if round_trip:
        agent = Agent.model_validate_json(agent.model_dump_json())

    assert _initialize(agent, tmp_path) == expected_tools


@pytest.mark.parametrize("round_trip", [False, True], ids=["direct", "serialized"])
def test_omitted_default_tool_selection_keeps_conditional_auto_attachment(
    round_trip: bool,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_vision_profile(monkeypatch)
    agent = _make_agent()
    if round_trip:
        agent = Agent.model_validate_json(agent.model_dump_json())

    assert _initialize(agent, tmp_path) == {
        "finish",
        "think",
        "invoke_skill",
        VISION_INSPECT_TOOL_NAME,
    }


@pytest.mark.parametrize("agent_class", [AgentBase, Agent])
@pytest.mark.parametrize("json_payload", [False, True], ids=["dict", "json"])
@pytest.mark.parametrize("settings_generated", [False, True])
def test_markerless_legacy_default_selection_keeps_conditional_auto_attachment(
    agent_class: type[AgentBase],
    json_payload: bool,
    settings_generated: bool,
    settings_agent: Agent,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_vision_profile(monkeypatch)
    original = settings_agent if settings_generated else _make_agent()
    payload = original.model_dump(mode="json" if json_payload else "python")
    payload.pop("_include_default_tools_explicit")

    agent = (
        agent_class.model_validate_json(json.dumps(payload))
        if json_payload
        else agent_class.model_validate(payload)
    )

    expected_tools = {"finish", "think", "invoke_skill", VISION_INSPECT_TOOL_NAME}
    if settings_generated:
        expected_tools.add("switch_llm")
    assert _initialize(agent, tmp_path) == expected_tools


@pytest.fixture
def settings_agent(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Agent:
    monkeypatch.setattr(
        "openhands.sdk.llm.llm_profile_store._DEFAULT_PROFILE_DIR",
        tmp_path / "profiles",
    )
    return OpenHandsAgentSettings(
        llm=LLM(
            model="openai/gpt-4o-mini",
            api_key="dummy",
            disable_vision=True,
        ),
        tools=[],
        agent_context=_agent_context(),
    ).create_agent()


def test_settings_generated_default_tool_list_keeps_auto_attachment(
    settings_agent: Agent,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_vision_profile(monkeypatch)

    assert _initialize(settings_agent, tmp_path) == {
        "finish",
        "think",
        "switch_llm",
        "invoke_skill",
        VISION_INSPECT_TOOL_NAME,
    }


def test_acp_agent_serialization_does_not_include_default_tool_provenance() -> None:
    agent = ACPAgent(acp_command=["echo", "test"])

    serialized = agent.model_dump()

    assert "_include_default_tools_explicit" not in serialized
    restored = ACPAgent.model_validate(serialized)
    assert "_include_default_tools_explicit" not in restored.model_dump()
