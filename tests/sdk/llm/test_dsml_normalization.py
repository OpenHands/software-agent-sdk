"""Tests for DeepSeek V4 DSML markup normalization and stream filtering."""

import glob
import json
import logging
import os
from unittest.mock import MagicMock

import pytest
from litellm import (
    ChatCompletionMessageToolCall,
    CustomStreamWrapper,
    ModelResponse,
    ModelResponseStream,
)
from litellm.types.utils import (
    Choices,
    Delta,
    Function,
    Message as LiteLLMMessage,
    StreamingChoices,
)

from openhands.sdk.agent.response_dispatch import LLMResponseType, classify_response
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.utils.dsml import (
    DSMLStreamFilter,
    has_dsml_markers,
    is_deepseek_v4_model,
    normalize_deepseek_v4_response,
    parse_dsml_tool_calls,
)


# ---------------------------------------------------------------------------
# 1. Model detection and marker checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,expected",
    [
        ("openai/deepseek-v4-flash", True),
        ("deepseek/deepseek-v4-pro", True),
        ("deepseek-v4", True),
        ("DeepSeek-V4-Flash", True),
        ("deepseek_v4", True),
        ("deepseek/deepseek-chat", False),
        ("deepseek/deepseek-reasoner", False),
        ("openai/gpt-4o", False),
        ("claude-3-5-sonnet", False),
        (None, False),
        ("", False),
    ],
)
def test_is_deepseek_v4_model(model, expected):
    assert is_deepseek_v4_model(model) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("<｜DSML｜tool_calls>", True),
        ("<|DSML|tool_calls>", True),
        ("<｜｜DSML｜｜r=execute_bash>", True),
        ("<||DSML||r=execute_bash>", True),
        ("Some normal text without markup", False),
        ("<function=execute_bash>", False),
        (None, False),
        ("", False),
    ],
)
def test_has_dsml_markers(text, expected):
    assert has_dsml_markers(text) is expected


# ---------------------------------------------------------------------------
# 2. Standard DSML Parsing: types, parameters, and multiple invokes
# ---------------------------------------------------------------------------


def test_parse_standard_dsml_types_and_parameters():
    content = """I will execute the inspection tool.

<｜DSML｜tool_calls>
<invoke name="inspect_env">
<parameter name="command" string="true">ls -la /workspace && echo '<test>'</parameter>
<parameter name="timeout" string="false">30</parameter>
<parameter name="flag" string="false">true</parameter>
<parameter name="neg_flag" string="false">false</parameter>
<parameter name="ratio" string="false">3.14</parameter>
<parameter name="items" string="false">["alpha", "beta"]</parameter>
<parameter name="config" string="false">{"port": 8000, "debug": true}</parameter>
<parameter name="empty_val" string="false">null</parameter>
</invoke>
</｜DSML｜tool_calls>

Please review the output."""

    calls, cleaned = parse_dsml_tool_calls(content)

    assert len(calls) == 1
    call = calls[0]
    assert call.id.startswith("call_dsml_")
    assert call.type == "function"
    assert call.function.name == "inspect_env"

    args = json.loads(call.function.arguments)
    assert args["command"] == "ls -la /workspace && echo '<test>'"
    assert args["timeout"] == 30
    assert args["flag"] is True
    assert args["neg_flag"] is False
    assert args["ratio"] == 3.14
    assert args["items"] == ["alpha", "beta"]
    assert args["config"] == {"port": 8000, "debug": True}
    assert args["empty_val"] is None

    assert "I will execute the inspection tool." in cleaned
    assert "Please review the output." in cleaned
    assert "<｜DSML｜" not in cleaned


