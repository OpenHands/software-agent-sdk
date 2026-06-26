"""Benchmark: SCA vs monolithic vs baseline — SWE-bench Lite single-turn patch generation.

Three modes run against the same tasks:
  baseline   — no system prompt (raw capability)
  monolithic — full engineering_agents.md as system prompt
  sca        — only the matched section from engineering_agents.md (SCA routing)

Pipeline per mode:
  1. Load N tasks from SWE-bench Lite
  2. Clone repo at base_commit (cached in ~/.cache/sca-swebench/)
  3. Localize relevant files via identifier grep
  4. ONE Ollama call per mode: system=<prompt strategy>, user=<issue + file>
  5. Parse SEARCH/REPLACE blocks → apply to file → capture git diff
  6. Write predictions-<mode>.json
  7. Optional: run SWE-bench Docker evaluation (--evaluate)

Cleanup:
    git clean -f examples/01_standalone_sdk/58_sca_swebench.py
    git clean -f examples/engineering_agents.md
    rm -rf ~/.cache/sca-swebench/

Usage:
    uv run python examples/01_standalone_sdk/58_sca_swebench.py --n 3
    uv run python examples/01_standalone_sdk/58_sca_swebench.py --n 5 --evaluate
    uv run python examples/01_standalone_sdk/58_sca_swebench.py --n 5 --mode sca
    uv run python examples/01_standalone_sdk/58_sca_swebench.py --n 5 --verbose

EXAMPLE_COST: 0
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import git
import litellm
from datasets import load_dataset
from rich.console import Console
from rich.table import Table
from rich import box

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "openhands-sdk"))

_OLLAMA_BASE = "http://localhost:11434"
_MODEL = "ollama/gemma4:26b"
_NUM_CTX = 32768
_CACHE_DIR = Path.home() / ".cache" / "sca-swebench"
_MAX_FILE_CHARS = 8_000
_MAX_FILES = 3
_MAX_TOTAL_CHARS = 20_000
_AGENTS_MD = Path(__file__).resolve().parents[1] / "engineering_agents.md"

MODES = ("baseline", "monolithic", "sca")

_PATCH_INSTRUCTIONS = """\
Output one or more SEARCH/REPLACE blocks — nothing else.
Format each block exactly like this:

<<<<<<< SEARCH
[exact lines from the file to replace, copied verbatim]
=======
[replacement lines]
>>>>>>> REPLACE

