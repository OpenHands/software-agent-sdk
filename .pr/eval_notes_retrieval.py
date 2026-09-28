"""Small paired recall evaluation; run --offline to validate the harness for free.

Live: set LLM_API_KEY and run this script; optional --llm-config supplies LLM kwargs.
Default model: gemini/gemini-3.5-flash-lite. Never commit credentials.
Use --suite challenge for nine trials covering repeated resets and delayed questions.
Use --resume to skip finished trials and retry incomplete trials in fresh conversations.
SDK estimated-cost limits require available pricing; request caps include SDK retries.
An in-flight request can exceed the USD budget. Offline outputs are not quality evidence.
"""  # noqa: E501

import argparse
import hashlib
import json
import math
import os
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, Literal
from uuid import uuid4

import litellm
from eval_memory_cases import MemoryCase, make_challenge_cases
from eval_retrieval_evidence import inspect_history_retrieval
from eval_transport import PacedLLM, RequestGate, RequestLimitReached
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, LocalConversation, StorageSafetyConfig
from openhands.sdk.context.condenser import (
    LLMSummarizingCondenser,
    NotesRetrievalCondenser,
)
from openhands.sdk.context.view import View
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import (
    ActionEvent,
    Condensation,
    ContextWindowReminderEvent,
    HistoryIndexEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.event.base import N_CHAR_PREVIEW
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.llm.exceptions import (
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins.context_notes import (
    ContextNotesObservation,
    get_context_notes,
)
from openhands.sdk.tool.builtins.conversation_history import (
    ConversationHistoryObservation,
    history_event_text,
)
from openhands.sdk.tool.builtins.finish import FinishAction


Condition = Literal["summary", "notes_only", "notes_history"]
CONDITIONS: tuple[Condition, ...] = ("summary", "notes_only", "notes_history")


class NotesOnlyIndex(HistoryIndexEvent):
    def to_llm_message(self) -> Message:
        return _text_message(
            "Context was reset. Use context_notes(command='read') to recover "
            "saved progress. No original-history retrieval tool is available.",
            role="user",
        )


class NotesOnlyReminder(ContextWindowReminderEvent):
    def to_llm_message(self) -> Message:
        return _text_message(
            "Context is nearing its limit. Save relevant progress in context_notes.",
            role="user",
        )


class NotesOnlyCondenser(NotesRetrievalCondenser):
    def required_tools(self) -> frozenset[str]:
        return frozenset({"context_notes"})

    def get_condensation(
        self, view: View, agent_llm: LLM | None = None
    ) -> HistoryIndexEvent:
        index = super().get_condensation(view, agent_llm)
        return NotesOnlyIndex(**index.model_dump(exclude={"kind"}))

    def get_reminder(
        self, view: View, agent_llm: LLM | None = None
    ) -> ContextWindowReminderEvent | None:
        reminder = super().get_reminder(view, agent_llm)
        return (
            NotesOnlyReminder(**reminder.model_dump(exclude={"kind"}))
            if reminder is not None
            else None
        )


@dataclass(frozen=True)
class Scenario:
    name: str
    facts: tuple[str, str]
    question: str
    expected: dict[str, str | int]
    query: str
    delayed_question: bool = False
    require_full_read: bool = False
    target_marker: str = ""

    @property
    def windows(self) -> tuple[tuple[str, ...], ...]:
        return (self.facts,)


Case = Scenario | MemoryCase


SCENARIOS = (
    Scenario(
        "exact_identifier",
        (
            "The release artifact ID is Atlas-Q7P2-4916-xB9. Preserve it exactly.",
            "Atlas-Q7P2-4916-x89 is a lookalike from a failed build; do not use it.",
        ),
        'Return the accepted release artifact ID as JSON {"artifact_id": "..."}.',
        {"artifact_id": "Atlas-Q7P2-4916-xB9"},
        "release artifact",
    ),
    Scenario(
        "superseded_fact",
        (
            "The deployment approval ticket is QX-17, awaiting verification.",
            "Correction: QX-17 was revoked. The current approval ticket is QX-71.",
        ),
        'Return the current approval ticket as JSON {"approval_ticket": "..."}.',
        {"approval_ticket": "QX-71"},
        "approval ticket",
    ),
    Scenario(
        "compound_constraints",
        (
            "Production region is eu-north-1; timeout is 17 seconds; retry count is 3.",
            "For production only, increase retry count to 5. Region and timeout "
            "stay unchanged. Staging uses us-west-2, timeout 30, retry count 2.",
        ),
        "Return production settings as JSON with region, timeout_seconds and retries.",
        {"region": "eu-north-1", "timeout_seconds": 17, "retries": 5},
        "production",
    ),
)


def _text_message(
    text: str, role: Literal["user", "assistant"] = "assistant"
) -> Message:
    return Message(role=role, content=[TextContent(text=text)])


def _tool_message(name: str, arguments: dict) -> Message:
    return Message(
        role="assistant",
        tool_calls=[
            MessageToolCall(
                id=f"call_{uuid4().hex}",
                name=name,
                arguments=json.dumps(arguments),
                origin="completion",
            )
        ],
    )


def _scripted_llm(scenario: Case, condition: Condition) -> TestLLM:
    messages = []
    for window_index, window in enumerate(scenario.windows):
        messages.extend(_text_message("ACK") for _ in range(len(window) + 3))
        if condition != "summary":
            if window_index:
                messages.append(_tool_message("context_notes", {"command": "read"}))
            content = (
                "Original archive is in conversation history: " + scenario.query
                if scenario.require_full_read and condition == "notes_history"
                else json.dumps(scenario.expected)
            )
            messages.append(
                _tool_message("context_notes", {"command": "write", "content": content})
            )
        messages.append(_text_message("READY"))
    return TestLLM.from_messages(messages, usage_id="agent")


def _offline_recall_messages(
    scenario: Case, condition: Condition, source_texts: dict[str, str]
) -> list[Message]:
    messages = []
    if condition != "summary":
        messages.append(_tool_message("context_notes", {"command": "read"}))
    if condition == "notes_history":
        messages.append(
            _tool_message(
                "conversation_history", {"command": "search", "query": scenario.query}
            )
        )
        if scenario.require_full_read:
            source_id, source_text = next(
                (event_id, text)
                for event_id, text in source_texts.items()
                if scenario.target_marker in text
            )
            for offset in range(0, len(source_text), 8000):
                messages.append(
                    _tool_message(
                        "conversation_history",
                        {
                            "command": "read",
                            "event_id": source_id,
                            "offset": offset,
                            "limit": 8000,
                        },
                    )
                )
    messages.append(_text_message(json.dumps(scenario.expected)))
    return messages


def _make_agent(
    condition: Condition,
    scenario: Case,
    config: dict,
    offline: bool,
    gate: RequestGate,
) -> Agent:
    agent_config = {**config, "usage_id": "agent"}
    llm = _scripted_llm(scenario, condition) if offline else PacedLLM(**agent_config)
    if isinstance(llm, PacedLLM):
        llm.bind_gate(gate)
        if llm.uses_responses_api():
            raise ValueError("This evaluation requires the chat-completions transport")
    tools = ["FinishTool"]
    if condition == "summary":
        summary_llm = (
            TestLLM.from_messages(
                [
                    _text_message(json.dumps(scenario.expected))
                    for _ in scenario.windows
                ],
                usage_id="condenser",
            )
            if offline
            else PacedLLM(**{**config, "usage_id": "condenser"})
        )
        if isinstance(summary_llm, PacedLLM):
            summary_llm.bind_gate(gate)
        condenser = LLMSummarizingCondenser(llm=summary_llm, max_size=240, keep_first=1)
    else:
        tools.append("ContextNotesTool")
        condenser_type = (
            NotesOnlyCondenser if condition == "notes_only" else NotesRetrievalCondenser
        )
        condenser = condenser_type(max_size=240, keep_first=1, keep_recent=1)
        if condition == "notes_history":
            tools.append("ConversationHistoryTool")
    return Agent(llm=llm, tools=[], include_default_tools=tools, condenser=condenser)


def _last_answer(conversation: LocalConversation) -> str:
    for event in reversed(conversation.state.active_branch()):
        if isinstance(event, MessageEvent) and event.source == "agent":
            return "\n".join(
                part.text
                for part in event.llm_message.content
                if isinstance(part, TextContent)
            )
        if isinstance(event, ActionEvent) and isinstance(event.action, FinishAction):
            return event.action.message
    return ""


def _grade(answer: str, expected: dict) -> bool:
    text = answer.strip()
    if text.startswith("```") and text.endswith("```"):
        text = "\n".join(text.splitlines()[1:-1])
    try:
        return json.loads(text) == expected
    except json.JSONDecodeError:
        return False


class EvaluationBudgetReached(RuntimeError):
    pass


def _error_chain(exc: BaseException) -> list[str]:
    names = []
    seen: set[int] = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        names.append(type(exc).__name__)
        cause = (
            exc.original_exception
            if isinstance(exc, ConversationRunError)
            else exc.__cause__
        )
        if cause is None:
            break
        exc = cause
    return names


def _save(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def run_trial(
    condition: Condition,
    scenario: Case,
    repeat: int,
    config: dict,
    offline: bool,
    budget: float,
    directory: Path,
    gate: RequestGate,
) -> dict:
    directory.mkdir(parents=True)
    fixture = {
        "name": scenario.name,
        "windows": scenario.windows,
        "question": scenario.question,
        "expected": scenario.expected,
        "query": scenario.query,
        "target_marker": scenario.target_marker,
    }
    _save(directory / "case.json", fixture)
    fixture_hash = hashlib.sha256(
        json.dumps(fixture, sort_keys=True).encode()
    ).hexdigest()
    conversation: LocalConversation | None = None
    start = time.monotonic()
    start_attempts = gate.attempted_requests
    previous_callback = gate.on_attempt
    source_ids: list[str] = []
    reset_checks: list[dict] = []
    notes_before_question = ""
    question_event_id: str | None = None
    error_chain: list[str] = []
    answer = ""
    hidden_sources = False
    raw_context_hidden = False
    storage_before_reset: int | None = None
    reset_position = 0
    view_summary: dict = {}
    metrics_before_reset: dict = {}
    stage = "initialization"

    def disk_bytes() -> int:
        return sum(
            path.stat().st_size
            for path in (directory / "state").rglob("*")
            if path.is_file()
        )

    def check_budget() -> None:
        assert conversation is not None
        if (
            conversation.conversation_stats.get_combined_metrics().accumulated_cost
            >= budget
        ):
            raise EvaluationBudgetReached("Reported trial cost limit reached")

    def before_attempt(attempt: int) -> None:
        check_budget()
        if previous_callback is not None:
            previous_callback(attempt)

    def run_agent() -> None:
        assert conversation is not None
        check_budget()
        conversation.run()
        if conversation.state.execution_status != ConversationExecutionStatus.FINISHED:
            raise RuntimeError("Conversation did not finish the requested turn")

    def run_turn(prompt: str) -> None:
        assert conversation is not None
        conversation.send_message(prompt)
        run_agent()

    try:
        agent = _make_agent(condition, scenario, config, offline, gate)
        conversation = LocalConversation(
            agent=agent,
            workspace=directory,
            persistence_dir=directory / "state",
            storage_safety=StorageSafetyConfig(min_free_ratio=0.05),
            visualizer=None,
            max_iteration_per_run=12 if scenario.require_full_read else 8,
            max_budget_per_run=budget,
        )
        conversation.llm_registry.subscribe(
            conversation.conversation_stats.register_llm
        )
        conversation.llm_registry.add(agent.llm)
        if isinstance(agent.condenser, LLMSummarizingCondenser):
            conversation.llm_registry.add(agent.condenser.llm)
        gate.on_attempt = before_attempt
        for window_index, window in enumerate(scenario.windows):
            stage = f"window_{window_index + 1}_seed"
            for fact in window:
                prompt = (
                    "Project record for the ongoing task. Use any available memory "
                    "mechanism as needed and briefly acknowledge. "
                    if scenario.delayed_question
                    else "Remember the following for a later exact-recall question. "
                    "Use only available tools if needed; briefly acknowledge. "
                )
                conversation.send_message(prompt + fact)
                source_ids.append(conversation.state.active_branch()[-1].id)
                run_agent()
            stage = f"window_{window_index + 1}_distractors"
            for batch in range(3):
                noise = "\n".join(
                    (
                        f"Log batch={batch} row={i}: staging-cache hit; unrelated job "
                        f"TEMP-{batch}-{i:04d}; elapsed_ms={i % 19}."
                        if isinstance(scenario, Scenario)
                        else f"Log window={window_index} batch={batch} row={i}: "
                        f"staging-cache hit; unrelated job TEMP-{batch}-{i:04d}; "
                        f"elapsed_ms={i % 19}."
                    )
                    for i in range(80)
                )
                run_turn(
                    "Unrelated logs; do not treat these as corrections. Reply ACK.\n"
                    + noise
                )
            stage = f"window_{window_index + 1}_checkpoint"
            run_turn(
                "Checkpoint: a context reset is next. Preserve the facts relevant to "
                "the pending recall task using any available memory mechanism. Do not "
                "repeat those facts in your response; reply only READY."
                if isinstance(scenario, Scenario)
                else "Checkpoint: a context reset is next. Preserve useful project "
                "information across all windows using any available memory "
                "mechanism. The follow-up question will be supplied later. "
                "Do not repeat saved facts in your response; reply only READY."
            )
            raw_ids = {
                event.id
                for event in conversation.state.active_branch()
                if isinstance(event, (MessageEvent, ActionEvent, ObservationEvent))
            }
            conversation.send_message(
                "Reset boundary. Await the next recall question."
                if isinstance(scenario, Scenario)
                else "Reset boundary. Await the next task message."
            )
            storage_before_reset = disk_bytes()
            metrics_before_reset = (
                conversation.conversation_stats.get_combined_metrics()
                .get_snapshot()
                .model_dump(mode="json")
            )
            stage = f"window_{window_index + 1}_reset"
            check_budget()
            conversation.condense()
            view = conversation.state.view
            active_ids = {event.id for event in view.events}
            hidden_sources = not active_ids.intersection(source_ids)
            raw_context_hidden = not active_ids.intersection(raw_ids)
            rendered = [
                event.to_llm_message().model_dump(mode="json") for event in view.events
            ]
            _save(
                directory / f"view-after-reset-{window_index + 1}.json",
                {"messages": rendered},
            )
            _save(directory / "view-before-retrieval.json", {"messages": rendered})
            view_summary = {
                "event_count": len(view.events),
                "messages_utf8_bytes": len(
                    json.dumps(rendered, ensure_ascii=False).encode()
                ),
                "all_prior_raw_messages_hidden": raw_context_hidden,
            }
            reset_position = len(conversation.state.active_branch())
            applied = [
                event
                for event in conversation.state.active_branch()
                if isinstance(event, (Condensation, HistoryIndexEvent))
            ]
            reset_checks.append(
                {
                    "window": window_index + 1,
                    "reset_event_id": applied[-1].id if applied else None,
                    "reset_count": len(applied),
                    "sources_hidden": hidden_sources,
                    "source_event_ids": list(source_ids),
                    "post_reset_view": view_summary,
                    "metrics": conversation.conversation_stats.get_combined_metrics()
                    .get_snapshot()
                    .model_dump(mode="json"),
                    "storage_bytes": disk_bytes(),
                }
            )
            if (
                not hidden_sources
                or len(applied) != window_index + 1
                or (condition != "summary" and not raw_context_hidden)
            ):
                raise AssertionError(
                    "Reset missing or original source/raw notes retained"
                )
        notes_before_question = get_context_notes(conversation.state.active_branch())[1]
        if offline:
            source_texts = {
                event.id: text
                for event in conversation.state.active_branch()
                if event.id in source_ids
                and (text := history_event_text(event)) is not None
            }
            conversation.switch_llm(
                TestLLM.from_messages(
                    _offline_recall_messages(scenario, condition, source_texts),
                    usage_id="recall",
                )
            )
        stage = "recall"
        conversation.send_message(
            scenario.question + " Use available memory tools when needed. JSON only."
        )
        question_event_id = conversation.state.active_branch()[-1].id
        run_agent()
        answer = _last_answer(conversation)
        stage = "complete"
    except (Exception, KeyboardInterrupt) as exc:
        error_chain = _error_chain(exc)
    finally:
        gate.on_attempt = previous_callback
        if conversation is not None:
            conversation.close()

    record: dict[str, Any] = {
        "condition": condition,
        "scenario": scenario.name,
        "case_sha256": fixture_hash,
        "repeat": repeat,
        "error_chain": error_chain,
        "stage": stage,
        "quality_evidence": not offline,
        "answer": answer,
        "sources_hidden": hidden_sources,
        "source_event_ids": source_ids,
        "post_reset_view": view_summary,
        "reset_checks": reset_checks,
        "expected_reset_count": len(scenario.windows),
        "expected": scenario.expected,
        "question_event_id": question_event_id,
        "question_delayed_until_after_reset": scenario.delayed_question,
        "full_read_requested_by_protocol": scenario.require_full_read,
        "target_in_notes_before_question": bool(scenario.target_marker)
        and scenario.target_marker in notes_before_question,
        "elapsed_seconds": time.monotonic() - start,
        "provider_attempts": gate.attempted_requests - start_attempts,
        "artifacts": str(directory),
    }
    transient_names = {
        LLMServiceUnavailableError.__name__,
        LLMRateLimitError.__name__,
        LLMTimeoutError.__name__,
        RequestLimitReached.__name__,
        EvaluationBudgetReached.__name__,
        "KeyboardInterrupt",
    }
    record["outcome"] = (
        "INCONCLUSIVE"
        if transient_names.intersection(error_chain)
        else "FAIL"
        if error_chain
        else "COMPLETE"
    )
    record["eligible"] = not error_chain and hidden_sources
    record["exact_match"] = _grade(answer, scenario.expected)
    if conversation is None:
        return record
    metrics = conversation.conversation_stats.get_combined_metrics()
    events = conversation.state.active_branch()
    after = events[reset_position:] if reset_position else []
    calls = Counter(
        event.tool_name for event in events if isinstance(event, ActionEvent)
    )
    post_calls = Counter(
        event.tool_name for event in after if isinstance(event, ActionEvent)
    )
    retrieval_results = [
        event.observation
        for event in after
        if isinstance(event, ObservationEvent)
        and (
            isinstance(event.observation, ConversationHistoryObservation)
            or isinstance(event.observation, ContextNotesObservation)
            and event.observation.command == "read"
        )
    ]
    hidden_history_results = 0
    for result in retrieval_results:
        if not isinstance(result, ConversationHistoryObservation) or result.is_error:
            continue
        payload = json.loads(result.text)
        if payload.get("in_active_view") is False or any(
            match["in_active_view"] is False for match in payload.get("matches", [])
        ):
            hidden_history_results += 1
    storage_after = disk_bytes()
    usage_metrics = conversation.conversation_stats.usage_to_metrics
    record.update(
        {
            "history_evidence": inspect_history_retrieval(
                events, reset_position, scenario.target_marker, source_ids
            ),
            "native_summary_source_preview": {
                "message_text_preview_limit": N_CHAR_PREVIEW,
                "target_marker_visible": bool(scenario.target_marker)
                and any(
                    scenario.target_marker in str(event)
                    for event in events
                    if event.id in source_ids
                ),
            },
            "execution_status": conversation.state.execution_status.value,
            "runtime_error_codes": [
                event.code
                for event in conversation.state.events
                if isinstance(event, ConversationErrorEvent)
            ],
            "reset_count": sum(
                isinstance(event, (Condensation, HistoryIndexEvent)) for event in events
            ),
            "tool_calls": dict(calls),
            "post_reset_tool_calls": dict(post_calls),
            "retrieval_overhead": {
                "response_count": len(retrieval_results),
                "response_utf8_bytes": sum(
                    len(result.text.encode()) for result in retrieval_results
                ),
                "error_count": sum(result.is_error for result in retrieval_results),
                "hidden_history_response_count": hidden_history_results,
            },
            "metrics": metrics.get_snapshot().model_dump(mode="json"),
            "metrics_before_reset": metrics_before_reset,
            "metrics_by_usage": {
                usage: value.get_snapshot().model_dump(mode="json")
                for usage, value in usage_metrics.items()
            },
            "usage_recorded": bool(metrics.token_usages),
            "cost_estimate_available": bool(metrics.costs)
            and len(metrics.costs) == len(metrics.response_latencies),
            "storage_bytes": storage_after,
            "storage_growth_after_reset_bytes": (
                storage_after - storage_before_reset
                if storage_before_reset is not None
                else None
            ),
            "notes_chars": len(get_context_notes(events)[1]),
        }
    )
    return record


def _key(record: dict) -> str:
    return f"{record['repeat']}/{record['scenario']}/{record['condition']}"


def summarize(report: dict) -> None:
    latest = {_key(record): record for record in report["runs"]}
    eligible = [record for record in latest.values() if record["eligible"]]
    paired = {
        (record["scenario"], record["repeat"])
        for record in eligible
        if sum(
            other["scenario"] == record["scenario"]
            and other["repeat"] == record["repeat"]
            for other in eligible
        )
        == len(CONDITIONS)
    }
    report["completed_runs"] = sum(
        record["outcome"] != "INCONCLUSIVE" for record in latest.values()
    )
    report["complete_paired_groups"] = len(paired)
    report["summary"] = {}
    for condition in CONDITIONS:
        records = [
            record
            for record in eligible
            if record["condition"] == condition
            and (record["scenario"], record["repeat"]) in paired
        ]
        report["summary"][condition] = {
            "paired_samples": len(records),
            "exact_matches": sum(record["exact_match"] for record in records),
            "exact_match_rate": sum(record["exact_match"] for record in records)
            / len(records)
            if records
            else None,
            "input_tokens": sum(
                record["metrics"]["accumulated_token_usage"]["prompt_tokens"]
                for record in records
            ),
            "output_tokens": sum(
                record["metrics"]["accumulated_token_usage"]["completion_tokens"]
                for record in records
            ),
            "usage_recorded_for_all": bool(records)
            and all(record["usage_recorded"] for record in records),
            "storage_bytes": sum(record["storage_bytes"] for record in records),
            "retrieval_response_utf8_bytes": sum(
                record["retrieval_overhead"]["response_utf8_bytes"]
                for record in records
            ),
            "provider_attempts": sum(record["provider_attempts"] for record in records),
            "elapsed_seconds": sum(record["elapsed_seconds"] for record in records),
            "full_read_verified_trials": sum(
                record.get("history_evidence", {}).get("full_read_evidence", False)
                for record in records
            ),
            "search_to_read_verified_trials": sum(
                record.get("history_evidence", {}).get("search_to_read_evidence", False)
                for record in records
            ),
        }
    cost = sum(
        record.get("metrics", {}).get("accumulated_cost", 0)
        for record in report["runs"]
    )
    cost_complete = (
        bool(report["runs"])
        and not report["interrupted_trials"]
        and all(
            record.get("cost_estimate_available", False) for record in report["runs"]
        )
    )
    report["sdk_reported_cost_usd"] = cost
    report["estimated_cost_usd"] = cost if cost_complete else None
    report["cost_estimate_complete"] = cost_complete
    report["excluded_or_pending_runs"] = report["planned_runs"] - len(eligible)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm-config", type=Path)
    parser.add_argument(
        "--budget",
        type=float,
        default=1.0,
        help="Soft SDK estimated USD limit; unavailable pricing cannot enforce it.",
    )
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--suite", choices=("baseline", "challenge"), default="baseline"
    )
    parser.add_argument("--seed", type=int, default=4916)
    parser.add_argument("--repeats", type=int)
    parser.add_argument(
        "--max-requests",
        type=int,
        default=None,
        help="Total dispatch attempts, including retries and prior invocations.",
    )
    parser.add_argument("--request-interval", type=float, default=6.0)
    parser.add_argument(
        "--max-trials",
        type=int,
        default=18,
        help="Trials in this invocation; --resume continues remaining trials.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args()
    args.repeats = (
        args.repeats
        if args.repeats is not None
        else (1 if args.suite == "challenge" else 2)
    )
    args.max_requests = (
        args.max_requests
        if args.max_requests is not None
        else (180 if args.suite == "challenge" else 250)
    )
    output_root = Path(".pr/evidence") / (
        "notes-challenge" if args.suite == "challenge" else "notes-comparison"
    )
    args.output = args.output or output_root / "report.json"
    args.artifacts_dir = args.artifacts_dir or output_root / "runs"
    if not math.isfinite(args.budget) or args.budget <= 0:
        parser.error("--budget must be positive and finite")
    if min(args.repeats, args.max_requests, args.max_trials) <= 0:
        parser.error("--repeats, --max-requests and --max-trials must be positive")
    if not math.isfinite(args.request_interval) or args.request_interval < 6:
        parser.error("--request-interval must be at least six seconds")
    config = json.loads(args.llm_config.read_text()) if args.llm_config else {}
    if not isinstance(config, dict):
        parser.error("--llm-config must contain a JSON object of LLM kwargs")
    config = {
        "model": os.environ.get("LLM_MODEL", "gemini/gemini-3.5-flash-lite"),
        "reasoning_effort": None,
        "max_output_tokens": 2048,
        **config,
        "num_retries": 3,
        "retry_multiplier": 20,
        "retry_min_wait": 20,
        "retry_max_wait": 60,
        "timeout": 90,
        "caching_prompt": False,
        "stream": False,
        "fallback_strategy": None,
    }
    public_config = {key: value for key, value in config.items() if key != "api_key"}
    fingerprint = hashlib.sha256(
        json.dumps(public_config, sort_keys=True).encode()
    ).hexdigest()
    protocol = {
        "version": 2,
        "repeats": args.repeats,
        "model": config["model"],
        "config_sha256": fingerprint,
        "offline": args.offline,
    }
    if args.suite == "challenge":
        protocol.update(version=3, suite=args.suite, seed=args.seed)
    if args.resume:
        if not args.output.exists():
            parser.error("--resume requires an existing report")
        report = json.loads(args.output.read_text())
        if report["configuration"] != protocol:
            parser.error(
                "Resume requires the same model, LLM configuration, "
                "repeats and offline mode"
            )
        if (
            args.max_requests != report["max_requests"]
            or args.budget != report["budget_usd"]
        ):
            parser.error("Resume must preserve the request cap and USD budget")
        if report["active_trial"] is not None:
            report["status"] = "PAUSED_UNCONFIRMED_USAGE"
            _save(args.output, report)
            parser.error(
                "The previous process exited without finalizing its active trial. "
                "Its in-flight cost is unknown; inspect its artifacts before "
                "starting a separately budgeted evaluation. This report is preserved."
            )
    else:
        if args.output.exists():
            parser.error("Report exists: use --resume or a different --output")
        args.artifacts_dir.mkdir(parents=True, exist_ok=True)
        root = Path(mkdtemp(prefix="run-", dir=args.artifacts_dir)).resolve()
        report = {
            "configuration": protocol,
            "protocol": (
                "Paired synthetic recall: three-window resets, delayed question, "
                "and long source pagination. Read evidence is separate from accuracy. "
                "Configured strategies, not equal retained-token budgets."
                if args.suite == "challenge"
                else "Paired synthetic exact recall after one explicit reset. "
                "Compare configured strategies, not equal retained-token budgets. "
                "Three scenarios; descriptive results only."
            ),
            "quality_evidence": not args.offline,
            "limitations": [
                "Native summary uses str(event): message text previews are "
                f"limited to {N_CHAR_PREVIEW} characters. Long sources are not "
                "necessarily supplied in full to its summarization LLM.",
                "Strategies retain different amounts of context; no equal-token "
                "budget or statistical significance claim.",
                "A correct answer need not involve history read; inspect "
                "history_evidence separately. Scripted offline answers are "
                "harness checks, not model-quality evidence.",
            ],
            "planned_runs": args.repeats * len(SCENARIOS) * len(CONDITIONS),
            "budget_usd": args.budget,
            "max_requests": args.max_requests,
            "request_interval": args.request_interval,
            "provider_attempts": 0,
            "artifacts_root": str(root),
            "runs": [],
            "interrupted_trials": [],
            "active_trial": None,
        }
    summarize(report)
    terminal = {
        _key(record) for record in report["runs"] if record["outcome"] != "INCONCLUSIVE"
    }
    if len(terminal) == report["planned_runs"]:
        failed = any(record["outcome"] == "FAIL" for record in report["runs"])
        print(f"Already complete; runtime_failures={failed}. Report: {args.output}")
        return 1 if failed else 0
    if not args.offline:
        key = (
            config.get("api_key")
            or os.environ.get("LLM_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )
        if not key:
            parser.error(
                "Set LLM_API_KEY or GEMINI_API_KEY in this terminal; "
                "do not put the key in the report"
            )
        config["api_key"] = SecretStr(key)
    litellm.num_retries = 0

    def checkpoint_attempt(attempt: int) -> None:
        report["provider_attempts"] = attempt
        _save(args.output, report)

    gate = RequestGate(
        interval=max(args.request_interval, report["request_interval"]),
        max_attempts=args.max_requests,
        initial_attempts=report["provider_attempts"],
        on_attempt=checkpoint_attempt,
    )
    report["status"] = "RUNNING"
    _save(args.output, report)
    trial_count = 0
    schedule = []
    for repeat in range(args.repeats):
        scenarios = (
            make_challenge_cases(args.seed, repeat)
            if args.suite == "challenge"
            else SCENARIOS
        )
        for scenario_index, scenario in enumerate(scenarios):
            offset = (repeat + scenario_index) % len(CONDITIONS)
            for condition in CONDITIONS[offset:] + CONDITIONS[:offset]:
                schedule.append((repeat, scenario, condition))
    for repeat, scenario, condition in schedule:
        trial = {"repeat": repeat, "scenario": scenario.name, "condition": condition}
        if _key(trial) in terminal:
            continue
        if trial_count >= args.max_trials:
            report["status"] = "PAUSED_TRIAL_LIMIT"
            break
        if not args.offline and gate.attempted_requests >= args.max_requests:
            report["status"] = "PAUSED_REQUEST_LIMIT"
            break
        remaining_budget = args.budget - report["sdk_reported_cost_usd"]
        if remaining_budget <= 0:
            report["status"] = "PAUSED_BUDGET"
            break
        directory = (
            Path(report["artifacts_root"])
            / f"{condition}-{scenario.name}-{repeat}-{uuid4().hex[:8]}"
        )
        report["active_trial"] = {**trial, "artifacts": str(directory)}
        _save(args.output, report)
        record = run_trial(
            condition,
            scenario,
            repeat,
            config,
            args.offline,
            remaining_budget,
            directory,
            gate,
        )
        report["runs"].append(record)
        report["active_trial"] = None
        trial_count += 1
        summarize(report)
        _save(directory / "report.json", record)
        print(
            f"{report['completed_runs']}/{report['planned_runs']} "
            f"{condition} {scenario.name}: "
            f"{record['outcome']} eligible={record['eligible']} "
            f"match={record['exact_match']}",
            flush=True,
        )
        if record["outcome"] != "COMPLETE":
            report["status"] = "PAUSED_" + record["outcome"]
            break
        _save(args.output, report)
    else:
        report["status"] = (
            "COMPLETE_WITH_FAILURES"
            if any(record["outcome"] == "FAIL" for record in report["runs"])
            else "COMPLETE"
        )
    _save(args.output, report)
    print(f"{report['status']}. Report: {args.output}; offline={args.offline}")
    if report["status"] in {"PAUSED_FAIL", "COMPLETE_WITH_FAILURES"}:
        return 1
    return 0 if report["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
