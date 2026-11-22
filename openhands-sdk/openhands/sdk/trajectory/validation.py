import json
from dataclasses import asdict
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk.trajectory.archive import (
    ArchiveGap,
    Coverage,
    Hash,
    ObjectRef,
    ObjectStore,
    coverage,
    read_seal,
    read_segments,
    verified_bytes,
)
from openhands.sdk.trajectory.capture import Origin
from openhands.sdk.trajectory.journal import Journal, digest, encode


class CandidateSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["legacy", "shadow"]
    store: str
    archive: ObjectRef


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str
    source: Literal["legacy", "shadow"]
    archive: ObjectRef
    evidence: dict[str, Hash] = Field(default_factory=dict)
    runs: dict[str, str] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list)


class LegacyInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str
    events: list[ObjectRef]
    gaps: list[str] = Field(default_factory=list)


class Admission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str
    origin: Origin
    run_ids: set[str]
    evidence: dict[str, Hash]
    externally_enumerated: bool = False


def read_witness(
    journal: Journal,
    origin: Origin,
    admitted_runs: set[str] | None,
) -> Admission:
    evidence = {}
    witnessed_runs = set()
    admitted = set()
    with journal.read() as (_, records):
        for record, _ in records:
            if record.kind == "admitted" and record.run_id is not None:
                admitted.add(record.run_id)
                continue
            if record.kind != "receipt":
                raise ValueError("Expected a witness spool")
            key = record.data["key"]
            evidence[key] = record.data["sha256"]
            if record.run_id is not None and key == f"run_start:{record.run_id}":
                witnessed_runs.add(record.run_id)
    return Admission(
        conversation_id=journal.conversation_id,
        origin=origin,
        run_ids=(admitted or witnessed_runs)
        if admitted_runs is None
        else admitted_runs,
        evidence=evidence,
        externally_enumerated=admitted_runs is not None,
    )


def _partial_state(report: Coverage) -> list[str]:
    gaps = [*report.observed_gaps, *report.missing_links]
    for name, statuses in (
        ("run", report.runs),
        ("request", report.requests),
        ("attempt", report.attempts),
        ("execution", report.executions),
    ):
        gaps.extend(
            f"unfinished_{name}:{key}"
            for key, status in statuses.items()
            if status == "unfinished"
        )
    gaps.extend(
        f"pending_action:{key}"
        for key, outcomes in report.actions.items()
        if not outcomes
    )
    return sorted(gaps)


def _legacy_evidence(
    store: ObjectStore,
    inventory: LegacyInventory,
) -> tuple[dict[str, str], list[str]]:
    evidence = {}
    events = {}
    gaps = list(inventory.gaps)
    directories = {PurePosixPath(ref.key).parent for ref in inventory.events}
    if len(directories) != 1 or next(iter(directories)).name.replace(
        "-", ""
    ) != inventory.conversation_id.replace("-", ""):
        raise ArchiveGap("Legacy inventory is outside the conversation directory")
    for ref in inventory.events:
        try:
            event = json.loads(verified_bytes(store, ref))
            if event["kind"] == "UnparsedEvent":
                original = event["raw_payload"]
                if (
                    original["id"] != event["id"]
                    or original["kind"] != event["original_kind"]
                ):
                    raise ValueError("Legacy wrapper identity differs")
                event = original
            event_id = event["id"]
            if PurePosixPath(ref.key).stem != event_id.replace("-", ""):
                raise ValueError("Legacy filename differs from native event identity")
            key = f"event:{event_id}"
            checksum = digest(encode(event))
            if key in evidence and evidence[key] != checksum:
                gaps.append(f"conflicting_event:{event_id}")
                continue
            events[event_id] = event
            evidence[key] = checksum
        except (ValueError, KeyError, AttributeError, TypeError) as exc:
            gaps.append(f"unverified_event:{ref.key}:{type(exc).__name__}")
    for event_id, event in events.items():
        for field in ("parent_id", "action_id"):
            parent = event.get(field)
            if (
                isinstance(parent, str)
                and parent != "__root__"
                and parent not in events
            ):
                gaps.append(f"unbound_{field}:{event_id}")
    return evidence, gaps


