import argparse
import json
from pathlib import Path

from pydantic import TypeAdapter

from openhands.agent_server.persistence.trajectory_archive import (
    DirectoryStore,
    ObjectRef,
    ObjectStore,
    coverage,
    read_seal,
    read_segments,
    seal_journal,
)
from openhands.agent_server.persistence.trajectory_gcs import GCSStore
from openhands.agent_server.persistence.trajectory_validation import (
    Admission,
    CandidateSource,
    compare,
    read_witness,
    select,
    verify_candidate,
)
from openhands.sdk.observability.trajectory_journal import Journal


def _store(location: str) -> ObjectStore:
    if not location.startswith("gs://"):
        return DirectoryStore(Path(location))

    return GCSStore(location)


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    seal = commands.add_parser("seal")
    seal.add_argument("spool", type=Path)
    seal.add_argument("conversation")
    seal.add_argument("store")
    witness = commands.add_parser("witness")
    witness.add_argument("spool", type=Path)
    witness.add_argument("conversation")
    witness.add_argument(
        "--origin",
        choices=["interactive", "automation", "evaluation", "unknown"],
        default="unknown",
    )
    witness.add_argument("--admitted-runs", type=Path)
    read = commands.add_parser("read")
    read.add_argument("store")
    read.add_argument("seal", type=Path)
    verify = commands.add_parser("compare")
    verify.add_argument("store")
    verify.add_argument("seal", type=Path)
    verify.add_argument("admission", type=Path)
    # TODO: Remove dual-write comparison after the rollback window and migration.
    # https://github.com/OpenHands/software-agent-sdk/issues/7036
    verify.add_argument("--legacy", type=Path)
    migrate = commands.add_parser("select")
    migrate.add_argument("admission", type=Path)
    migrate.add_argument("candidates", type=Path)
    args = parser.parse_args()

    if args.command == "select":
        admission = Admission.model_validate_json(args.admission.read_bytes())
        sources = TypeAdapter(list[CandidateSource]).validate_json(
            args.candidates.read_bytes()
        )
        candidates = [
            verify_candidate(_store(source.store), source, admission.conversation_id)
            for source in sources
        ]
        selected, status = select(admission, candidates)
        print(
            json.dumps(
                {
                    "conversation_id": admission.conversation_id,
                    "status": status,
                    "selected": selected.model_dump() if selected else None,
                    "candidates": [item.model_dump() for item in candidates],
                },
                sort_keys=True,
            )
        )
        return

    if args.command == "witness":
        if not (args.spool / f"{args.conversation}.sqlite3").is_file():
            parser.error("Expected an existing witness spool")
        admitted = (
            TypeAdapter(set[str]).validate_json(args.admitted_runs.read_bytes())
            if args.admitted_runs
            else None
        )
        admission = read_witness(
            Journal(args.spool, args.conversation), args.origin, admitted
        )
        print(admission.model_dump_json())
        return

    store = _store(args.store)
    if args.command == "seal":
        if not (args.spool / f"{args.conversation}.sqlite3").is_file():
            parser.error("Expected an existing conversation spool")
        ref = seal_journal(Journal(args.spool, args.conversation), store)
        print(ref.model_dump_json())
        return

    ref = ObjectRef.model_validate_json(args.seal.read_bytes())
    snapshot = read_seal(store, ref)
    records = read_segments(store, snapshot)
    if args.command == "read":
        print(json.dumps({"seal": snapshot.model_dump(), "seal_ref": ref.model_dump()}))
        for item in records:
            print(
                json.dumps(
                    {
                        "record": item.record.model_dump(),
                        "payloads": item.payloads,
                    },
                    ensure_ascii=False,
                )
            )
        return

    admission = Admission.model_validate_json(args.admission.read_bytes())
    if snapshot.conversation_id != admission.conversation_id:
        parser.error("Admission and archive conversation IDs differ")
    legacy = None
    if args.legacy:
        source = CandidateSource.model_validate_json(args.legacy.read_bytes())
        if source.source != "legacy":
            parser.error("Expected a legacy candidate source")
        legacy = verify_candidate(
            _store(source.store), source, admission.conversation_id
        )
    report = compare(admission, coverage(records), legacy)
    report["shadow_archive"] = {
        "watermark": snapshot.through,
        "segments": len(snapshot.segments),
        "bytes": ref.size + sum(segment.object.size for segment in snapshot.segments),
    }
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
