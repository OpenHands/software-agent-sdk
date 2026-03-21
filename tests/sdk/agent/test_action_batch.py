"""Unit tests for _ActionBatch."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from openhands.sdk.agent.agent import _ActionBatch
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.event.llm_convertible import UserRejectObservation
from openhands.sdk.tool.builtins import FinishTool


def _ae(tool_name: str = "tool", action_id: str | None = None) -> ActionEvent:
    """Minimal ActionEvent mock (typed as ActionEvent for static analysis)."""
    ae = MagicMock(spec=ActionEvent)
    ae.tool_name = tool_name
    ae.id = action_id or str(id(ae))
    ae.tool_call_id = f"tc-{ae.id}"
    return ae  # type: ignore[return-value]


_F = FinishTool.name


@pytest.mark.parametrize(
    "names, expected_names, expected_finish",
    [
        ([], [], False),
        (["a", "b"], ["a", "b"], False),
        ([_F], [_F], True),
        (["a", _F], ["a", _F], True),
        (["a", _F, "b", "c"], ["a", _F], True),
    ],
    ids=["empty", "no_finish", "finish_only", "finish_last", "discards_after_finish"],
)
def test_truncate_at_finish(names, expected_names, expected_finish):
    events = [_ae(n) for n in names]
    result, has_finish = _ActionBatch._truncate_at_finish(events)
    assert [e.tool_name for e in result] == expected_names
    assert has_finish == expected_finish


def _make_state(blocked: dict[str, str] | None = None):
    """Mock ConversationState with pop_blocked_action support."""
    blocked = dict(blocked or {})
    state = MagicMock()
    state.pop_blocked_action = lambda aid: blocked.pop(aid, None)
    return state


def _make_executor(side_effect: Any = None) -> Any:
    """Mock ParallelToolExecutor."""
    executor = MagicMock()
    if side_effect:
        executor.execute_batch = side_effect
    else:
        executor.execute_batch = lambda actions, runner: [runner(a) for a in actions]
    return executor


def _run(ae: ActionEvent) -> list[Any]:
    return [f"result-{ae.id}"]


def test_prepare_simple():
    events = [_ae("a", "1"), _ae("b", "2")]
    batch = _ActionBatch.prepare(events, _make_state(), _make_executor(), _run)

    assert batch.action_events == events
    assert not batch.has_finish
    assert batch.blocked_reasons == {}
    assert batch.results_by_id == {"1": ["result-1"], "2": ["result-2"]}


def test_prepare_with_blocked():
    events = [_ae("a", "1"), _ae("b", "2"), _ae("c", "3")]
    state = _make_state({"2": "denied by policy"})
    executed = []

    def tracking_runner(ae: ActionEvent) -> list[Any]:
        executed.append(ae.id)
        return [f"ok-{ae.id}"]

    batch = _ActionBatch.prepare(events, state, _make_executor(), tracking_runner)

    assert batch.blocked_reasons == {"2": "denied by policy"}
    assert "2" not in batch.results_by_id
    assert set(executed) == {"1", "3"}


def test_prepare_truncates_before_blocking():
    """FinishTool truncation happens before blocked partitioning."""
    events = [_ae("a", "1"), _ae(FinishTool.name, "2"), _ae("c", "3")]
    state = _make_state({"3": "should not appear"})

    batch = _ActionBatch.prepare(events, state, _make_executor(), _run)

    assert batch.has_finish
    assert len(batch.action_events) == 2
    assert "3" not in batch.blocked_reasons  # truncated before we checked


def test_prepare_all_blocked():
    events = [_ae("a", "1"), _ae("b", "2")]
    state = _make_state({"1": "no", "2": "no"})
    executor = MagicMock()
    executor.execute_batch = MagicMock(return_value=[])

    batch = _ActionBatch.prepare(events, state, executor, _run)

    assert len(batch.blocked_reasons) == 2
    assert batch.results_by_id == {}
    assert executor.execute_batch.call_args[0][0] == []


def test_prepare_empty():
    batch = _ActionBatch.prepare([], _make_state(), _make_executor(), _run)
    assert batch.action_events == []
    assert not batch.has_finish
    assert batch.results_by_id == {}


# ── emit ──────────────────────────────────────────────────────────


def _obs(label: str) -> ObservationEvent:
    """Create a minimal ObservationEvent stub for testing."""
    obs = MagicMock(spec=ObservationEvent)
    obs._label = label
    return obs  # type: ignore[return-value]


def test_emit_results_in_order():
    o1, o2a, o2b = _obs("o1"), _obs("o2a"), _obs("o2b")
    events = [_ae("a", "1"), _ae("b", "2")]
    batch = _ActionBatch(
        action_events=events,
        has_finish=False,
        results_by_id={"1": [o1], "2": [o2a, o2b]},
    )
    emitted: list[Any] = []
    batch.emit(emitted.append)
    assert emitted == [o1, o2a, o2b]


def test_emit_blocked_produces_rejection():
    o2 = _obs("o2")
    events = [_ae("a", "1"), _ae("b", "2")]
    batch = _ActionBatch(
        action_events=events,
        has_finish=False,
        blocked_reasons={"1": "policy"},
        results_by_id={"2": [o2]},
    )
    emitted: list[Any] = []
    batch.emit(emitted.append)

    assert len(emitted) == 2
    assert isinstance(emitted[0], UserRejectObservation)
    assert emitted[0].rejection_reason == "policy"
    assert emitted[1] is o2


# ── finalize ──────────────────────────────────────────────────────


def test_finalize_noop_when_no_finish():
    batch = _ActionBatch(action_events=[_ae("a", "1")], has_finish=False)
    finished: list[bool] = []
    batch.finalize(
        on_event=lambda e: None,
        check_iterative_refinement=lambda ae: (False, None),
        mark_finished=lambda: finished.append(True),
    )
    assert finished == []


def test_finalize_marks_finished():
    events = [_ae(_F, "1")]
    batch = _ActionBatch(
        action_events=events,
        has_finish=True,
        results_by_id={"1": [_obs("o")]},
    )
    finished: list[bool] = []
    batch.finalize(
        on_event=lambda e: None,
        check_iterative_refinement=lambda ae: (False, None),
        mark_finished=lambda: finished.append(True),
    )
    assert finished == [True]


def test_finalize_emits_followup_on_refinement():
    events = [_ae(_F, "1")]
    batch = _ActionBatch(
        action_events=events,
        has_finish=True,
        results_by_id={"1": [_obs("o")]},
    )
    emitted: list[Any] = []
    batch.finalize(
        on_event=emitted.append,
        check_iterative_refinement=lambda ae: (True, "try again"),
        mark_finished=lambda: None,
    )
    assert len(emitted) == 1
    assert emitted[0].llm_message.content[0].text == "try again"


def test_finalize_noop_when_finish_blocked():
    events = [_ae(_F, "1")]
    batch = _ActionBatch(
        action_events=events,
        has_finish=True,
        blocked_reasons={"1": "denied"},
    )
    finished: list[bool] = []
    batch.finalize(
        on_event=lambda e: None,
        check_iterative_refinement=lambda ae: (False, None),
        mark_finished=lambda: finished.append(True),
    )
    assert finished == []
