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
        max_output_tokens=2000,
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
        # The provider input/output upper bound never fits a 0.001 ceiling.
        assert conv.conversation_stats.get_combined_metrics().accumulated_cost == 0.0


def test_run_completes_and_exposes_accumulated_cost_within_budget():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conv = _conversation(tmp, max_budget_per_run=2.0)
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


def test_budget_is_accumulated_across_runs():
    """The cap bounds accumulated cost: prior runs drain later runs' allowance."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # A call's worst-case reservation is 1.30 (128k input ceiling plus 2000
        # output tokens). A 1.31 cap admits the first run, which records 0.02.
        # The second run's reservation (1.30) plus that recorded spend exceeds
        # the cap, so the accumulated ceiling stops it.
        conv = _conversation(tmp, max_budget_per_run=1.31)

        def fake_completion(**kwargs):
            return _response_with_cost(kwargs.get("model", "gpt-4o-mini"))

        with patch(
            "openhands.sdk.llm.llm.litellm_completion", side_effect=fake_completion
        ):
            conv.send_message(Message(role="user", content=[TextContent(text="one")]))
            conv.run()
            assert conv.state.execution_status == ConversationExecutionStatus.FINISHED

            conv.send_message(Message(role="user", content=[TextContent(text="two")]))
            conv.run()

        assert conv.state.execution_status == ConversationExecutionStatus.ERROR
        codes = [
            e.code for e in conv.state.events if isinstance(e, ConversationErrorEvent)
        ]
        assert "MaxBudgetReached" in codes
        # Only the first run's cost was recorded; the second was denied pre-call.
        cost = conv.conversation_stats.get_combined_metrics().accumulated_cost
        assert cost == pytest.approx(0.02)


def test_raising_budget_takes_effect_without_a_new_run():
    """Raising the cap re-seeds the ledger, so a denied run can continue."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conv = _conversation(tmp, max_budget_per_run=1.31)

        def fake_completion(**kwargs):
            return _response_with_cost(kwargs.get("model", "gpt-4o-mini"))

        with patch(
            "openhands.sdk.llm.llm.litellm_completion", side_effect=fake_completion
        ):
            conv.send_message(Message(role="user", content=[TextContent(text="one")]))
            conv.run()
            assert conv.state.execution_status == ConversationExecutionStatus.FINISHED

            conv.send_message(Message(role="user", content=[TextContent(text="two")]))
            conv.run()
            assert conv.state.execution_status == ConversationExecutionStatus.ERROR

            # Raising the cap rebuilds the ledger against the accumulated cost,
            # so the next call fits.
            conv.max_budget_per_run = 5.0
            conv.send_message(Message(role="user", content=[TextContent(text="three")]))
            conv.run()

        assert conv.state.execution_status == ConversationExecutionStatus.FINISHED
        cost = conv.conversation_stats.get_combined_metrics().accumulated_cost
        assert cost == pytest.approx(0.04)


def test_acp_agent_rejects_budget():
    """Pre-call reservation is impossible for out-of-process ACP prompts, so a
    budget on an ACP conversation is rejected instead of silently unenforced.
    """
    from openhands.sdk.agent.acp_agent import ACPAgent
    from openhands.sdk.conversation.exceptions import CostBudgetUnsupportedError

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with pytest.raises(CostBudgetUnsupportedError, match="ACP agents"):
            Conversation(
                agent=ACPAgent(acp_command=["echo", "test"]),
                workspace=str(tmp),
                persistence_dir=str(tmp / "persist"),
                max_budget_per_run=1.0,
                visualizer=None,
                delete_on_close=False,
            )


def test_acp_agent_without_budget_is_allowed():
    from openhands.sdk.agent.acp_agent import ACPAgent

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conv = Conversation(
            agent=ACPAgent(acp_command=["echo", "test"]),
            workspace=str(tmp),
            persistence_dir=str(tmp / "persist"),
            visualizer=None,
            delete_on_close=False,
        )
        assert conv.max_budget_per_run is None
        assert conv._cost_budget is None


