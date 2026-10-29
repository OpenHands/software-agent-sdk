"""LocalConversation and RemoteConversation must behave the same.

Each scenario drives the public ``BaseConversation`` API through the same
steps on a ``LocalConversation`` and on a ``RemoteConversation`` backed by a
live agent server, with the same scripted LLM replies, and compares what a
caller can observe: the event stream (type, source, content and tree shape),
the execution status, and the values the calls return.

``test_every_public_conversation_member_is_covered`` fails when a public
member is added to ``BaseConversation`` without a parity scenario or an
explicit exemption, so new API gets a remote check at the same time.
"""

import asyncio
import json
import threading
import time
from collections import deque
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Message, TextContent
from openhands.sdk.conversation.base import BaseConversation
from openhands.sdk.event import (
    ActionEvent,
    ConversationStateUpdateEvent,
    Event,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.tool import (
    Action,
    Observation,
    Tool,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)
from openhands.sdk.tool.builtins import ThinkAction
from openhands.sdk.workspace import RemoteWorkspace
from tests.cross.conftest import live_server_env


# Calls made without tools (title generation, ask_agent) get this reply, so the
# server's automatic title does not consume a scripted agent reply.
SIDE_REPLY = "Side reply"

# Events only the remote client receives: they carry state the local
# conversation already holds in memory.
TRANSPORT_EVENTS = (ConversationStateUpdateEvent,)


class _EchoAction(Action):
    text: str


class _EchoObservation(Observation):
    echoed: str

    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        return [TextContent(text=self.echoed)]


# An echo of BLOCK_TEXT holds the tool open until the test releases it, so a
# scenario can act while a step is in flight.
BLOCK_TEXT = "block"
TOOL_ENTERED = threading.Event()
TOOL_RELEASED = threading.Event()


class _EchoExecutor(ToolExecutor[_EchoAction, _EchoObservation]):
    def __call__(self, action: _EchoAction, conversation=None) -> _EchoObservation:
        if action.text == BLOCK_TEXT:
            TOOL_ENTERED.set()
            TOOL_RELEASED.wait(timeout=10)
        return _EchoObservation(echoed=action.text)


class _EchoTool(ToolDefinition[_EchoAction, _EchoObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["_EchoTool"]:
        return [
            cls(
                description="Echo the text back.",
                action_type=_EchoAction,
                observation_type=_EchoObservation,
                executor=_EchoExecutor(),
            )
        ]


# The live server runs in this process, so it resolves the tool from the same
# registry as the local conversation.
register_tool("ParityEchoTool", _EchoTool)
ECHO_TOOL_NAME = _EchoTool.create()[0].name


def _echo_call(call_id: str, text: str = "parity") -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="Echoing.")],
        tool_calls=[
            MessageToolCall(
                id=call_id,
                name=ECHO_TOOL_NAME,
                arguments=json.dumps({"text": text}),
                origin="completion",
            )
        ],
    )


def _reply(text: str) -> Message:
    return Message(role="assistant", content=[TextContent(text=text)])


@dataclass
class Scenario:
    """Steps to run on both conversations, with the LLM replies they consume."""

    drive: Callable[[BaseConversation], dict[str, Any]]
    script: Sequence[Message]
    covers: frozenset[str]
    # Strings whose presence in what the agent sends the LLM must match.
    markers: tuple[str, ...] = ()


def _run_with_tool(conversation: BaseConversation) -> dict[str, Any]:
    conversation.send_message("Plan, then answer.")
    conversation.run()
    stats = conversation.conversation_stats
    return {
        "id_is_set": conversation.id is not None,
        "usage_ids": sorted(stats.usage_to_metrics),
    }


def _arun_with_tool(conversation: BaseConversation) -> dict[str, Any]:
    conversation.send_message("Plan, then answer.")
    asyncio.run(conversation.arun())
    return {}


def _pause_then_run(conversation: BaseConversation) -> dict[str, Any]:
    conversation.send_message("Answer.")
    conversation.pause()
    paused = conversation.state.execution_status.value
    conversation.run()
    return {"status_after_pause": paused}


def _interrupt_idle(conversation: BaseConversation) -> dict[str, Any]:
    conversation.send_message("Answer.")
    conversation.interrupt()
    interrupted = conversation.state.execution_status.value
    conversation.run()
    return {"status_after_interrupt": interrupted}