def verify_candidate(
    store: ObjectStore,
    source: CandidateSource,
    conversation_id: str,
) -> Candidate:
    candidate = Candidate(
        conversation_id=conversation_id,
        source=source.source,
        archive=source.archive,
    )
    try:
        if source.source == "shadow":
            seal = read_seal(store, source.archive)
            if seal.conversation_id != conversation_id:
                raise ArchiveGap("Shadow archive belongs to another conversation")
            report = coverage(read_segments(store, seal))
            return candidate.model_copy(
                update={
                    "evidence": report.evidence,
                    "runs": report.runs,
                    "gaps": _partial_state(report),
                }
            )
        inventory = LegacyInventory.model_validate_json(
            verified_bytes(store, source.archive)
        )
        if inventory.conversation_id != conversation_id:
            raise ArchiveGap("Legacy archive belongs to another conversation")
        evidence, gaps = _legacy_evidence(store, inventory)
        return candidate.model_copy(update={"evidence": evidence, "gaps": gaps})
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return candidate.model_copy(
            update={"gaps": [f"unverified_archive:{type(exc).__name__}"]}
        )


def _unwitnessed_runs(admission: Admission) -> list[str]:
    return sorted(
        run_id
        for run_id in admission.run_ids
        if f"run_start:{run_id}" not in admission.evidence
        or f"run_end:{run_id}" not in admission.evidence
    )


def compare(
    admission: Admission,
    shadow: Coverage,
    legacy: Candidate | None,
) -> dict[str, Any]:
    verified = {"shadow": shadow.evidence, "legacy": legacy.evidence if legacy else {}}
    stores = {
        name: {
            "missing": sorted(admission.evidence.keys() - evidence.keys()),
            "conflicting": sorted(
                key
                for key in admission.evidence.keys() & evidence.keys()
                if admission.evidence[key] != evidence[key]
            ),
            "unwitnessed": sorted(evidence.keys() - admission.evidence.keys()),
        }
        for name, evidence in verified.items()
    }
    missing = sorted(admission.run_ids - shadow.runs.keys())
    unregistered = sorted(shadow.runs.keys() - admission.run_ids)
    gaps = _partial_state(shadow)
    unwitnessed_runs = _unwitnessed_runs(admission)
    return {
        "conversation_id": admission.conversation_id,
        "origin": admission.origin,
        "externally_enumerated": admission.externally_enumerated,
        "missing_runs": missing,
        "unregistered_runs": unregistered,
        "unwitnessed_runs": unwitnessed_runs,
        "stores": stores,
        "legacy_gaps": legacy.gaps if legacy else ["archive missing"],
        "shadow_gaps": gaps,
        "shadow_eligible": (
            admission.externally_enumerated
            and bool(admission.run_ids)
            and not (
                missing
                or unregistered
                or unwitnessed_runs
                or gaps
                or any(stores["shadow"].values())
            )
        ),
        "shadow": {
            **asdict(shadow),
            "missing_links": sorted(shadow.missing_links),
            "utility_actions": sorted(shadow.utility_actions),
        },
    }


def select(
    admission: Admission, candidates: list[Candidate]
) -> tuple[Candidate | None, str]:
    if any(item.conversation_id != admission.conversation_id for item in candidates):
        raise ValueError("Candidate belongs to another conversation")
    if _unwitnessed_runs(admission):
        return None, "missing_admitted_runs"
    if not admission.evidence:
        return None, "missing_verified_evidence"
    compatible = [
        item
        for item in candidates
        if all(
            item.evidence[key] == admission.evidence[key]
            for key in item.evidence.keys() & admission.evidence.keys()
        )
    ]
    verified = sorted(
        (
            item
            for item in compatible
            if not item.gaps
            and admission.evidence.items() <= item.evidence.items()
            and all(
                item.runs.get(run_id, "unfinished") != "unfinished"
                for run_id in admission.run_ids
            )
        ),
        key=lambda item: (
            item.source != "legacy",
            item.archive.key,
            item.archive.sha256,
        ),
    )
    if not verified:
        return None, "missing_verified_evidence"
    for item in verified:
        if all(other.evidence.items() <= item.evidence.items() for other in compatible):
            return item, "verified"
    return None, "conflicting_or_incomparable_evidence"
