"""Benchmark: SCA vs monolithic — live token comparison via Ollama.

Runs the same task against two agents:
  - Monolithic: full AGENTS.md as system prompt
  - SCA:        only the matched section as system prompt

Measures actual prompt_tokens reported by Ollama for each call.
No files are created. Cleanup: git clean -f examples/01_standalone_sdk/56_sca_live_benchmark.py

Usage:
    uv run python examples/01_standalone_sdk/56_sca_live_benchmark.py
    uv run python examples/01_standalone_sdk/56_sca_live_benchmark.py --verbose
    uv run python examples/01_standalone_sdk/56_sca_live_benchmark.py path/to/AGENTS.md

EXAMPLE_COST: 0
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import NamedTuple

import litellm
from rich.console import Console
from rich.table import Table
from rich import box

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "openhands-sdk"))
sys.path.insert(0, str(_REPO_ROOT / "openhands-tools"))

from openhands.sdk.subagent.section_parser import (
    parse_sections,
    parse_xml_sections,
    ALWAYS_ACTIVE_SENTINEL,
)

_OLLAMA_BASE = "http://localhost:11434"
_MODEL = "ollama/gemma4:26b"
_NUM_CTX = 32768

# 3 tasks — enough to show routing variety without a long wait
BENCHMARK_TASKS = [
    "Fix the failing unit tests in the CI pipeline",
    "Add a new REST API endpoint that returns JSON",
    "Set up the Docker development environment",
]


class RunResult(NamedTuple):
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    response_text: str


def chat(system: str, user: str) -> RunResult:
    """Single completion via Ollama. Returns token counts and response."""
    t0 = time.perf_counter()
    resp = litellm.completion(
        model=_MODEL,
        api_base=_OLLAMA_BASE,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=256,
        temperature=0.1,
        num_ctx=_NUM_CTX,
    )
    latency = time.perf_counter() - t0
    usage = resp.usage
    text = resp.choices[0].message.content or ""
    return RunResult(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        latency_s=latency,
        response_text=text,
    )


def pick_section(task: str, sections: list) -> tuple:
    """Return (agent, match_reasons) for the best-matching section."""
    task_lower = task.lower()
    always_active_match = None

    for agent in sections:
        if ALWAYS_ACTIVE_SENTINEL in agent.triggers:
            if always_active_match is None:
                always_active_match = (agent, [ALWAYS_ACTIVE_SENTINEL])
            continue
        matched = [t for t in agent.triggers if t in task_lower]
        if matched:
            return agent, matched

    if always_active_match:
        return always_active_match

    return None, []


def run_benchmark(agents_md_path: Path, verbose: bool = False) -> None:
    console = Console()
    content = agents_md_path.read_text(encoding="utf-8")

    console.print(f"\n[bold cyan]SCA Live Benchmark — Ollama[/bold cyan]")
    console.print(f"File:  [dim]{agents_md_path}[/dim]")
    console.print(f"Model: [dim]{_MODEL}[/dim]")
    console.print(f"Tasks: {len(BENCHMARK_TASKS)}\n")

    # Use XML sections (richer for this AGENTS.md) falling back to ##
    sections = parse_xml_sections(content, source_path=str(agents_md_path))
    if not sections:
        sections = parse_sections(content, source_path=str(agents_md_path))
    if not sections:
        console.print("[red]No sections found — cannot benchmark SCA routing.[/red]")
        sys.exit(1)

    monolithic_system = content

    results_table = Table(title="Live Results", box=box.ROUNDED)
    results_table.add_column("Task", max_width=38, style="white")
    results_table.add_column("Agent", style="cyan", no_wrap=True)
    results_table.add_column("Prompt tok", justify="right")
    results_table.add_column("Completion tok", justify="right")
    results_table.add_column("Latency", justify="right")
    results_table.add_column("Saving", justify="right")

    totals: dict[str, list[int]] = {"mono_pt": [], "sca_pt": []}

    for task in BENCHMARK_TASKS:
        console.print(f"  Running: [italic]{task}[/italic]")

        # --- Monolithic run ---
        mono = chat(monolithic_system, task)
        totals["mono_pt"].append(mono.prompt_tokens)
        results_table.add_row(
            task[:38],
            "[dim]monolithic[/dim]",
            f"{mono.prompt_tokens:,}",
            f"{mono.completion_tokens:,}",
            f"{mono.latency_s:.1f}s",
            "baseline",
        )

        if verbose:
            console.print(f"    [dim]Mono response:[/dim] {mono.response_text[:120]}…")

        # --- SCA run ---
        agent, reasons = pick_section(task, sections)
        if agent is None:
            results_table.add_row(
                "",
                "[yellow]no SCA match[/yellow]",
                "-",
                "-",
                "-",
                "-",
            )
            continue

        sca = chat(agent.system_prompt, task)
        totals["sca_pt"].append(sca.prompt_tokens)
        saving = (1 - sca.prompt_tokens / mono.prompt_tokens) * 100
        reason_str = ", ".join(reasons[:2])

        results_table.add_row(
            "",
            f"[green]{agent.name}[/green] ({reason_str})",
            f"{sca.prompt_tokens:,}",
            f"{sca.completion_tokens:,}",
            f"{sca.latency_s:.1f}s",
            f"[bold green]{saving:.0f}%[/bold green]",
        )

        if verbose:
            console.print(f"    [dim]SCA  response:[/dim] {sca.response_text[:120]}…")

    console.print(results_table)

    # --- Summary ---
    if totals["mono_pt"] and totals["sca_pt"]:
        avg_mono = sum(totals["mono_pt"]) / len(totals["mono_pt"])
        avg_sca = sum(totals["sca_pt"]) / len(totals["sca_pt"])
        overall_saving = (1 - avg_sca / avg_mono) * 100

        summary = Table(title="Summary", box=box.SIMPLE)
        summary.add_column("Metric", style="bold")
        summary.add_column("Value", justify="right")
        summary.add_row("Avg monolithic prompt tokens", f"{avg_mono:,.0f}")
        summary.add_row("Avg SCA prompt tokens", f"{avg_sca:,.0f}")
        summary.add_row(
            "Avg prompt token reduction",
            f"[bold green]{overall_saving:.1f}%[/bold green]",
        )
        summary.add_row(
            "Projected saving at 1000 spawns/day",
            f"{(avg_mono - avg_sca) * 1000:,.0f} tokens/day",
        )
        console.print(summary)

    print("\nEXAMPLE_COST: 0")


if __name__ == "__main__":
    args = sys.argv[1:]
    verbose = "--verbose" in args
    args = [a for a in args if a != "--verbose"]
    path = Path(args[0]) if args else _REPO_ROOT / "AGENTS.md"

    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        sys.exit(1)

    run_benchmark(path, verbose=verbose)