def test_parse_standard_dsml_parallel_invokes_and_implicit_closures():
    content = """<|DSML|tool_calls>
<invoke name="read_file">
<parameter name="path" string="true">/app/src/index.ts
<parameter name="offset" string="false">100
<invoke name="run_command">
<parameter name="cmd" string="true">wc -l /app/src/index.ts</parameter>
</invoke>
</|DSML|tool_calls>"""

    calls, cleaned = parse_dsml_tool_calls(content)

    assert len(calls) == 2

    # First invoke had implicit parameter closure and implicit invoke closure
    assert calls[0].function.name == "read_file"
    args1 = json.loads(calls[0].function.arguments)
    assert args1["path"] == "/app/src/index.ts"
    assert args1["offset"] == 100

    # Second invoke
    assert calls[1].function.name == "run_command"
    args2 = json.loads(calls[1].function.arguments)
    assert args2["cmd"] == "wc -l /app/src/index.ts"

    assert cleaned == ""


def test_parse_multiline_parameter_with_quotes_and_brackets():
    multiline_script = """def test_func():
    items = ["<one>", "<two>"]
    if True:
        print("Hello 'world'!")
"""
    content = f"""<｜DSML｜tool_calls>
<invoke name="write_file">
<parameter name="path" string="true">/tmp/script.py</parameter>
<parameter name="content" string="true">{multiline_script}</parameter>
</invoke>
</｜DSML｜tool_calls>"""

    calls, cleaned = parse_dsml_tool_calls(content)
    assert len(calls) == 1
    args = json.loads(calls[0].function.arguments)
    assert args["path"] == "/tmp/script.py"
    assert args["content"] == multiline_script.strip("\r\n")


# ---------------------------------------------------------------------------
# 3. Log Parity: Test against the 23 CVE benchmark completions
# ---------------------------------------------------------------------------


def test_all_cve_benchmark_completions_parse_successfully():
    completions_dir = (
        "/Users/aibot/Downloads/cyberSecurityBenchmark/902AILAB/eval-executor/repo/"
        "runs/concurrent-cve-2026-19373-20260903T122344Z/model-cve-2026-19373-20260903T122443Z/agent/completions"
    )
    if not os.path.isdir(completions_dir):
        pytest.skip("Completions directory not found on local path")

    files = sorted(glob.glob(os.path.join(completions_dir, "*.json")))
    assert len(files) > 0

    dsml_tested = 0
    for fpath in files:
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        msg = data["response"]["choices"][0]["message"]
        content = msg.get("content") or ""
        if "DSML" in content:
            dsml_tested += 1
            calls, cleaned = parse_dsml_tool_calls(content)
            assert len(calls) >= 1, f"Failed to parse DSML in {os.path.basename(fpath)}"
            assert calls[0].function.name == "execute_bash"
            args = json.loads(calls[0].function.arguments)
            assert "command" in args
            assert len(args["command"]) > 0

    assert dsml_tested == 23


# ---------------------------------------------------------------------------
# 4. Guardrails & Safeguards
# ---------------------------------------------------------------------------


def test_non_deepseek_model_is_not_normalized():
    model_response = ModelResponse(
        id="resp-1",
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content=(
                        "<｜DSML｜tool_calls><invoke name='bash'>"
                        "<parameter name='cmd' string='true'>ls</parameter>"
                        "</invoke></｜DSML｜tool_calls>"
                    ),
                ),
            )
        ],
    )

    result = normalize_deepseek_v4_response(model_response, model="gpt-4o")
    assert result.choices[0].message.tool_calls is None
    assert result.choices[0].message.content is not None
    assert "<｜DSML｜" in result.choices[0].message.content


def test_already_structured_tool_calls_are_untouched():
    existing_call = ChatCompletionMessageToolCall(
        id="call_orig_123",
        type="function",
        function=Function(name="existing_tool", arguments='{"param": "val"}'),
    )
    model_response = ModelResponse(
        id="resp-2",
        choices=[
            Choices(
                finish_reason="tool_calls",
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content=(
                        "<｜DSML｜tool_calls><invoke name='bash'>"
                        "<parameter name='cmd' string='true'>ls</parameter>"
                        "</invoke></｜DSML｜tool_calls>"
                    ),
                    tool_calls=[existing_call],
                ),
            )
        ],
    )

    result = normalize_deepseek_v4_response(
        model_response, model="openai/deepseek-v4-flash"
    )
    tool_calls = result.choices[0].message.tool_calls
    assert tool_calls is not None
    assert len(tool_calls) == 1
    assert tool_calls[0].id == "call_orig_123"


