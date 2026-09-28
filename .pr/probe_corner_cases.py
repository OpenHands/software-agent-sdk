"""Offline lifecycle probes, with optional merge-base dispatch for comparison.

The base comparison restores only response_dispatch.py in memory. All other SDK
production files are unchanged by this PR. No provider API is called.
"""

import argparse
import copy
import json
import subprocess
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from litellm.types.utils import ModelResponse

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import ActionEvent, Event, ObservationEvent
from openhands.sdk.llm import LLMResponse, Message, MessageToolCall, TextContent
from openhands.sdk.security.confirmation_policy import AlwaysConfirm, NeverConfirm
from openhands.sdk.tool.builtins.think import (
    ThinkAction,
    ThinkExecutor,
    ThinkObservation,
)


BASE = "3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14"


def probe(scenario: str) -> dict[str, Any]:
    llm = LLM(model="gpt-4o", api_key="unused-test-key", usage_id="corner-probe")
    calls = [
        MessageToolCall(
            id=f"call_{i}",
            name="think",
            arguments=json.dumps({"thought": str(i)}),
            origin="completion",
        )
        for i in range(3)
    ]
    if scenario != "action_callback":
        calls[1].arguments = "{}"
    if scenario == "finish_tail":
        calls[0].name = "finish"
        calls[0].arguments = '{"message": "done"}'
    if scenario == "action_callback":
        calls[0].name = "nonexistent_tool"
    requests: list[list[Message]] = []
    executions: Counter[str] = Counter()
    callback_failed = False
    callback_exception: str | None = None

    def generate(_self: LLM, *, messages: list[Message], **_kwargs: Any) -> LLMResponse:
        requests.append(copy.deepcopy(messages))
        assert len(requests) <= 2
        return LLMResponse(
            message=Message(role="assistant", content=[], tool_calls=calls)
            if len(requests) == 1
            else Message(role="assistant", content=[TextContent(text="done")]),
            metrics=llm.metrics.get_snapshot(),
            raw_response=ModelResponse(id="probe"),
        )

    def execute(
        _self: ThinkExecutor,
        action: ThinkAction,
        _conversation: object = None,
    ) -> ThinkObservation:
        executions[action.thought] += 1
        return ThinkObservation.from_text("completed")

    def callback(event: Event) -> None:
        nonlocal callback_failed
        if callback_failed:
            return
        if scenario == "result_callback" and isinstance(event, ObservationEvent):
            callback_failed = True
            raise RuntimeError("synthetic observation callback failure")
        if scenario == "action_callback" and isinstance(event, ActionEvent):
            callback_failed = True
            raise ValueError("synthetic action callback failure")

    with TemporaryDirectory() as temp, ExitStack() as stack:
        root = Path(temp)
        stack.enter_context(patch.object(LLM, "generate", generate))
        stack.enter_context(patch.object(ThinkExecutor, "__call__", execute))
        conversation = Conversation(
            agent=Agent(llm=llm, tools=[]),
            workspace=root,
            persistence_dir=root / "state",
            callbacks=[callback],
            visualizer=None,
            max_iteration_per_run=3,
        )
        try:
            conversation.send_message("synthetic probe")
            if scenario == "pending_resume":
                conversation.set_confirmation_policy(AlwaysConfirm())
            try:
                conversation.run()
            except Exception as exc:
                callback_exception = type(exc).__name__
            after_first = dict(executions)
            pending = [
                event.tool_call_id
                for event in ConversationState.get_unmatched_actions(
                    conversation.state.active_branch()
                )
            ]
            if scenario == "pending_resume":
                conversation_id = conversation.state.id
                conversation.close()
                conversation = LocalConversation(
                    agent=None,
                    workspace=root,
                    persistence_dir=root / "state",
                    conversation_id=conversation_id,
                    visualizer=None,
                    max_iteration_per_run=3,
                )
                conversation.set_confirmation_policy(NeverConfirm())
            if scenario == "finish_tail":
                conversation.send_message("continue")
            if scenario != "action_callback":
                conversation.run()
            next_groups = [
                [call.id for call in m.tool_calls] for m in requests[1] if m.tool_calls
            ]
            return {
                "case": scenario,
                "callback_exception": callback_exception,
                "pending_after_first_run": pending,
                "executions_after_first_run": after_first,
                "executions_after_second_run": dict(executions),
                "next_request_tool_call_groups": next_groups,
            }
        finally:
            conversation.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dispatch", action="store_true")
    args = parser.parse_args()
    with ExitStack() as stack:
        if args.base_dispatch:
            source = subprocess.check_output(
                [
                    "git",
                    "show",
                    f"{BASE}:openhands-sdk/openhands/sdk/agent/response_dispatch.py",
                ],
                text=True,
            )
            namespace: dict[str, Any] = {"__name__": "baseline_response_dispatch"}
            exec(compile(source, "baseline_response_dispatch.py", "exec"), namespace)
            original = namespace["ResponseDispatchMixin"]
            stack.enter_context(
                patch.object(Agent, "_handle_tool_calls", original._handle_tool_calls)
            )
            stack.enter_context(
                patch.object(Agent, "_ahandle_tool_calls", original._ahandle_tool_calls)
            )
        print(
            json.dumps(
                {
                    "dispatch": "base" if args.base_dispatch else "pr",
                    "results": [
                        probe(case)
                        for case in (
                            "pending_resume",
                            "finish_tail",
                            "result_callback",
                            "action_callback",
                        )
                    ],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
