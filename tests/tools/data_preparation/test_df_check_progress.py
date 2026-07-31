"""Tests for the DataFlow pipeline progress check tool."""

import json

from openhands.tools.data_preparation.progress import (
    DfCheckProgressAction,
    DfCheckProgressExecutor,
)


def _make_executor(files: dict[str, bytes]) -> DfCheckProgressExecutor:
    executor = DfCheckProgressExecutor(storage_base_url="https://storage.test/api")

    def fake_get_size_and_url(path: str, headers: dict[str, str]):
        if path not in files:
            return f"not found: {path}"
        return len(files[path]), f"https://cdn.test{path}"

    def fake_download_range(url: str, start: int | None, end: int | None):
        path = url.removeprefix("https://cdn.test")
        data = files[path]
        if start is None or end is None:
            return data
        return data[start : end + 1]

    executor._get_size_and_url = fake_get_size_and_url
    executor._download_range = fake_download_range
    return executor


def test_check_progress_full_snapshot() -> None:
    progress = {
        "total": 100,
        "processed": 40,
        "succeeded": 38,
        "failed": 2,
        "elapsed_ms": 20000,
        "records_per_second": 2.0,
        "eta_ms": 30000,
        "updated_at": "2026-07-28T10:00:00Z",
    }
    processed = "".join(json.dumps({"id": i}) + "\n" for i in range(5))
    files = {
        "/run/progress.json": json.dumps(progress).encode(),
        "/run/processed.jsonl": processed.encode(),
    }
    executor = _make_executor(files)

    obs = executor(DfCheckProgressAction(output_dir="/run/"), conversation=None)

    assert obs.output_dir == "/run"
    assert obs.progress_found is True
    assert obs.total == 100
    assert obs.processed == 40
    assert obs.succeeded == 38
    assert obs.failed == 2
    assert obs.percent == 40.0
    assert obs.eta_ms == 30000
    assert len(obs.latest_records) == 5
    assert obs.latest_records[-1] == {"id": 4}


def test_check_progress_missing_progress_file() -> None:
    executor = _make_executor({})

    obs = executor(DfCheckProgressAction(output_dir="/run"), conversation=None)

    assert obs.progress_found is False
    assert obs.percent is None
    assert obs.total is None
    assert obs.latest_records == []


def test_check_progress_tail_drops_partial_first_line() -> None:
    records = [json.dumps({"id": i, "pad": "x" * 20}) for i in range(10)]
    processed = "".join(record + "\n" for record in records)
    progress = {"total": 10, "processed": 10, "succeeded": 10, "failed": 0}
    files = {
        "/run/progress.json": json.dumps(progress).encode(),
        "/run/processed.jsonl": processed.encode(),
    }
    executor = _make_executor(files)
    executor._tail_bytes = 100  # force a mid-line start

    obs = executor(
        DfCheckProgressAction(output_dir="/run", tail_lines=3),
        conversation=None,
    )

    assert obs.percent == 100.0
    assert len(obs.latest_records) <= 3
    assert all(isinstance(record, dict) for record in obs.latest_records)
    assert obs.latest_records[-1]["id"] == 9
