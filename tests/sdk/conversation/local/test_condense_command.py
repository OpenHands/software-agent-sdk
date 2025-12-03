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

        from openhands.sdk.llm import LLMResponse, TextContent
        from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage

        # Return a non-empty summary when used by the condenser (usage_id == "cond").
        # Otherwise, return an empty assistant message to keep agent logic simple.
        if getattr(self, "usage_id", None) == "cond":
            message = Message(
                role="assistant", content=[TextContent(text="TEST SUMMARY")]
            )
        else:
            message = Message(role="assistant", content=[])

        return LLMResponse(
            message=message,
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
    condenser = LLMSummarizingCondenser(
        llm=llm.model_copy(update={"usage_id": "cond"}), max_size=10, keep_first=2
    )
    agent = Agent(llm=llm, tools=[], condenser=condenser)
    convo = Conversation(agent=agent)

    for i in range(3):
        convo.send_message(f"msg {i}")
        convo.run()

    # Issue the condensation request and run once to process it
    convo.send_message("/condense please")
    convo.run()

    # Verify a Condensation event was produced, and it includes a summary
    conds = [e for e in convo.state.events if isinstance(e, Condensation)]
    assert conds, "No Condensation event found"
    assert any(c.summary for c in conds), "Condensation event missing summary"


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
