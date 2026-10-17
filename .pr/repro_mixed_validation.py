"""Reproduce SDK tool-call batch splitting before provider-specific serialization.

Only LLM transport is scripted; the formatter observer delegates to the real SDK.
--check fails if the original batch is split. --responses and --anthropic inspect
additional serialization paths without sending API requests.
"""

import argparse
import copy
import json
import platform
from importlib.metadata import version
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from litellm import ChatCompletionMessageToolCall
from litellm.litellm_core_utils.prompt_templates.factory import anthropic_messages_pt
from litellm.types.llms.openai import AllMessageValues
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.llm import Message


def scripted_response(*, first: bool) -> ModelResponse:
    calls = None
    if first:
        calls = [
            ChatCompletionMessageToolCall(
                id=call_id,
                type="function",
                function=Function(name="think", arguments=json.dumps(arguments)),
            )
            for call_id, arguments in [
                ("call_A", {"thought": "A succeeds"}),
                ("call_B", {}),
                ("call_C", {"thought": "C succeeds"}),
            ]
        ]
    return ModelResponse(
        id="response_batch" if first else "response_done",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content="One batch." if first else "Done.",
                    tool_calls=calls,
                ),
                finish_reason="tool_calls" if first else "stop",
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--responses", action="store_true")
    parser.add_argument("--anthropic", action="store_true")
    args = parser.parse_args()
    requests: list[list[dict[str, Any]]] = []
    sdk_histories: list[list[Message]] = []
    original_formatter = LLM.format_messages_for_llm

    def observe_formatter(llm: LLM, messages: list[Message]) -> list[dict]:
        sdk_histories.append(copy.deepcopy(messages))
        return original_formatter(llm, messages)

    def fake_completion(
        messages: list[dict[str, Any]], **_kwargs: Any
    ) -> ModelResponse:
        requests.append(copy.deepcopy(messages))
        if len(requests) > 2:
            raise AssertionError("Unexpected extra LLM request")
        return scripted_response(first=len(requests) == 1)

    with TemporaryDirectory(prefix="openhands-batch-repro-") as workspace:
        llm = LLM(
            model="test-model",
            usage_id="repro",
            api_mode="chat",
            api_key=SecretStr("unused-test-key"),
            base_url="http://unused.invalid",
        )
        agent = Agent(llm=llm, tools=[], tool_concurrency_limit=1)
        conversation = Conversation(
            agent=agent,
            workspace=workspace,
            visualizer=None,
            max_iteration_per_run=3,
        )
        try:
            with (
                patch.object(LLM, "format_messages_for_llm", observe_formatter),
                patch(
                    "openhands.sdk.llm.llm.litellm_completion",
                    side_effect=fake_completion,
                ),
            ):
                conversation.send_message("Execute the scripted batch.")
                conversation.run()
        finally:
            conversation.close()

    assert len(requests) == len(sdk_histories) == 2
    common_history = sdk_histories[1]
    history = requests[1]
    assert common_history[0].role == history[0]["role"] == "system"
    common_groups = [
        [call.id for call in message.tool_calls]
        for message in common_history
        if message.tool_calls
    ]
    chat_groups = [
        [call["id"] for call in message["tool_calls"]]
        for message in history
        if message.get("tool_calls")
    ]
    preserved = common_groups == chat_groups == [["call_A", "call_B", "call_C"]]
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "sdk_version": version("openhands-sdk"),
                "litellm_version": version("litellm"),
                "pydantic_version": version("pydantic"),
                "tool_call_groups_in_common_history": common_groups,
                "tool_call_groups_in_next_request": chat_groups,
                "original_batch_preserved": preserved,
            },
            indent=2,
        )
    )
    print("Chat Completions payload at scripted transport boundary:")
    for message in history:
        if message.get("tool_calls"):
            print("assistant", [call["id"] for call in message["tool_calls"]])
        elif message["role"] == "tool":
            print("tool", message["tool_call_id"], message["content"])

    if args.responses:
        instructions, items = llm.format_messages_for_responses(common_history)
        assert instructions
        print("Responses SDK serialization of the SAME history (no API request):")
        print(json.dumps(items, indent=2))
    if args.anthropic:
        provider_history = cast(
            list[AllMessageValues],
            copy.deepcopy([m for m in history if m["role"] != "system"]),
        )
        transformed = anthropic_messages_pt(
            provider_history, model="claude-sonnet-4-5", llm_provider="anthropic"
        )
        print("Anthropic LiteLLM serialization of the SAME history (no API request):")
        print(json.dumps(transformed, indent=2))
    return 1 if args.check and not preserved else 0


if __name__ == "__main__":
    raise SystemExit(main())
