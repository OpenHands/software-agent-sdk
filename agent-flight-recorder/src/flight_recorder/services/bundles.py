"""Portable trace bundle storage and validation."""

import hashlib
import json
import shutil
import tempfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from flight_recorder.errors import InvalidBundle, UnsupportedFormat
from flight_recorder.models.database import TraceIndex
from flight_recorder.models.envelopes import Record


@dataclass(frozen=True)
class BundleValidation:
    trace_id: str
    record_count: int


@dataclass(frozen=True)
class BundleMergeSource:
    path: Path
    parent_trace_id: str | None = None
    agent_name: str | None = None


def _safe_zip_name(name: str) -> bool:
    path = Path(name)
    return not path.is_absolute() and ".." not in path.parts


class BundleService:
    def __init__(
        self, index: TraceIndex | None = None, traces: Path | None = None
    ) -> None:
        self.index = index
        self.traces = traces

    def validate_bundle(self, source: Path) -> BundleValidation:
        root, temporary = self._open(source)
        try:
            manifest_path = root / "manifest.json"
            records_path = root / "records.jsonl"
            if not manifest_path.is_file() or not records_path.is_file():
                raise InvalidBundle("manifest.json and records.jsonl are required")
            self._validate_checksums(root)
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("format") != "agent-flight-recorder":
                raise InvalidBundle("unexpected bundle format")
            if manifest.get("format_version") != 1:
                raise UnsupportedFormat(str(manifest.get("format_version")))
            trace_id = str(manifest["trace_id"])
            records = list(self.iter_record_file(records_path))
            if any(record.trace_id != trace_id for record in records):
                raise InvalidBundle("record trace_id differs from manifest")
            sequences = [record.sequence for record in records]
            if any(sequence is None for sequence in sequences):
                raise InvalidBundle("persisted records require a sequence")
            concrete_sequences = [
                sequence for sequence in sequences if sequence is not None
            ]
            if concrete_sequences != sorted(set(concrete_sequences)):
                raise InvalidBundle("record sequences must strictly increase")
            if len({record.record_id for record in records}) != len(records):
                raise InvalidBundle("record IDs must be unique")
            return BundleValidation(trace_id=trace_id, record_count=len(records))
        finally:
            if temporary is not None:
                temporary.cleanup()

    def import_bundle(self, source: Path) -> str:
        validation = self.validate_bundle(source)
        if self.index is None:
            raise ValueError("import requires an index")
        root, temporary = self._open(source)
        try:
            records = list(self.iter_record_file(root / "records.jsonl"))
            self.index.replace_trace_records(validation.trace_id, records)
            if self.traces is not None:
                self.traces.mkdir(parents=True, exist_ok=True)
                destination = self.traces / f"{validation.trace_id}.afr"
                staging = self.traces / f".{validation.trace_id}.afr.tmp"
                shutil.rmtree(staging, ignore_errors=True)
                shutil.copytree(root, staging)
                shutil.rmtree(destination, ignore_errors=True)
                staging.replace(destination)
        finally:
            if temporary is not None:
                temporary.cleanup()
        return validation.trace_id

    def export_bundle(self, source: Path | str, destination: Path) -> Path:
        if isinstance(source, str):
            if self.traces is None:
                raise ValueError("export by trace ID requires managed trace storage")
            source = self.traces / f"{source}.afr"
        validation = self.validate_bundle(source)
        if source.is_dir():
            self._finalize(source, validation)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.suffix == ".zip":
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(source.rglob("*")):
                    if path.is_file():
                        archive.write(
                            path, Path(source.name) / path.relative_to(source)
                        )
        else:
            shutil.copytree(source, destination, dirs_exist_ok=True)
        return destination

    def finalize_bundle(self, source: Path) -> BundleValidation:
        if not source.is_dir():
            raise ValueError("in-place finalization requires a bundle directory")
        validation = self.validate_bundle(source)
        self._finalize(source, validation)
        return self.validate_bundle(source)

    def merge_bundles(
        self, sources: list[BundleMergeSource], destination: Path
    ) -> BundleValidation:
        if not sources:
            raise ValueError("at least one bundle is required")
        if any(source.path == destination for source in sources):
            raise ValueError("merge destination must differ from every source")

        loaded = [
            (
                source,
                self.validate_bundle(source.path),
                list(self.iter_record_file(source.path / "records.jsonl")),
            )
            for source in sources
        ]
        trace_id = loaded[0][1].trace_id
        root_agents = {
            validation.trace_id: next(
                (
                    record.span_id
                    for record in records
                    if record.kind == "agent.started" and record.parent_span_id is None
                ),
                None,
            )
            for _, validation, records in loaded
        }
        merged = []
        for source_index, (source, validation, records) in enumerate(loaded):
            root_agent = root_agents[validation.trace_id]
            parent_agent = root_agents.get(source.parent_trace_id or "")
            for record in records:
                parent_span_id = record.parent_span_id
                if (
                    source.parent_trace_id is not None
                    and record.span_id == root_agent
                    and parent_span_id is None
                ):
                    parent_span_id = parent_agent
                payload = record.payload
                if (
                    source.agent_name is not None
                    and record.kind == "agent.started"
                    and record.span_id == root_agent
                ):
                    payload = {**payload, "name": source.agent_name}
                merged.append(
                    (
                        source_index,
                        record.model_copy(
                            update={
                                "trace_id": trace_id,
                                "parent_span_id": parent_span_id,
                                "payload": payload,
                                "sequence": None,
                            }
                        ),
                    )
                )
        merged.sort(
            key=lambda item: (
                item[1].observed_at.isoformat(),
                item[0],
                item[1].sequence or 0,
                item[1].record_id,
            )
        )
        records = [
            record.model_copy(update={"sequence": sequence})
            for sequence, (_, record) in enumerate(merged, start=1)
        ]
        if len({record.record_id for record in records}) != len(records):
            raise InvalidBundle("record IDs collide across merged bundles")

        shutil.rmtree(destination, ignore_errors=True)
        destination.mkdir(parents=True)
        (destination / "manifest.json").write_text(
            json.dumps(
                {
                    "format": "agent-flight-recorder",
                    "format_version": 1,
                    "trace_id": trace_id,
                    "status": "snapshot",
                    "recorder_version": "0.1.0",
                    "content_encoding": "zstd",
                },
                indent=2,
            )
            + "\n"
        )
        (destination / "records.jsonl").write_text(
            "".join(record.model_dump_json() + "\n" for record in records)
        )
        (destination / "findings.jsonl").touch()
        for source, _, _ in loaded:
            content = source.path / "content"
            if content.is_dir():
                shutil.copytree(content, destination / "content", dirs_exist_ok=True)
        return self.finalize_bundle(destination)

    def _finalize(self, root: Path, validation: BundleValidation) -> None:
        findings_path = root / "findings.jsonl"
        findings_path.touch(exist_ok=True)
        finding_count = sum(
            1 for line in findings_path.read_text().splitlines() if line
        )

        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(
            record_count=validation.record_count,
            finding_count=finding_count,
        )
        temporary_manifest = manifest_path.with_suffix(".json.tmp")
        temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
        temporary_manifest.replace(manifest_path)

        portable_files = [manifest_path, root / "records.jsonl", findings_path]
        content = root / "content"
        if content.is_dir():
            portable_files.extend(path for path in content.rglob("*") if path.is_file())
        checksum_lines = [
            f"{self.checksum(path)}  {path.relative_to(root).as_posix()}"
            for path in sorted(
                portable_files, key=lambda path: path.relative_to(root).as_posix()
            )
        ]
        checksum_path = root / "checksums.sha256"
        temporary_checksums = checksum_path.with_suffix(".sha256.tmp")
        temporary_checksums.write_text("\n".join(checksum_lines) + "\n")
        temporary_checksums.replace(checksum_path)

    def _validate_checksums(self, root: Path) -> None:
        checksum_path = root / "checksums.sha256"
        if not checksum_path.is_file():
            return

        checksums: dict[str, str] = {}
        for line in checksum_path.read_text().splitlines():
            try:
                digest, relative_path = line.split("  ", maxsplit=1)
            except ValueError as exc:
                raise InvalidBundle("invalid checksum entry") from exc
            if (
                len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or not _safe_zip_name(relative_path)
                or relative_path in checksums
                or relative_path == checksum_path.name
            ):
                raise InvalidBundle("invalid checksum entry")
            checksums[relative_path] = digest

        expected_paths = {"manifest.json", "records.jsonl", "findings.jsonl"}
        content = root / "content"
        if content.is_dir():
            expected_paths.update(
                path.relative_to(root).as_posix()
                for path in content.rglob("*")
                if path.is_file()
            )
        if checksums.keys() != expected_paths:
            raise InvalidBundle("checksum entries do not match bundle files")
        for relative_path, expected_digest in checksums.items():
            path = root / relative_path
            if not path.is_file() or self.checksum(path) != expected_digest:
                raise InvalidBundle(f"checksum mismatch for {relative_path}")

    @staticmethod
    def iter_record_file(path: Path) -> Iterable[Record]:
        with path.open() as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.endswith("\n"):
                    break
                try:
                    yield Record.model_validate_json(line)
                except ValueError as exc:
                    raise InvalidBundle(
                        f"invalid record at line {line_number}"
                    ) from exc

    @staticmethod
    def checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _open(source: Path) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
        if source.is_dir():
            return source, None
        temporary = tempfile.TemporaryDirectory()
        try:
            with zipfile.ZipFile(source) as archive:
                if any(
                    not _safe_zip_name(item.filename) for item in archive.infolist()
                ):
                    raise InvalidBundle("unsafe ZIP entry")
                archive.extractall(temporary.name)
            roots = list(Path(temporary.name).iterdir())
            root = (
                roots[0]
                if len(roots) == 1 and roots[0].is_dir()
                else Path(temporary.name)
            )
            return root, temporary
        except Exception:
            temporary.cleanup()
            raise
