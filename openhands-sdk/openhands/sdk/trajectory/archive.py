import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveInt

from openhands.sdk.trajectory.evidence import boundary_evidence, model_input
from openhands.sdk.trajectory.journal import (
    SCHEMA_VERSION,
    Journal,
    Record,
    digest,
    encode,
)


Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ObjectRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    generation: str | None = None
    sha256: Hash
    size: NonNegativeInt


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    first: PositiveInt
    last: PositiveInt
    object: ObjectRef


class Seal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    capture: Literal["shadow"] = "shadow"
    conversation_id: str
    through: NonNegativeInt
    segments: list[Segment]
    runs: dict[str, str]


@dataclass(frozen=True)
class Head:
    revision: str
    data: bytes


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> ObjectRef: ...
    def get(self, ref: ObjectRef) -> bytes: ...
    def head(self, key: str) -> Head | None: ...
    def advance(self, key: str, previous: Head | None, data: bytes) -> None: ...


class ArchiveGap(ValueError):
    pass


def verified_bytes(store: ObjectStore, ref: ObjectRef) -> bytes:
    try:
        data = store.get(ref)
    except (OSError, ValueError) as exc:
        raise ArchiveGap(
            f"Unavailable object {ref.key} generation {ref.generation}"
        ) from exc
    if len(data) != ref.size or digest(data) != ref.sha256:
        raise ArchiveGap(f"Checksum mismatch for {ref.key} generation {ref.generation}")
    return data


def _persist(store: ObjectStore, key: str, data: bytes) -> ObjectRef:
    ref = store.put(key, data)
    if (
        ref.key != key
        or digest(data) != ref.sha256
        or verified_bytes(store, ref) != data
    ):
        raise ArchiveGap(f"Conflicting object {key}")
    return ref


def seal_journal(
    journal: Journal, store: ObjectStore, *, records_per_segment: int = 128
) -> ObjectRef:
    prefix = f"shadow/v{SCHEMA_VERSION}/{journal.conversation_id}"
    head_key = f"{prefix}/head.json"
    current = store.head(head_key)
    previous = (
        read_seal(store, ObjectRef.model_validate_json(current.data))
        if current
        else None
    )
    if previous is not None and previous.conversation_id != journal.conversation_id:
        raise ArchiveGap("Archive head belongs to another conversation")
    segments = []
    with TemporaryDirectory() as directory:
        seal, staged = _stage_segments(
            journal, Path(directory), records_per_segment, previous
        )
        for first, last, path in staged:
            data = path.read_bytes()
            if previous is not None and first <= previous.through:
                old = previous.segments[len(segments)]
                ref = _recover_segment(store, old.object, data)
            else:
                name = f"{first:012d}-{last:012d}-{digest(data)}.jsonl.gz"
                key = f"{prefix}/segments/{name}"
                ref = _persist(store, key, data)
            segments.append(Segment(first=first, last=last, object=ref))
    seal = seal.model_copy(update={"segments": segments})
    for _ in read_segments(store, seal):
        pass
    data = encode(seal.model_dump())
    key = f"{prefix}/seals/{seal.through:012d}-{digest(data)}.json"
    ref = _persist(store, key, data)
    pointer = encode(ref.model_dump())
    if current is None or current.data != pointer:
        store.advance(head_key, current, pointer)
    return ref


def _recover_segment(store: ObjectStore, old: ObjectRef, data: bytes) -> ObjectRef:
    if len(data) != old.size or digest(data) != old.sha256:
        raise ArchiveGap(f"Spool disagrees with acknowledged segment {old.key}")
    try:
        verified_bytes(store, old)
    except ArchiveGap:
        return _persist(store, old.key, data)
    return old