def _interrupt_during_tool(conversation: BaseConversation) -> dict[str, Any]:
    TOOL_ENTERED.clear()
    TOOL_RELEASED.clear()
    conversation.send_message("Echo slowly.")
    runner = threading.Thread(
        target=lambda: asyncio.run(conversation.arun()), daemon=True
    )
    runner.start()
    try:
        assert TOOL_ENTERED.wait(timeout=10), "the tool never started"
        conversation.interrupt()
    finally:
        TOOL_RELEASED.set()
        runner.join(timeout=10)
    return {"arun_returned": not runner.is_alive()}


def _confirm_then_reject(conversation: BaseConversation) -> dict[str, Any]:
    conversation.set_confirmation_policy(AlwaysConfirm())
    active = (
        conversation.confirmation_policy_active,
        conversation.is_confirmation_mode_active,
    )
    conversation.send_message("Plan, then answer.")
    conversation.run()
    waiting = conversation.state.execution_status.value
    conversation.reject_pending_actions("Not now")
    return {"confirmation_active": active, "status_while_waiting": waiting}


def _ask_and_title(conversation: BaseConversation) -> dict[str, Any]:
    conversation.send_message("Answer.")
    conversation.run()
    return {
        "ask_agent": conversation.ask_agent("What did you do?"),
        "title": conversation.generate_title(),
    }


def _execute_tool(conversation: BaseConversation) -> dict[str, Any]:
    observation = conversation.execute_tool("think", ThinkAction(thought="direct"))
    return {"observation": type(observation).__name__}


def _fork(conversation: BaseConversation) -> dict[str, Any]:
    conversation.send_message("Answer.")
    conversation.run()
    fork = conversation.fork(title="Fork")
    try:
        return {
            "fork_has_new_id": fork.id != conversation.id,
            "fork_events": _wait_for_events(
                fork, _normalize(conversation.state.events)
            ),
        }
    finally:
        fork.close()


def _navigate_and_branch(conversation: BaseConversation) -> dict[str, Any]:
    conversation.send_message("First question.")
    conversation.run()
    first_user_message = next(
        event
        for event in conversation.state.events
        if isinstance(event, MessageEvent) and event.source == "user"
    )
    conversation.navigate_to(first_user_message.id)
    conversation.send_message("Second question.")
    conversation.run()
    return {}


def _secrets_before_run(conversation: BaseConversation) -> dict[str, Any]:
    conversation.update_secrets({"PARITY_TOKEN": "parity-secret-value"})
    conversation.send_message("Answer.")
    conversation.run()
    return {}


def _security_analyzer_before_run(conversation: BaseConversation) -> dict[str, Any]:
    conversation.set_security_analyzer(LLMSecurityAnalyzer())
    conversation.send_message("Echo.")
    conversation.run()
    return {"hook_processor_is_none": conversation.hook_processor is None}


def _condense_without_condenser(conversation: BaseConversation) -> dict[str, Any]:
    conversation.send_message("Answer.")
    conversation.run()
    try:
        conversation.condense()
    except Exception as error:  # the outcome itself is what we compare
        return {"condense": type(error).__name__}
    return {"condense": None}


