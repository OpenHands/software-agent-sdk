"""A content-policy HTTP error can be recovered with a nudge.
A chat completion explicitly stopped by the provider's filter ends the run.
"""

from unittest.mock import patch

import pytest
from litellm.types.utils import ModelResponse
from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import LLM
from openhands.sdk.llm.exceptions import LLMContentPolicyViolationError


class ContentPolicyRaisingLLM(LLM):
    _force_responses: bool = PrivateAttr(default=False)

    def __init__(self, *, model: str = "test-model", force_responses: bool = False):
        super().__init__(model=model, usage_id="test-llm")
        self._force_responses = force_responses

    def uses_responses_api(self) -> bool:  # override gating
        return self._force_responses

    def completion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMContentPolicyViolationError()

    async def acompletion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMContentPolicyViolationError()

    def responses(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMContentPolicyViolationError()

    async def aresponses(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        raise LLMContentPolicyViolationError()


def _content_policy_nudges(events: list) -> list[MessageEvent]:
    return [
        e
        for e in events
        if isinstance(e, MessageEvent)
        and e.source == "user"
        and any(
            "content filter" in getattr(c, "text", "") for c in e.llm_message.content
        )
    ]


@pytest.mark.parametrize("force_responses", [True, False])
def test_step_emits_soft_nudge_on_content_policy(force_responses: bool):
    llm = ContentPolicyRaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[])
    convo = Conversation(agent=agent)
    convo._ensure_agent_ready()

    seen: list = []
    # Must not raise — a content-policy block is recoverable.
    agent.step(convo, on_event=seen.append)

    assert len(_content_policy_nudges(seen)) == 1
    assert not any(isinstance(e, ConversationErrorEvent) for e in seen)


@pytest.mark.parametrize("force_responses", [True, False])
@pytest.mark.asyncio
async def test_astep_emits_soft_nudge_on_content_policy(force_responses: bool):
    llm = ContentPolicyRaisingLLM(force_responses=force_responses)
    agent = Agent(llm=llm, tools=[])
    convo = Conversation(agent=agent)
    convo._ensure_agent_ready()

    seen: list = []
    await agent.astep(convo, on_event=seen.append)

    assert len(_content_policy_nudges(seen)) == 1
    assert not any(isinstance(e, ConversationErrorEvent) for e in seen)


@pytest.mark.parametrize(
    ("async_run", "native_tools", "finish_reason", "content"),
    [
        pytest.param(False, True, "refusal", None, id="empty-refusal"),
        pytest.param(
            True, False, "content_filter", "<function=finish>", id="partial-tool"
        ),
    ],
)
@pytest.mark.asyncio
async def test_filtered_completion_ends_run_on_first_call(
    async_run, native_tools, finish_reason, content
):
    llm = LLM(
        model="gpt-4o",
        native_tool_calling=native_tools,
        num_retries=2,
        retry_min_wait=0,
        retry_max_wait=0,
    )
    convo = Conversation(agent=Agent(llm=llm), visualizer=None, max_iteration_per_run=2)
    convo.send_message("Hello")
    filtered = ModelResponse(
        id="chat-filtered-1",
        model="gpt-4o",
        choices=[
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        usage={"prompt_tokens": 17, "completion_tokens": 0, "total_tokens": 17},
    )
    # Some adapters retain the native stop reason instead of normalizing it.
    filtered.choices[0] = filtered.choices[0].model_copy(
        update={"finish_reason": finish_reason}
    )
    transport = "litellm_acompletion" if async_run else "litellm_completion"
    with patch(f"openhands.sdk.llm.llm.{transport}", return_value=filtered) as call:
        with pytest.raises(ConversationRunError):
            if async_run:
                await convo.arun()
            else:
                convo.run()

    errors = [e for e in convo.state.events if isinstance(e, ConversationErrorEvent)]
    assert [e.code for e in errors] == ["LLMResponseRefusedError"]
    assert "content filter" in errors[0].detail
    assert filtered.id in errors[0].detail
    classification = errors[0].classification
    assert classification is not None and classification.kind == "agent_action"
    assert not classification.retryable
    assert convo.state.execution_status == ConversationExecutionStatus.ERROR
    assert len([e for e in convo.state.events if isinstance(e, MessageEvent)]) == 1
    call.assert_called_once()
    usage = convo.state.stats.get_combined_metrics().accumulated_token_usage
    assert usage is not None and usage.prompt_tokens == 17