def test_malformed_unrecoverable_dsml_warns_and_avoids_partial_execution(caplog):
    malformed_content = "<｜DSML｜tool_calls> <<<broken non-xml syntax without invoke"
    with caplog.at_level(logging.WARNING):
        calls, cleaned = parse_dsml_tool_calls(malformed_content)

    assert len(calls) == 0
    assert cleaned == malformed_content
    assert any(
        "Malformed DeepSeek V4 DSML markup detected" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# 5. Stream Filter
# ---------------------------------------------------------------------------


def test_dsml_stream_filter_suppresses_dsml_and_preserves_thoughts():
    received_tokens = []

    def on_token(chunk):
        if chunk.choices and chunk.choices[0].delta.content:
            received_tokens.append(chunk.choices[0].delta.content)

    stream_filter = DSMLStreamFilter(on_token)

    def make_chunk(text):
        return ModelResponseStream(
            id="stream-1",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(content=text, role="assistant"),
                    finish_reason=None,
                )
            ],
        )

    # Simulating arbitrary character chunk streaming
    chunks = [
        make_chunk("Let me "),
        make_chunk("inspect the directory.\n\n"),
        make_chunk("<"),
        make_chunk("｜"),
        make_chunk("DSML"),
        make_chunk("｜tool_calls>\n"),
        make_chunk('<invoke name="execute_bash">\n'),
        make_chunk('<parameter name="command" string="true">ls -la</parameter>\n'),
        make_chunk("</invoke>\n"),
        make_chunk("</｜DSML｜tool_calls>"),
        make_chunk("\nExecution scheduled."),
    ]

    for c in chunks:
        filtered = stream_filter.filter_chunk(c)
        if filtered is not None:
            on_token(filtered)

    remaining = stream_filter.flush_remaining()
    if remaining:
        received_tokens.append(remaining)

    combined_stream_output = "".join(received_tokens)
    assert "Let me inspect the directory.\n\n" in combined_stream_output
    assert "\nExecution scheduled." in combined_stream_output
    assert "DSML" not in combined_stream_output
    assert "execute_bash" not in combined_stream_output


def test_dsml_stream_filter_non_dsml_angle_brackets():
    received_tokens = []

    def on_token(chunk):
        if chunk.choices and chunk.choices[0].delta.content:
            received_tokens.append(chunk.choices[0].delta.content)

    stream_filter = DSMLStreamFilter(on_token)

    def make_chunk(text):
        return ModelResponseStream(
            id="stream-2",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(content=text, role="assistant"),
                    finish_reason=None,
                )
            ],
        )

    chunks = [
        make_chunk("Condition: "),
        make_chunk("a < b "),
        make_chunk("and c > d. "),
        make_chunk("<tag>inner</tag>"),
    ]

    for c in chunks:
        filtered = stream_filter.filter_chunk(c)
        if filtered is not None:
            on_token(filtered)

    remaining = stream_filter.flush_remaining()
    if remaining:
        received_tokens.append(remaining)

    combined = "".join(received_tokens)
    assert combined == "Condition: a < b and c > d. <tag>inner</tag>"


# ---------------------------------------------------------------------------
# 6. LLM Completion Layer & Agent Response Dispatch Integration
# ---------------------------------------------------------------------------


