"""Write persisted conversations with whichever openhands-sdk is installed.

This file runs under *old* releases, so it must stay standalone (no imports
from ``tests``) and use only public SDK API that exists since v1.12.0, the
first release that ships ``TestLLM``.

For each scenario it writes a conversation to ``<output>/<scenario>/`` and
then probes a throwaway copy: resume, send ``FOLLOW_UP``, and record the
messages the agent sends to its LLM. That record, the old release's view of
its own conversation, goes into ``manifest.json``.
``tests/cross/test_persisted_conversation_compat.py`` runs the same
``probe()`` under the current SDK and requires the same view.

Usage::

    python generate.py OUTPUT_DIR

``.github/scripts/check_persisted_conversation_compat.py`` runs this inside a
virtualenv with a published release installed, both in CI and to add the
checked-in fixtures under this directory.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import PrivateAttr

from openhands.sdk import LLM, Agent, Conversation, Message, TextContent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.security.confirmation_policy import AlwaysConfirm, NeverConfirm
from openhands.sdk.testing import TestLLM


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation


FOLLOW_UP = "Follow-up after the upgrade."
RESUMED_REPLY = "Resumed."


class RecordingLLM(TestLLM):
    """TestLLM that keeps the messages of every completion call."""

    __test__ = False
    _seen: list[list[Message]] = PrivateAttr(default_factory=list)

    def completion(self, messages: list[Message], *args: Any, **kwargs: Any):
        self._seen.append(list(messages))
        return super().completion(messages, *args, **kwargs)


def reply(text: str) -> Message:
    return Message(role="assistant", content=[TextContent(text=text)])


def call(call_id: str, name: str, **arguments: Any) -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[
            MessageToolCall(
                id=call_id,
                name=name,
                arguments=json.dumps(arguments),
                origin="completion",
            )
        ],
    )


def parallel(*calls: Message) -> Message:
    tool_calls = [tc for message in calls for tc in message.tool_calls or []]
    return Message(
        role="assistant", content=[TextContent(text="")], tool_calls=tool_calls
    )


def agent_llm(replies: list[Message]) -> RecordingLLM:
    scripted: list[Message | Exception] = [*replies]
    llm = RecordingLLM.from_messages(scripted, usage_id="agent")
    assert isinstance(llm, RecordingLLM)
    return llm


def build_agent(scenario: str, llm: LLM) -> Agent:
    """The agent a scenario runs with, before and after the upgrade."""
    condenser = None
    if scenario == "condensed":
        summary = TestLLM.from_messages(
            [reply("Summary: the user greeted twice.")], usage_id="condenser"
        )
        condenser = LLMSummarizingCondenser(llm=summary, max_size=100, keep_first=1)
    return Agent(
        llm=llm,
        tools=[],
        include_default_tools=["FinishTool", "ThinkTool"],
        condenser=condenser,
    )


# Each scenario drives a conversation from the outside, as a client would.
# Keep scripts deterministic: the conversation id and every LLM reply are fixed.
def _tool_round_trip(conversation: BaseConversation) -> None:
    conversation.send_message("Think about the plan.")
    conversation.run()
    conversation.send_message("Anything else?")
    conversation.run()


def _finished(conversation: BaseConversation) -> None:
    conversation.send_message("Say goodbye.")
    conversation.run()


def _awaiting_confirmation(conversation: BaseConversation) -> None:
    conversation.set_confirmation_policy(AlwaysConfirm())
    conversation.send_message("Think, but ask first.")
    conversation.run()


def _condensed(conversation: BaseConversation) -> None:
    for text in ("Hello.", "Hello again.", "Still there?"):
        conversation.send_message(text)
        conversation.run()
    conversation.condense()


SCENARIOS: dict[str, tuple[Callable[[BaseConversation], None], list[Message]]] = {
    "tool_round_trip": (
        _tool_round_trip,
        [
            call("call_think", "think", thought="Plan: answer briefly."),
            reply("Planned."),
            reply("Nothing else."),
        ],
    ),
    "finished": (
        _finished,
        [call("call_finish", "finish", message="Goodbye.")],
    ),
    "awaiting_confirmation": (
        _awaiting_confirmation,
        # A lone think or finish call never asks for confirmation; two do.
        [
            parallel(
                call("call_think_1", "think", thought="First, needs approval."),
                call("call_think_2", "think", thought="Second, needs approval."),
            )
        ],
    ),
    "condensed": (
        _condensed,
        [reply("Hi."), reply("Hi again."), reply("Yes.")],
    ),
}


def conversation_id(scenario: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"openhands-compat/{scenario}")


def _open(scenario: str, root: Path, llm: LLM):
    return Conversation(
        agent=build_agent(scenario, llm),
        workspace=str(root / "workspace"),
        persistence_dir=str(root),
        conversation_id=conversation_id(scenario),
        visualizer=None,
    )


def normalize(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the LLM is told, minus wording that may change on purpose.

    Takes message dumps, so it reads what any release recorded. Observation
    wording is a product decision and is left out. Roles, user and assistant
    text, tool calls and the call each tool result answers must survive an
    upgrade. If the current SDK changes one of those on purpose, teach this
    function to ignore it rather than dropping a fixture.
    """
    view = []
    for message in messages:
        entry: dict[str, Any] = {"role": message["role"]}
        if message["role"] == "tool":
            entry["tool_call_id"] = message["tool_call_id"]
        else:
            entry["text"] = [part["text"] for part in message["content"]]
        if message.get("tool_calls"):
            entry["tool_calls"] = [
                [c["id"], c["name"], json.loads(c["arguments"])]
                for c in message["tool_calls"]
            ]
        view.append(entry)
    return view


def probe(scenario: str, root: Path) -> list[dict[str, Any]]:
    """Resume the conversation under ``root`` and return the non-system
    messages of the agent's next LLM call.

    A conversation paused for confirmation gets its actions approved, as a
    user clicking approve after a restart would; any other gets ``FOLLOW_UP``.
    """
    llm = agent_llm([reply(RESUMED_REPLY)])
    conversation = _open(scenario, root, llm)
    paused = conversation.state.execution_status.value == "waiting_for_confirmation"
    conversation.set_confirmation_policy(NeverConfirm())
    if not paused:
        conversation.send_message(FOLLOW_UP)
    conversation.run()
    conversation.close()
    assert llm._seen, f"{scenario}: the resumed agent never called its LLM"
    return [
        m.model_dump(
            mode="json", include={"role", "content", "tool_calls", "tool_call_id"}
        )
        for m in llm._seen[0]
        if m.role != "system"
    ]


def generate(scenario: str, root: Path) -> None:
    script, replies = SCENARIOS[scenario]
    llm = agent_llm(replies)
    conversation = _open(scenario, root, llm)
    script(conversation)
    errors = [
        e for e in conversation.state.events if type(e).__name__ == "AgentErrorEvent"
    ]
    conversation.close()
    assert not errors, f"{scenario}: agent errors {errors}"
    leftover = llm.remaining_responses
    assert leftover == 0, f"{scenario}: {leftover} scripted replies unused"

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / scenario
        shutil.copytree(root, copy)
        messages = probe(scenario, copy)

    shutil.rmtree(root / "workspace", ignore_errors=True)
    manifest = {
        "scenario": scenario,
        "sdk_version": version("openhands-sdk"),
        "resumed_llm_input": messages,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main(output: Path) -> None:
    for scenario in SCENARIOS:
        target = output / scenario
        if target.exists():
            raise SystemExit(f"{target} already exists")
        generate(scenario, target)
        print(f"wrote {target}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
