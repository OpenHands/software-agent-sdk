import json
import zipfile
from pathlib import Path

import pytest
from flight_recorder.errors import InvalidBundle
from flight_recorder.models.database import TraceIndex
from flight_recorder.models.envelopes import Record
from flight_recorder.recorder import Recorder
from flight_recorder.services.bundles import BundleMergeSource, BundleService


def test_bundle_validation_and_import_are_idempotent(tmp_path: Path) -> None:
    bundle = tmp_path / "trace.afr"
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
    index = TraceIndex(tmp_path / "index.db")
    service = BundleService(index)

    assert service.validate_bundle(bundle).record_count == 1
    assert service.import_bundle(bundle) == "trace"
    assert service.import_bundle(bundle) == "trace"
    assert index.iter_records("trace") == [record]


def test_export_finalizes_and_round_trips_portable_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "trace.afr"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "format": "agent-flight-recorder",
                "format_version": 1,
                "trace_id": "trace",
                "created_at": "2026-09-01T12:00:00",
                "status": "completed",
                "completed_at": "2026-09-01T12:01:00",
                "stop_reason": "finished",
                "recorder_version": "0.1.0",
                "content_encoding": "zstd",
            }
        )
    )
    records = [
        Record(
            record_id=f"record-{sequence}",
            producer_id="producer",
            trace_id="trace",
            sequence=sequence,
            kind=kind,
        )
        for sequence, kind in ((1, "run.started"), (2, "run.finished"))
    ]
    (bundle / "records.jsonl").write_text(
        "".join(record.model_dump_json() + "\n" for record in records)
    )
    archive = tmp_path / "trace.afr.zip"

    BundleService().export_bundle(bundle, archive)

    with zipfile.ZipFile(archive) as exported:
        names = set(exported.namelist())
        manifest = json.loads(exported.read("trace.afr/manifest.json"))
    assert manifest["record_count"] == 2
    assert manifest["finding_count"] == 0
    assert "trace.afr/findings.jsonl" in names
    assert "trace.afr/checksums.sha256" in names

    replay_index = TraceIndex(tmp_path / "replay.db")
    replay_index.add_records(
        [
            Record(
                record_id="stale-record",
                producer_id="producer",
                trace_id="trace",
                sequence=3,
                kind="stale",
            )
        ]
    )
    replay_service = BundleService(replay_index)
    assert replay_service.validate_bundle(archive).record_count == 2
    assert replay_service.import_bundle(archive) == "trace"
    assert replay_index.iter_records("trace") == records

    exported_directory = tmp_path / "exported.afr"
    BundleService().export_bundle(bundle, exported_directory)
    manifest_path = exported_directory / "manifest.json"
    manifest_path.write_text(manifest_path.read_text() + "\n")

    with pytest.raises(InvalidBundle, match="checksum"):
        BundleService().validate_bundle(exported_directory)


def test_merge_bundles_links_child_root_agent_to_parent(tmp_path: Path) -> None:
    parent = tmp_path / "parent.afr"
    child = tmp_path / "child.afr"
    parent_index = TraceIndex(tmp_path / "parent.db")
    child_index = TraceIndex(tmp_path / "child.db")
    parent_recorder = Recorder(parent, parent_index)
    child_recorder = Recorder(child, child_index)
    parent_recorder.close()
    child_recorder.close()
    destination = tmp_path / "merged.afr"

    merged = BundleService().merge_bundles(
        [
            BundleMergeSource(parent),
            BundleMergeSource(
                child,
                parent_trace_id=parent_recorder.trace_id,
                agent_name="Child implementation",
            ),
        ],
        destination,
    )

    records = list(BundleService.iter_record_file(destination / "records.jsonl"))
    child_start = next(
        record
        for record in records
        if record.kind == "agent.started"
        and record.span_id == child_recorder.agent_span_id
    )
    assert merged.trace_id == parent_recorder.trace_id
    assert child_start.trace_id == parent_recorder.trace_id
    assert child_start.parent_span_id == parent_recorder.agent_span_id
    assert child_start.payload["name"] == "Child implementation"
    assert BundleService().validate_bundle(destination).record_count == len(records)