def _stage_segments(
    journal: Journal, directory: Path, records_per_segment: int, previous: Seal | None
) -> tuple[Seal, list[tuple[int, int, Path]]]:
    staged = []
    runs: dict[str, str] = {}
    lines: list[bytes] = []
    size = 0
    first = sequence = 1
    prior_ranges = iter(previous.segments if previous else ())
    prior = next(prior_ranges, None)

    def stage() -> None:
        path = directory / str(first)
        with (
            path.open("wb") as file,
            gzip.GzipFile(filename="", fileobj=file, mode="wb", mtime=0) as compressed,
        ):
            compressed.writelines(lines)
        staged.append((first, sequence - 1, path))
        lines.clear()

    with journal.read() as (head, records):
        if previous is not None and head < previous.through:
            raise ArchiveGap("Spool ends before the acknowledged watermark")
        seal = Seal(
            conversation_id=journal.conversation_id, through=head, segments=[], runs={}
        )
        for record, payloads in records:
            _check_record(record, seal, sequence)
            if record.run_id is not None and record.kind in {"run_start", "run_end"}:
                runs[record.run_id] = record.data.get("status", "unfinished")
            line = encode({"record": record.model_dump(), "payloads": payloads}) + b"\n"
            if (
                prior is None
                and lines
                and (
                    len(lines) >= records_per_segment or size + len(line) > 8 * 1024**2
                )
            ):
                stage()
                first, size = sequence, 0
            lines.append(line)
            size += len(line)
            sequence += 1
            if prior is not None and sequence == prior.last + 1:
                if prior.first != first or prior.last < first:
                    raise ArchiveGap("Invalid prior seal range")
                stage()
                prior = next(prior_ranges, None)
                first, size = sequence, 0
        if lines:
            stage()
    if sequence != seal.through + 1 or prior is not None:
        raise ArchiveGap(f"Missing journal sequence {sequence}")
    return seal.model_copy(update={"runs": runs}), staged


def _check_record(record: Record, seal: Seal, sequence: int) -> None:
    if (
        record.conversation_id != seal.conversation_id
        or record.schema_version != SCHEMA_VERSION
    ):
        raise ArchiveGap(f"Unexpected journal identity at sequence {sequence}")
    if record.sequence != sequence:
        raise ArchiveGap(f"Expected sequence {sequence}, found {record.sequence}")


def read_seal(store: ObjectStore, ref: ObjectRef) -> Seal:
    return Seal.model_validate_json(verified_bytes(store, ref))


@dataclass(frozen=True)
class DecodedRecord:
    record: Record
    payloads: tuple[Any, ...]

    def model_input(self) -> dict[str, Any]:
        if self.record.kind != "attempt_start":
            raise ValueError("Expected a model attempt start")
        return model_input(self.record.data, self.payloads)


def read_segments(store: ObjectStore, seal: Seal) -> Iterator[DecodedRecord]:
    known_payloads: dict[str, Any] = {}
    runs: dict[str, str] = {}
    sequence = 1
    for segment in seal.segments:
        if (
            segment.first != sequence
            or not segment.first <= segment.last <= seal.through
        ):
            raise ArchiveGap(f"Invalid segment coverage at sequence {sequence}")
        data = verified_bytes(store, segment.object)
        try:
            for line in gzip.decompress(data).splitlines():
                if sequence > segment.last:
                    raise ArchiveGap(f"Unexpected record past sequence {segment.last}")
                row = json.loads(line)
                record = Record.model_validate(row["record"])
                _check_record(record, seal, sequence)
                if record.run_id is not None and record.kind in {
                    "run_start",
                    "run_end",
                }:
                    runs[record.run_id] = record.data.get("status", "unfinished")
                for key, payload in row["payloads"].items():
                    if digest(encode(payload)) != key:
                        raise ArchiveGap(
                            f"Invalid payload digest at sequence {sequence}"
                        )
                    known_payloads[key] = payload
                payloads = tuple(
                    known_payloads[ref] for ref in record.data.get("payload_refs", [])
                )
                yield DecodedRecord(record, payloads)
                sequence += 1
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ArchiveGap(
                f"Unreadable segment {segment.object.key} at sequence {sequence}"
            ) from exc
        if sequence != segment.last + 1:
            raise ArchiveGap(f"Missing records at sequence {sequence}")
    if sequence != seal.through + 1:
        raise ArchiveGap(f"Missing segment at sequence {sequence}")
    if runs != seal.runs:
        raise ArchiveGap("Seal run outcomes differ from archived records")


