"""The JSON Schema keywords an `Action` declares, as the model is sent them.

`IssueRefundAction` declares a `pattern` on `order_id`, a one-value
`Literal` (`const`) on `currency`, and `extra="forbid"`
(`additionalProperties: false`). The program renders the tool the way the
agent sends it (`to_openai_tool()`, `to_responses_tool()`) and reports which
of those keywords survive, next to what pydantic's own schema has.
"""

from collections.abc import Sequence
from typing import Literal

from pydantic import ConfigDict, Field
from sdk_verify import emit

from openhands.sdk import Action, Observation, ToolDefinition
from openhands.sdk.tool import ToolExecutor


class IssueRefundAction(Action):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(pattern=r"^ORD-[0-9]{6}$", description="Order id")
    amount: int = Field(description="Amount in cents")
    currency: Literal["USD"] = Field(description="Currency")


class RefundObservation(Observation):
    pass


class RefundExecutor(ToolExecutor[IssueRefundAction, RefundObservation]):
    def __call__(self, action, conversation=None):  # noqa: ARG002
        return RefundObservation.from_text("ok")


class IssueRefundTool(ToolDefinition[IssueRefundAction, RefundObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["IssueRefundTool"]:  # noqa: ARG003
        return [
            cls(
                description="Issue a refund.",
                action_type=IssueRefundAction,
                observation_type=RefundObservation,
                executor=RefundExecutor(),
            )
        ]


tool = IssueRefundTool.create()[0]
pyd = IssueRefundAction.model_json_schema()
openai_params = tool.to_openai_tool()["function"]["parameters"]
responses_params = tool.to_responses_tool()["parameters"]


def keywords(params):
    props = params.get("properties", {})
    currency = props.get("currency", {})
    return {
        "order_id_pattern": props.get("order_id", {}).get("pattern"),
        "currency_const": currency.get("const")
        or (currency.get("enum") if len(currency.get("enum") or []) == 1 else None),
        "additional_properties": params.get("additionalProperties"),
    }


emit(
    {
        "ok": True,
        "pydantic": keywords(pyd),
        "openai": keywords(openai_params),
        "responses": keywords(responses_params),
        "openai_order_id": openai_params.get("properties", {}).get("order_id"),
        "openai_currency": openai_params.get("properties", {}).get("currency"),
    }
)
