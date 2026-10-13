#!/usr/bin/env python3
"""sdk-map: check, route, replay and report on the in-process SDK feature map.

The SDK has no server to launch, so its map drives the SDK through pytest,
the example scripts and the integration runner. This tool keeps that map
honest the way control-agent-server keeps the agent-server map honest: it
checks structure and every path the map cites, maps changed files to
families, reads and moves the maintenance baseline, replays a family's `sh`
blocks into a ledger, and compares ledgers.

Every command prints one JSON object. Exit codes: 0 ok, 1 action failed,
2 usage. Standard library only.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn


CLI = "sdk-map"
SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_DIR.parents[2]
FEATURE_MAP_DIR = SKILL_DIR / "references" / "feature-map"
HOME_ENV = "SDK_VERIFY_HOME"
RESULTS = ("pass", "fail", "blocked", "not-run")
RESULT_PRECEDENCE = {"fail": 0, "pass": 1, "blocked": 2, "not-run": 3}
SECTIONS = ["Sub-features", "How to get to it (agent POV)", "Driving it", "Gotchas"]
FAMILY_FILE_RE = re.compile(r"^S\d{2}-[a-z0-9-]+\.md$")
SUB_FEATURE_RE = re.compile(r"^- `(S\d{2}\.[a-z0-9-]+)`")
INDEX_ROW_RE = re.compile(
    r"^\| (S\d{2}) \| \[([^\]]+)\]\(([^)]+)\) \|(.*)\| (\d+) \| (\d+) \|$"
)
# Repository paths the map cites: Source:, Examples: and Unit:/Example:/Real-LLM:.
CITED_PATH_RE = re.compile(
    r"`((?:openhands-[a-z]+|tests|examples|scripts|\.github)/[^`\s]*)`"
)
UNTESTED_RE = re.compile(r"\*\*(?:none \()?untested\)?\*\*")
# Placeholders such as `vN/` or `v1..v8/` name a pattern, not a path.
PLACEHOLDER_RE = re.compile(r"/vN/|\.\.|<")
PRODUCT_PATH_RE = re.compile(r"^openhands-(sdk|tools|workspace)/")
# Commands that need a model key: example scripts, the examples test runner
# and the integration runner. Deterministic pytest under tests/integration/
# (test_tool_presets.py and friends) is not one of them.
MODEL_COMMAND_RE = re.compile(
    r"(?:^|[\s'\"=])(?:tests/)?examples/|tests/integration/run_infer\.py"
)
INTEGRATION_RE = re.compile(r"tests/integration/run_infer\.py")
BASELINE_LINE_RE = re.compile(
    r"^Maintenance baseline: main@([0-9a-f]{7,40}) \((\d{4}-\d{2}-\d{2})\)(.*)$",
    re.M,
)


class CliError(Exception):
    def __init__(self, message: str, *, exit_code: int = 1, **data: Any) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.data = data


def usage_error(message: str, example: str | None = None) -> NoReturn:
    raise CliError(message, exit_code=2, **({"example": example} if example else {}))


def git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip()


# --------------------------------------------------------------------------
# Map parsing
# --------------------------------------------------------------------------


def index_path() -> Path:
    return FEATURE_MAP_DIR / "README.md"


def family_files(only: str | None = None) -> list[Path]:
    files = sorted(
        p for p in FEATURE_MAP_DIR.glob("S*.md") if FAMILY_FILE_RE.match(p.name)
    )
    if only:
        files = [p for p in files if p.name == only or p.stem.startswith(only)]
        if not files:
            usage_error(f"No family file matches {only}", f"{CLI} ids")
    return files


def sections(path: Path) -> dict[str, list[tuple[int, str]]]:
    found: dict[str, list[tuple[int, str]]] = {"": []}
    current = ""
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if line.startswith("## "):
            current = line[3:].strip()
            found.setdefault(current, [])
            continue
        found.setdefault(current, []).append((number, line))
    return found


def collect_ids(files: Sequence[Path] | None = None) -> list[dict[str, Any]]:
    rows = []
    for path in files or family_files():
        for number, line in sections(path).get("Sub-features", []):
            match = SUB_FEATURE_RE.match(line)
            if match:
                rows.append(
                    {
                        "id": match.group(1),
                        "file": path.name,
                        "line": number,
                        "untested": bool(UNTESTED_RE.search(line)),
                    }
                )
    return rows


def parse_index() -> list[dict[str, Any]]:
    if not index_path().exists():
        return []
    rows = []
    for line in index_path().read_text().splitlines():
        match = INDEX_ROW_RE.match(line)
        if match:
            rows.append(
                {
                    "id": match.group(1),
                    "title": match.group(2),
                    "file": match.group(3),
                    "count": int(match.group(5)),
                    "untested": int(match.group(6)),
                    "line": line,
                }
            )
    return rows


def source_prefixes(path: Path) -> list[str]:
    prefixes = []
    for line in path.read_text().splitlines():
        if line.startswith("Source:"):
            prefixes += re.findall(r"`([^`]+)`", line)
    return prefixes


def cited_path_exists(cited: str) -> bool:
    path = cited.split("::", 1)[0].rstrip(",.;:")
    if PLACEHOLDER_RE.search(path):
        return True
    if any(c in path for c in "*?["):
        return bool(glob.glob(str(REPO_ROOT / path)))
    return (REPO_ROOT / path).exists()


def bullets(path: Path) -> list[dict[str, Any]]:
    """The `Driving it` bullets: label, IDs named in it, and their sh blocks."""
    known = [row["id"] for row in collect_ids([path])]
    out: list[dict[str, Any]] = []
    block: list[str] | None = None
    for _, line in sections(path).get("Driving it", []):
        if block is not None:
            if line.strip().startswith("```"):
                out[-1]["blocks"].append("\n".join(block))
                block = None
            else:
                block.append(line[2:] if line.startswith("  ") else line)
            continue
        if line.startswith("- **"):
            label = line[2:]
            ids: list[str] = []
            for token in re.findall(r"`(S\d{2}\.[a-z0-9*-]+)`", label):
                if "*" in token:
                    pattern = re.compile(re.escape(token).replace(r"\*", ".*") + "$")
                    ids += [i for i in known if pattern.match(i)]
                else:
                    ids.append(token)
            out.append(
                {
                    "label": label,
                    "ids": list(dict.fromkeys(ids)),
                    "blocked": bool(re.search(r"\bblocked\b", label, re.I)),
                    "blocks": [],
                }
            )
        elif line.strip().startswith("```sh") and out:
            block = []
    return out


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------


def check_family(path: Path) -> list[str]:
    problems = []
    text = path.read_text()
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        problems.append("missing H1 title")
    headings = [line[3:].strip() for line in lines if line.startswith("## ")]
    if headings != SECTIONS:
        problems.append(f"H2 sections are {headings}, expected {SECTIONS}")
    for key in ("Source:", "Examples:"):
        if not any(line.startswith(key) for line in lines):
            problems.append(f"missing {key} line")
    family = path.name[:3]
    ids = collect_ids([path])
    if not ids:
        problems.append("no sub-features")
    for row in ids:
        if not row["id"].startswith(family + "."):
            problems.append(f"{row['id']} does not belong to {family}")
    known = {row["id"] for row in ids}
    # Citations live on the header lines and the Sub-features bullets; prose
    # elsewhere may name files a run creates (outputs/, a script the agent
    # writes), which need not exist.
    cited_lines = {n for n, _ in sections(path)[""]} | {
        n for n, _ in sections(path).get("Sub-features", [])
    }
    for number, line in enumerate(lines, 1):
        if number in cited_lines:
            for cited in CITED_PATH_RE.findall(line):
                if not cited_path_exists(cited):
                    problems.append(f"line {number}: {cited} does not exist")
        for ref in re.findall(rf"`({family}\.[a-z0-9-]+)`", line):
            if ref not in known:
                problems.append(f"line {number}: {ref} is not a sub-feature here")
    driven = {i for b in bullets(path) for i in b["ids"]}
    for row in ids:
        if row["id"] not in driven:
            problems.append(f"{row['id']} has no `Driving it` bullet")
    return problems


def baseline_problems(text: str) -> list[str]:
    lines = re.findall(r"^Maintenance baseline:.*$", text, re.M)
    if not lines:
        return ["no `Maintenance baseline: main@<sha> (<date>)` line"]
    if len(lines) > 1:
        return [f"{len(lines)} `Maintenance baseline:` lines; keep one"]
    if not BASELINE_LINE_RE.search(text):
        return [f"malformed baseline line: {lines[0]}"]
    return []


def cmd_check(args: argparse.Namespace) -> dict[str, Any]:
    files = family_files(args.file)
    problems: dict[str, list[str]] = {}
    for path in files:
        found = check_family(path)
        if found:
            problems[path.name] = found
    all_ids = collect_ids()
    seen: dict[str, str] = {}
    for row in all_ids:
        if row["id"] in seen:
            problems.setdefault(row["file"], []).append(
                f"duplicate ID {row['id']} (also in {seen[row['id']]})"
            )
        seen[row["id"]] = row["file"]
    if not args.file and index_path().exists():
        index = problems.setdefault("README.md", [])
        text = index_path().read_text()
        rows = parse_index()
        listed = {row["file"] for row in rows}
        index += [
            f"{p.name} is not in the index table"
            for p in family_files()
            if p.name not in listed
        ]
        counts: dict[str, list[int]] = {}
        for row in all_ids:
            c = counts.setdefault(row["file"], [0, 0])
            c[0] += 1
            c[1] += row["untested"]
        for row in rows:
            if not (FEATURE_MAP_DIR / row["file"]).exists():
                index.append(f"index links missing file {row['file']}")
                continue
            have = counts.get(row["file"], [0, 0])
            if [row["count"], row["untested"]] != have:
                if args.fix_counts:
                    new = re.sub(
                        r"\| \d+ \| \d+ \|$", f"| {have[0]} | {have[1]} |", row["line"]
                    )
                    text = text.replace(row["line"], new)
                else:
                    index.append(
                        f"{row['file']}: index says {row['count']} sub-features and "
                        f"{row['untested']} untested, file has {have[0]} and {have[1]}"
                        " (run `check --fix-counts`)"
                    )
        total = len(all_ids)
        untested = sum(row["untested"] for row in all_ids)
        totals = f"**{total} sub-features**, **{untested} of them untested**"
        current = re.search(
            r"\*\*\d+ sub-features\*\*, \*\*\d+ of them untested\*\*", text
        )
        if not current:
            index.append("no `**N sub-features**, **M of them untested**` line")
        elif current.group(0) != totals:
            if args.fix_counts:
                text = text.replace(current.group(0), totals)
            else:
                index.append(
                    f"totals say {current.group(0)}, map has {totals}"
                    " (run `check --fix-counts`)"
                )
        if args.fix_counts:
            index_path().write_text(text)
        index += baseline_problems(text)
        for number, line in enumerate(text.splitlines(), 1):
            for cited in CITED_PATH_RE.findall(line):
                if not cited_path_exists(cited):
                    index.append(f"line {number}: {cited} does not exist")
        if not index:
            del problems["README.md"]
    return {
        "ok": not problems,
        "files": len(files),
        "ids": len(all_ids),
        "untested": sum(row["untested"] for row in all_ids),
        "problems": problems,
    }


def cmd_ids(args: argparse.Namespace) -> dict[str, Any]:
    rows = collect_ids(family_files(args.file))
    return {"ok": True, "count": len(rows), "ids": [row["id"] for row in rows]}


# --------------------------------------------------------------------------
# owners, baseline
# --------------------------------------------------------------------------


def read_baseline() -> dict[str, str] | None:
    path = index_path()
    match = BASELINE_LINE_RE.search(path.read_text()) if path.exists() else None
    return {"sha": match.group(1), "date": match.group(2)} if match else None


def cmd_owners(args: argparse.Namespace) -> dict[str, Any]:
    diff_range = args.changed
    if not diff_range and not args.paths:
        baseline = read_baseline()
        if not baseline:
            usage_error(
                "no paths, no --changed and no baseline line in the map index",
                f"{CLI} owners --changed $BASE..$TARGET",
            )
        diff_range = f"{baseline['sha']}..{args.target or 'HEAD'}"
    if diff_range:
        diff = git("diff", "--name-only", diff_range)
        if diff is None:
            usage_error(f"git diff {diff_range} failed (shallow clone? fetch more)")
        paths = [p for p in diff.splitlines() if p]
    else:
        paths = list(args.paths)
    prefixes = {f.name: source_prefixes(f) for f in family_files()}
    mapping, unowned = [], []
    for path in paths:
        owners = [
            name
            for name, pre in prefixes.items()
            if any(path.startswith(p.rstrip("*")) for p in pre)
        ]
        if owners:
            mapping.append({"path": path, "families": owners})
        elif PRODUCT_PATH_RE.match(path):
            unowned.append(path)
    return {
        "ok": True,
        "range": diff_range,
        "changed": len(paths),
        "families": sorted({n for row in mapping for n in row["families"]}),
        "mapped": mapping,
        "unowned_product_paths": unowned,
    }


def cmd_baseline(args: argparse.Namespace) -> dict[str, Any]:
    text = index_path().read_text()
    problems = baseline_problems(text)
    if args.set:
        lines = re.findall(r"^Maintenance baseline:.*$", text, re.M)
        if len(lines) != 1:
            usage_error(problems[0])
        sha = git("rev-parse", "--verify", f"{args.set}^{{commit}}")
        if sha is None:
            usage_error(f"{args.set} is not a commit here (fetch it first)")
        if git("merge-base", "--is-ancestor", sha, args.main) is None:
            usage_error(f"{sha} is not on {args.main}; a baseline must be on main")
        date = args.date or datetime.now(UTC).strftime("%Y-%m-%d")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            usage_error(f"--date must be YYYY-MM-DD, got {date}")
        match = BASELINE_LINE_RE.match(lines[0])
        rest = (match.group(3) if match else "") or (
            ". The next maintenance pass starts from this commit; a pass proposes"
            " the next baseline in its PR, and merging that PR accepts it."
        )
        index_path().write_text(
            text.replace(lines[0], f"Maintenance baseline: main@{sha} ({date}){rest}")
        )
        return {"ok": True, "previous": lines[0], "sha": sha, "date": date}
    if problems:
        return {"ok": False, "problems": problems}
    baseline = read_baseline()
    assert baseline
    behind = git("rev-list", "--count", f"{baseline['sha']}..HEAD")
    return {
        "ok": True,
        **baseline,
        "commits_since": None if behind is None else int(behind),
        "hint": None
        if behind is not None
        else "baseline not in this clone; deepen it (git fetch --deepen=...)",
    }


# --------------------------------------------------------------------------
# run and report
# --------------------------------------------------------------------------


def run_dir(arg: str | None) -> Path:
    if arg:
        path = Path(arg)
    else:
        home = Path(
            os.environ.get(HOME_ENV) or Path(tempfile.gettempdir()) / "sdk-verify"
        )
        path = home / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (path / "transcripts").mkdir(parents=True, exist_ok=True)
    return path


def append_row(out: Path, row: dict[str, Any]) -> None:
    with (out / "ledger.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")


def split_statements(block: str) -> list[str]:
    """Top-level shell statements: lines joined while a quote is open or a
    line continues (trailing backslash, `&&`, `||` or `|`)."""
    statements, current = [], ""
    for line in block.splitlines():
        current = f"{current}\n{line}" if current else line
        if not current.strip():
            current = ""
            continue
        if re.search(r"(\\|&&|\|\||\|)\s*$", current):
            continue
        try:
            shlex.split(current, comments=True)
        except ValueError:  # an open quote: the statement goes on
            continue
        statements.append(current)
        current = ""
    if current.strip():
        statements.append(current)
    return statements


def needs_model(statement: str) -> bool:
    return bool(MODEL_COMMAND_RE.search(statement))


def proof_kind(statement: str) -> str:
    """deterministic (no model), example, or integration (the runner)."""
    if INTEGRATION_RE.search(statement):
        return "integration"
    return "example" if needs_model(statement) else "deterministic"


def kill_group(pgid: int) -> None:
    """End every process a recipe started, whatever it backgrounded."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.1)


