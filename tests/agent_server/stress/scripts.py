"""Helpers shared by stress suites.

Centralises: scripted-LLM construction, the "create conversation through the
service then swap the LLM" dance, and a small polling helper. Lives here (not
in conftest) because it's plain Python — easier to import from test files
without fixture indirection.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

import httpx
import psutil
from pydantic import PrivateAttr, SecretStr

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import ConversationInfo, StartConversationRequest
from openhands.sdk import LLM, Agent, Tool
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.llm.streaming import TokenCallbackType
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.tool import ToolDefinition
from openhands.sdk.workspace import LocalWorkspace


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLMCallContext


class SlowTestLLM(TestLLM):
    """TestLLM with synthetic per-call latency.

    Latency applied via ``time.sleep`` so it blocks the worker thread the LLM
    runs on. This makes parallelism observable: when 8 sub-agents (or 16
    conversations) execute concurrently, each gets its own thread and the
    sleeps overlap; if execution serializes, they don't.
    """

    _latency_s: float = PrivateAttr(default=0.0)

    def __init__(self, *, latency_s: float = 0.0, **data: Any) -> None:
        super().__init__(**data)
        self._latency_s = latency_s

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        if self._latency_s > 0:
            time.sleep(self._latency_s)
        return super().completion(
            messages,
            tools,
            add_security_risk_prediction,
            on_token,
            call_context=call_context,
            **kwargs,
        )


def placeholder_llm(usage_id: str) -> LLM:
    """A valid-looking LLM for the StartConversationRequest payload.

    The agent-server's ``_start_conversation`` does ``model_dump(mode='json')``
    then revalidates from JSON, which strips TestLLM's private scripted
    responses. We pass this placeholder through that round-trip and swap in
    the real TestLLM via ``conversation.switch_llm`` *after* the conversation
    is created — switch_llm uses ``model_copy(update={'llm': ...})`` which
    preserves the TestLLM instance and its scripted state.
    """
    return LLM(usage_id=usage_id, model="openai/gpt-4o", api_key=SecretStr("unused"))


def text_message(text: str) -> Message:
    return Message(role="assistant", content=[TextContent(text=text)])


def descendants_of(pid: int) -> list[psutil.Process]:
    """All recursive descendants of ``pid``. Empty if the process is gone
    or psutil can't read it (Windows / sandboxed runners)."""
    try:
        return psutil.Process(pid).children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


async def start_conversation_with_test_llm(
    conversation_service: ConversationService,
    *,
    parent_llm: TestLLM,
    workspace_dir: str,
    usage_id: str,
    tools: list[Tool] | None = None,
    tool_concurrency_limit: int = 1,
    initial_text: str | None = "stress test",
) -> ConversationInfo:
    """Create a conversation, install ``parent_llm``, then optionally queue
    an initial user message (without auto-running).

    Returns ``ConversationInfo``. Caller is responsible for triggering the
    run explicitly (POST ``/api/conversations/<id>/run`` or
    ``event_service.run()``).

    Why we *don't* use StartConversationRequest.initial_message:
        ``_start_conversation`` calls ``send_message(..., run_after_send=True)``
        for the initial message — which schedules a fire-and-forget run
        BEFORE this helper has had a chance to install the TestLLM via
        ``switch_llm``. The placeholder LLM then makes a real network call,
        triggers retries, and the explicit /run later fights it (409, races,
        flake). Queueing the message after switch_llm with run=False keeps
        the run path single-shot and deterministic.
    """
    request = StartConversationRequest(
        agent=Agent(
            llm=placeholder_llm(usage_id),
            tools=tools or [],
            tool_concurrency_limit=tool_concurrency_limit,
        ),
        workspace=LocalWorkspace(working_dir=workspace_dir),
        # initial_message intentionally omitted — see docstring.
        autotitle=False,
    )
    info, _is_new = await conversation_service.start_conversation(request)
    assert isinstance(info, ConversationInfo)
    event_service = await conversation_service.get_event_service(info.id)
    assert event_service is not None, (
        f"start_conversation returned info.id={info.id} but "
        f"get_event_service returned None — ConversationService invariant "
        f"violation."
    )
    conv = event_service.get_conversation()
    conv.switch_llm(parent_llm)

    if initial_text is not None:
        await event_service.send_message(
            Message(role="user", content=[TextContent(text=initial_text)]),
            run=False,
        )
    return info


_TERMINAL_STATES: Final[frozenset[ConversationExecutionStatus]] = frozenset(
    {
        ConversationExecutionStatus.FINISHED,
        ConversationExecutionStatus.ERROR,
        ConversationExecutionStatus.STUCK,
    }
)


async def wait_for_terminal(
    client: httpx.AsyncClient,
    conversation_id: UUID,
    *,
    timeout_s: float = 30.0,
    poll_s: float = 0.05,
) -> ConversationExecutionStatus:
    """Poll the conversation until it reaches a terminal state.

    Polling rather than subscribing because websocket coverage is exercised
    by separate suites; we want this helper to work without WS infra.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        # Cap each request at the remaining wall-time (with a 0.1 s floor)
        # so a hung GET can't bypass the overall poll deadline.
        remaining = max(0.1, deadline - time.monotonic())
        resp = await client.get(
            f"/api/conversations/{conversation_id.hex}", timeout=remaining
        )
        assert resp.status_code == 200, resp.text
        st = ConversationExecutionStatus(resp.json()["execution_status"])
        if st in _TERMINAL_STATES:
            return st
        await asyncio.sleep(poll_s)
    raise TimeoutError(
        f"Conversation {conversation_id} did not reach terminal state in {timeout_s}s"
    )