def test_acp_budget_rejection_leaves_no_orphan_state():
    """A rejected ACP budget must not write base_state.json.

    If it did, retrying the same conversation id without a budget would resume
    the rejected ACP agent instead of creating the requested conversation.
    """
    from uuid import uuid4

    from openhands.sdk.agent.acp_agent import ACPAgent
    from openhands.sdk.conversation.exceptions import CostBudgetUnsupportedError

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        persist = tmp / "persist"
        cid = uuid4()
        with pytest.raises(CostBudgetUnsupportedError, match="ACP agents"):
            Conversation(
                agent=ACPAgent(acp_command=["echo", "test"]),
                workspace=str(tmp),
                persistence_dir=str(persist),
                conversation_id=cid,
                max_budget_per_run=1.0,
                visualizer=None,
                delete_on_close=False,
            )
        assert not (persist / cid.hex / "base_state.json").exists()


def test_acp_delegate_rejects_inherited_budget():
    """An ACP delegate that inherits a parent's budget must be rejected.

    ``DelegateExecutor`` forwards the parent's LLM call context without an
    explicit cap, so a delegate's effective budget is the inherited one. Its
    prompts bypass reservation, so the ceiling must be checked even when
    ``max_budget_per_run`` is unset.
    """
    from openhands.sdk.agent.acp_agent import ACPAgent
    from openhands.sdk.conversation.exceptions import CostBudgetUnsupportedError

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        parent = _conversation(tmp, max_budget_per_run=1.0)
        with pytest.raises(CostBudgetUnsupportedError, match="ACP agents"):
            LocalConversation(
                agent=ACPAgent(acp_command=["echo", "test"]),
                workspace=str(tmp),
                persistence_dir=str(tmp / "sub"),
                max_budget_per_run=None,
                visualizer=None,
                delete_on_close=False,
                _parent_llm_call_context=parent.get_llm_call_context(),
            )
        # Rejection happened before any conversation state was created.
        assert not (tmp / "sub").exists()


def test_acp_delegate_without_inherited_budget_is_allowed():
    from openhands.sdk.agent.acp_agent import ACPAgent

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        parent = _conversation(tmp, max_budget_per_run=None)
        conv = LocalConversation(
            agent=ACPAgent(acp_command=["echo", "test"]),
            workspace=str(tmp),
            persistence_dir=str(tmp / "sub"),
            max_budget_per_run=None,
            visualizer=None,
            delete_on_close=False,
            _parent_llm_call_context=parent.get_llm_call_context(),
        )
        assert conv._cost_budget is None


def test_delegate_install_does_not_weaken_the_parent_ceiling():
    """A delegate's ledger install must not reseed the shared budget tree.

    Regression: ``_install_budget_ledger`` reseeded unconditionally, so
    constructing a delegate child wiped the parent's settled totals and let the
    parent spend past its cap. Only the budget-owning conversation may reseed.
    """
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        parent = _conversation(tmp, max_budget_per_run=1.31)
        parent_budget = parent._cost_budget
        assert parent_budget is not None
        reservation = parent_budget.try_reserve(1.0)
        assert reservation is not None
        parent_budget.settle(reservation, 1.0)
        assert parent_budget.live_total == pytest.approx(1.0)

        # A delegate with its own cap shares the parent's tree.
        LocalConversation(
            agent=Agent(
                llm=LLM(
                    model="gpt-4o-mini",
                    usage_id="sub",
                    num_retries=0,
                    input_cost_per_token=1e-5,
                    output_cost_per_token=1e-5,
                    max_output_tokens=2000,
                ),
                tools=[],
            ),
            workspace=str(tmp),
            persistence_dir=str(tmp / "sub"),
            max_budget_per_run=0.5,
            visualizer=None,
            delete_on_close=False,
            _parent_llm_call_context=parent.get_llm_call_context(),
        )

        # The parent keeps its recorded spend; only $0.31 of headroom remains.
        assert parent_budget.live_total == pytest.approx(1.0)
        assert parent_budget.try_reserve(0.40) is None