def test_llm_build_completion_result_normalizes_dsml():
    raw_response = ModelResponse(
        id="resp-cve-14",
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content=(
                        "Let me examine the full compiled distribution code:\n\n"
                        "<｜｜DSML｜｜r=execute_bash>\n"
                        "<｜｜DSML｜｜m>cat /app/dist/index.js</｜｜DSML｜｜m>\n"
                        "</｜｜DSML｜｜r>"
                    ),
                ),
            )
        ],
    )

    llm = LLM(
        model="openai/deepseek-v4-flash",
        api_key="sk-test",
        stream=True,
    )

    # Validated chat response converts DSML before building LLMResponse
    validated_resp = llm._validate_chat_response(
        raw_response,
        use_mock_tools=False,
        formatted_messages=[],
        cc_tools=[],
        add_security_risk_prediction=False,
    )

    assert validated_resp.choices[0].finish_reason == "tool_calls"
    assert validated_resp.choices[0].message.tool_calls is not None
    assert len(validated_resp.choices[0].message.tool_calls) == 1

    llm_response = llm._build_completion_result(validated_resp)
    assert llm_response.message.tool_calls is not None
    assert len(llm_response.message.tool_calls) == 1

    tool_call = llm_response.message.tool_calls[0]
    assert tool_call.name == "execute_bash"
    assert json.loads(tool_call.arguments) == {"command": "cat /app/dist/index.js"}

    # Classifies as TOOL_CALLS rather than CONTENT, preventing premature FINISHED
    classification = classify_response(llm_response.message)
    assert classification == LLMResponseType.TOOL_CALLS

    # Text outside DSML is preserved in message content
    assert len(llm_response.message.content) == 1
    first_item = llm_response.message.content[0]
    assert isinstance(first_item, TextContent)
    assert first_item.text == "Let me examine the full compiled distribution code:"


def test_llm_completion_and_streaming_end_to_end(monkeypatch):
    dsml_text = (
        "Thinking about command...\n\n"
        "<｜DSML｜tool_calls>\n"
        '<invoke name="execute_bash">\n'
        '<parameter name="command" string="true">ls -la</parameter>\n'
        "</invoke>\n"
        "</｜DSML｜tool_calls>"
    )

    llm = LLM(
        model="openai/deepseek-v4-flash",
        api_key="sk-test",
        stream=True,
    )

    # 1. Non-streaming mock
    def mock_completion(*args, **kwargs):
        return ModelResponse(
            id="resp-test-sync",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=LiteLLMMessage(role="assistant", content=dsml_text),
                )
            ],
        )

    monkeypatch.setattr("openhands.sdk.llm.llm.litellm_completion", mock_completion)

    resp = llm.completion(
        messages=[Message(role="user", content=[TextContent(text="Run ls")])]
    )
    assert resp.message.tool_calls is not None
    assert len(resp.message.tool_calls) == 1
    assert resp.message.tool_calls[0].name == "execute_bash"
    assert json.loads(resp.message.tool_calls[0].arguments) == {"command": "ls -la"}
    first_item = resp.message.content[0]
    assert isinstance(first_item, TextContent)
    assert first_item.text == "Thinking about command..."

    # 2. Streaming mock with on_token
    token_feed = [
        "Thinking about command...\n\n",
        "<",
        "｜DSML｜tool",
        '_calls>\n<invoke name="execute_bash">\n',
        '<parameter name="command" string="true">ls -la</parameter>\n',
        "</invoke>\n</｜DSML｜tool_calls>",
    ]

    def mock_streaming_completion(*args, **kwargs):
        def stream_gen():
            for t in token_feed:
                yield ModelResponseStream(
                    id="stream-test",
                    choices=[
                        StreamingChoices(
                            index=0,
                            delta=Delta(content=t, role="assistant"),
                            finish_reason=None,
                        )
                    ],
                )

        return CustomStreamWrapper(
            completion_stream=stream_gen(),
            model="openai/deepseek-v4-flash",
            custom_llm_provider="openai",
            logging_obj=MagicMock(),
        )

    monkeypatch.setattr(
        "openhands.sdk.llm.llm.litellm_completion", mock_streaming_completion
    )
    final_resp = ModelResponse(
        id="stream-final",
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(role="assistant", content=dsml_text),
            )
        ],
    )
    monkeypatch.setattr(
        "openhands.sdk.llm.llm.litellm.stream_chunk_builder",
        lambda chunks, messages=None: final_resp,
    )

    seen_tokens = []

    def on_token_cb(chunk):
        if chunk.choices and chunk.choices[0].delta.content:
            seen_tokens.append(chunk.choices[0].delta.content)

    resp_streamed = llm.completion(
        messages=[Message(role="user", content=[TextContent(text="Run ls")])],
        on_token=on_token_cb,
    )

    # Streaming tokens never received DSML tags
    streamed_combined = "".join(seen_tokens)
    assert "Thinking about command..." in streamed_combined
    assert "DSML" not in streamed_combined
    assert "execute_bash" not in streamed_combined

    # Aggregated response still has parsed tool_calls and identical result
    # to non-streaming
    assert resp_streamed.message.tool_calls is not None
    assert len(resp_streamed.message.tool_calls) == 1
    assert resp_streamed.message.tool_calls[0].name == "execute_bash"
    assert json.loads(resp_streamed.message.tool_calls[0].arguments) == {
        "command": "ls -la"
    }
    first_item = resp_streamed.message.content[0]
    assert isinstance(first_item, TextContent)
    assert first_item.text == "Thinking about command..."


