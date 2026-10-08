"""Per-conversation cost budget: server-enforced reservation + live cost."""

import tempfile
from itertools import count
from pathlib import Path
from unittest.mock import patch

import pytest
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage

from openhands.sdk import LLM, Agent, Conversation, Message, TextContent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.conversation_error import ConversationErrorEvent


def _response_with_cost(model: str) -> ModelResponse:
    # 1000 in + 1000 out tokens priced at the model's per-token rates.
    return ModelResponse(
        id=f"resp-{next(count())}",
        choices=[Choices(message=LiteLLMMessage(role="assistant", content="done"))],
        usage=Usage(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000),
        model=model,
    )


def _conversation(tmp: Path, *, max_budget_per_run: float | None) -> LocalConversation:
    llm = LLM(
        model="gpt-4o-mini",
        usage_id="main",
        num_retries=0,
        input_cost_per_token=1e-5,
        output_cost_per_token=1e-5,
    )
    return Conversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=str(tmp),
        persistence_dir=str(tmp / "persist"),
        max_budget_per_run=max_budget_per_run,
        visualizer=None,
        delete_on_close=False,
    )


def test_run_aborts_with_max_budget_reached_when_reservation_would_exceed():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conv = _conversation(tmp, max_budget_per_run=0.001)
        conv.send_message(Message(role="user", content=[TextContent(text="go")]))

        def fake_completion(**kwargs):
            return _response_with_cost(kwargs.get("model", "gpt-4o-mini"))

        with patch(
            "openhands.sdk.llm.llm.litellm_completion", side_effect=fake_completion
        ):
            conv.run()

        assert conv.state.execution_status == ConversationExecutionStatus.ERROR
        codes = [
            e.code for e in conv.state.events if isinstance(e, ConversationErrorEvent)
        ]
        assert "MaxBudgetReached" in codes
        # The cheapest call (0.002 USD) never fits a 0.001 ceiling.
        assert conv.conversation_stats.get_combined_metrics().accumulated_cost == 0.0


def test_run_completes_and_exposes_accumulated_cost_within_budget():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conv = _conversation(tmp, max_budget_per_run=1.0)
        conv.send_message(Message(role="user", content=[TextContent(text="go")]))

        def fake_completion(**kwargs):
            return _response_with_cost(kwargs.get("model", "gpt-4o-mini"))

        with patch(
            "openhands.sdk.llm.llm.litellm_completion", side_effect=fake_completion
        ):
            conv.run()

        assert conv.state.execution_status == ConversationExecutionStatus.FINISHED
        cost = conv.conversation_stats.get_combined_metrics().accumulated_cost
        assert cost == pytest.approx(0.02)