def test_live_cap_change_preserves_in_flight_reservation():
    """Raising the cap mid-run must not forget an in-flight reservation.

    Regression: ``_install_budget_ledger`` replaced the ledger on a cap change,
    so a reservation held by the running conversation vanished and a later call
    could reserve against the new ceiling as if the in-flight work did not exist.
    """
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conv = _conversation(tmp, max_budget_per_run=1.0)
        budget = conv._cost_budget
        assert budget is not None

        reservation = budget.try_reserve(0.8)
        assert reservation is not None

        conv.max_budget_per_run = 1.5

        # The running conversation's captured context still points at the same,
        # retuned ledger, and the in-flight $0.80 still counts.
        assert conv._cost_budget is budget
        assert conv.get_llm_call_context().cost_budget is budget
        assert budget.live_total == pytest.approx(0.8)
        assert budget.try_reserve(0.8) is None
        assert budget.try_reserve(0.5) is not None


def test_setting_budget_on_existing_acp_conversation_is_rejected():
    """A cap PATCHed onto an ACP conversation must be refused, not unenforced."""
    from openhands.sdk.agent.acp_agent import ACPAgent
    from openhands.sdk.conversation.exceptions import CostBudgetUnsupportedError

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        conv = Conversation(
            agent=ACPAgent(acp_command=["echo", "test"]),
            workspace=str(tmp),
            persistence_dir=str(tmp / "persist"),
            visualizer=None,
            delete_on_close=False,
        )
        with pytest.raises(CostBudgetUnsupportedError, match="ACP agents"):
            conv.max_budget_per_run = 1.0
        assert conv.max_budget_per_run is None
        # Clearing stays allowed.
        conv.max_budget_per_run = None


def test_resumed_acp_conversation_rejects_persisted_budget():
    """A persisted ACP cap must be rejected when the conversation is reloaded.

    Regression: the resume path only checked the constructor argument, so a cap
    written into ``base_state.json`` survived reload and stayed unenforced.
    """
    import json
    from uuid import uuid4

    from openhands.sdk.agent.acp_agent import ACPAgent
    from openhands.sdk.conversation.exceptions import CostBudgetUnsupportedError

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        persist = tmp / "persist"
        cid = uuid4()
        # Write an ACP conversation whose persisted cap the SDK can no longer
        # accept, as an older/other writer could have.
        conv = Conversation(
            agent=ACPAgent(acp_command=["echo", "test"]),
            workspace=str(tmp),
            persistence_dir=str(persist),
            conversation_id=cid,
            visualizer=None,
            delete_on_close=False,
        )
        conv.close()
        base = persist / cid.hex / "base_state.json"
        data = json.loads(base.read_text())
        data["max_budget_per_run"] = 1.0
        base.write_text(json.dumps(data))

        with pytest.raises(CostBudgetUnsupportedError, match="ACP agents"):
            LocalConversation(
                agent=None,
                workspace=str(tmp),
                persistence_dir=str(persist),
                conversation_id=cid,
                visualizer=None,
                delete_on_close=False,
            )


def test_fork_with_copied_metrics_seeds_its_ledger():
    """A fork that copies the source's cost must seed its ledger with it.

    Regression: the fork built its ledger from its then-empty stats, so a fork
    with ``reset_metrics=False`` could reserve a call against a ceiling that
    ignored the cost it reported as accumulated.
    """
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        src = _conversation(tmp, max_budget_per_run=2.0)
        from openhands.sdk.llm.utils.metrics import Metrics

        metric = Metrics()
        metric.accumulated_cost = 1.5
        src._state.stats.usage_to_metrics["main"] = metric

        fork = src.fork(reset_metrics=False)

        assert (
            fork.conversation_stats.get_combined_metrics().accumulated_cost
            == pytest.approx(1.5)
        )
        fork_budget = fork._cost_budget
        assert fork_budget is not None
        # Only $0.50 of the $2.00 cap remains after the copied $1.50.
        assert fork_budget.live_total == pytest.approx(1.5)
        assert fork_budget.try_reserve(0.6) is None
        assert fork_budget.try_reserve(0.5) is not None