@pytest.mark.asyncio
async def test_llm_acompletion_streaming_end_to_end(monkeypatch):
    dsml_text = (
        "Let me check:\n\n"
        "<｜｜DSML｜｜r=execute_bash>\n"
        "<｜｜DSML｜｜m>pwd</｜｜DSML｜｜m>\n"
        "</｜｜DSML｜｜r>"
    )

    llm = LLM(
        model="openai/deepseek-v4-flash",
        api_key="sk-test",
        stream=True,
    )

    token_feed = [
        "Let me check:\n\n",
        "<｜｜DSML｜｜r=execute_bash>\n",
        "<｜｜DSML｜｜m>pwd</｜｜DSML｜｜m>\n",
        "</｜｜DSML｜｜r>",
    ]

    async def mock_streaming_acompletion(*args, **kwargs):
        async def astream_gen():
            for t in token_feed:
                yield ModelResponseStream(
                    id="stream-test-async",
                    choices=[
                        StreamingChoices(
                            index=0,
                            delta=Delta(content=t, role="assistant"),
                            finish_reason=None,
                        )
                    ],
                )

        return astream_gen()

    monkeypatch.setattr(
        "openhands.sdk.llm.llm.litellm_acompletion", mock_streaming_acompletion
    )
    final_resp_async = ModelResponse(
        id="stream-final-async",
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(role="assistant", content=dsml_text),
            )
        ],
    )
    monkeypatch.setattr(
        "openhands.sdk.llm.llm.litellm.stream_chunk_builder",
        lambda chunks, messages=None: final_resp_async,
    )

    seen_tokens = []

    async def on_token_cb(chunk):
        if chunk.choices and chunk.choices[0].delta.content:
            seen_tokens.append(chunk.choices[0].delta.content)

    resp_streamed = await llm.acompletion(
        messages=[Message(role="user", content=[TextContent(text="Check pwd")])],
        on_token=on_token_cb,
    )

    streamed_combined = "".join(seen_tokens)
    assert "Let me check:" in streamed_combined
    assert "DSML" not in streamed_combined

    assert resp_streamed.message.tool_calls is not None
    assert len(resp_streamed.message.tool_calls) == 1
    assert resp_streamed.message.tool_calls[0].name == "execute_bash"
    assert json.loads(resp_streamed.message.tool_calls[0].arguments) == {
        "command": "pwd"
    }
    first_item = resp_streamed.message.content[0]
    assert isinstance(first_item, TextContent)
    assert first_item.text == "Let me check:"
