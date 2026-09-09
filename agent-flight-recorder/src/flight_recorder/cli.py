"""Command-line interface for trace operations."""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from flight_recorder.config import RecorderPaths
from flight_recorder.errors import FlightRecorderError
from flight_recorder.models.database import TraceIndex
from flight_recorder.services.bundles import BundleService


if TYPE_CHECKING:
    from flight_recorder.recorder import Recorder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-flight-recorder")
    parser.set_defaults(trace=None)
    commands = parser.add_subparsers(dest="command")
    record = commands.add_parser("record")
    record.add_argument("--output", required=True, type=Path)
    record.add_argument("target")
    validate = commands.add_parser("validate")
    validate.add_argument("source", type=Path)
    import_command = commands.add_parser("import")
    import_command.add_argument("source", type=Path)
    export = commands.add_parser("export")
    export.add_argument("trace_id")
    export.add_argument("destination", type=Path)
    return parser


def run_cli(args: argparse.Namespace) -> int | None:
    if args.command is None:
        return None
    paths = RecorderPaths.defaults()
    paths.create()
    index = TraceIndex(paths.index)
    service = BundleService(index, paths.traces)
    try:
        if args.command == "record":
            from flight_recorder.recorder import Recorder

            target = _load_record_target(args.target)
            recorder = Recorder(args.output, index)
            try:
                result = target(recorder)
            finally:
                recorder.close()
            print(recorder.trace_id)
            return 0 if result is None else result
        if args.command == "validate":
            result = service.validate_bundle(args.source)
            print(f"{result.trace_id}: {result.record_count} records")
        elif args.command == "import":
            print(service.import_bundle(args.source))
        elif args.command == "export":
            print(service.export_bundle(args.trace_id, args.destination))
    except (FlightRecorderError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _load_record_target(target: str) -> Callable[[Recorder], int | None]:
    module_name, separator, function_name = target.partition(":")
    if not separator or not module_name or not function_name:
        raise ValueError("record target must use module:function syntax")
    module = importlib.import_module(module_name)
    function = module.__dict__.get(function_name)
    if not callable(function):
        raise ValueError(f"record target is not callable: {target}")
    return cast("Callable[[Recorder], int | None]", function)
