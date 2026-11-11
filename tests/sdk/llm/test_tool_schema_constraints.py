"""Declared action constraints must reach native tool-calling requests."""

from collections.abc import Sequence
from typing import Any, Literal, Self

import pytest
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse
from pydantic import BaseModel, ConfigDict, Field

import openhands.sdk.llm.llm as llm_module
from openhands.sdk import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.tool import Action, Tool, register_tool
from openhands.sdk.tool.tool import ToolDefinition


class RefundTransportDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str = Field(pattern=r"^REF-[0-9]{3}$")
    currency: Literal["USD"]


class RefundTransportAction(Action):
    order_id: str = Field(pattern=r"^ORD-[0-9]{6}$")
    currency: Literal["USD"]
    details: RefundTransportDetails


class RefundTransportTool(ToolDefinition[RefundTransportAction, None]):
    name = "refund_schema_transport"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[Self]:
        return [cls(description="Issue a refund", action_type=RefundTransportAction)]


def _assert_constraints(schema: dict[str, Any]) -> None:
    assert schema["additionalProperties"] is False
    props = schema["properties"]
    assert props["order_id"]["pattern"] == r"^ORD-[0-9]{6}$"
    assert props["currency"]["const"] == "USD"
    assert props["details"]["additionalProperties"] is False
    assert props["details"]["properties"]["reference"]["pattern"] == r"^REF-[0-9]{3}$"
    assert props["details"]["properties"]["currency"]["const"] == "USD"
    assert "summary" in props
    assert "kind" not in props


@pytest.mark.parametrize("api", ["openai", "responses"])
@pytest.mark.parametrize("risk", [False, True])
def test_tool_exports_preserve_constraints_with_summary_and_risk(api, risk):
    [tool] = RefundTransportTool.create()
    if api == "openai":
        schema = tool.to_openai_tool(add_security_risk_prediction=risk)["function"].get(
            "parameters"
        )
    else:
        schema = tool.to_responses_tool(add_security_risk_prediction=risk).get(
            "parameters"
        )

    assert isinstance(schema, dict)
    _assert_constraints(schema)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert ("security_risk" in properties) is risk


@pytest.fixture
def captured_tool_request(monkeypatch):
    requests: list[dict[str, Any]] = []

    def completion(**kwargs):
        requests.append(kwargs)
        return ModelResponse(
            id="schema-constraints-response",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=LiteLLMMessage(role="assistant", content="Done"),
                )
            ],
            created=0,
            model=kwargs["model"],
            object="chat.completion",
        )

    monkeypatch.setattr(llm_module, "litellm_completion", completion)
    return requests


@pytest.mark.parametrize("model", ["openai/gpt-4o", "anthropic/claude-sonnet-4-5"])
@pytest.mark.parametrize("entry", ["completion", "agent_step"])
def test_native_request_preserves_action_constraints(
    model, entry, captured_tool_request, tmp_path
):
    llm = LLM(model=model, api_key="test", num_retries=0, native_tool_calling=True)
    message = Message(role="user", content=[TextContent(text="Refund the order")])
    if entry == "completion":
        llm.completion(
            messages=[
                Message(
                    role="system", content=[TextContent(text="Assist with refunds")]
                ),
                message,
            ],
            tools=list(RefundTransportTool.create()),
            add_security_risk_prediction=True,
        )
    else:
        register_tool(RefundTransportTool.__name__, RefundTransportTool)
        agent = Agent(llm=llm, tools=[Tool(name=RefundTransportTool.__name__)])
        conversation = LocalConversation(agent=agent, workspace=tmp_path)
        try:
            conversation.send_message(message)
            agent.step(conversation, on_event=lambda event: None)
        finally:
            conversation.close()

    assert len(captured_tool_request) == 1
    tools = captured_tool_request[0]["tools"]
    [refund] = [
        tool for tool in tools if tool["function"]["name"] == RefundTransportTool.name
    ]
    _assert_constraints(refund["function"]["parameters"])
