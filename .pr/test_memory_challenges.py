"""Offline regressions separating answer correctness from history-read evidence."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import eval_notes_retrieval as evaluation
import pytest
from eval_memory_cases import make_challenge_cases
from eval_transport import RequestGate

from openhands.sdk.llm import Message


@pytest.mark.parametrize(
    "snippet_contains_target", [False, True], ids=["long", "short"]
)
def test_correct_answer_without_read_has_no_full_read_evidence(
    snippet_contains_target: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = next(
        case
        for case in make_challenge_cases(seed=4916, repeat=0)
        if case.name == "long_history"
    )
    if snippet_contains_target:
        case = replace(
            case,
            windows=((case.query + "\n" + json.dumps(case.expected),),),
        )
    original_recall_messages = evaluation._offline_recall_messages

    def without_history_reads(
        scenario: evaluation.Case,
        condition: evaluation.Condition,
        source_texts: dict[str, str],
    ) -> list[Message]:
        return [
            message
            for message in original_recall_messages(scenario, condition, source_texts)
            if not any(
                call.name == "conversation_history"
                and json.loads(call.arguments).get("command") == "read"
                for call in message.tool_calls or []
            )
        ]

    monkeypatch.setattr(evaluation, "_offline_recall_messages", without_history_reads)
    monkeypatch.setattr(
        "openhands.sdk.agent.base.has_vision_profile_available", lambda: False
    )
    gate = RequestGate(interval=0)
    with (
        patch("socket.getaddrinfo", side_effect=AssertionError("Unexpected network")),
        patch(
            "socket.socket.connect", side_effect=AssertionError("Unexpected network")
        ),
    ):
        record = evaluation.run_trial(
            condition="notes_history",
            scenario=case,
            repeat=0,
            config={},
            offline=True,
            budget=1.0,
            directory=tmp_path / "trial",
            gate=gate,
        )

    assert record["eligible"], record["error_chain"]
    assert record["exact_match"]
    evidence = record["history_evidence"]
    assert evidence["hidden_source_searches"] == 1
    assert evidence["read_calls"] == 0
    assert evidence["search_snippet_contains_target"] is snippet_contains_target
    assert evidence["full_read_evidence"] is False
    assert evidence["search_to_read_evidence"] is False
    assert record["provider_attempts"] == 0
