"""Provider rejections of history content route into condensation recovery.

See https://github.com/OpenHands/software-agent-sdk/issues/5225. The rejected
content sits in a persisted event, so without a recovery branch every later
run rebuilds the same request and the conversation can never progress.
"""

from typing import TYPE_CHECKING

import pytest
from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.context.condenser.base import CondenserBase
from openhands.sdk.context.view import View
from openhands.sdk.conversation import Conversation
from openhands.sdk.event.condenser import CondensationRequest
from openhands.sdk.llm import LLM
from openhands.sdk.llm.exceptions import LLMInvalidToolResultContentError


if TYPE_CHECKING:
    from openhands.sdk.event.condenser import Condensation


ANTHROPIC_MESSAGE = (
    "messages.148.content.0.tool_result.content.1.image.source.base64: "
    "invalid base64 data"
)


class InvalidContentRaisingLLM(LLM):
    _force_responses: bool = PrivateAttr(default=False)

    def __init__(self, *, model: str = "test-model", force_responses: bool = False):
        super().__init__(model=model, usage_id="test-llm")
        self._force_responses = force_responses

    def uses_responses_api(self) -> bool:  # override gating
        return self._force_responses

    def completion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMInvalidToolResultContentError(ANTHROPIC_MESSAGE)

    async def acompletion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMInvalidToolResultContentError(ANTHROPIC_MESSAGE)

    def responses(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMInvalidToolResultContentError(ANTHROPIC_MESSAGE)

    async def aresponses(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMInvalidToolResultContentError(ANTHROPIC_MESSAGE)


class PassthroughCondenser(CondenserBase):
    def condense(
        self, view: View, agent_llm: "LLM | None" = None
    ) -> "View | Condensation":  # pragma: no cover - trivial passthrough
        return view

    def handles_condensation_requests(self) -> bool:
        return True


@pytest.mark.parametrize("force_responses", [True, False])
def test_step_requests_condensation_on_rejected_content(force_responses: bool, caplog):
    llm = InvalidContentRaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[], condenser=PassthroughCondenser())
    convo = Conversation(agent=agent)
    convo._ensure_agent_ready()

    seen: list = []
    agent.step(convo, on_event=seen.append)

    assert any(isinstance(e, CondensationRequest) for e in seen)
    assert any(
        "rejected content in the conversation history" in record.message
        for record in caplog.records
    )


@pytest.mark.parametrize("force_responses", [True, False])
@pytest.mark.asyncio
async def test_astep_requests_condensation_on_rejected_content(force_responses: bool):
    llm = InvalidContentRaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[], condenser=PassthroughCondenser())
    convo = Conversation(agent=agent)
    convo._ensure_agent_ready()

    seen: list = []
    await agent.astep(convo, on_event=seen.append)

    assert any(isinstance(e, CondensationRequest) for e in seen)


@pytest.mark.parametrize("force_responses", [True, False])
def test_step_raises_rejected_content_error_when_no_condenser(
    force_responses: bool, caplog
):
    llm = InvalidContentRaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[], condenser=None)
    convo = Conversation(agent=agent)
    convo._ensure_agent_ready()

    with pytest.raises(LLMInvalidToolResultContentError):
        agent.step(convo, on_event=lambda e: None)

    assert any(
        "no condenser can handle condensation requests" in record.message
        for record in caplog.records
    )