Rules:
- SEARCH must match the file content character-for-character (copy it exactly)
- Make the smallest change that fixes the reported behaviour
- Do not modify unrelated code or reformat whitespace
- One block per logical change; multiple blocks if needed
"""

_BASE_INSTRUCTION = (
    "You are an expert software engineer fixing bugs in open-source Python projects.\n"
    "You will be given a GitHub issue and the relevant source files.\n\n"
    + _PATCH_INSTRUCTIONS
)


# ---------------------------------------------------------------------------
# SCA routing — pick the right section from engineering_agents.md
# ---------------------------------------------------------------------------

def _load_sca_sections() -> list[tuple[str, str]]:
    """Return list of (header, body) from engineering_agents.md."""
    if not _AGENTS_MD.exists():
        return []
    content = _AGENTS_MD.read_text()
    from openhands.sdk.subagent.section_parser import parse_sections
    agents = parse_sections(content, source_path=str(_AGENTS_MD))
    return [(a.description, a.system_prompt) for a in agents]


_SCA_SECTIONS: list[tuple[str, str]] = []


def _pick_section(problem_statement: str) -> tuple[str, str] | None:
    """Return (title, system_prompt) for the most relevant section."""
    global _SCA_SECTIONS
    if not _SCA_SECTIONS:
        _SCA_SECTIONS = _load_sca_sections()
    if not _SCA_SECTIONS:
        return None

    from openhands.sdk.subagent.section_parser import ALWAYS_ACTIVE_SENTINEL
    problem_lower = problem_statement.lower()

    # Score by trigger hits
    from openhands.sdk.subagent.section_parser import parse_sections
    content = _AGENTS_MD.read_text()
    agents = parse_sections(content, source_path=str(_AGENTS_MD))

    best_agent = None
    best_score = -1
    always_active = None

    for agent in agents:
        if ALWAYS_ACTIVE_SENTINEL in agent.triggers:
            if always_active is None:
                always_active = agent
            continue
        score = sum(1 for t in agent.triggers if t in problem_lower)
        if score > best_score:
            best_score = score
            best_agent = agent

    chosen = best_agent if best_agent and best_score > 0 else always_active
    if chosen is None:
        return None
    return chosen.description, _BASE_INSTRUCTION + "\n\n" + chosen.system_prompt


def get_system_prompt(mode: str, problem_statement: str) -> tuple[str, str]:
    """Return (mode_label, system_prompt) for the given mode."""
    if mode == "baseline":
        return "baseline", _BASE_INSTRUCTION

    if mode == "monolithic":
        if _AGENTS_MD.exists():
            full = _AGENTS_MD.read_text()
            return "monolithic", _BASE_INSTRUCTION + "\n\n" + full
        return "monolithic (no agents.md)", _BASE_INSTRUCTION

    if mode == "sca":
        section = _pick_section(problem_statement)
        if section:
            title, prompt = section
            return f"sca:{title}", prompt
        return "sca (no match→baseline)", _BASE_INSTRUCTION

    raise ValueError(f"Unknown mode: {mode}")


# ---------------------------------------------------------------------------
# Localization heuristic
# ---------------------------------------------------------------------------

def _extract_identifiers(problem_statement: str) -> list[str]:
    """Pull candidate identifiers from backticks, code blocks, and CamelCase names."""
    found: list[str] = []

    # Backtick-quoted: `name`, `Class.method`, `module.func`
    found.extend(re.findall(r"`([A-Za-z_][A-Za-z0-9_.]*)`", problem_statement))

    # Inline Python paths: from x.y import Z  /  import x.y.z
    found.extend(re.findall(r"(?:from|import)\s+([\w.]+)", problem_statement))

    # CamelCase class names not already captured
    found.extend(re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", problem_statement))

    # snake_case function names (min 3 chars each segment)
    found.extend(re.findall(r"\b([a-z][a-z0-9]+(?:_[a-z][a-z0-9]+)+)\b", problem_statement))

    # Dedupe while preserving order, drop very short tokens and common words
    _SKIP = {"the", "and", "for", "not", "but", "are", "from", "with", "that", "this"}
    seen: set[str] = set()
    result: list[str] = []
    for tok in found:
        tok = tok.strip(".")
        if tok and tok not in seen and tok.lower() not in _SKIP and len(tok) >= 4:
            seen.add(tok)
            result.append(tok)
    return result[:20]  # cap to avoid too many greps


def _module_to_path(identifier: str, repo_path: Path) -> Path | None:
    """Convert dotted module path to a .py file path if it exists (never __init__.py)."""
    parts = identifier.replace(".", "/")
    candidate = repo_path / f"{parts}.py"
    if candidate.exists():
        return candidate
    return None


def localize_files(problem_statement: str, repo_path: Path) -> list[Path]:
    """Return up to _MAX_FILES relevant source files ranked by identifier hits."""
    identifiers = _extract_identifiers(problem_statement)
    hit_counts: dict[Path, int] = {}

    for ident in identifiers:
        # Try as a module path first
        module_file = _module_to_path(ident.split(".")[0] if "." in ident else ident, repo_path)
        if module_file:
            hit_counts[module_file] = hit_counts.get(module_file, 0) + 3  # weight direct path hit

        # Grep for definition of the identifier (def/class), not just any use
        for grep_pat in (f"def {ident}", f"class {ident}"):
            try:
                result = subprocess.run(
                    ["grep", "-rl", "--include=*.py", grep_pat, str(repo_path)],
                    capture_output=True, text=True, timeout=10,
                )
                for line in result.stdout.splitlines():
                    p = Path(line.strip())
                    if (p.exists()
                            and "test" not in p.parts
                            and "tests" not in p.parts
                            and p.name != "__init__.py"):
                        hit_counts[p] = hit_counts.get(p, 0) + 2  # definition > usage
            except subprocess.TimeoutExpired:
                pass

        # Also grep for any usage (lower weight)
        try:
            result = subprocess.run(
                ["grep", "-rl", "--include=*.py", ident, str(repo_path)],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                p = Path(line.strip())
                if (p.exists()
                        and "test" not in p.parts
                        and "tests" not in p.parts
                        and p.name != "__init__.py"):
                    hit_counts[p] = hit_counts.get(p, 0) + 1
        except subprocess.TimeoutExpired:
            pass

    ranked = sorted(hit_counts.items(), key=lambda x: x[1], reverse=True)
    return [p for p, _ in ranked[:_MAX_FILES]]


# ---------------------------------------------------------------------------
# Repo management
# ---------------------------------------------------------------------------

def _get_repo(repo_name: str, base_commit: str, console: Console) -> Path:
    """Clone (first time) or fetch the repo, then return a worktree at base_commit."""
    cache_path = _CACHE_DIR / "repos" / repo_name.replace("/", "__")
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if not cache_path.exists():
        console.print(f"  Cloning [cyan]{repo_name}[/cyan]…", end=" ")
        git.Repo.clone_from(
            f"https://github.com/{repo_name}.git",
            str(cache_path),
            depth=None,
        )
        console.print("done")
    else:
        console.print(f"  Using cached [cyan]{repo_name}[/cyan]")

    # Create a worktree at the exact base_commit so we don't mutate the cache
    worktree_dir = _CACHE_DIR / "worktrees" / f"{repo_name.replace('/', '__')}__{base_commit[:8]}"
    if not worktree_dir.exists():
        repo = git.Repo(str(cache_path))
        try:
            repo.git.fetch("origin", base_commit)
        except git.GitCommandError:
            pass  # already have it
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)
        repo.git.worktree("add", "--detach", str(worktree_dir), base_commit)

    return worktree_dir


def _cleanup_worktree(repo_name: str, base_commit: str) -> None:
    cache_path = _CACHE_DIR / "repos" / repo_name.replace("/", "__")
    worktree_dir = _CACHE_DIR / "worktrees" / f"{repo_name.replace('/', '__')}__{base_commit[:8]}"
    if worktree_dir.exists():
        try:
            repo = git.Repo(str(cache_path))
            repo.git.worktree("remove", "--force", str(worktree_dir))
        except Exception:
            shutil.rmtree(worktree_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Patch generation
# ---------------------------------------------------------------------------

def _read_file_excerpt(path: Path, max_chars: int = _MAX_FILE_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:max_chars] + ("\n... [truncated]" if len(text) > max_chars else "")
    except OSError:
        return ""


def generate_patch_with_tokens(
    instance_id: str,
    problem_statement: str,
    relevant_files: list[Path],
    repo_path: Path,
    system_prompt: str,
) -> tuple[str, int]:
    """Single LLM call → (unified diff string, prompt_tokens). Diff may be empty."""
    file_sections: list[str] = []
    total = 0
    for fpath in relevant_files:
        rel = fpath.relative_to(repo_path)
        content = _read_file_excerpt(fpath, _MAX_FILE_CHARS)
        section = f"### {rel}\n```python\n{content}\n```"
        total += len(section)
        if total > _MAX_TOTAL_CHARS:
            break
        file_sections.append(section)

    user_msg = (
        f"GitHub issue ({instance_id}):\n{problem_statement}\n\n"
        f"Relevant source files:\n"
        f"{chr(10).join(file_sections) if file_sections else '(no files localized — use your best judgment)'}"
    )

    resp = litellm.completion(
        model=_MODEL,
        api_base=_OLLAMA_BASE,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=4096,
        temperature=0.1,
        num_ctx=_NUM_CTX,
        extra_body={"think": False},
    )
    raw = resp.choices[0].message.content or ""
    prompt_tokens = getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0
    patch = _apply_search_replace(raw, relevant_files, repo_path)
    return patch, prompt_tokens


def generate_patch(
    instance_id: str,
    problem_statement: str,
    relevant_files: list[Path],
    repo_path: Path,
    system_prompt: str,
) -> str:
    patch, _ = generate_patch_with_tokens(instance_id, problem_statement, relevant_files, repo_path, system_prompt)
    return patch


def _apply_search_replace(raw: str, relevant_files: list[Path], repo_path: Path) -> str:
    """Parse SEARCH/REPLACE blocks, apply to files, return unified diff."""
    block_re = re.compile(
        r"<{7}\s*SEARCH\s*\n(.*?)\n={7}\s*\n(.*?)\n>{7}\s*REPLACE",
        re.DOTALL,
    )
    blocks = block_re.findall(raw)
    if not blocks:
        # Fall back only to a properly-formatted diff (not reasoning text)
        fence = re.search(r"```(?:diff|patch)?\n(.*?)```", raw, re.DOTALL)
        candidate = fence.group(1).strip() if fence else ""
        if not candidate:
            diff_start = re.search(r"^(diff --git |-{3} a/)", raw, re.MULTILINE)
            candidate = raw[diff_start.start():].strip() if diff_start else ""
        # Reject if it doesn't look like a real diff
        if candidate and re.search(r"^[-+]{3} ", candidate, re.MULTILINE):
            return candidate
        return ""

    # Group blocks by target file so we apply all changes to one file in a
    # single pass — avoids duplicate diffs when the model emits multiple
    # SEARCH/REPLACE blocks for the same file.
    from collections import defaultdict
    file_blocks: dict[Path, list[tuple[str, str]]] = defaultdict(list)

    for search_text, replace_text in blocks:
        target: Path | None = None
        for fpath in relevant_files:
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if search_text in content:
                target = fpath
                break
        if target is not None:
            file_blocks[target].append((search_text, replace_text))

    patches: list[str] = []
    for target, file_changes in file_blocks.items():
        original = target.read_text(encoding="utf-8", errors="replace")
        modified = original
        for search_text, replace_text in file_changes:
            modified = modified.replace(search_text, replace_text, 1)
        if modified == original:
            continue

        target.write_text(modified, encoding="utf-8")
        rel = str(target.relative_to(repo_path))
        result = subprocess.run(
            ["git", "diff", "--", rel],
            cwd=str(repo_path),
            capture_output=True, text=True,
        )
        if result.stdout.strip():
            patches.append(result.stdout.strip())
        target.write_text(original, encoding="utf-8")  # restore

    return "\n".join(patches)


# ---------------------------------------------------------------------------
# SWE-bench evaluation
# ---------------------------------------------------------------------------

def run_swebench_eval(predictions_path: Path, console: Console, run_id: str = "sca_benchmark") -> dict:
    """Run official SWE-bench Docker evaluation. Returns results dict."""
    console.print(f"\n[bold]Running SWE-bench evaluation (Docker) — {run_id}…[/bold]")
    try:
        result = subprocess.run(
            [
                "uv", "run", "python", "-m", "swebench.harness.run_evaluation",
                "--dataset_name", "princeton-nlp/SWE-bench_Lite",
                "--split", "test",
                "--predictions_path", str(predictions_path),
                "--max_workers", "4",
                "--run_id", run_id,
            ],
            capture_output=True, text=True, timeout=3600,
        )
        console.print(result.stdout[-3000:] if result.stdout else "(no stdout)")
        if result.returncode != 0:
            console.print(f"[red]Eval exited {result.returncode}[/red]")
            console.print(result.stderr[-1000:])
            return {}
    except subprocess.TimeoutExpired:
        console.print("[red]Evaluation timed out (1h)[/red]")
        return {}

    # Look for results file — filename format is "{model_id}.{run_id}.json"
    results_glob = list(Path(".").glob(f"**/*{run_id}*.json"))
    if results_glob:
        return json.loads(results_glob[0].read_text())
    return {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run_mode(
    mode: str,
    tasks: list,
    skip_clone: bool,
    verbose: bool,
    console: Console,
) -> tuple[list[dict], list[dict]]:
    """Run one mode across all tasks. Returns (predictions, stats)."""
    predictions: list[dict] = []
    stats: list[dict] = []
    model_id = f"sca_gemma4_26b_{mode}"

    for i, task in enumerate(tasks, 1):
        instance_id = task["instance_id"]
        repo_name = task["repo"]
        base_commit = task["base_commit"]
        problem = task["problem_statement"]

        # Repo worktree
        if skip_clone:
            repo_path = _CACHE_DIR / "worktrees" / f"{repo_name.replace('/', '__')}__{base_commit[:8]}"
            if not repo_path.exists():
                console.print(f"  [red]{instance_id}: worktree not cached[/red]")
                predictions.append({"instance_id": instance_id, "model_patch": "", "model_name_or_path": model_id})
                stats.append({"instance_id": instance_id, "mode": mode, "patch_len": 0, "prompt_tokens": 0})
                continue
        else:
            try:
                repo_path = _get_repo(repo_name, base_commit, console)
            except Exception as e:
                console.print(f"  [red]{instance_id}: clone failed: {e}[/red]")
                predictions.append({"instance_id": instance_id, "model_patch": "", "model_name_or_path": model_id})
                stats.append({"instance_id": instance_id, "mode": mode, "patch_len": 0, "prompt_tokens": 0, "error": str(e)})
                continue

        relevant = localize_files(problem, repo_path)

        mode_label, system_prompt = get_system_prompt(mode, problem)
        prompt_tokens = len(system_prompt) // 4  # rough estimate before LLM call

        patch, actual_prompt_tokens = generate_patch_with_tokens(
            instance_id, problem, relevant, repo_path, system_prompt
        )

        if verbose:
            console.print(f"    [{mode}:{mode_label}] patch={len(patch)}ch tokens≈{actual_prompt_tokens}")
            if patch:
                console.print(f"    [dim]{patch[:200]}[/dim]")

        predictions.append({"instance_id": instance_id, "model_patch": patch, "model_name_or_path": model_id})
        stats.append({
            "instance_id": instance_id,
            "mode": mode,
            "mode_label": mode_label,
            "patch_len": len(patch),
            "prompt_tokens": actual_prompt_tokens,
        })

    return predictions, stats


def run_benchmark(
    n_tasks: int,
    modes: list[str],
    evaluate: bool,
    verbose: bool,
    skip_clone: bool,
    output_dir: Path,
) -> None:
    console = Console()
    console.print(f"\n[bold cyan]SWE-bench A/B Benchmark[/bold cyan]")
    console.print(f"Model:  [dim]{_MODEL}[/dim]")
    console.print(f"Tasks:  {n_tasks}  |  Modes: {', '.join(modes)}")
    console.print(f"Eval:   {'yes (Docker)' if evaluate else 'predictions only'}\n")

    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    tasks = list(ds)[:n_tasks]

    # Clone all repos once (shared across modes)
    if not skip_clone:
        console.print("[bold]Cloning repos…[/bold]")
        for task in tasks:
            try:
                _get_repo(task["repo"], task["base_commit"], console)
            except Exception as e:
                console.print(f"  [red]Clone failed for {task['repo']}: {e}[/red]")

    all_stats: dict[str, list[dict]] = {}
    output_dir.mkdir(parents=True, exist_ok=True)

    for mode in modes:
        console.rule(f"[bold cyan]Mode: {mode}[/bold cyan]")
        preds, stats = _run_mode(mode, tasks, skip_clone=True, verbose=verbose, console=console)
        output_path = output_dir / f"predictions_{mode}.json"
        output_path.write_text(json.dumps(preds, indent=2))
        console.print(f"  Written → {output_path}")
        all_stats[mode] = stats

    # Comparison table
    console.rule("[bold]Comparison[/bold]")
    table = Table(title="Mode × Task Results", box=box.ROUNDED)
    table.add_column("Instance", style="cyan", no_wrap=True)
    for mode in modes:
        table.add_column(f"{mode}\npatch?", justify="center")
        table.add_column(f"tokens", justify="right")

    for i, task in enumerate(tasks):
        iid = task["instance_id"]
        row = [iid]
        for mode in modes:
            s = all_stats[mode][i] if i < len(all_stats.get(mode, [])) else {}
            patch_len = s.get("patch_len", 0)
            tokens = s.get("prompt_tokens", 0)
            row.append("[green]✓[/green]" if patch_len > 0 else "[red]✗[/red]")
            row.append(f"{tokens:,}" if tokens else "-")
        table.add_row(*row)
    console.print(table)

    # Token reduction summary
    summary = Table(title="Token Usage by Mode", box=box.SIMPLE)
    summary.add_column("Mode", style="bold")
    summary.add_column("Patches", justify="right")
    summary.add_column("Avg prompt tokens", justify="right")
    summary.add_column("vs baseline", justify="right")

    baseline_avg = None
    for mode in modes:
        stats_list = all_stats.get(mode, [])
        tokens = [s["prompt_tokens"] for s in stats_list if s.get("prompt_tokens", 0) > 0]
        patches = sum(1 for s in stats_list if s.get("patch_len", 0) > 0)
        avg = sum(tokens) / len(tokens) if tokens else 0
        if mode == "baseline":
            baseline_avg = avg
        vs = ""
        if baseline_avg and avg and mode != "baseline":
            saving = (1 - avg / baseline_avg) * 100
            vs = f"[{'green' if saving > 0 else 'red'}]{saving:+.0f}%[/]"
        summary.add_row(mode, f"{patches}/{n_tasks}", f"{avg:,.0f}", vs)
    console.print(summary)

    # Docker evaluation per mode
    if evaluate:
        eval_summary = Table(title="SWE-bench Eval Results", box=box.ROUNDED)
        eval_summary.add_column("Mode")
        eval_summary.add_column("Resolved", justify="right")
        eval_summary.add_column("Unresolved", justify="right")
        eval_summary.add_column("Errors", justify="right")
        eval_summary.add_column("Resolve rate", justify="right")

        for mode in modes:
            output_path = output_dir / f"predictions_{mode}.json"
            run_id = f"sca_bench_{mode}"
            results = run_swebench_eval(output_path, console, run_id=run_id)
            resolved = results.get("resolved_instances", 0)
            unresolved = results.get("unresolved_instances", 0)
            errors = results.get("error_instances", 0)
            submitted = results.get("submitted_instances", 1)
            rate = resolved / max(submitted, 1) * 100
            eval_summary.add_row(
                mode,
                str(resolved),
                str(unresolved),
                str(errors),
                f"[{'green' if resolved > 0 else 'dim'}]{rate:.0f}%[/]",
            )
        console.print(eval_summary)

    print("\nEXAMPLE_COST: 0")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SWE-bench A/B benchmark: baseline vs monolithic vs SCA")
    parser.add_argument("--n", type=int, default=3, help="Number of tasks to run")
    parser.add_argument("--evaluate", action="store_true", help="Run Docker evaluation after generation")
    parser.add_argument("--verbose", action="store_true", help="Print patch previews")
    parser.add_argument("--skip-clone", action="store_true", help="Use cached worktrees only")
    parser.add_argument(
        "--mode", nargs="+", choices=list(MODES) + ["all"], default=["all"],
        help="Which modes to run (default: all three)",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("/tmp/sca_swebench_ab"),
        help="Directory for predictions JSON files per mode",
    )
    args = parser.parse_args()

    modes = list(MODES) if "all" in args.mode else args.mode

    run_benchmark(
        n_tasks=args.n,
        modes=modes,
        evaluate=args.evaluate,
        verbose=args.verbose,
        skip_clone=args.skip_clone,
        output_dir=args.output_dir,
    )
