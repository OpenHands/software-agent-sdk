"""Agent Flight Recorder executable entry point."""

import sys
from pathlib import Path

from flight_recorder.app import run
from flight_recorder.cli import build_parser, run_cli


def main() -> int:
    arguments = sys.argv[1:]
    commands = {"record", "validate", "import", "export"}
    if (
        len(arguments) == 1
        and not arguments[0].startswith("-")
        and arguments[0] not in commands
    ):
        return run(Path(arguments[0]))
    args = build_parser().parse_args()
    result = run_cli(args)
    return run(args.trace) if result is None else result


if __name__ == "__main__":
    raise SystemExit(main())
