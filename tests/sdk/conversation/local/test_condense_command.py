from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation import Conversation
from openhands.sdk.event import Condensation, CondensationRequest
from openhands.sdk.llm import LLM, Message


class DummyLLM(LLM):
    _calls: list[str] = PrivateAttr(default_factory=list)

    def __init__(self, *, model: str = "test-model"):
        super().__init__(model=model, usage_id="test-llm")

    def completion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        return self._basic_response()

    def responses(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        return self._basic_response()

    def _basic_response(self):
        from unittest.mock import MagicMock

        from litellm.types.utils import ModelResponse

        from openhands.sdk.llm import LLMResponse
        from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage

        return LLMResponse(
            message=Message(role="assistant", content=[]),
            metrics=MetricsSnapshot(
                model_name="test",
                accumulated_cost=0.0,
                max_budget_per_task=0.0,
                accumulated_token_usage=TokenUsage(model="test"),
            ),
            raw_response=MagicMock(spec=ModelResponse, id="resp-1"),
        )


def test_send_message_with_slash_condense_emits_request():
    llm = DummyLLM()
    agent = Agent(llm=llm, tools=[])
    convo = Conversation(agent=agent)

    convo.send_message("/condense")

    assert any(isinstance(e, CondensationRequest) for e in convo.state.events)
    assert isinstance(convo.state.events[-1], CondensationRequest)


def test_condense_request_triggers_condenser_on_next_step():
    llm = DummyLLM()
    # Configure condenser to satisfy validator: keep_first < max_size // 2 - 1
    # With max_size=10 and keep_first=2, condensation is allowed.
    condenser = LLMSummarizingCondenser(
        llm=llm.model_copy(update={"usage_id": "cond"}), max_size=10, keep_first=2
    )
    agent = Agent(llm=llm, tools=[], condenser=condenser)
    convo = Conversation(agent=agent)

    for i in range(3):
        convo.send_message(f"msg {i}")
        convo.run()

    convo.send_message("/condense please")

    convo.run()

    assert any(isinstance(e, Condensation) for e in convo.state.events)


def test_condense_skill_trigger_name():
    from openhands.sdk.context import AgentContext, KeywordTrigger, Skill

    llm = DummyLLM()
    skill = Skill(
        name="condense",
        content="Use condenser now",
        trigger=KeywordTrigger(keywords=["/condense"]),
    )
    agent_context = AgentContext(skills=[skill])

    agent = Agent(llm=llm, tools=[], agent_context=agent_context)
    convo = Conversation(agent=agent)

    convo.send_message("Could you /condense the context?")

    assert isinstance(convo.state.events[-1], CondensationRequest)
