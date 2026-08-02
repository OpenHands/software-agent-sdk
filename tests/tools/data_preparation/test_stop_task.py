"""Tests for the df_stop_task tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openhands.tools.data_preparation.platform_submit import (
    DataPreparationTaskAssociation,
    DataPreparationTaskStore,
)
from openhands.tools.data_preparation.stop_task import (
    DfStopTaskAction,
    DfStopTaskExecutor,
    _coerce_task_id,
)


def _make_executor(
    tmp_path: Path,
    response: dict[str, Any] | str,
) -> tuple[DfStopTaskExecutor, list[str]]:
    executor = DfStopTaskExecutor(
        stop_url="https://portal.test/std2/studio_api/api/stop_task",
        task_store_dir=str(tmp_path / "tasks"),
    )
    calls: list[str] = []

    def fake_post_stop(task_id: str, headers: dict[str, str]) -> dict[str, Any] | str:
        calls.append(task_id)
        return response

    executor._post_stop = fake_post_stop
    return executor, calls


def _save_association(tmp_path: Path, **overrides: Any) -> None:
    fields: dict[str, Any] = {
        "task_id": "task-9",
        "conversation_id": "conv-9",
        "run_id": "run-9",
        "output_dir": "/out/run-9",
        "input_path": "/data/in.jsonl",
        "script_path": "/scripts/p.py",
    }
    fields.update(overrides)
    store = DataPreparationTaskStore(tmp_path / "tasks")
    store.save(DataPreparationTaskAssociation(**fields))


def test_stop_by_task_id_success(tmp_path: Path) -> None:
    executor, calls = _make_executor(tmp_path, {"success": True})

    obs = executor(DfStopTaskAction(task_id="344"), conversation=None)

    assert obs.stopped is True
    assert obs.status == "Stopped"
    assert obs.task_id == "344"
    assert calls == ["344"]


def test_stop_without_identifier(tmp_path: Path) -> None:
    executor, calls = _make_executor(tmp_path, {"success": True})

    obs = executor(DfStopTaskAction(), conversation=None)

    assert obs.stopped is False
    assert obs.is_error
    assert obs.task_id is None
    assert calls == []


def test_stop_resolve_by_run_id(tmp_path: Path) -> None:
    _save_association(tmp_path, task_id="task-run", run_id="run-abc")
    executor, calls = _make_executor(tmp_path, {"success": True})

    obs = executor(DfStopTaskAction(run_id="run-abc"), conversation=None)

    assert obs.stopped is True
    assert obs.task_id == "task-run"
    assert calls == ["task-run"]


def test_stop_resolve_by_output_dir(tmp_path: Path) -> None:
    _save_association(tmp_path, task_id="task-out", output_dir="/out/run-xyz")
    executor, calls = _make_executor(tmp_path, {"success": True})

    obs = executor(DfStopTaskAction(output_dir="/out/run-xyz/"), conversation=None)

    assert obs.stopped is True
    assert obs.task_id == "task-out"
    assert calls == ["task-out"]


def test_stop_unresolved_lookup(tmp_path: Path) -> None:
    executor, calls = _make_executor(tmp_path, {"success": True})

    obs = executor(DfStopTaskAction(run_id="missing"), conversation=None)

    assert obs.stopped is False
    assert obs.is_error
    assert calls == []


def test_stop_api_transport_error(tmp_path: Path) -> None:
    executor, _ = _make_executor(tmp_path, "ConnectError: boom")

    obs = executor(DfStopTaskAction(task_id="344"), conversation=None)

    assert obs.stopped is False
    assert obs.is_error
    assert obs.status == "Failed"
    assert "boom" in obs.text


def test_stop_api_failure_payload(tmp_path: Path) -> None:
    executor, _ = _make_executor(
        tmp_path, {"success": False, "message": "task not running"}
    )

    obs = executor(DfStopTaskAction(task_id="344"), conversation=None)

    assert obs.stopped is False
    assert obs.is_error
    assert "task not running" in obs.text


def test_coerce_task_id() -> None:
    assert _coerce_task_id("344") == 344
    assert _coerce_task_id("task-9") == "task-9"
