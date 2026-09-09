import json
import zipfile
from pathlib import Path

import pytest
from flight_recorder.errors import InvalidBundle, UnsupportedFormat
from flight_recorder.models.envelopes import Record
from flight_recorder.services.bundles import BundleService


def bundle(tmp_path: Path, version: int = 1) -> Path:
    root = tmp_path / "trace.afr"
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "format": "agent-flight-recorder",
                "format_version": version,
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
    (root / "records.jsonl").write_text(record.model_dump_json() + "\n")
    return root


def test_rejects_unsupported_format(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedFormat):
        BundleService().validate_bundle(bundle(tmp_path, version=2))


def test_ignores_truncated_final_record(tmp_path: Path) -> None:
    root = bundle(tmp_path)
    with (root / "records.jsonl").open("a") as stream:
        stream.write('{"incomplete":')

    assert BundleService().validate_bundle(root).record_count == 1


def test_rejects_zip_parent_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("../outside", "invalid")

    with pytest.raises(InvalidBundle):
        BundleService().validate_bundle(archive)


def test_checksum_uses_file_content(tmp_path: Path) -> None:
    content = tmp_path / "content"
    content.write_bytes(b"flight recorder")

    assert len(BundleService.checksum(content)) == 64
