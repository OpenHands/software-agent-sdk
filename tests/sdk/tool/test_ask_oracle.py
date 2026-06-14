from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import PrivateAttr, ValidationError

from openhands.sdk import LLM, LocalConversation, OpenHandsAgentSettings, Tool
from openhands.sdk.agent import Agent
from openhands.sdk.llm import Message, TextContent, llm_profile_store
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import ToolDefinition
from openhands.sdk.tool.builtins import (
    AskOracleAction,
    AskOracleObservation,
    AskOracleTool,
)


class CapturingTestLLM(TestLLM):
    _last_messages: list[Message] = PrivateAttr(default_factory=list)
    _last_tools: Sequence[ToolDefinition] | None = PrivateAttr(default=None)

    @property
    def last_messages(self) -> list[Message]:
        return self._last_messages

    @property
    def last_tools(self) -> Sequence[ToolDefinition] | None:
        return self._last_tools

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        _return_metrics: bool = False,
        add_security_risk_prediction: bool = False,
        on_token: Any = None,
        **kwargs: Any,
    ):
        self._last_messages = list(messages)
        self._last_tools = tools
        return super().completion(
            messages=messages,
            tools=tools,
            _return_metrics=_return_metrics,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            **kwargs,
        )


def _make_llm(model: str, usage_id: str) -> LLM:
    return TestLLM.from_messages([], model=model, usage_id=usage_id)


def _assistant_message(text: str) -> Message:
    return Message(role="assistant", content=[TextContent(text=text)])


def _message_text(message: Message) -> str:
    return "".join(
        content.text for content in message.content if isinstance(content, TextContent)
    )


def _make_conversation(profile_name: str = "oracle") -> LocalConversation:
    return LocalConversation(
        agent=Agent(
            llm=_make_llm("default-model", "default"),
            tools=[
                Tool(name=AskOracleTool.name, params={"profile_name": profile_name})
            ],
            include_default_tools=[],
        ),
        workspace=Path.cwd(),
    )


def test_ask_oracle_tool_description_guides_second_opinion_usage() -> None:
    tool = AskOracleTool.create(profile_name="oracle")[0]

    assert "Ask the Oracle for a second opinion" in tool.description
    assert "Treat the Oracle's response as strong guidance" in tool.description


def test_ask_oracle_tool_validates_profile_name() -> None:
    with pytest.raises(ValueError, match="Invalid Oracle profile name"):
        AskOracleTool.create(profile_name="../oracle")


def test_agent_settings_adds_ask_oracle_tool_when_profile_is_configured() -> None:
    agent = OpenHandsAgentSettings(
        llm=_make_llm("default-model", "default"),
        oracle_llm_profile="oracle",
    ).create_agent()

    assert any(
        tool.name == AskOracleTool.name and tool.params == {"profile_name": "oracle"}
        for tool in agent.tools
    )

    conversation = LocalConversation(agent=agent, workspace=Path.cwd())
    conversation._ensure_agent_ready()
    assert "ask_oracle" in agent.tools_map


def test_agent_settings_configured_profile_updates_existing_ask_oracle_tool() -> None:
    agent = OpenHandsAgentSettings(
        llm=_make_llm("default-model", "default"),
        oracle_llm_profile="oracle",
        tools=[
            Tool(
                name=AskOracleTool.name,
                params={
                    "profile_name": "stale",
                    "profile_store_dir": "/tmp/profiles",
                },
            )
        ],
    ).create_agent()

    oracle_tools = [tool for tool in agent.tools if tool.name == AskOracleTool.name]
    assert len(oracle_tools) == 1
    assert oracle_tools[0].params == {
        "profile_name": "oracle",
        "profile_store_dir": "/tmp/profiles",
    }


def test_agent_settings_omits_ask_oracle_tool_without_profile() -> None:
    agent = OpenHandsAgentSettings(
        llm=_make_llm("default-model", "default"),
    ).create_agent()

    assert all(tool.name != AskOracleTool.name for tool in agent.tools)

    conversation = LocalConversation(agent=agent, workspace=Path.cwd())
    conversation._ensure_agent_ready()
    assert "ask_oracle" not in agent.tools_map


def test_agent_settings_rejects_invalid_oracle_profile_name() -> None:
    with pytest.raises(ValidationError, match="oracle_llm_profile"):
        OpenHandsAgentSettings(oracle_llm_profile="../oracle")


def test_ask_oracle_tool_returns_oracle_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle_llm = cast(
        CapturingTestLLM,
        CapturingTestLLM.from_messages(
            [_assistant_message("Prefer the smaller, typed settings field.")],
            model="oracle-model",
            usage_id="oracle",
        ),
    )

    def load_profile(
        self: LLMProfileStore,
        name: str,
        *,
        cipher=None,
    ) -> LLM:
        assert name == "oracle"
        return oracle_llm

    monkeypatch.setattr(LLMProfileStore, "load", load_profile)
    conversation = _make_conversation()

    observation = conversation.execute_tool(
        "ask_oracle",
        AskOracleAction(
            question="Should I add one setting or two?",
            context="The tool needs an Oracle profile name.",
        ),
    )

    assert isinstance(observation, AskOracleObservation)
    assert not observation.is_error
    assert observation.response == "Prefer the smaller, typed settings field."
    assert observation.text == "Prefer the smaller, typed settings field."
    assert "Prefer the smaller" in observation.visualize.plain
    assert [message.role for message in oracle_llm.last_messages] == ["system", "user"]
    assert "You are the Oracle" in _message_text(oracle_llm.last_messages[0])
    assert "Should I add one setting or two?" in _message_text(
        oracle_llm.last_messages[1]
    )
    assert "The tool needs an Oracle profile name." in _message_text(
        oracle_llm.last_messages[1]
    )
    assert oracle_llm.last_tools == []
    assert conversation.agent.llm.model == "default-model"
    assert conversation.state.agent.llm.model == "default-model"


def test_ask_oracle_tool_reports_missing_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()

    monkeypatch.setattr(llm_profile_store, "_DEFAULT_PROFILE_DIR", profile_dir)
    conversation = _make_conversation(profile_name="missing")

    observation = conversation.execute_tool(
        "ask_oracle",
        AskOracleAction(question="What should I do next?"),
    )

    assert isinstance(observation, AskOracleObservation)
    assert observation.is_error
    assert observation.response == ""
    assert "not available" in observation.text


def test_ask_oracle_tool_reports_empty_oracle_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle_llm = TestLLM.from_messages(
        [Message(role="assistant", content=[])],
        model="oracle-model",
        usage_id="oracle",
    )

    def load_profile(
        self: LLMProfileStore,
        name: str,
        *,
        cipher=None,
    ) -> LLM:
        return oracle_llm

    monkeypatch.setattr(LLMProfileStore, "load", load_profile)
    conversation = _make_conversation()

    observation = conversation.execute_tool(
        "ask_oracle",
        AskOracleAction(question="What should I do next?"),
    )

    assert isinstance(observation, AskOracleObservation)
    assert observation.is_error
    assert observation.response == ""
    assert "did not return a response" in observation.text
