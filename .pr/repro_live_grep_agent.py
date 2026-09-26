"""Run a real model-driven OpenHands conversation for PR #4984.

Use the same script, model, workspace, and prompt on the base and PR checkouts.
LLM_API_KEY, LLM_MODEL, and optionally LLM_BASE_URL configure the real service.
"""

import argparse
import hashlib
import inspect
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, LocalConversation, Tool
from openhands.sdk.event import ActionEvent, Event, MessageEvent, ObservationEvent
from openhands.tools.grep import GrepAction, GrepExecutor, GrepObservation, GrepTool


CASES = {
    r"^(foo|bar)+[0-9]{2}$": ("extended", "FooBAR12\n", "foo12 extra\n"),
    r"^foo\(bar\)\+$": ("escaped", "foo(bar)+\n", "foobar\n"),
}
PROMPT = r"""Use the grep tool to search the existing fixture files.
Search the extended subdirectory with the exact regex ^(foo|bar)+[0-9]{2}$.
Search the escaped subdirectory with the exact regex ^foo\(bar\)\+$.
For each call, use an absolute path to that subdirectory and include *.txt.
Preserve both patterns exactly; do not simplify, correct, or retry them.
Report the matching filenames returned by each tool call, including empty
results. Do not infer results from the filenames. Then finish.
"""


