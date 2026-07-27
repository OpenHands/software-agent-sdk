import json
from pathlib import Path
from typing import Any

from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.tools.data_preparation.definition import (
    DfConvertAction,
    DfConvertExecutor,
    DfConvertObservation,
)
from openhands.tools.data_preparation.runner import openai_compatible_model_name


class _FakeWorkspace:
    def __init__(self, working_dir: Path) -> None:
        self.working_dir = str(working_dir)


def _fake_conversation(
    tmp_path: Path,
    *,
    secret_registry: SecretRegistry | None = None,
    agent_state: dict[str, Any] | None = None,
):
    return type(
        "FakeConversation",
        (),
        {
            "workspace": _FakeWorkspace(tmp_path),
            "state": type(
                "FakeState",
                (),
                {
                    "secret_registry": secret_registry or SecretRegistry(),
                    "agent_state": agent_state or {},
                },
            )(),
        },
    )()


def test_openai_compatible_model_name() -> None:
    assert openai_compatible_model_name("gpt-4o-mini") == "gpt-4o-mini"
    assert openai_compatible_model_name("openai/gpt-4o") == "gpt-4o"
    assert (
        openai_compatible_model_name("openrouter/anthropic/claude-3-5-sonnet")
        == "anthropic/claude-3-5-sonnet"
    )


def test_df_convert_messages_format(tmp_path: Path) -> None:
    # Prepare input
    input_file = tmp_path / "input.jsonl"
    input_file.write_text(
        json.dumps({"problem": "1+1=?", "reasoning": "1+1=2", "answer": "2"})
        + "\n"
        + json.dumps({"problem": "2+2=?", "answer": "4"})
        + "\n"
        + json.dumps({"no_field": "x"})
    )
    output_file = tmp_path / "messages.jsonl"

    # Run convert
    action = DfConvertAction(
        input_path=str(input_file),
        output_path=str(output_file),
        format="messages",
        text_field="problem",
        reasoning_field="reasoning",
        answer_field="answer",
        system_prompt="Solve the problem step by step.",
    )
    executor = DfConvertExecutor()
    conv = _fake_conversation(tmp_path)
    obs = executor(action, conv)

    # Check output
    assert isinstance(obs, DfConvertObservation)
    assert obs.converted == 2
    assert obs.skipped == 1
    assert output_file.exists()
    records = [json.loads(line) for line in output_file.read_text().splitlines()]
    assert len(records) == 2
    assert records[0]["messages"][0]["content"] == "Solve the problem step by step."
    assert records[0]["messages"][1]["content"] == "1+1=?"
    assert records[0]["messages"][2]["content"] == "1+1=2\n\n2"
    assert records[1]["messages"][0]["content"] == "Solve the problem step by step."
    assert records[1]["messages"][1]["content"] == "2+2=?"
    assert records[1]["messages"][2]["content"] == "4"


def test_df_convert_preference_format(tmp_path: Path) -> None:
    # Prepare input
    input_file = tmp_path / "input.jsonl"
    input_file.write_text(
        json.dumps({"prompt": "1+1=?", "chosen": "2", "rejected": "3"})
        + "\n"
        + json.dumps({"prompt": "2+2=?", "chosen": "4"})
    )
    output_file = tmp_path / "preference.jsonl"

    # Run convert
    action = DfConvertAction(
        input_path=str(input_file),
        output_path=str(output_file),
        format="preference",
        text_field="prompt",
        chosen_field="chosen",
        rejected_field="rejected",
    )
    executor = DfConvertExecutor()
    conv = _fake_conversation(tmp_path)
    obs = executor(action, conv)

    assert isinstance(obs, DfConvertObservation)
    assert obs.converted == 1
    assert obs.skipped == 1
    assert output_file.exists()
    records = [json.loads(line) for line in output_file.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["prompt"] == "1+1=?"
    assert records[0]["chosen"] == "2"
    assert records[0]["rejected"] == "3"
