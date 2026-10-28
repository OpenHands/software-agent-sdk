import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import SecretStr

from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.context.condenser.base import NoCondensationAvailableException
from openhands.sdk.context.view import View
from openhands.sdk.event import LLMConvertibleEvent, MessageEvent, SystemPromptEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import LLMContextWindowExceedError
from openhands.sdk.tool.builtins import FinishTool
from tests.sdk.context.view.conftest import (
    create_action_event,
    create_observation_event,
    message_event,
)


def make_llm() -> LLM:
    tokenizers = pytest.importorskip("tokenizers")
    transformers = pytest.importorskip("transformers")
    tokenizer = tokenizers.Tokenizer(
        tokenizers.models.WordLevel({"[UNK]": 0}, unk_token="[UNK]")
    )
    tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    native = transformers.PreTrainedTokenizerFast(
        tokenizer_object=tokenizer, unk_token="[UNK]"
    )
    native.chat_template = (
        "{% for message in messages %}{{ message.role }} {{ message.content }} "
        "{% endfor %}{% if tools %}{{ tools | tojson }}{% endif %}"
        "{% if add_generation_prompt %} assistant{% endif %}"
    )
    llm = LLM(
        model="openai/context-budget-test",
        api_key=SecretStr("test"),
        max_output_tokens=16,
        native_tool_calling=True,
        base_url="http://127.0.0.1:1",
        num_retries=1,
    )
    llm._chat_template_tokenizer = native
    return llm


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("native_tools", [False, True])
def test_full_request_budget_blocks_before_transport(asynchronous, native_tools):
    llm = make_llm()
    messages = [
        Message(role="system", content=[TextContent(text="instructions")]),
        Message(role="user", content=[TextContent(text="task")]),
    ]
    tools = list(FinishTool.create())
    llm = llm.model_copy(update={"native_tool_calling": native_tools})
    history = llm.get_token_count(messages)
    full = llm.get_token_count(messages, tools, add_security_risk_prediction=True)
    assert full > history
    llm = llm.model_copy(update={"max_context_tokens": full + 15})
    with pytest.raises(LLMContextWindowExceedError):
        if asynchronous:
            asyncio.run(
                llm.acompletion(messages, tools, add_security_risk_prediction=True)
            )
        else:
            llm.completion(messages, tools, add_security_risk_prediction=True)


@pytest.mark.parametrize("output_key", ["max_tokens", "max_completion_tokens"])
def test_budget_exact_boundary_and_output_override(output_key):
    llm = make_llm()
    messages = [
        Message(role="system", content=[TextContent(text="instructions")]),
        Message(role="user", content=[TextContent(text="task")]),
    ]
    prompt = llm.get_token_count(messages)
    llm = llm.model_copy(update={"max_context_tokens": prompt + 16})
    llm._prepare_completion_params(messages, None, False, {})
    with pytest.raises(LLMContextWindowExceedError):
        llm._prepare_completion_params(messages, None, False, {output_key: 17})
    restored = LLM.model_validate_json(llm.model_dump_json())
    assert restored.max_context_tokens == prompt + 16


@pytest.mark.parametrize("asynchronous", [False, True])
def test_hard_reset_preserves_task_with_real_summary_transport(asynchronous):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["messages"][0]["role"] == "system"
            payload = json.dumps(
                {
                    "id": "offline-summary",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "context-budget-test",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": "Work completed so far.",
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        llm = make_llm().model_copy(
            update={"base_url": f"http://127.0.0.1:{server.server_port}/v1"}
        )
        condenser = LLMSummarizingCondenser(llm=llm, keep_first=2, max_size=10)
        events: list[LLMConvertibleEvent] = [
            SystemPromptEvent(system_prompt=TextContent(text="Instructions"), tools=[])
        ]
        events.extend(
            MessageEvent(
                source="user",
                llm_message=Message(role="user", content=[TextContent(text=text)]),
            )
            for text in ["ORIGINAL TASK", "old work", "recent work"]
        )
        view = View.from_events(events)
        if asynchronous:
            result = asyncio.run(condenser.ahard_context_reset(view))
        else:
            result = condenser.hard_context_reset(view)
        assert result is not None
        applied = result.apply(view.events)
        assert applied[:2] == view.events[:2]
        assert result.summary_offset == 2
        assert result.summary == "Work completed so far."
        protected_only = View.from_events(events[:2])
        with pytest.raises(NoCondensationAvailableException, match="protected prefix"):
            if asynchronous:
                asyncio.run(condenser.ahard_context_reset(protected_only))
            else:
                condenser.hard_context_reset(protected_only)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_explicit_safety_margin_and_responses_rejection():
    llm = make_llm()
    messages = [
        Message(role="system", content=[TextContent(text="instructions")]),
        Message(role="user", content=[TextContent(text="task")]),
    ]
    prompt = llm.get_token_count(messages)
    llm = llm.model_copy(
        update={"max_context_tokens": prompt + 16, "context_token_safety_margin": 1}
    )
    with pytest.raises(LLMContextWindowExceedError):
        llm._prepare_completion_params(messages, None, False, {})
    with pytest.raises(ValueError, match="only supported for Chat Completions"):
        llm._prepare_responses_params(messages, None, None, None, False, {})
    with pytest.raises(ValueError, match="only supported for Chat Completions"):
        asyncio.run(
            llm._aprepare_responses_params(messages, None, None, None, False, {})
        )


def test_hard_reset_prefix_rounds_forward_over_tool_turn():
    condenser = LLMSummarizingCondenser(llm=make_llm(), keep_first=2, max_size=10)
    events = [
        SystemPromptEvent(system_prompt=TextContent(text="instructions"), tools=[]),
        create_action_event("response", "call"),
        create_observation_event("call"),
        message_event("later task"),
    ]
    view = View.from_events(events)
    assert 2 not in view.manipulation_indices
    assert condenser._hard_reset_prefix(view) == 3


def test_no_budget_leaves_request_unchanged():
    llm = make_llm()
    messages = [
        Message(role="system", content=[TextContent(text="instructions")]),
        Message(role="user", content=[TextContent(text="task")]),
    ]
    _, _, _, kwargs, _ = llm._prepare_completion_params(messages, None, False, {})
    assert kwargs["max_completion_tokens"] == 16
    assert "max_context_tokens" not in kwargs
    assert "context_token_safety_margin" not in kwargs
