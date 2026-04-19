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
    # OutputFunctionToolCall variant (LiteLLM typed)
    ofc = OutputFunctionToolCall(
        type="function_call",
        name="x",
        arguments="{}",
        call_id="fc_1",
        id="fc_1",
        status="completed",
    )
    mtc1 = MessageToolCall.from_responses_function_call(ofc)
    assert mtc1.id == "fc_1" and mtc1.origin == "responses"

    # ResponseFunctionToolCall variant (OpenAI typed)
    rfc = ResponseFunctionToolCall(
        type="function_call", name="y", arguments="{}", call_id="fc_2", id="fc_2"
    )
    mtc2 = MessageToolCall.from_responses_function_call(rfc)  # type: ignore[arg-type]
    assert mtc2.id == "fc_2" and mtc2.name == "y"


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
    # Adds fc_ prefix when missing
    mtc = MessageToolCall(id="123", name="do", arguments="{}", origin="responses")
    d = mtc.to_responses_dict()
    assert d["id"].startswith("fc_") and d["call_id"].startswith("fc_")

    # Keeps existing fc_ prefix
    mtc2 = MessageToolCall(id="fc_99", name="do", arguments="{}", origin="responses")
    d2 = mtc2.to_responses_dict()
    assert d2["id"] == "fc_99" and d2["call_id"] == "fc_99"


def test_malformed_arguments_sanitized_in_to_chat_dict():
    """Malformed JSON arguments must be replaced with '{}' in to_chat_dict.

    llama-server (and potentially other inference backends) parse the
    arguments field of tool calls in conversation history. If the arguments
    are invalid JSON, the server rejects the entire prompt causing an
    infinite retry loop (see issue #2887).
    """
    malformed = '{"command":"create","path":"/tmp/x","file_text":"unterminated'
    mtc = MessageToolCall(
        id="call_bad", name="file_editor", arguments=malformed, origin="completion"
    )
    d = mtc.to_chat_dict()
    # Must be valid JSON
    args_str = d["function"]["arguments"]
    parsed = json.loads(args_str)
    assert isinstance(parsed, dict)
    assert args_str == "{}"


def test_malformed_arguments_sanitized_in_to_responses_dict():
    """Same as above but for the Responses API serialization path."""
    malformed = '{"key": "value'
    mtc = MessageToolCall(
        id="fc_bad", name="tool", arguments=malformed, origin="responses"
    )
    d = mtc.to_responses_dict()
    parsed = json.loads(d["arguments"])
    assert isinstance(parsed, dict)
    assert d["arguments"] == "{}"


def test_valid_arguments_unchanged_in_serialization():
    """Valid JSON arguments must pass through untouched."""
    valid = json.dumps({"command": "view", "path": "/tmp"})
    mtc = MessageToolCall(
        id="call_ok", name="file_editor", arguments=valid, origin="completion"
    )
    assert mtc.to_chat_dict()["function"]["arguments"] == valid
    mtc2 = MessageToolCall(
        id="fc_ok", name="file_editor", arguments=valid, origin="responses"
    )
    assert mtc2.to_responses_dict()["arguments"] == valid