SCENARIOS: dict[str, Scenario] = {
    "run_with_tool_call": Scenario(
        drive=_run_with_tool,
        script=[_echo_call("call_1"), _reply("Done.")],
        covers=frozenset({"send_message", "run", "state", "id", "conversation_stats"}),
    ),
    "arun_with_tool_call": Scenario(
        drive=_arun_with_tool,
        script=[_echo_call("call_1"), _reply("Done.")],
        covers=frozenset({"arun"}),
    ),
    "pause_before_run": Scenario(
        drive=_pause_then_run,
        script=[_reply("Done.")],
        covers=frozenset({"pause"}),
    ),
    "interrupt_while_idle": Scenario(
        drive=_interrupt_idle,
        script=[_reply("Done.")],
        covers=frozenset({"interrupt"}),
    ),
    "interrupt_during_tool": Scenario(
        drive=_interrupt_during_tool,
        script=[_echo_call("call_1", text=BLOCK_TEXT)],
        covers=frozenset({"interrupt"}),
    ),
    "confirm_then_reject": Scenario(
        drive=_confirm_then_reject,
        script=[_echo_call("call_1")],
        covers=frozenset(
            {
                "set_confirmation_policy",
                "confirmation_policy_active",
                "is_confirmation_mode_active",
                "reject_pending_actions",
            }
        ),
    ),
    "ask_agent_and_generate_title": Scenario(
        drive=_ask_and_title,
        script=[_reply("Done."), _reply("I answered.")],
        covers=frozenset({"ask_agent", "generate_title"}),
    ),
    "execute_tool_directly": Scenario(
        drive=_execute_tool,
        script=[],
        covers=frozenset({"execute_tool"}),
    ),
    "fork_after_run": Scenario(
        drive=_fork,
        script=[_reply("Done.")],
        covers=frozenset({"fork"}),
    ),
    "navigate_then_branch": Scenario(
        drive=_navigate_and_branch,
        script=[_reply("First answer."), _reply("Second answer.")],
        covers=frozenset({"navigate_to"}),
    ),
    "update_secrets_before_run": Scenario(
        drive=_secrets_before_run,
        script=[_reply("Done.")],
        covers=frozenset({"update_secrets"}),
        # Whether the LLM is told the secret's name, and whether it ever sees
        # the value, must match.
        markers=("PARITY_TOKEN", "parity-secret-value"),
    ),
    "security_analyzer_before_run": Scenario(
        drive=_security_analyzer_before_run,
        script=[_echo_call("call_1"), _reply("Done.")],
        covers=frozenset({"set_security_analyzer", "hook_processor"}),
        markers=("security_risk",),
    ),
    "condense_without_condenser": Scenario(
        drive=_condense_without_condenser,
        script=[_reply("Done.")],
        covers=frozenset({"condense"}),
    ),
}

# Every scenario closes both conversations and compares the cleanup flags.
COVERED_BY_EVERY_SCENARIO = frozenset(
    {"close", "cleanup_initiated", "cleanup_complete"}
)

# Public members that are not compared, and why. Adding a member here is a
# decision reviewers can see; leaving it out fails the coverage test.
EXEMPT: dict[str, str] = {
    "compose_callbacks": "Static helper shared by both implementations.",
    "get_persistence_dir": "Static helper shared by both implementations.",
    "load_plugin": "Needs a plugin source; not covered yet.",
}

# Scenarios where the implementations differ today. strict=True makes the test
# fail once they agree, so the entry gets removed with the fix.
KNOWN_DIVERGENCES: dict[str, str] = {
    "execute_tool_directly": (
        "RemoteConversation.execute_tool raises NotImplementedError by design."
    ),
    "condense_without_condenser": (
        "Local raises ValueError; remote raises httpx.HTTPStatusError (409)."
    ),
}


def _public_members() -> set[str]:
    return {name for name in dir(BaseConversation) if not name.startswith("_")}


def _normalize(events: Sequence[Event]) -> list[tuple[Any, ...]]:
    """What a caller can compare across implementations: no ids or timestamps."""
    kept = [event for event in events if not isinstance(event, TRANSPORT_EVENTS)]
    position = {event.id: index for index, event in enumerate(kept)}
    normalized: list[tuple[Any, ...]] = []
    for event in kept:
        if isinstance(event, MessageEvent):
            detail: Any = (
                event.llm_message.role,
                [
                    c.text
                    for c in event.llm_message.content
                    if isinstance(c, TextContent)
                ],
            )
        elif isinstance(event, ActionEvent):
            detail = (event.tool_name, event.tool_call_id)
        elif isinstance(event, ObservationEvent):
            detail = (event.tool_name, event.tool_call_id)
        else:
            detail = None
        parent = position.get(event.parent_id) if event.parent_id else None
        normalized.append((type(event).__name__, event.source, detail, parent))
    return normalized


def _wait_for_events(
    conversation: BaseConversation, expected: list[tuple[Any, ...]]
) -> list[tuple[Any, ...]]:
    """Remote events arrive over a WebSocket; give them time to land."""
    deadline = time.monotonic() + 5
    while True:
        observed = _normalize(conversation.state.events)
        if observed == expected or time.monotonic() > deadline:
            return observed
        time.sleep(0.1)


@dataclass
class ScriptedLLM:
    replies: deque[Message]
    # Messages and tool schemas of each agent call, flattened to text.
    sent: list[str]

    def saw(self, markers: Sequence[str]) -> dict[str, bool]:
        return {marker: any(marker in text for text in self.sent) for marker in markers}


