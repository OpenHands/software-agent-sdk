import argparse
import json
from collections.abc import Iterator
from pathlib import Path


def generate_records(count: int = 100_000) -> Iterator[dict[str, object]]:
    for sequence in range(1, count + 1):
        yield {
            "record_id": f"record-{sequence}",
            "producer_id": "large-trace-fixture",
            "trace_id": "large-trace",
            "sequence": sequence,
            "kind": "llm.stream_delta" if sequence % 10 else "iteration.finished",
            "payload": {"made_progress": True},
        }


def write_large_trace(destination: Path, count: int = 100_000) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "manifest.json").write_text(
        json.dumps(
            {
                "format": "agent-flight-recorder",
                "format_version": 1,
                "trace_id": "large-trace",
            },
            indent=2,
        )
        + "\n"
    )
    with (destination / "records.jsonl").open("w") as stream:
        for record in generate_records(count):
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("--count", type=int, default=100_000)
    args = parser.parse_args()
    write_large_trace(args.destination, args.count)


if __name__ == "__main__":
    main()
