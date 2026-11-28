"""Durable, evidence-linked recovery reports for stopped conversation runs."""

from __future__ import annotations

import json
from collections.abc import Iterable
from contextlib import nullcontext
from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event.base import Event, EventID
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.llm_convertible import AgentErrorEvent, MessageEvent
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.event.llm_convertible.observation import (
    ObservationEvent,
    UserRejectObservation,
)
from openhands.sdk.io import FileStore
from openhands.sdk.llm import TextContent
from openhands.sdk.utils.datetime import utc_now


RUN_RECOVERY_FILE = "run_recovery/latest.json"
MAX_RECOVERY_EVENTS = 200
MAX_ACTIVITY_ITEMS = 8
MAX_AGENT_UPDATE_CHARS = 400


class RunReceiptPhase(StrEnum):
    RUNNING = "running"
    SETTLED = "settled"


class RunOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    STUCK = "stuck"
    PAUSED = "paused"
    WAITING = "waiting"
    INTERRUPTED_UNKNOWN = "interrupted_unknown"


class EvidenceCoverage(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class RunReceipt(BaseModel):
    """Small persisted boundary for the latest execution attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    phase: RunReceiptPhase = RunReceiptPhase.RUNNING
    outcome: RunOutcome | None = None
    reason_code: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    start_event_id: EventID | None = None
    start_event_index: int | None = None
    end_event_id: EventID | None = None
    error_event_id: EventID | None = None
    owner_generation: int | None = None
    legacy_recovery: bool = False


class RecoveryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: EventID
    kind: str
    label: str


class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: EventID
    text: str


class IncompleteTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    status: str


class UncertainOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_event_id: EventID
    tool_name: str
    possible_side_effects: bool = True
    reason: str


class RecoveryReport(BaseModel):
    """A bounded projection of persisted events; never an LLM-generated claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    outcome: RunOutcome
    reason_code: str | None = None
    summary: str
    started_at: datetime
    ended_at: datetime | None = None
    error_event_id: EventID | None = None
    evidence_coverage: EvidenceCoverage
    omitted_event_count: int | None = None
    last_agent_update: AgentUpdate | None = None
    recent_activity: list[RecoveryEvidence] = Field(default_factory=list)
    incomplete_tasks: list[IncompleteTask] = Field(default_factory=list)
    uncertain_operations: list[UncertainOperation] = Field(default_factory=list)


def begin_run(
    state: ConversationState, *, owner_generation: int | None = None
) -> RunReceipt:
    """Persist a run boundary before initialization or agent execution begins."""
    with state:
        start_event_id = state._resolve_active_leaf()
        start_event_index = len(state.events)
    receipt = RunReceipt(
        start_event_id=start_event_id,
        start_event_index=start_event_index,
        owner_generation=owner_generation,
    )
    _write_receipt(state, receipt)
    return receipt


def legacy_interrupted_receipt(
    state: ConversationState, *, owner_generation: int | None = None
) -> RunReceipt:
    """Build a bounded, explicitly partial boundary for pre-receipt histories."""
    active_events = state.active_branch(limit=MAX_RECOVERY_EVENTS)
    user_messages = [
        event
        for event in active_events
        if isinstance(event, MessageEvent) and event.source == "user"
    ]
    if user_messages:
        start_event_id = user_messages[-1].id
        start_event_index = state.events.get_index(start_event_id) + 1
    else:
        start_event_id = None
        start_event_index = max(0, len(state.events) - MAX_RECOVERY_EVENTS)
    return RunReceipt(
        phase=RunReceiptPhase.RUNNING,
        reason_code="ServerRestarted",
        start_event_id=start_event_id,
        start_event_index=start_event_index,
        owner_generation=owner_generation,
        legacy_recovery=True,
    )


def finish_run(
    state: ConversationState,
    receipt: RunReceipt,
    *,
    reason_code: str | None = None,
    owner_generation: int | None = None,
    legacy_recovery: bool | None = None,
    outcome_override: RunOutcome | None = None,
) -> tuple[RunReceipt, RecoveryReport]:
    """Finalize a run from its status and bounded persisted event ancestry."""
    with state:
        status = state.execution_status
        try:
            end_event_id = state._resolve_active_leaf()
            events, coverage, omitted_count = _events_for_run(
                state, receipt, end_event_id
            )
        except Exception:
            end_event_id = None
            events = []
            coverage = EvidenceCoverage.UNAVAILABLE
            omitted_count = None
    errors = [event for event in events if isinstance(event, ConversationErrorEvent)]
    restart_errors = [
        event
        for event in events
        if isinstance(event, AgentErrorEvent)
        and event.recovery_reason == "server_restart"
    ]
    last_error = errors[-1] if errors else None
    final_reason = reason_code or (
        last_error.code if last_error else "ServerRestarted" if restart_errors else None
    )
    outcome = outcome_override or _outcome_for_status(status)
    if outcome == RunOutcome.FAILED and final_reason is None:
        final_reason = "UnknownRunFailure"
    error_event_id = (
        last_error.id
        if last_error
        else restart_errors[-1].id
        if restart_errors
        else None
    )
    final_receipt = receipt.model_copy(
        update={
            "phase": RunReceiptPhase.SETTLED,
            "outcome": outcome,
            "reason_code": final_reason,
            "ended_at": utc_now(),
            "end_event_id": end_event_id,
            "error_event_id": error_event_id,
            "owner_generation": (
                owner_generation
                if owner_generation is not None
                else receipt.owner_generation
            ),
            "legacy_recovery": (
                receipt.legacy_recovery if legacy_recovery is None else legacy_recovery
            ),
        }
    )
    report = build_recovery_report(
        final_receipt,
        events,
        status,
        evidence_coverage=coverage,
        omitted_event_count=omitted_count,
        mask_text=state.secret_registry.mask_secrets_in_output,
    )
    _write_receipt(
        state,
        final_receipt,
        report=report,
        expected_run_id=None if receipt.legacy_recovery else receipt.run_id,
        expected_owner_generation=(
            None if receipt.legacy_recovery else receipt.owner_generation
        ),
    )
    return final_receipt, report


def read_receipt(
    file_store: FileStore,
) -> tuple[RunReceipt, RecoveryReport | None] | None:
    """Load the latest persisted receipt, returning None for older conversations."""
    try:
        payload = file_store.read(RUN_RECOVERY_FILE)
    except FileNotFoundError:
        return None
    decoded = json.loads(payload)
    receipt = RunReceipt.model_validate(decoded["receipt"])
    report_payload = decoded.get("report")
    report = RecoveryReport.model_validate(report_payload) if report_payload else None
    return receipt, report


def _write_receipt(
    state: ConversationState,
    receipt: RunReceipt,
    *,
    report: RecoveryReport | None = None,
    expected_run_id: str | None = None,
    expected_owner_generation: int | None = None,
) -> None:
    payload = json.dumps(
        {
            "receipt": receipt.model_dump(mode="json"),
            "report": report.model_dump(mode="json") if report else None,
        },
        separators=(",", ":"),
    )
    write_guard = state._write_guard or nullcontext
    with state._fs.lock(RUN_RECOVERY_FILE):
        with write_guard():
            if expected_run_id is not None:
                current = read_receipt(state._fs)
                if (
                    current is None
                    or current[0].run_id != expected_run_id
                    or current[0].owner_generation != expected_owner_generation
                ):
                    raise RuntimeError("Run recovery receipt was superseded")
            state._fs.write(RUN_RECOVERY_FILE, payload)


def _events_for_run(
    state: ConversationState,
    receipt: RunReceipt,
    end_event_id: EventID | None,
) -> tuple[list[Event], EvidenceCoverage, int | None]:
    event_log = state.events
    tree_events: list[Event] = []
    reached_boundary = receipt.start_event_id is None
    if end_event_id is not None:
        walk = event_log.walk_back(end_event_id, until=receipt.start_event_id)
        for event in walk:
            event_index = event_log.get_index(event.id)
            if event.id == receipt.start_event_id:
                reached_boundary = True
                break
            if (
                receipt.start_event_index is not None
                and event_index < receipt.start_event_index
            ):
                break
            if len(tree_events) == MAX_RECOVERY_EVENTS:
                return list(reversed(tree_events)), EvidenceCoverage.PARTIAL, None
            tree_events.append(event)

    if receipt.start_event_index is not None:
        event_start = receipt.start_event_index
    elif receipt.start_event_id is not None:
        try:
            event_start = event_log.get_index(receipt.start_event_id) + 1
        except KeyError:
            event_start = len(event_log)
            reached_boundary = False
    else:
        event_start = 0
        reached_boundary = True

    error_scan_start = max(event_start, len(event_log) - MAX_RECOVERY_EVENTS)
    run_errors: list[Event] = []
    for index in range(error_scan_start, len(event_log)):
        event = event_log[index]
        if isinstance(event, ConversationErrorEvent):
            run_errors.append(event)
    combined = {event.id: event for event in tree_events}
    combined.update({event.id: event for event in run_errors})
    events = sorted(combined.values(), key=lambda event: event_log.get_index(event.id))
    if len(events) > MAX_RECOVERY_EVENTS:
        events = events[-MAX_RECOVERY_EVENTS:]
        return events, EvidenceCoverage.PARTIAL, None
    if receipt.legacy_recovery or error_scan_start > event_start:
        reached_boundary = False
    coverage = (
        EvidenceCoverage.COMPLETE if reached_boundary else EvidenceCoverage.PARTIAL
    )
    return events, coverage, 0 if coverage == EvidenceCoverage.COMPLETE else None


def _outcome_for_status(status: ConversationExecutionStatus) -> RunOutcome:
    return {
        ConversationExecutionStatus.FINISHED: RunOutcome.COMPLETED,
        ConversationExecutionStatus.ERROR: RunOutcome.FAILED,
        ConversationExecutionStatus.STUCK: RunOutcome.STUCK,
        ConversationExecutionStatus.PAUSED: RunOutcome.PAUSED,
        ConversationExecutionStatus.WAITING_FOR_CONFIRMATION: RunOutcome.WAITING,
        ConversationExecutionStatus.IDLE: RunOutcome.WAITING,
        ConversationExecutionStatus.RUNNING: RunOutcome.INTERRUPTED_UNKNOWN,
    }.get(status, RunOutcome.INTERRUPTED_UNKNOWN)


def build_recovery_report(
    receipt: RunReceipt,
    events: Iterable[Event],
    status: ConversationExecutionStatus,
    *,
    evidence_coverage: EvidenceCoverage = EvidenceCoverage.COMPLETE,
    omitted_event_count: int | None = 0,
    mask_text=lambda text: text,
) -> RecoveryReport:
    """Create a safe report from public text and typed event relationships."""
    event_list = list(events)
    outcome = receipt.outcome or _outcome_for_status(status)
    last_update: AgentUpdate | None = None
    activities: list[RecoveryEvidence] = []
    latest_tasks: list[IncompleteTask] = []
    actions: dict[EventID, ActionEvent] = {}
    completed_actions: set[EventID] = set()
    uncertain_action_reasons: dict[EventID, str] = {}

    for event in event_list:
        if isinstance(event, MessageEvent) and event.source == "agent":
            text = "".join(
                item.text
                for item in event.llm_message.content
                if isinstance(item, TextContent)
            ).strip()
            if text:
                last_update = AgentUpdate(
                    event_id=event.id,
                    text=mask_text(text)[:MAX_AGENT_UPDATE_CHARS],
                )
                activities.append(
                    RecoveryEvidence(
                        event_id=event.id,
                        kind="agent_update",
                        label="Agent update",
                    )
                )
        elif isinstance(event, ActionEvent):
            if event.action is not None:
                actions[event.id] = event
            activities.append(
                RecoveryEvidence(
                    event_id=event.id,
                    kind="tool_call",
                    label=f"{event.tool_name} requested",
                )
            )
        elif isinstance(event, ObservationEvent):
            completed_actions.add(event.action_id)
            observation_data = event.observation.model_dump(mode="python")
            task_list = observation_data.get("task_list")
            if isinstance(task_list, list):
                latest_tasks = [
                    IncompleteTask(
                        title=mask_text(str(task.get("title", "Task")))[:200],
                        status=mask_text(str(task.get("status", "todo")))[:80],
                    )
                    for task in task_list
                    if isinstance(task, dict) and task.get("status") != "done"
                ][:20]
            if observation_data.get("exit_code") == -1:
                uncertain_action_reasons[event.action_id] = (
                    "The command timed out while its process may still be running."
                )
            activities.append(
                RecoveryEvidence(
                    event_id=event.id,
                    kind="tool_result",
                    label=f"{event.tool_name} result recorded",
                )
            )
        elif isinstance(event, UserRejectObservation):
            completed_actions.add(event.action_id)
            activities.append(
                RecoveryEvidence(
                    event_id=event.id,
                    kind="tool_rejected",
                    label=f"{event.tool_name} rejected",
                )
            )
        elif (
            isinstance(event, AgentErrorEvent)
            and event.recovery_reason
            and event.parent_id is not None
        ):
            uncertain_action_reasons[event.parent_id] = (
                "The operation was interrupted before a final result was recorded."
            )
        elif isinstance(event, ConversationErrorEvent):
            activities.append(
                RecoveryEvidence(
                    event_id=event.id,
                    kind="run_error",
                    label="Run stopped",
                )
            )

    uncertain: list[UncertainOperation] = []
    for action_id, action in actions.items():
        if action_id in uncertain_action_reasons:
            reason = uncertain_action_reasons[action_id]
        elif action_id not in completed_actions:
            reason = "No completed result was recorded for this operation."
        else:
            continue
        uncertain.append(
            UncertainOperation(
                action_event_id=action.id,
                tool_name=action.tool_name,
                reason=reason,
            )
        )

    summary = _summary_for(outcome, receipt.reason_code)
    return RecoveryReport(
        run_id=receipt.run_id,
        outcome=outcome,
        reason_code=receipt.reason_code,
        summary=summary,
        started_at=receipt.started_at,
        ended_at=receipt.ended_at,
        error_event_id=receipt.error_event_id,
        evidence_coverage=evidence_coverage,
        omitted_event_count=omitted_event_count,
        last_agent_update=last_update,
        recent_activity=activities[-MAX_ACTIVITY_ITEMS:],
        incomplete_tasks=latest_tasks,
        uncertain_operations=uncertain[:MAX_ACTIVITY_ITEMS],
    )


def _summary_for(outcome: RunOutcome, reason_code: str | None) -> str:
    if reason_code == "MaxBudgetReached":
        return "The configured spending limit was reached."
    if reason_code == "MaxIterationsReached":
        return "The run reached its step limit."
    if reason_code == "LLMRateLimitError":
        return "The model provider rate limit was reached."
    if reason_code in {"LLMAuthenticationError", "ACPAuthRequired"}:
        return "The model provider rejected authentication. Check provider settings."
    if reason_code == "ServerRestarted":
        return "The server restarted before the run recorded a final result."
    if outcome == RunOutcome.STUCK:
        return "The agent stopped after a repeated or unproductive pattern."
    if outcome == RunOutcome.INTERRUPTED_UNKNOWN:
        return "The previous run did not record a final outcome."
    if outcome == RunOutcome.FAILED:
        return "The agent stopped after an error. Review the recorded error details."
    if outcome == RunOutcome.PAUSED:
        return "The run was paused. Review any operation without a final result."
    if outcome == RunOutcome.WAITING:
        return "The agent is waiting for confirmation or user input."
    return "The agent completed this run."