def validate_calls(
    actions: list[ActionEvent],
    observations: list[ObservationEvent],
    workspace: Path,
    expected: dict[str, list[str]],
) -> list[str]:
    """Require one exact, successful grep call per fixture, without retries."""
    errors: list[str] = []
    if len(actions) != len(CASES) or len(observations) != len(CASES):
        errors.append("Expected exactly two grep calls and two grep observations")
    seen: set[str] = set()
    for event in actions:
        action = event.action
        if not isinstance(action, GrepAction) or action.pattern not in CASES:
            errors.append("Unexpected grep action or pattern")
            continue
        pattern = action.pattern
        if pattern in seen:
            errors.append(f"Repeated grep pattern: {pattern}")
        seen.add(pattern)
        search_path = workspace / CASES[pattern][0]
        if action.path != str(search_path) or action.include != "*.txt":
            errors.append(f"Unexpected grep path/include for {pattern}")
        matches = [
            item for item in observations if item.tool_call_id == event.tool_call_id
        ]
        if len(matches) != 1:
            errors.append(f"Expected one corresponding observation for {pattern}")
            continue
        observation = matches[0].observation
        if not isinstance(observation, GrepObservation):
            errors.append(f"Unexpected observation type for {pattern}")
            continue
        if (
            observation.is_error
            or observation.pattern != pattern
            or observation.search_path != str(search_path)
            or observation.include_pattern != "*.txt"
            or observation.truncated
        ):
            errors.append(f"Invalid or unsuccessful observation for {pattern}")
        expected_paths = [str(search_path / name) for name in expected[pattern]]
        if observation.matches != expected_paths:
            errors.append(f"Unexpected matching files for {pattern}")
    if seen != set(CASES):
        errors.append("The agent did not execute both exact requested patterns")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect", choices=("base", "head"), required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    output = args.output.resolve()
    if output == workspace or workspace in output.parents:
        parser.error("Keep evidence outside the agent's fixture workspace")
    if workspace.exists() and any(workspace.iterdir()):
        parser.error("Use an empty workspace; existing files are never overwritten")
    if output.exists() and any(output.iterdir()):
        parser.error("Use an empty output directory; runs must not share evidence")
    workspace.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    for directory, matching, other in CASES.values():
        fixture = workspace / directory
        fixture.mkdir()
        (fixture / "matching.txt").write_text(matching)
        (fixture / "other.txt").write_text(other)

    grep = shutil.which("grep")
    if grep is None:
        parser.error("System grep is required")
    checkout = Path.cwd().resolve()
    source = Path(inspect.getfile(GrepExecutor)).resolve()
    imported_sources = {
        "grep": source,
        "agent": Path(inspect.getfile(Agent)).resolve(),
        "conversation": Path(inspect.getfile(LocalConversation)).resolve(),
    }
    if any(checkout not in path.parents for path in imported_sources.values()):
        parser.error("Run from the checkout whose editable packages are installed")
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, cwd=checkout
    ).strip()
    metadata = {
        "started_at": datetime.now(UTC).isoformat(),
        "revision": revision,
        "expect": args.expect,
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "grep_binary": grep,
        "grep_source": str(source),
        "grep_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "imported_sources": {
            name: str(path) for name, path in imported_sources.items()
        },
        "model": os.environ.get("LLM_MODEL"),
        "prompt": PROMPT,
        "fixtures": {
            directory: {"matching.txt": matching, "other.txt": other}
            for directory, matching, other in CASES.values()
        },
        "path_isolation": "PATH contains only a symlink to system grep; no rg",
        "prepare_only": args.prepare_only,
    }
    (output / "environment.json").write_text(json.dumps(metadata, indent=2) + "\n")
    original_path = os.environ.get("PATH", "")
    grep_actions: list[ActionEvent] = []
    grep_observations: list[ObservationEvent] = []
    tool_calls: list[str] = []

    with tempfile.TemporaryDirectory(prefix="openhands-grep-only-") as binary_dir:
        (Path(binary_dir) / "grep").symlink_to(grep)
        os.environ["PATH"] = binary_dir
        try:
            assert shutil.which("rg") is None
            assert shutil.which("grep") is not None
            if args.prepare_only:
                print("Prepared fixtures and isolated PATH; no model was called.")
                return 0
            model = os.environ.get("LLM_MODEL")
            key = os.environ.get("LLM_API_KEY")
            if not model or not key:
                parser.error("Set LLM_MODEL and LLM_API_KEY before a live run")
            llm = LLM(
                model=model,
                api_key=SecretStr(key),
                base_url=os.environ.get("LLM_BASE_URL"),
                max_output_tokens=1024,
                num_retries=0,
                timeout=60,
            )

            def record(event: Event) -> None:
                entry: dict[str, object] = {"event": type(event).__name__}
                if isinstance(event, ActionEvent):
                    entry["tool_call"] = event.tool_call.model_dump(mode="json")
                    entry["llm_response_id"] = str(event.llm_response_id)
                    entry["action"] = (
                        event.action.model_dump(mode="json") if event.action else None
                    )
                    tool_calls.append(event.tool_call.name)
                    if event.tool_name == GrepTool.name:
                        grep_actions.append(event)
                elif isinstance(event, ObservationEvent):
                    entry["tool_name"] = event.tool_name
                    entry["tool_call_id"] = str(event.tool_call_id)
                    entry["observation"] = event.observation.model_dump(mode="json")
                    if event.tool_name == GrepTool.name:
                        grep_observations.append(event)
                elif isinstance(event, MessageEvent):
                    entry["role"] = event.llm_message.role
                    entry["content"] = [
                        item.model_dump(mode="json")
                        for item in event.llm_message.content
                    ]
                else:
                    return
                line = json.dumps(entry)
                with (output / "events.jsonl").open("a") as file:
                    file.write(line + "\n")
                print(line, flush=True)

            conversation = LocalConversation(
                agent=Agent(llm=llm, tools=[Tool(name=GrepTool.name)]),
                workspace=workspace,
                callbacks=[record],
                visualizer=None,
                max_iteration_per_run=5,
                max_budget_per_run=1.0,
            )
            try:
                conversation.send_message(PROMPT)
                conversation.run()
            finally:
                conversation.close()
            expected = {pattern: ["matching.txt"] for pattern in CASES}
            if args.expect == "base":
                expected = {
                    r"^(foo|bar)+[0-9]{2}$": [],
                    r"^foo\(bar\)\+$": ["other.txt"],
                }
            errors = validate_calls(
                grep_actions, grep_observations, workspace, expected
            )
            summary = {
                "observed": [
                    {
                        "tool_call_id": str(event.tool_call_id),
                        "observation": event.observation.model_dump(mode="json"),
                    }
                    for event in grep_observations
                ],
                "expected": expected,
                "tool_calls": tool_calls,
                "validation_errors": errors,
                "matches_expected_revision_behavior": not errors,
                "reported_cost_usd": llm.metrics.accumulated_cost,
            }
            (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            print(json.dumps(summary), flush=True)
            return 0 if not errors else 1
        finally:
            os.environ["PATH"] = original_path


if __name__ == "__main__":
    raise SystemExit(main())