def run_script(script: str, log: Any, timeout: float) -> int:
    """Run a script in its own process group; nothing it starts outlives it."""
    proc = subprocess.Popen(
        ["bash", "-eo", "pipefail", "-c", script],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        code = 124
    except BaseException:
        kill_group(proc.pid)
        proc.wait()
        raise
    kill_group(proc.pid)
    proc.wait()
    return code


def with_output_dir(statement: str, out_dir: Path) -> str:
    """Point an integration run at a fresh directory we read back."""
    if "--output-dir" in statement:
        return statement
    return f"{statement.rstrip()} --output-dir {shlex.quote(str(out_dir))}"


def integration_verdict(statement: str, out_dir: Path) -> tuple[str, str]:
    """run_infer.py exits 0 even when its tests fail: read its results.json."""
    files = sorted(out_dir.rglob("results.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return "fail", f"integration run wrote no results.json under {out_dir}"
    data = json.loads(files[-1].read_text())
    results = {
        i["instance_id"]: i["test_result"] for i in data.get("test_instances", [])
    }
    match = re.search(r"--eval-ids[ =]([\w,-]+)", statement)
    wanted = match.group(1).split(",") if match else list(results)
    if not wanted:
        return "fail", "integration run reported no tests"
    missing = [t for t in wanted if t not in results]
    failed = [
        t
        for t in wanted
        if t in results
        and not results[t].get("success")
        and not results[t].get("skipped")
    ]
    skipped = [t for t in wanted if t in results and results[t].get("skipped")]
    where = f"({files[-1]})"
    if missing or failed:
        return "fail", f"integration tests missing {missing} failed {failed} {where}"
    if skipped:
        return "blocked", f"integration tests skipped {skipped} {where}"
    return "pass", f"integration tests passed {wanted}"


def run_bullet(
    bullet: dict[str, Any], log: Any, work: Path, with_llm: bool, timeout: float
) -> dict[str, Any]:
    """Run a bullet's blocks: deterministic statements always, model-backed
    ones only with a key, and integration runs judged by their results."""
    result = {"outcome": "pass", "notes": [], "proof": set(), "skipped_model": 0}

    severity = {"pass": 0, "blocked": 1, "fail": 2}

    def worse(outcome: str, note: str) -> None:
        if severity[outcome] > severity[result["outcome"]]:
            result["outcome"] = outcome
        result["notes"].append(note)

    ran = 0
    for b, block in enumerate(bullet["blocks"], 1):
        kept, checks = [], []
        for k, statement in enumerate(split_statements(block), 1):
            if needs_model(statement) and not with_llm:
                result["skipped_model"] += 1
                continue
            if INTEGRATION_RE.search(statement):
                out_dir = work / f"block{b}-{k}"
                statement = with_output_dir(statement, out_dir)
                checks.append((statement, out_dir))
            kept.append(statement)
        if not kept:
            continue
        ran += 1
        script = "\n".join(kept)
        log.write(f"$ {script}\n")
        log.flush()
        start_offset = log.tell()
        started = time.monotonic()
        code = run_script(script, log, timeout)
        log.write(f"\n[exit {code} after {time.monotonic() - started:.0f}s]\n")
        log.flush()
        if code != 0:
            worse(
                "fail",
                f"block {b} exited {code}" + (" (timeout)" if code == 124 else ""),
            )
            break
        result["proof"].update(proof_kind(st) for st in kept)
        if any(proof_kind(st) == "example" for st in kept):
            output = Path(log.name).read_text(errors="replace")[start_offset:]
            if re.search(r"^EXAMPLE_COST: 0(?:\.0+)?\s*$", output, re.M):
                result["notes"].append(
                    f"block {b}: an example reported EXAMPLE_COST: 0"
                    " (no paid model calls)"
                )
        for statement, out_dir in checks:
            outcome, note = integration_verdict(statement, out_dir)
            log.write(f"[{note}]\n")
            if outcome != "pass":
                worse(outcome, note)
    if result["skipped_model"]:
        skipped = result["skipped_model"]
        result["notes"].append(
            f"{skipped} model-backed command(s) skipped (no LLM_API_KEY)"
        )
    if ran == 0 and result["outcome"] == "pass":
        result["outcome"] = "blocked"
    return result


def cmd_run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.file and not args.all:
        usage_error("name a family with --file, or pass --all", f"{CLI} run --file S05")
    files = family_files() if args.all else family_files(args.file)
    only = set(args.only.split(",")) if args.only else None
    known = {row["id"] for row in collect_ids()}
    if only and only - known:
        usage_error(f"unknown IDs: {sorted(only - known)}", f"{CLI} ids --file S05")
    with_llm = args.with_llm or bool(os.environ.get("LLM_API_KEY"))
    out = None if args.dry_run else run_dir(args.out)
    revision = git("rev-parse", "HEAD") or "unknown"
    summary: list[dict[str, Any]] = []
    per_feature: dict[str, list[dict[str, Any]]] = {}
    for path in files:
        for n, bullet in enumerate(bullets(path), 1):
            ids = [i for i in bullet["ids"] if not only or i in only]
            if not ids:
                continue
            outcome, note, transcript, proof = "pass", "", None, []
            if bullet["blocked"]:
                outcome, note = "blocked", "bullet is labeled blocked"
            elif not bullet["blocks"]:
                outcome, note = "not-run", "bullet has no sh block"
            elif args.dry_run:
                outcome, note = "not-run", "dry run"
            else:
                assert out is not None
                transcript = out / "transcripts" / f"{path.stem}-{n:02d}.log"
                work = out / "work" / f"{path.stem}-{n:02d}"
                with transcript.open("w") as log:
                    ran = run_bullet(bullet, log, work, with_llm, args.timeout)
                outcome, note = ran["outcome"], "; ".join(ran["notes"])
                proof = sorted(ran["proof"])
            entry = {
                "family": path.name,
                "bullet": n,
                "ids": ids,
                "result": outcome,
                "proof": proof,
                "note": note,
                "transcript": str(transcript) if transcript else None,
            }
            summary.append(entry)
            for feature in ids:
                per_feature.setdefault(feature, []).append(
                    entry | {"label": bullet["label"]}
                )
    if out is not None:
        # One row per sub-feature: a feature several bullets prove fails if
        # any of them failed, passes if one passed, and is blocked only when
        # nothing that ran could prove it. `proof` says which kinds of proof
        # passed (deterministic tests, examples, integration runs).
        for feature, entries in per_feature.items():
            worst = min(entries, key=lambda e: RESULT_PRECEDENCE[e["result"]])
            append_row(
                out,
                {
                    "feature": feature,
                    "result": worst["result"],
                    "proof": sorted(
                        {
                            k
                            for e in entries
                            if e["result"] == "pass"
                            for k in e["proof"]
                        }
                    ),
                    "entry": " | ".join(e["label"] for e in entries),
                    "note": "; ".join(
                        f"bullet {e['bullet']}: {e['result']}"
                        + (f" ({e['note']})" if e["note"] else "")
                        for e in entries
                    ),
                    "revision": revision,
                    "transcript": worst["transcript"],
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
            )
    counts: dict[str, int] = {}
    for entry in summary:
        counts[entry["result"]] = counts.get(entry["result"], 0) + 1
    return {
        "ok": not counts.get("fail"),
        "run": str(out) if out else None,
        "revision": revision,
        "with_llm": with_llm,
        "bullets": counts,
        "results": summary,
    }


def read_ledger(path: Path) -> list[dict[str, Any]]:
    if (path / "ledger.jsonl").exists():
        path = path / "ledger.jsonl"
    if not path.is_file():
        usage_error(f"no ledger at {path}")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def latest_by_time(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The newest row per feature by its recorded time, whatever order the
    ledgers were given in (ties keep the later argument)."""
    ordered = sorted(rows, key=lambda row: row.get("at") or "")
    return {row["feature"]: row for row in ordered}


def conflicting_attempts(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Features whose attempts in this set disagree, oldest first."""
    seen: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(rows, key=lambda row: row.get("at") or ""):
        seen.setdefault(row["feature"], []).append(row)
    return [
        {
            "feature": feature,
            "attempts": [
                {"at": a.get("at"), "result": a["result"], "run": a.get("run_dir")}
                for a in attempts
            ],
        }
        for feature, attempts in sorted(seen.items())
        if len({a["result"] for a in attempts}) > 1
    ]


def compare_ledgers(
    before_rows: Iterable[dict[str, Any]], after_rows: Iterable[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    after_rows = list(after_rows)
    before = latest_by_time(before_rows)
    after = latest_by_time(after_rows)
    changes: dict[str, list[dict[str, Any]]] = {
        "newly_failing": [],
        "newly_passing": [],
        "other": [],
        "not_checked": [],
        "conflicting": conflicting_attempts(after_rows),
    }
    for feature in sorted(set(before) | set(after)):
        old = before.get(feature, {}).get("result")
        new = after.get(feature, {}).get("result")
        if old == new:
            continue
        entry = {"feature": feature, "before": old, "after": new}
        if new is None:
            changes["not_checked"].append(entry)
        elif new == "fail":
            changes["newly_failing"].append(entry)
        elif new == "pass" and old is not None:
            changes["newly_passing"].append(entry)
        else:
            changes["other"].append(entry)
    return changes


def cmd_report(args: argparse.Namespace) -> dict[str, Any]:
    rows = [row | {"run_dir": r} for r in args.runs for row in read_ledger(Path(r))]
    latest = latest_by_time(rows)
    conflicting = conflicting_attempts(rows)
    order = {"fail": 0, "blocked": 1, "not-run": 2, "pass": 3}
    ordered = sorted(latest.values(), key=lambda r: (order[r["result"]], r["feature"]))
    counts: dict[str, int] = {}
    for row in ordered:
        counts[row["result"]] = counts.get(row["result"], 0) + 1
    lines: list[str] = []
    changes = None
    if args.baseline:
        changes = compare_ledgers(read_ledger(Path(args.baseline)), rows)
        lines += ["## Changes since baseline", ""]
        for key, title in (
            ("newly_failing", "Newly failing"),
            ("newly_passing", "Newly passing"),
            ("other", "Other changes"),
            ("not_checked", "Not checked this run"),
        ):
            if changes[key]:
                lines += [f"{title}:", ""]
                for c in changes[key]:
                    old, new = c["before"] or "absent", c["after"] or "absent"
                    lines.append(f"- {c['feature']}: {old} -> {new}")
                lines.append("")
        if not any(v for k, v in changes.items() if k != "conflicting"):
            lines += ["No result changed against the baseline.", ""]
    if conflicting:
        lines += ["## Conflicting attempts", ""]
        for c in conflicting:
            steps = ", ".join(f"{a['result']} at {a['at']}" for a in c["attempts"])
            lines.append(f"- {c['feature']}: {steps}")
        lines.append("")
    if lines:
        lines += ["## Checks", ""]
    lines += [
        "| Feature | Result | Revision | Note | Transcript |",
        "|---|---|---|---|---|",
    ]
    for row in ordered:
        cells = [
            row["feature"],
            row["result"],
            (row.get("revision") or "")[:12],
            row.get("note") or "",
            row.get("transcript") or "",
        ]
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    markdown = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(markdown + "\n")
    missing = [r["id"] for r in collect_ids() if r["id"] not in latest]
    return {
        "ok": True,
        "counts": counts,
        "features_with_evidence": len(latest),
        "map_ids_without_evidence": len(missing),
        "changes": changes,
        "conflicting": conflicting,
        "markdown": markdown if args.print_markdown else None,
        "out": args.out,
    }


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

TOP_HELP = f"""\
{CLI}: check, route, replay and report on the in-process SDK feature map
(.agents/skills/verify-sdk/references/feature-map/). Every command prints one
JSON object. Exit codes: 0 ok, 1 action failed, 2 usage.

  check      structure, IDs, index counts, every cited path exists, baseline line
  ids        every sub-feature ID
  owners     map changed source paths to families (default: since the baseline)
  baseline   read or move the index's Maintenance baseline line
  run        replay a family's `sh` blocks into a ledger
  report     latest result per sub-feature, fail first; compare with a baseline

Examples:
  {CLI} check
  {CLI} owners
  {CLI} run --file S05
  {CLI} run --file S05 --only S05.event-base,S05.json-roundtrip --out /tmp/sdk-run
  {CLI} report --runs /tmp/sdk-run --baseline /tmp/yesterday --print-markdown
"""


def build_parser() -> argparse.ArgumentParser:
    fmt = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(
        prog=CLI, description=TOP_HELP, formatter_class=fmt
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser(
        "check",
        help="validate the map",
        formatter_class=fmt,
        epilog=f"Examples:\n  {CLI} check\n  {CLI} check --file S05\n"
        f"  {CLI} check --fix-counts",
    )
    p.set_defaults(func=cmd_check)
    p.add_argument("--file")
    p.add_argument("--fix-counts", action="store_true")

    p = sub.add_parser(
        "ids",
        help="every sub-feature ID",
        formatter_class=fmt,
        epilog=f"Examples:\n  {CLI} ids --file S05",
    )
    p.set_defaults(func=cmd_ids)
    p.add_argument("--file")

    p = sub.add_parser(
        "owners",
        help="map changed source paths to families",
        formatter_class=fmt,
        epilog=f"""Examples:
  {CLI} owners                                  # since the maintenance baseline
  {CLI} owners --target "$TARGET"
  {CLI} owners --changed $BASE..$TARGET
  {CLI} owners openhands-sdk/openhands/sdk/event/base.py""",
    )
    p.set_defaults(func=cmd_owners)
    p.add_argument("paths", nargs="*")
    p.add_argument("--changed", metavar="BASE..TARGET")
    p.add_argument("--target", help="with no paths or --changed: baseline..TARGET")

    p = sub.add_parser(
        "baseline",
        help="read or move the Maintenance baseline line",
        formatter_class=fmt,
        epilog=f"""Examples:
  {CLI} baseline
  {CLI} baseline --set "$TARGET"            # in the pass's PR; merging accepts it""",
    )
    p.set_defaults(func=cmd_baseline)
    p.add_argument("--set", metavar="SHA")
    p.add_argument("--date", help="YYYY-MM-DD for --set (default: today, UTC)")
    p.add_argument("--main", default="origin/main")

    p = sub.add_parser(
        "run",
        help="replay a family's sh blocks into a ledger",
        formatter_class=fmt,
        epilog=f"""Examples:
  {CLI} run --file S05
  {CLI} run --file S05 --only S05.event-base
  {CLI} run --all --out "$SDK_VERIFY_HOME/full"
  {CLI} run --file S01 --dry-run

Each bullet's ```sh blocks run in Bash with `set -eo pipefail` from the
repository root, stdin closed, each block in its own process group that is
killed when the block ends or times out. A bullet passes when every block it
ran exits 0. Commands that run examples/ scripts, tests/examples/ or the
integration runner (tests/integration/run_infer.py) need a model: they run
when LLM_API_KEY is set (or --with-llm) and are skipped otherwise, while the
deterministic commands in the same block still run; a bullet with nothing
left to run is `blocked`. Integration runs get a fresh --output-dir and pass
only when every requested test is present and successful in its
results.json (the runner exits 0 on failed tests). Each ledger row's `proof`
says which kinds passed: deterministic, example, integration. An example that
prints `EXAMPLE_COST: 0` gets a note, since some examples run partly without
a model. A bullet whose label says
"blocked" is reported and not executed. Rows go to <out>/ledger.jsonl and
transcripts to <out>/transcripts/; <out> defaults to a new directory under
${HOME_ENV} (or $TMPDIR/sdk-verify).""",
    )
    p.set_defaults(func=cmd_run)
    p.add_argument("--file")
    p.add_argument("--all", action="store_true")
    p.add_argument("--only", help="comma-separated sub-feature IDs")
    p.add_argument("--out", help="run directory (created if missing)")
    p.add_argument("--with-llm", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--timeout", type=float, default=1800, help="seconds per block")

    p = sub.add_parser(
        "report",
        help="latest result per sub-feature, fail first",
        formatter_class=fmt,
        epilog=f"""Examples:
  {CLI} report --runs <run> --print-markdown
  {CLI} report --runs <runA> <runB> --baseline <yesterday's run> --out report.md""",
    )
    p.set_defaults(func=cmd_report)
    p.add_argument("--runs", nargs="+", required=True)
    p.add_argument("--baseline", help="an earlier run directory or ledger.jsonl")
    p.add_argument("--out")
    p.add_argument("--print-markdown", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0 if argv is None or not argv else 2
    try:
        result = args.func(args)
    except CliError as exc:
        print(
            json.dumps(
                {"ok": False, "exit": exc.exit_code, "error": str(exc), **exc.data},
                indent=2,
            )
        )
        return exc.exit_code
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
