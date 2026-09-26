"""Check real retrieval after eviction; --offline checks the harness without an API.

Live: set LLM_MODEL and LLM_API_KEY, then uv run .pr/strict_notes_retrieval.py.
Evidence is retained under .pr/evidence/strict-notes/. This is a functional
acceptance test, not a comparison of memory strategies or model quality.
"""

import argparse
import json
import os
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

import litellm
from litellm import ModelResponse
from pydantic import PrivateAttr, SecretStr

from openhands.sdk import LLM, Agent, LocalConversation, StorageSafetyConfig
from openhands.sdk.context.condenser import NotesRetrievalCondenser
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import (
    ActionEvent,
    Event,
    HistoryIndexEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.llm.exceptions import (
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from openhands.sdk.llm.streaming import TokenCallbackType
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins.context_notes import (
    ContextNotesObservation,
    get_context_notes,
)
from openhands.sdk.tool.builtins.conversation_history import (
    ConversationHistoryAction,
    ConversationHistoryObservation,
)
from openhands.sdk.tool.builtins.finish import FinishAction


@dataclass
class RequestGate:
    interval: float = 6.0
    max_attempts: int = 12
    starts: list[float] = field(default_factory=list)

    def admit(self) -> None:
        if len(self.starts) >= self.max_attempts:
            raise RuntimeError("Strict test request limit reached")
        if self.starts:
            time.sleep(max(0.0, self.starts[-1] + self.interval - time.monotonic()))
        self.starts.append(time.monotonic())
        print(f"Provider attempt {len(self.starts)}/{self.max_attempts}", flush=True)


class PacedLLM(LLM):
    _gate: RequestGate = PrivateAttr(default_factory=RequestGate)

    def _transport_call(
        self,
        *,
        messages: list[dict[str, Any]],
        enable_streaming: bool = False,
        on_token: TokenCallbackType | None = None,
        **kwargs,
    ) -> ModelResponse:
        # This boundary also covers SDK retries, unlike a completion-level sleep.
        self._gate.admit()
        kwargs.update(num_retries=0, max_retries=0)
        return super()._transport_call(
            messages=messages,
            enable_streaming=enable_streaming,
            on_token=on_token,
            **kwargs,
        )

    @property
    def attempted_requests(self) -> int:
        return len(self._gate.starts)


def message(text: str) -> Message:
    return Message(role="assistant", content=[TextContent(text=text)])


def tool(name: str, **arguments: Any) -> Message:
    return Message(
        role="assistant",
        tool_calls=[
            MessageToolCall(
                id=f"call_{secrets.token_hex(8)}",
                name=name,
                arguments=json.dumps(arguments),
                origin="completion",
            )
        ],
    )


def require(checks: dict[str, bool], name: str, passed: bool) -> None:
    checks[name] = passed
    print(f"{'PASS' if passed else 'FAIL'} {name}", flush=True)
    if not passed:
        raise AssertionError(name)


def run_turn(conversation: LocalConversation, prompt: str) -> str:
    conversation.send_message(prompt)
    source_id = conversation.state.active_branch()[-1].id
    conversation.run()
    if conversation.state.execution_status != ConversationExecutionStatus.FINISHED:
        raise RuntimeError("Conversation did not finish this turn")
    return source_id


def last_answer(events: Sequence[Event]) -> str:
    for event in reversed(events):
        if isinstance(event, ActionEvent) and isinstance(event.action, FinishAction):
            return event.action.message
        if isinstance(event, MessageEvent) and event.source == "agent":
            return "\n".join(
                part.text
                for part in event.llm_message.content
                if isinstance(part, TextContent)
            )
    return ""


def verify_retrieval(
    events: Sequence[Event],
    reset_id: str,
    history_id: str,
    notes_version: str,
    notes_text: str,
    expected: dict[str, str],
) -> dict[str, bool]:
    reset_position = next(i for i, event in enumerate(events) if event.id == reset_id)
    after = events[reset_position + 1 :]
    actions = {event.id: event for event in after if isinstance(event, ActionEvent)}
    checks = {
        "notes_read_after_reset": False,
        "hidden_history_search_after_reset": False,
        "hidden_history_read_after_reset": False,
    }
    for event in after:
        if not isinstance(event, ObservationEvent) or event.observation.is_error:
            continue
        action = actions.get(event.action_id)
        if action is None:
            continue
        observation = event.observation
        if isinstance(observation, ContextNotesObservation):
            if observation.command == "read":
                result = json.loads(observation.text)
                checks["notes_read_after_reset"] |= (
                    result["version_id"] == notes_version
                    and result["text"] == notes_text
                    and result["next_offset"] is None
                )
        elif isinstance(observation, ConversationHistoryObservation):
            assert isinstance(action.action, ConversationHistoryAction)
            result = json.loads(observation.text)
            if action.action.command == "search":
                checks["hidden_history_search_after_reset"] |= any(
                    match["event_id"] == history_id and match["in_active_view"] is False
                    for match in result["matches"]
                )
            else:
                checks["hidden_history_read_after_reset"] |= (
                    result["event_id"] == history_id
                    and result["in_active_view"] is False
                    and expected["history_value"] in result["text"]
                )
    answer = last_answer(after).strip()
    if answer.startswith("```") and answer.endswith("```"):
        answer = "\n".join(answer.splitlines()[1:-1])
    try:
        checks["answer_exact_match"] = json.loads(answer) == expected
    except json.JSONDecodeError:
        checks["answer_exact_match"] = False
    return checks


def protocol(
    conversation: LocalConversation,
    directory: Path,
    report: dict[str, Any],
    expected: dict[str, str],
    offline: bool,
    offline_case: str,
) -> None:
    checks = report["checks"]
    anchor = "archived deployment verification record"
    note_id = run_turn(
        conversation,
        f"Write exactly this value to context_notes: {expected['note_value']}. "
        "Then reply ACK without repeating the value.",
    )
    history_id = run_turn(
        conversation,
        f"{anchor}\n"
        + "Unrelated staging log: build completed.\n" * 24
        + f"\nhistory_value: {expected['history_value']}\n"
        "Keep this record in history only. Do not put its value in context_notes. "
        "Reply ACK without repeating the value.",
    )
    notes_event, notes_text = get_context_notes(conversation.state.active_branch())
    require(
        checks,
        "notes_committed_without_history_answer",
        notes_event is not None
        and expected["note_value"] in notes_text
        and expected["history_value"] not in notes_text,
    )
    assert notes_event is not None
    report.update(
        source_event_ids=[note_id, history_id], notes_version_id=notes_event.id
    )

    conversation.send_message("Neutral checkpoint. A new retrieval task follows.")
    if offline_case != "skip-reset":
        conversation.condense()
    view = conversation.state.view
    visible_ids = {event.id for event in view.events}
    visible_messages = [
        event.to_llm_message().model_dump(mode="json") for event in view.events
    ]
    (directory / "view-before-retrieval.json").write_text(
        json.dumps(visible_messages, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    require(
        checks,
        "source_events_hidden",
        not visible_ids.intersection({note_id, history_id}),
    )
    visible_text = json.dumps(visible_messages, ensure_ascii=False)
    require(
        checks,
        "answers_absent_from_active_context",
        all(value not in visible_text for value in expected.values()),
    )
    resets = [
        event
        for event in conversation.state.active_branch()
        if isinstance(event, HistoryIndexEvent)
    ]
    require(checks, "reset_completed_before_retrieval", len(resets) == 1)
    reset_id = resets[0].id
    report["reset_event_id"] = reset_id

    if offline:
        responses = []
        if offline_case != "skip-retrieval":
            responses.extend(
                [
                    tool("context_notes", command="read"),
                    tool("conversation_history", command="search", query=anchor),
                    tool("conversation_history", command="read", event_id=history_id),
                ]
            )
        responses.append(message(json.dumps(expected)))
        conversation.switch_llm(
            TestLLM.from_messages(responses, usage_id="offline-retrieval")
        )
    run_turn(
        conversation,
        "The context reset is complete. Read context_notes for note_value. "
        f"Search conversation_history for '{anchor}', then use read on the "
        "original user event ID returned by search to obtain history_value. "
        "The search preview does not contain the full record. Do not change notes. "
        'Return only JSON {"note_value": "...", "history_value": "..."}.',
    )
    events = conversation.state.active_branch()
    retrieval_checks = verify_retrieval(
        events, reset_id, history_id, notes_event.id, notes_text, expected
    )
    report["answer"] = last_answer(events)
    checks.update(retrieval_checks)
    for name, passed in retrieval_checks.items():
        require(checks, name, passed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--offline-case",
        choices=("normal", "skip-reset", "skip-retrieval", "unavailable"),
        default="normal",
    )
    parser.add_argument(
        "--artifacts-dir", type=Path, default=Path(".pr/evidence/strict-notes")
    )
    args = parser.parse_args()
    if not args.offline and args.offline_case != "normal":
        parser.error("--offline-case requires --offline")
    if not args.offline and not all(
        os.environ.get(name) for name in ("LLM_MODEL", "LLM_API_KEY")
    ):
        parser.error("Set LLM_MODEL and LLM_API_KEY in your terminal")
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    directory = Path(mkdtemp(prefix="run-", dir=args.artifacts_dir)).resolve()
    report: dict[str, Any] = {
        "passed": False,
        "outcome": "FAIL",
        "offline": args.offline,
        "offline_case": args.offline_case,
        "model": "TestLLM" if args.offline else os.environ["LLM_MODEL"],
        "checks": {},
        "request_policy": {
            "max_total_attempts": 12,
            "min_interval_seconds": 6,
            "attempts_per_completion": 3,
            "retry_wait_seconds": [20, 40],
        },
        "scope": (
            "Post-reset native tool retrieval; no quality comparison or restart test"
        ),
    }
    expected = {
        "note_value": f"NOTE-{secrets.token_hex(8)}",
        "history_value": f"HIST-{secrets.token_hex(8)}",
    }
    report["expected"] = expected
    conversation = None
    llm = None
    print(f"Evidence directory: {directory}", flush=True)
    try:
        if args.offline:
            seed_responses: list[Message | Exception] = [
                tool("context_notes", command="write", content=expected["note_value"]),
                message("ACK"),
                message("ACK"),
            ]
            if args.offline_case == "unavailable":
                seed_responses = [LLMServiceUnavailableError()]
            llm = TestLLM.from_messages(seed_responses, usage_id="offline-seed")
        else:
            litellm.num_retries = 0
            llm = PacedLLM(
                model=os.environ["LLM_MODEL"],
                api_key=SecretStr(os.environ["LLM_API_KEY"]),
                base_url=os.environ.get("LLM_BASE_URL"),
                usage_id="agent",
                num_retries=3,
                retry_multiplier=20,
                retry_min_wait=20,
                retry_max_wait=60,
                timeout=90,
                reasoning_effort=None,
                max_output_tokens=2048,
                caching_prompt=False,
                stream=False,
                fallback_strategy=None,
            )
            if llm.uses_responses_api():
                raise ValueError("This harness requires the chat completions transport")
        conversation = LocalConversation(
            agent=Agent(
                llm=llm,
                tools=[],
                include_default_tools=[
                    "FinishTool",
                    "ContextNotesTool",
                    "ConversationHistoryTool",
                ],
                condenser=NotesRetrievalCondenser(
                    max_size=240, keep_first=1, keep_recent=1
                ),
            ),
            workspace=directory / "workspace",
            persistence_dir=directory / "state",
            storage_safety=StorageSafetyConfig(min_free_ratio=0.05),
            max_iteration_per_run=6,
            max_budget_per_run=0.10,
            visualizer=None,
        )
        conversation.llm_registry.subscribe(
            conversation.conversation_stats.register_llm
        )
        conversation.llm_registry.add(llm)
        report["conversation_id"] = str(conversation.id)
        protocol(
            conversation, directory, report, expected, args.offline, args.offline_case
        )
        report.update(passed=True, outcome="PASS")
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        cause = exc.original_exception if isinstance(exc, ConversationRunError) else exc
        report["cause_type"] = type(cause).__name__
        if isinstance(
            cause, (LLMServiceUnavailableError, LLMRateLimitError, LLMTimeoutError)
        ) and all(report["checks"].values()):
            report["outcome"] = "INCONCLUSIVE"
            report["reason"] = "Model service prevented completion of the checks"
        print(f"Strict test stopped: {type(cause).__name__}", flush=True)
    finally:
        if isinstance(llm, PacedLLM):
            report["provider_attempts"] = llm.attempted_requests
        if conversation is not None:
            report["execution_status"] = conversation.state.execution_status.value
            report["runtime_error_codes"] = [
                event.code
                for event in conversation.state.events
                if isinstance(event, ConversationErrorEvent)
            ]
            report["metrics"] = (
                conversation.conversation_stats.get_combined_metrics().model_dump(
                    mode="json"
                )
            )
            try:
                conversation.close()
            except Exception as exc:
                report.update(
                    passed=False, outcome="FAIL", close_error_type=type(exc).__name__
                )
        (directory / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"STRICT_RESULT: {report['outcome']}")
        print(f"Report: {directory / 'report.json'}")
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[report["outcome"]]


if __name__ == "__main__":
    raise SystemExit(main())
