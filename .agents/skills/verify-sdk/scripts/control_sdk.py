#!/usr/bin/env python3
"""control-sdk: drive the OpenHands SDK in-process, the way a program uses it.

Each command does one thing and prints one JSON object. Recipes chain them:

    control-sdk new                                  # an isolated run
    CID=$(control-sdk conversation new --script s.json --field id)
    control-sdk conversation send "$CID" --text "list the files" --run
    control-sdk conversation state "$CID" --check execution_status eq finished
    control-sdk program S05/roundtrip --check kinds contains ActionEvent

A run is a directory with its own HOME, OH_PERSISTENCE_DIR, conversation
store and workspace; nothing reads or writes the caller's ~/.openhands.
Conversations persist there, so each command resumes the one before it.

Models: a conversation or program with a script uses the SDK's scripted
`TestLLM` (no key, deterministic). Without one it uses the run's real model
(`new --llm deepseek-flash`), with the key read from DEEPSEEK_API_KEY at call
time and never printed or written.

Exit codes: 0 ok, 1 action or check failed, 2 usage, 3 blocked (a missing
prerequisite such as the model key).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any


CLI = "control-sdk"
SKILL_DIR = Path(__file__).resolve().parent.parent
PROGRAMS_DIR = SKILL_DIR / "programs"
REPO_ROOT = SKILL_DIR.parent.parent.parent
HOME_ENV = "SDK_VERIFY_HOME"
RUN_ENV = "SDK_VERIFY_RUN"
BLOCKED_EXIT = 3
# Inherited variables that would point a run at the caller's state or model.
SCRUBBED_PREFIXES = ("LLM_", "OH_", "OPENHANDS_", "LITELLM_")

sys.path.insert(0, str(PROGRAMS_DIR))
import sdk_verify  # noqa: E402


class CliError(Exception):
    def __init__(self, message: str, *, exit_code: int = 1, **data: Any) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.data = data


def usage_error(message: str, example: str | None = None) -> None:
    raise CliError(message, exit_code=2, **({"example": example} if example else {}))


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


def verify_home() -> Path:
    base = os.environ.get(HOME_ENV) or os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "sdk-verify"
    )
    return Path(base)


def resolve_run(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
    elif os.environ.get(RUN_ENV):
        path = Path(os.environ[RUN_ENV])
    else:
        runs = sorted(
            (verify_home() / "runs").glob("*/run.json"),
            key=lambda p: p.stat().st_mtime,
        )
        if not runs:
            raise CliError(
                "no run yet", exit_code=2, hint=f"start one with `{CLI} new`"
            )
        path = runs[-1].parent
    if not (path / "run.json").is_file():
        raise CliError(f"not a run directory: {path}", exit_code=2)
    return path


def tmux_dir(run: Path) -> Path:
    """A short per-run directory for tmux sockets: a socket path past ~100
    characters fails ("File name too long"), and run paths nest deeply."""
    import hashlib

    digest = hashlib.sha1(str(run.resolve()).encode()).hexdigest()[:10]
    path = Path(os.environ.get("TMPDIR") or "/tmp") / f"sdkv-{digest}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_env(run: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(SCRUBBED_PREFIXES) and k != RUN_ENV
    }
    env.update(
        {
            RUN_ENV: str(run),
            "HOME": str(run / "home"),
            "OH_PERSISTENCE_DIR": str(run / "openhands"),
            "PYTHONPATH": os.pathsep.join(
                [str(PROGRAMS_DIR), *filter(None, [env.get("PYTHONPATH")])]
            ),
            # A private tmux server per run, so runs never share terminals.
            "TMUX_TMPDIR": str(tmux_dir(run)),
            # Keep runs quiet, offline where they can be, and comparable.
            "LOG_LEVEL": env.get("LOG_LEVEL", "WARNING"),
            "OPENHANDS_SUPPRESS_BANNER": "1",
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        }
    )
    env.update(extra or {})
    return env


def enter_run(run: Path) -> None:
    """Make this process look like a program started inside the run."""
    env = run_env(run)
    os.environ.clear()
    os.environ.update(env)
    os.chdir(run / "workspace")


def cmd_new(args: argparse.Namespace) -> dict[str, Any]:
    if args.llm != "scripted" and args.llm not in sdk_verify.MODELS:
        usage_error(
            f"--llm must be scripted or one of {', '.join(sdk_verify.MODELS)}",
            f"{CLI} new --llm deepseek-flash",
        )
    name = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    run = verify_home() / "runs" / name
    for sub in ("home", "openhands", "conversations", "workspace", "state"):
        (run / sub).mkdir(parents=True, exist_ok=True)
    config = {"run": str(run), "llm": args.llm, "created": time.time()}
    (run / "run.json").write_text(json.dumps(config, indent=2))
    key_env = sdk_verify.MODELS.get(args.llm, {}).get("env")
    return {
        "ok": True,
        **config,
        "model_key": None
        if key_env is None
        else ("present" if os.environ.get(key_env) else f"missing: set {key_env}"),
        "hint": f"export {RUN_ENV}={run}",
    }


def cmd_show(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    config = json.loads((run / "run.json").read_text())
    convs = sorted(p.stem for p in (run / "state").glob("*.json"))
    return {"ok": True, **config, "conversations": convs}


# --------------------------------------------------------------------------
# Conversations (in-process SDK)
# --------------------------------------------------------------------------

TOOL_MODULES = sdk_verify.TOOL_MODULES


def read_script(args: argparse.Namespace) -> list[dict[str, Any]] | None:
    if args.script and args.script_json:
        usage_error("use one of --script and --script-json")
    if args.script:
        path = Path(args.script)
        if not path.is_absolute() and not path.exists():
            path = PROGRAMS_DIR / args.script
        return json.loads(path.read_text())
    if args.script_json:
        return json.loads(args.script_json)
    return None


def conversation_record(run: Path, cid: str) -> dict[str, Any]:
    path = run / "state" / f"{cid}.json"
    if not path.is_file():
        raise CliError(
            f"unknown conversation {cid}", exit_code=2, hint=f"{CLI} show lists them"
        )
    return json.loads(path.read_text())


def save_record(run: Path, record: dict[str, Any]) -> None:
    (run / "state" / f"{record['id']}.json").write_text(json.dumps(record, indent=2))


def open_conversation(_run: Path, record: dict[str, Any]) -> tuple[Any, Any]:
    """Build the agent and (re)open the persisted conversation."""
    script = record.get("script")
    remaining = None if script is None else script[record.get("used", 0) :]
    llm = sdk_verify.make_llm(remaining)
    return sdk_verify.open_conversation(record, llm=llm), llm


def consume_script(record: dict[str, Any], llm: Any, before: int | None) -> None:
    if record.get("script") is None or before is None:
        return
    left = len(getattr(llm, "_scripted_responses", []))
    record["used"] = record.get("used", 0) + (before - left)


def state_view(conversation: Any, record: dict[str, Any]) -> dict[str, Any]:
    state = conversation.state
    events = list(state.events)
    last_agent = None
    for event in reversed(events):
        summary = sdk_verify.event_summary(0, event)
        if summary["kind"] == "MessageEvent" and summary.get("role") == "assistant":
            last_agent = summary.get("text")
            break
        if summary.get("tool") == "finish" and "action" in summary:
            last_agent = summary["action"].get("message")
            break
    stats = state.stats.get_combined_metrics() if state.stats else None
    script = record.get("script")
    return {
        "id": record["id"],
        "execution_status": str(state.execution_status.value),
        "event_count": len(events),
        "kinds": sorted({type(e).__name__ for e in events}),
        "last_agent_message": last_agent,
        "accumulated_cost": getattr(stats, "accumulated_cost", None),
        "tools": record["tools"],
        "script_left": None if script is None else len(script) - record.get("used", 0),
    }


def cmd_conversation_new(args: argparse.Namespace) -> dict[str, Any]:
    import uuid

    run = resolve_run(args.run)
    enter_run(run)
    record = {
        "id": str(uuid.uuid4()),
        "tools": [t for t in args.tools.split(",") if t],
        "script": read_script(args),
        "used": 0,
        "max_iterations": args.max_iterations,
    }
    conversation, _ = open_conversation(run, record)
    save_record(run, record)
    out = {"ok": True, **state_view(conversation, record)}
    conversation.close()
    return out


def drive(
    run: Path, cid: str, text: str | None, do_run: bool, allow_error: bool = False
) -> dict[str, Any]:
    enter_run(run)
    record = conversation_record(run, cid)
    conversation, llm = open_conversation(run, record)
    before = (
        len(getattr(llm, "_scripted_responses", []))
        if record.get("script") is not None
        else None
    )
    error = None
    try:
        if text is not None:
            conversation.send_message(text)
        if do_run:
            conversation.run()
    except Exception as exc:  # the conversation records it; report it too
        error = f"{type(exc).__name__}: {exc}"
    finally:
        consume_script(record, llm, before)
        save_record(run, record)
    out = {"ok": error is None or allow_error, **state_view(conversation, record)}
    if error:
        out["error"] = error[:500]
    conversation.close()
    return out


def cmd_conversation_send(args: argparse.Namespace) -> dict[str, Any]:
    return drive(
        resolve_run(args.run), args.id, args.text, args.do_run, args.allow_error
    )


def cmd_conversation_run(args: argparse.Namespace) -> dict[str, Any]:
    return drive(resolve_run(args.run), args.id, None, True, args.allow_error)


def cmd_conversation_state(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    enter_run(run)
    record = conversation_record(run, args.id)
    conversation, _ = open_conversation(run, record)
    out = {"ok": True, **state_view(conversation, record)}
    store = run / "conversations" / record["id"].replace("-", "") / "events"
    out["event_files"] = len(list(store.glob("event-*.json")))
    conversation.close()
    return out


def cmd_conversation_events(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    enter_run(run)
    record = conversation_record(run, args.id)
    conversation, _ = open_conversation(run, record)
    rows = [
        sdk_verify.event_summary(i, e) for i, e in enumerate(conversation.state.events)
    ]
    conversation.close()
    if args.kind:
        rows = [r for r in rows if r["kind"] == args.kind]
    if args.source:
        rows = [r for r in rows if r["source"] == args.source]
    if args.last:
        rows = rows[-args.last :]
    return {
        "ok": True,
        "count": len(rows),
        "kinds": [r["kind"] for r in rows],
        "events": rows,
    }


# --------------------------------------------------------------------------
# Programs and commands
# --------------------------------------------------------------------------


def parse_env(items: Sequence[str] | None) -> dict[str, str]:
    env = {}
    for item in items or []:
        if "=" not in item:
            usage_error("--env must look like NAME=VALUE", "--env LOG_LEVEL=DEBUG")
        name, value = item.split("=", 1)
        env[name] = value
    return env


def last_json(text: str) -> Any:
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def model_env(run: Path) -> dict[str, str]:
    """LLM_* variables for example scripts, from the run's real model."""
    name = json.loads((run / "run.json").read_text()).get("llm")
    spec = sdk_verify.MODELS.get(name or "")
    if spec is None:
        raise CliError(
            "--model-env needs a run with a real model",
            exit_code=2,
            hint=f"{CLI} new --llm deepseek-flash",
        )
    key = os.environ.get(spec["env"])
    if not key:
        raise CliError(
            f"blocked: set {spec['env']}", exit_code=BLOCKED_EXIT, blocked=spec["env"]
        )
    return {"LLM_MODEL": spec["model"], "LLM_API_KEY": key}


