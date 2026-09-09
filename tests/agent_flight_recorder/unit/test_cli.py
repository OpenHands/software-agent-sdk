import json
import sys
from pathlib import Path
from types import ModuleType

from flight_recorder.cli import build_parser, run_cli
from flight_recorder.config import RecorderPaths
from flight_recorder.models.envelopes import Record
from flight_recorder.recorder import Recorder
from flight_recorder.services.bundles import BundleService


def test_parser_accepts_record_target_and_output() -> None:
    args = build_parser().parse_args(["record", "--output", "trace.afr", "example:run"])

    assert args.command == "record"
    assert args.output == Path("trace.afr")
    assert args.target == "example:run"


def test_cli_records_with_attachment_target(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    paths = RecorderPaths(
        data=tmp_path / "data",
        cache=tmp_path / "cache",
        traces=tmp_path / "data" / "traces",
        index=tmp_path / "data" / "index.db",
    )
    monkeypatch.setattr(RecorderPaths, "defaults", lambda: paths)
    module = ModuleType("recording_target")

    def run(recorder: Recorder) -> None:
        recorder.on_completion_log(
            "completion.json", json.dumps({"response_id": "response-1"})
        )

    module.__dict__["run"] = run
    monkeypatch.setitem(sys.modules, module.__name__, module)
    output = tmp_path / "recorded.afr"

    result = run_cli(
        build_parser().parse_args(
            ["record", "--output", str(output), "recording_target:run"]
        )
    )

    assert result == 0
    assert BundleService().validate_bundle(output).record_count == 5
    assert capsys.readouterr().out.strip()


def test_cli_imports_and_exports_trace_by_id(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    paths = RecorderPaths(
        data=tmp_path / "data",
        cache=tmp_path / "cache",
        traces=tmp_path / "data" / "traces",
        index=tmp_path / "data" / "index.db",
    )
    monkeypatch.setattr(RecorderPaths, "defaults", lambda: paths)
    bundle = tmp_path / "source.afr"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "format": "agent-flight-recorder",
                "format_version": 1,
                "trace_id": "trace",
            }
        )
    )
    record = Record(
        record_id="record",
        producer_id="producer",
        trace_id="trace",
        sequence=1,
        kind="run.started",
    )
    (bundle / "records.jsonl").write_text(record.model_dump_json() + "\n")

    assert run_cli(build_parser().parse_args(["import", str(bundle)])) == 0
    assert capsys.readouterr().out.strip() == "trace"

    destination = tmp_path / "exported.afr.zip"
    assert (
        run_cli(build_parser().parse_args(["export", "trace", str(destination)])) == 0
    )
    assert capsys.readouterr().out.strip() == str(destination)
    assert destination.is_file()


def test_cli_validation_errors_go_to_stderr(tmp_path: Path, capsys) -> None:
    result = run_cli(
        build_parser().parse_args(["validate", str(tmp_path / "missing.afr")])
    )

    output = capsys.readouterr()
    assert result == 1
    assert output.out == ""
    assert output.err