@pytest.fixture
def scripted_llm(monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    """Answer every LLM call in this process from one queue of replies.

    The live server runs in the test process, so the same patch serves the
    local conversation and the server's copy of the remote one.
    """
    scripted = ScriptedLLM(replies=deque(), sent=[])

    def completion(self: LLM, messages: Any, tools: Any = None, **_: Any) -> Any:
        if tools:
            texts = [
                content.text
                for message in messages
                for content in message.content
                if isinstance(content, TextContent)
            ]
            scripted.sent.append("\n".join(texts) + json.dumps(tools, default=str))
            message = scripted.replies.popleft()
        else:
            message = _reply(SIDE_REPLY)
        raw = ModelResponse(
            id="parity",
            created=0,
            model=self.model,
            choices=[
                Choices(
                    index=0,
                    finish_reason="stop",
                    message=LiteLLMMessage(role="assistant", content=""),
                )
            ],
        )
        return LLMResponse(
            message=message, metrics=self.metrics.get_snapshot(), raw_response=raw
        )

    async def acompletion(self: LLM, messages: Any, tools: Any = None, **kw: Any):
        return completion(self, messages, tools, **kw)

    monkeypatch.setattr(LLM, "completion", completion)
    monkeypatch.setattr(LLM, "acompletion", acompletion)
    return scripted


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Generator[dict[str, Any]]:
    """One live agent server for every scenario; each uses its own conversation."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        with live_server_env(tmp_path_factory.mktemp("server"), monkeypatch) as env:
            yield env


def _agent() -> Agent:
    # A known model keeps agent setup from looking up vision profiles over HTTP.
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test"), usage_id="parity")
    return Agent(llm=llm, tools=[Tool(name="ParityEchoTool")])


def _observe(
    conversation: BaseConversation,
    scenario: Scenario,
    scripted: ScriptedLLM,
    expected_events: list[tuple[Any, ...]] | None = None,
) -> tuple[list[tuple[Any, ...]], str, dict[str, Any]]:
    """Run a scenario and return the events, final status and returned values."""
    scripted.replies.extend(scenario.script)
    scripted.sent.clear()
    try:
        returned = scenario.drive(conversation)
        if expected_events is None:
            events = _normalize(conversation.state.events)
        else:
            events = _wait_for_events(conversation, expected_events)
        status = conversation.state.execution_status.value
    finally:
        conversation.close()
    returned["closed"] = (conversation.cleanup_initiated, conversation.cleanup_complete)
    returned["llm_saw"] = scripted.saw(scenario.markers)
    returned["unused_replies"] = len(scripted.replies)
    scripted.replies.clear()
    return events, status, returned


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(
            name,
            marks=(
                [pytest.mark.xfail(strict=True, reason=KNOWN_DIVERGENCES[name])]
                if name in KNOWN_DIVERGENCES
                else []
            ),
        )
        for name in SCENARIOS
    ],
)
def test_local_and_remote_conversations_match(
    name: str,
    tmp_path: Path,
    server: dict[str, Any],
    scripted_llm: ScriptedLLM,
) -> None:
    scenario = SCENARIOS[name]

    local = Conversation(agent=_agent(), workspace=str(tmp_path / "local"))
    local_events, local_status, local_returned = _observe(local, scenario, scripted_llm)
    assert local_returned["unused_replies"] == 0, "the scenario script is too long"

    remote = Conversation(
        agent=_agent(),
        workspace=RemoteWorkspace(
            host=server["host"], working_dir=str(server["workspace_path"])
        ),
    )
    remote_events, remote_status, remote_returned = _observe(
        remote, scenario, scripted_llm, expected_events=local_events
    )

    assert remote_events == local_events
    assert remote_status == local_status
    assert remote_returned == local_returned


def test_every_public_conversation_member_is_covered() -> None:
    covered = set(COVERED_BY_EVERY_SCENARIO).union(
        *(scenario.covers for scenario in SCENARIOS.values())
    )
    assert not covered & set(EXEMPT), "a member is both covered and exempt"
    missing = _public_members() - covered - set(EXEMPT)
    stale = (covered | set(EXEMPT)) - _public_members()
    assert not missing, (
        f"Add a parity scenario or an EXEMPT entry for: {sorted(missing)}"
    )
    assert not stale, f"No longer on BaseConversation: {sorted(stale)}"