def run_command(
    run: Path,
    command: list[str],
    env: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=run / "workspace",
            env=run_env(run, env),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        code = 124
        stdout = (
            (exc.stdout or b"").decode(errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        stderr = "timed out"
    secret = env.get("LLM_API_KEY")
    if secret:
        stdout, stderr = stdout.replace(secret, "***"), stderr.replace(secret, "***")
    log = run / "state" / "exec.log"
    with log.open("a") as fh:
        fh.write(f"$ {' '.join(command)}\n{stdout}\n{stderr}\n[exit {code}]\n")
    return {
        "exit": code,
        "seconds": round(time.monotonic() - started, 1),
        "result": last_json(stdout),
        "stdout_tail": stdout[-1500:],
        "stderr_tail": stderr[-1500:],
        "_stdout": stdout,
    }


def python_command() -> list[str]:
    return [sys.executable]


def finish_command(out: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    # --expect-output searches all of stdout, not just the tail it prints.
    stdout = out.pop("_stdout", out["stdout_tail"])
    if out["exit"] == BLOCKED_EXIT:
        blocked = (out.get("result") or {}).get("blocked", "a prerequisite")
        raise CliError(f"blocked: {blocked}", exit_code=BLOCKED_EXIT, **out)
    expected = args.expect_exit
    ok = out["exit"] == expected
    missing = [s for s in args.expect_output or [] if s not in stdout]
    if missing:
        ok = False
        out["missing_output"] = missing
    out["ok"] = ok
    return out


def cmd_program(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    name = args.name if args.name.endswith(".py") else args.name + ".py"
    path = PROGRAMS_DIR / name
    if not path.is_file():
        usage_error(
            f"no program {name} under {PROGRAMS_DIR}", f"{CLI} program S05/roundtrip"
        )
    out = run_command(
        run,
        [*python_command(), str(path), *(args.arg or [])],
        parse_env(args.env),
        args.timeout,
    )
    out = finish_command(out, args)
    # A program's result is the body checks read; keep the run facts beside it.
    result = out.get("result")
    if not isinstance(result, dict):
        out["ok"] = False
        out["error"] = "the program printed no JSON result"
        return out
    return {
        **result,
        "ok": bool(out["ok"] and result.get("ok", True)),
        "_exit": out["exit"],
        "_seconds": out["seconds"],
        **({"_stderr_tail": out["stderr_tail"]} if not out["ok"] else {}),
    }


def cmd_exec(args: argparse.Namespace) -> dict[str, Any]:
    run = resolve_run(args.run)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        usage_error("name a command after --", f"{CLI} exec -- python examples/x.py")
    if command[0] == "python":
        command = [*python_command(), *command[1:]]
    # Paths in the command are relative to the checkout, not the run.
    command = [
        str(REPO_ROOT / c) if not c.startswith("-") and (REPO_ROOT / c).is_file() else c
        for c in command
    ]
    env = parse_env(args.env)
    if args.model_env:
        env.update(model_env(run))
    return finish_command(run_command(run, command, env, args.timeout), args)


def cmd_assert(args: argparse.Namespace) -> dict[str, Any]:
    sources = [s is not None for s in (args.json, args.value)] + [args.stdin]
    if sum(sources) != 1:
        usage_error("pass exactly one of --json, --value, --stdin")
    if args.json is not None:
        body = json.loads(args.json)
    elif args.value is not None:
        body = {"value": args.value}
    else:
        body = json.loads(sys.stdin.read())
    if not args.check:
        usage_error("assert needs at least one --check", "--check value eq 3")
    return body if isinstance(body, dict) else {"value": body}


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

CHECK_OPS = (
    "eq",
    "ne",
    "lt",
    "le",
    "gt",
    "ge",
    "contains",
    "not-contains",
    "matches",
    "exists",
    "missing",
    "len-eq",
    "len-ge",
)


def dig(body: Any, path: str) -> Any:
    """Follow a dotted path; `\\.` is a literal dot inside a key
    (`spans.0.attributes.lmnr\\.association\\.properties\\.user_id`)."""
    current = body
    for raw in re.split(r"(?<!\\)\.", path):
        part = raw.replace("\\.", ".")
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and re.fullmatch(r"-?\d+", part):
            index = int(part)
            current = current[index] if -len(current) <= index < len(current) else None
        else:
            return None
    return current


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value if isinstance(value, str) else json.dumps(value, default=str)


def evaluate_check(body: Any, spec: Sequence[str]) -> dict[str, Any]:
    if len(spec) < 2 or spec[1] not in CHECK_OPS:
        usage_error(
            f"--check needs FIELD OP [VALUE] with OP in {', '.join(CHECK_OPS)}",
            "--check execution_status eq finished",
        )
    path, op = spec[0], spec[1]
    expected = spec[2] if len(spec) > 2 else ""
    actual = body if path in (".", "") else dig(body, path)
    present = actual is not None
    if op == "exists":
        ok = present
    elif op == "missing":
        ok = not present
    elif op in ("eq", "ne"):
        same = _text(actual) == expected or (
            _num(actual) is not None and _num(actual) == _num(expected)
        )
        ok = same if op == "eq" else not same
    elif op in ("lt", "le", "gt", "ge"):
        a, b = _num(actual), _num(expected)
        ok = (
            a is not None
            and b is not None
            and {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}[op]
        )
    elif op in ("contains", "not-contains"):
        if isinstance(actual, list):
            found = any(_text(i) == expected or expected in _text(i) for i in actual)
        else:
            found = actual is not None and expected in _text(actual)
        ok = found if op == "contains" else not found
    elif op == "matches":
        ok = actual is not None and re.search(expected, _text(actual)) is not None
    else:
        size = len(actual) if isinstance(actual, list | dict | str) else None
        target = _num(expected)
        ok = (
            size is not None
            and target is not None
            and (size == target if op == "len-eq" else size >= target)
        )
    shown = _text(actual)
    return {"check": " ".join(spec), "ok": ok, "actual": shown[:200]}


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


class Formatter(argparse.RawDescriptionHelpFormatter):
    pass


def add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run", help=f"run directory (default ${RUN_ENV}, else newest)")
    p.add_argument(
        "--check",
        nargs="+",
        action="append",
        metavar="FIELD OP [VALUE]",
        help="assert on the JSON output; repeatable; exit 1 when one fails",
    )
    p.add_argument("--field", help="print only this field of the output, raw")


def add_command_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--env", action="append", metavar="NAME=VALUE")
    p.add_argument("--expect-exit", type=int, default=0)
    p.add_argument("--expect-output", action="append", metavar="TEXT")
    p.add_argument("--timeout", type=float, default=600)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=CLI,
        formatter_class=Formatter,
        description=(__doc__ or "").split("\n\n", 1)[0],
        epilog=f"""Verbs:
  new            an isolated run (HOME, OH_PERSISTENCE_DIR, conversations, workspace)
  show           the run's model and conversations
  conversation   new | send | run | state | events: a persisted conversation
  program        run a verify-sdk program and check its JSON result
  exec           run any command (an example script) inside the run
  assert         check a value computed in the shell

Which run: --run DIR, else ${RUN_ENV}, else the newest under ${HOME_ENV}
(default $TMPDIR/sdk-verify). Exit codes: 0 ok, 1 failed, 2 usage, 3 blocked.""",
    )
    sub = parser.add_subparsers(dest="command", metavar="<verb>")

    p = sub.add_parser(
        "new",
        help="create an isolated run",
        formatter_class=Formatter,
        epilog=f"""Examples:
  export {RUN_ENV}=$({CLI} new --field run)
  export {RUN_ENV}=$({CLI} new --llm deepseek-flash --field run)""",
    )
    add_common(p)
    p.add_argument(
        "--llm",
        default="scripted",
        help="scripted (default), deepseek-flash, deepseek-pro",
    )
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("show", help="the run's model and conversations")
    add_common(p)
    p.set_defaults(func=cmd_show)

    conv = sub.add_parser(
        "conversation",
        help="a persisted in-process conversation",
        formatter_class=Formatter,
        epilog=f"""Examples:
  CID=$({CLI} conversation new --script S01/finish.json --field id)
  {CLI} conversation send "$CID" --text "say hi" --run """
        f"""--check execution_status eq finished
  {CLI} conversation events "$CID" --kind ActionEvent --check count eq 1""",
    )
    csub = conv.add_subparsers(
        dest="conversation_command", metavar="<new|send|run|state|events>"
    )

    p = csub.add_parser(
        "new",
        help="create a conversation (scripted with --script, else the run's model)",
        formatter_class=Formatter,
        epilog="""A script is a JSON list, one step per LLM call:
  {"text": "..."}  {"tool": "terminal", "args": {"command": "ls"}}
  {"tools": [...]}  {"finish": "done"}  {"error": "LLMContextWindowExceedError"}
Relative script paths resolve against the skill's programs/ directory.""",
    )
    add_common(p)
    p.add_argument("--tools", default="terminal,file_editor,task_tracker")
    p.add_argument("--script", help="JSON script file")
    p.add_argument("--script-json", help="JSON script inline")
    p.add_argument("--max-iterations", type=int, default=50)
    p.set_defaults(func=cmd_conversation_new)

    p = csub.add_parser("send", help="send a user message (and --run)")
    add_common(p)
    p.add_argument("id")
    p.add_argument("--text", required=True)
    p.add_argument("--run-agent", "--then-run", dest="do_run", action="store_true")
    p.add_argument(
        "--allow-error",
        action="store_true",
        help="a run that ends in an error is the expected outcome; check it",
    )
    p.set_defaults(func=cmd_conversation_send)
    # `--run` names the run directory everywhere, so `send --run` would clash;
    # accept the natural spelling by rewriting it before parsing (see main).

    p = csub.add_parser("run", help="run the agent until it finishes or stops")
    add_common(p)
    p.add_argument("id")
    p.add_argument("--allow-error", action="store_true")
    p.set_defaults(func=cmd_conversation_run)

    p = csub.add_parser("state", help="execution status, counts, last agent message")
    add_common(p)
    p.add_argument("id")
    p.set_defaults(func=cmd_conversation_state)

    p = csub.add_parser("events", help="the event log, summarized")
    add_common(p)
    p.add_argument("id")
    p.add_argument("--kind")
    p.add_argument("--source", choices=["agent", "user", "environment", "hook"])
    p.add_argument("--last", type=int)
    p.set_defaults(func=cmd_conversation_events)

    p = sub.add_parser(
        "program",
        help="run a program under programs/ and check its JSON result",
        formatter_class=Formatter,
        epilog=f"""Examples:
  {CLI} program S05/roundtrip --check kinds contains ActionEvent
  {CLI} program S02/live_completion --arg "say OK" --check text contains OK

The checks read the program's own JSON result (its last output line).""",
    )
    add_common(p)
    add_command_flags(p)
    p.add_argument("name", help="path under programs/, with or without .py")
    p.add_argument("--arg", action="append", help="an argument for the program")
    p.set_defaults(func=cmd_program)

    p = sub.add_parser(
        "exec",
        help="run a command inside the run (cwd = workspace, isolated env)",
        formatter_class=Formatter,
        epilog=f"""Examples:
  {CLI} exec --model-env --expect-output "All done" -- """
        """python examples/01_standalone_sdk/01_hello_world.py

Paths that exist in the checkout are made absolute. --model-env exports
LLM_MODEL and LLM_API_KEY from the run's real model (exit 3 without its key).""",
    )
    add_common(p)
    add_command_flags(p)
    p.add_argument("--model-env", action="store_true")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_exec)

    p = sub.add_parser(
        "assert",
        help="check a value the shell computed",
        formatter_class=Formatter,
        epilog=f"""Examples:
  {CLI} assert --value "$(ls | wc -l)" --check value eq 2
  {CLI} assert --json "$OUT" --check events.0.kind eq SystemPromptEvent""",
    )
    add_common(p)
    p.add_argument("--json")
    p.add_argument("--value")
    p.add_argument("--stdin", action="store_true")
    p.set_defaults(func=cmd_assert)
    return parser


def normalize_argv(argv: list[str]) -> list[str]:
    """`conversation send ID --text T --run` means "then run the agent"."""
    if argv[:2] == ["conversation", "send"]:
        out = []
        for i, a in enumerate(argv):
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if a == "--run" and (nxt is None or nxt.startswith("-")):
                out.append("--then-run")
            else:
                out.append(a)
        return out
    return argv


def emit(out: dict[str, Any], field: str | None) -> None:
    if field:
        value = dig(out, field)
        print(value if isinstance(value, str) else json.dumps(value, default=str))
    else:
        print(json.dumps(out, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(
        normalize_argv(list(sys.argv[1:] if argv is None else argv))
    )
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    field = getattr(args, "field", None)
    try:
        out = args.func(args)
    except CliError as exc:
        emit({"ok": False, "exit": exc.exit_code, "error": str(exc), **exc.data}, None)
        return exc.exit_code
    except Exception as exc:  # the SDK raised: report it as a failed action
        import traceback

        frames = traceback.format_exception(exc)
        emit(
            {
                "ok": False,
                "exit": 1,
                "error": f"{type(exc).__name__}: {exc}"[:500],
                "traceback_tail": "".join(frames)[-1500:],
            },
            None,
        )
        return 1
    checks = [evaluate_check(out, spec) for spec in getattr(args, "check", None) or []]
    if checks:
        out["checks"] = checks
    failed = (not out.get("ok", True)) or any(not c["ok"] for c in checks)
    emit(out, None if failed else field)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