@dataclass
class Coverage:
    runs: dict[str, str] = field(default_factory=dict)
    attempts: dict[str, str] = field(default_factory=dict)
    requests: dict[str, str] = field(default_factory=dict)
    actions: dict[str, list[str]] = field(default_factory=dict)
    model_responses: dict[str, str] = field(default_factory=dict)
    missing_links: set[str] = field(default_factory=set)
    events: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, str] = field(default_factory=dict)
    utility_actions: set[str] = field(default_factory=set)
    executions: dict[str, str] = field(default_factory=dict)
    observed_gaps: list[str] = field(default_factory=list)


def coverage(records: Iterator[DecodedRecord]) -> Coverage:
    result = Coverage()
    for item in records:
        record, data = item.record, item.record.data
        evidence = boundary_evidence(record.kind, record.run_id, data, item.payloads)
        if evidence is not None:
            key, checksum = evidence
            if key in result.evidence and result.evidence[key] != checksum:
                raise ArchiveGap(f"Conflicting evidence for {key}")
            result.evidence[key] = checksum
        match record.kind:
            case "capture_gap":
                result.observed_gaps.append(f"{data['kind']}:{data['error_type']}")
            case "run_start" | "run_end":
                if record.run_id is None:
                    raise ArchiveGap("Run record is missing a run ID")
                if record.kind == "run_end" and record.run_id not in result.runs:
                    result.missing_links.add(f"run_start:{record.run_id}")
                result.runs[record.run_id] = data.get("status", "unfinished")
            case "attempt_start":
                if data.get("unsupported_options"):
                    result.observed_gaps.append(
                        f"unsupported_options:{data['attempt_id']}"
                    )
                result.requests.setdefault(data["request_id"], "unfinished")
                result.attempts[data["attempt_id"]] = "unfinished"
            case "attempt_end":
                if data["attempt_id"] not in result.attempts:
                    result.missing_links.add(f"attempt:{data['attempt_id']}")
                if (
                    data["status"] == "completed"
                    and data["attempt_id"] not in result.model_responses
                ):
                    result.missing_links.add(f"response:{data['attempt_id']}")
                result.attempts[data["attempt_id"]] = data["status"]
            case "attempt_response":
                if data["attempt_id"] not in result.attempts:
                    result.missing_links.add(f"attempt:{data['attempt_id']}")
                result.model_responses[data["attempt_id"]] = data["readable_reasoning"]
            case "request_end":
                result.requests[data["request_id"]] = data["status"]
            case "event":
                event_id = data["event_id"]
                parent = data["parent_id"]
                if parent and parent != "__root__" and parent not in result.events:
                    result.missing_links.add(f"parent:{parent}")
                result.events[event_id] = data["event_kind"]
                action_id = data["action_id"]
                if action_id is not None and action_id not in result.actions:
                    result.missing_links.add(f"action:{action_id}")
                if data["event_kind"] == "ActionEvent":
                    result.actions.setdefault(event_id, [])
                    if item.payloads[0].get("tool_name") in {"think", "finish"}:
                        result.utility_actions.add(event_id)
                if data["event_kind"] == "UserRejectObservation":
                    action_id = data["action_id"]
                    if action_id not in result.actions:
                        result.missing_links.add(f"action:{action_id}")
                    result.actions.setdefault(action_id, []).append("rejected")
            case "tool_start" | "tool_result":
                action_id = data["action_id"]
                if action_id not in result.actions:
                    result.missing_links.add(f"action:{action_id}")
                outcomes = result.actions.setdefault(action_id, [])
                execution_id = data["execution_id"]
                if record.kind == "tool_start":
                    result.executions[execution_id] = "unfinished"
                else:
                    if execution_id is not None:
                        if execution_id not in result.executions:
                            result.missing_links.add(f"execution:{execution_id}")
                        result.executions[execution_id] = data["status"]
                    outcomes.append(data["status"])
            case "model_event":
                if data["attempt_id"] not in result.model_responses:
                    result.missing_links.add(f"response:{data['attempt_id']}")
    return result
