import json
from types import SimpleNamespace

import pytest
from litellm import ChatCompletionMessageToolCall
from litellm.types.responses.main import OutputFunctionToolCall
from litellm.types.utils import Function
from openai.types.responses.response_function_tool_call import (
    ResponseFunctionToolCall,
)

from openhands.sdk.llm.message import MessageToolCall


def test_from_chat_tool_call_success():
    tool_call = ChatCompletionMessageToolCall(
        id="call_123",
        type="function",
        function=Function(name="do_thing", arguments="{}"),
    )
    mtc = MessageToolCall.from_chat_tool_call(tool_call)
    assert mtc.id == "call_123"
    assert mtc.name == "do_thing"
    assert mtc.arguments == "{}"
    assert mtc.origin == "completion"


def test_from_chat_tool_call_non_function_type_raises():
    bogus = SimpleNamespace(
        id="x", type="not_function", function=Function(name="n", arguments="{}")
    )
    with pytest.raises(ValueError, match="Unsupported tool call type"):
        MessageToolCall.from_chat_tool_call(bogus)  # type: ignore[arg-type]


def test_from_chat_tool_call_missing_function_raises():
    bogus = SimpleNamespace(id="x", type="function", function=None)
    with pytest.raises(ValueError, match="tool_call.function is None"):
        MessageToolCall.from_chat_tool_call(bogus)  # type: ignore[arg-type]


def test_from_chat_tool_call_missing_function_name_raises():
    bogus_func = SimpleNamespace(name=None, arguments="{}")
    bogus = SimpleNamespace(id="x", type="function", function=bogus_func)
    with pytest.raises(ValueError, match="tool_call.function.name is None"):
        MessageToolCall.from_chat_tool_call(bogus)  # type: ignore[arg-type]


def test_from_responses_function_call_output_and_response_variants():
    ofc = OutputFunctionToolCall(
        type="function_call",
        name="x",
        arguments="{}",
        call_id="call_xyz789",
        id="fc_abc123",
        status="completed",
    )
    mtc1 = MessageToolCall.from_responses_function_call(ofc)
    assert mtc1.id == "call_xyz789"
    assert mtc1.responses_item_id == "fc_abc123"
    assert mtc1.origin == "responses"

    rfc = ResponseFunctionToolCall(
        type="function_call", name="y", arguments="{}", call_id="call_2", id="fc_2"
    )
    mtc2 = MessageToolCall.from_responses_function_call(rfc)  # type: ignore[arg-type]
    assert mtc2.id == "call_2"
    assert mtc2.responses_item_id == "fc_2"
    assert mtc2.name == "y"


def test_from_responses_function_call_missing_ids_raises():
    # Neither call_id nor id provided
    bogus = SimpleNamespace(
        type="function_call", name="x", arguments="{}", call_id=None, id=None
    )
    with pytest.raises(ValueError, match="missing call_id/id"):
        MessageToolCall.from_responses_function_call(bogus)  # type: ignore[arg-type]


def test_from_responses_function_call_missing_name_raises():
    bogus = SimpleNamespace(
        type="function_call", name="", arguments="{}", call_id="fc_1", id=None
    )
    with pytest.raises(ValueError, match="missing name"):
        MessageToolCall.from_responses_function_call(bogus)  # type: ignore[arg-type]


def test_to_responses_dict_prefix_and_stringify_arguments():
    # No responses_item_id: synthesize `fc_{id}` for the item id; call_id verbatim.
    mtc = MessageToolCall(id="123", name="do", arguments="{}", origin="responses")
    d = mtc.to_responses_dict()
    assert d["id"] == "fc_123" and d["call_id"] == "123"

    # id already fc-prefixed: pass through unchanged.
    mtc2 = MessageToolCall(id="fc_99", name="do", arguments="{}", origin="responses")
    d2 = mtc2.to_responses_dict()
    assert d2["id"] == "fc_99" and d2["call_id"] == "fc_99"

    # Ensure dict arguments are stringified
    mtc3 = MessageToolCall.model_construct(
        id="5", name="do", arguments={"a": 1}, origin="responses"
    )
    d3 = mtc3.to_responses_dict()
    assert isinstance(d3["arguments"], str)
    assert json.loads(d3["arguments"]) == {"a": 1}


def test_responses_function_call_round_trip_preserves_ids():
    """Regression for #2905: Responses ingest → replay must be byte-identical."""
    original = ResponseFunctionToolCall(
        type="function_call",
        id="fc_abc123",
        call_id="call_xyz789",
        name="bash",
        arguments='{"cmd": "ls"}',
    )
    mtc = MessageToolCall.from_responses_function_call(original)  # type: ignore[arg-type]
    assert mtc.to_responses_dict() == {
        "type": "function_call",
        "id": "fc_abc123",
        "call_id": "call_xyz789",
        "name": "bash",
        "arguments": '{"cmd": "ls"}',
    }
