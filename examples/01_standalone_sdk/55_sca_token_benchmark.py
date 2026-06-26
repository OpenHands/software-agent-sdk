"""Benchmark: SCA section-split agents vs monolithic agent — static token comparison.

Loads an AGENTS.md file, splits it via parse_sections(), and compares the
system_prompt token count each approach injects per spawned agent.

No LLM calls are made. Token counts use litellm's token_counter (tiktoken
cl100k_base via gpt-4 model — consistent for relative comparison).

Usage:
    uv run python examples/01_standalone_sdk/55_sca_token_benchmark.py
    uv run python examples/01_standalone_sdk/55_sca_token_benchmark.py path/to/AGENTS.md

EXAMPLE_COST: 0
"""

from __future__ import annotations

import sys
from pathlib import Path

import litellm
from rich.console import Console
from rich.table import Table
from rich import box

# Add the repo root to sys.path so we can import from openhands-sdk
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "openhands-sdk"))
sys.path.insert(0, str(_REPO_ROOT / "openhands-tools"))

from openhands.sdk.subagent.section_parser import (
    parse_sections,
    parse_xml_sections,
    ALWAYS_ACTIVE_SENTINEL,
)


_MODEL = "gpt-4"  # cl100k_base tokenizer — consistent across runs, no API call needed


def count_tokens(text: str) -> int:
    """Count tokens using litellm's local tokenizer (no API call)."""
    try:
        return litellm.token_counter(model=_MODEL, text=text)
    except Exception:
        # Fallback: rough approximation (4 chars ≈ 1 token)
        return len(text) // 4


# ---------------------------------------------------------------------------
# Sample task descriptions to simulate routing decisions
# ---------------------------------------------------------------------------
SAMPLE_TASKS = [
    "Fix the failing unit tests in the build pipeline",
    "Set up Docker containers for the development environment",
    "Add a new REST API endpoint that returns JSON",
    "Review the pull request for the authentication module",
    "Improve code simplicity and reduce complexity in the data layer",
    "Update the commit message format in the contributing guide",
    "Debug why the CI pipeline is failing on the test step",
    "Refactor the async LLM completion call chain",
]


def match_triggers(task: str, triggers: list[str]) -> tuple[bool, list[str]]:
    """Return (matched, matching_triggers) for a task against an agent's triggers."""
    if ALWAYS_ACTIVE_SENTINEL in triggers:
        return True, [ALWAYS_ACTIVE_SENTINEL]
    task_lower = task.lower()
    matched = [t for t in triggers if t != ALWAYS_ACTIVE_SENTINEL and t in task_lower]
    return bool(matched), matched


def run_benchmark(agents_md_path: Path, force_xml: bool = False) -> None:
    console = Console()
    content = agents_md_path.read_text(encoding="utf-8")

    console.print(f"\n[bold cyan]SCA Token Benchmark[/bold cyan]")
    console.print(f"File: [dim]{agents_md_path}[/dim]")
    console.print(f"File size: {len(content):,} chars\n")

    # --- Monolithic agent ---
    monolithic_tokens = count_tokens(content)
    if monolithic_tokens == 0:
        console.print("[yellow]File is empty — nothing to benchmark.[/yellow]")
        return

    # --- SCA split: ## headers first, XML tags as fallback (or forced) ---
    sections: list = []
    split_mode = ""
    if not force_xml:
        sections = parse_sections(content, source_path=str(agents_md_path))
        split_mode = "## headers"
    if not sections:
        sections = parse_xml_sections(content, source_path=str(agents_md_path))
        split_mode = "XML tags"

    if not sections:
        console.print(
            "[yellow]No ## headers or XML tag sections found.[/yellow]\n"
            "File would load as a single monolithic agent.\n"
        )
        console.print(f"Monolithic token count: [bold]{monolithic_tokens:,}[/bold]")
        print("EXAMPLE_COST: 0")
        return

    console.print(f"Split mode: [bold]{split_mode}[/bold]")

    # --- Section token table ---
    table = Table(title="Section Agents", box=box.ROUNDED)
    table.add_column("Agent name", style="cyan", no_wrap=True)
    table.add_column("Tokens", justify="right", style="green")
    table.add_column("vs mono", justify="right")
    table.add_column("Always active?", justify="center")
    table.add_column("Triggers (first 5)", style="dim")

    section_tokens = []
    for agent in sections:
        toks = count_tokens(agent.system_prompt)
        section_tokens.append(toks)
        pct = toks / monolithic_tokens * 100
        always = "✓" if ALWAYS_ACTIVE_SENTINEL in agent.triggers else ""
        display_triggers = ", ".join(
            t for t in agent.triggers if t != ALWAYS_ACTIVE_SENTINEL
        )[:60]
        table.add_row(
            agent.name,
            f"{toks:,}",
            f"{pct:.1f}%",
            always,
            display_triggers,
        )

    console.print(table)

    # --- Summary stats ---
    avg_section_tokens = sum(section_tokens) / len(section_tokens)
    min_section_tokens = min(section_tokens)
    max_section_tokens = max(section_tokens)
    savings_pct = (1 - avg_section_tokens / monolithic_tokens) * 100

    summary = Table(title="Token Summary", box=box.SIMPLE)
    summary.add_column("Metric", style="bold")
    summary.add_column("Value", justify="right")
    summary.add_row("Monolithic system_prompt tokens", f"{monolithic_tokens:,}")
    summary.add_row("Sections found", str(len(sections)))
    summary.add_row("Avg section tokens", f"{avg_section_tokens:,.0f}")
    summary.add_row("Min section tokens", f"{min_section_tokens:,}")
    summary.add_row("Max section tokens", f"{max_section_tokens:,}")
    summary.add_row(
        "Avg token reduction per spawn",
        f"[bold green]{savings_pct:.1f}%[/bold green]",
    )
    console.print(summary)

    # --- Routing simulation ---
    route_table = Table(title="Task Routing Simulation", box=box.ROUNDED)
    route_table.add_column("Task", style="white", max_width=45)
    route_table.add_column("Matched agent", style="cyan")
    route_table.add_column("Match reason", style="dim", max_width=30)
    route_table.add_column("Tokens", justify="right", style="green")
    route_table.add_column("Saving", justify="right")

    for task in SAMPLE_TASKS:
        best_agent = None
        best_triggers: list[str] = []

        for agent in sections:
            matched, triggers = match_triggers(task, agent.triggers)
            if matched:
                # Prefer always-active sections last (foundational fallback),
                # specific matches first
                if ALWAYS_ACTIVE_SENTINEL not in triggers:
                    best_agent = agent
                    best_triggers = triggers
                    break
                elif best_agent is None:
                    best_agent = agent
                    best_triggers = triggers

        if best_agent is None:
            route_table.add_row(
                task[:45],
                "[dim]no match → general-purpose[/dim]",
                "",
                f"{monolithic_tokens:,}",
                "0%",
            )
        else:
            toks = count_tokens(best_agent.system_prompt)
            saving = (1 - toks / monolithic_tokens) * 100
            reason = ", ".join(best_triggers[:3])
            route_table.add_row(
                task[:45],
                best_agent.name,
                reason,
                f"{toks:,}",
                f"[green]{saving:.0f}%[/green]",
            )

    console.print(route_table)

    # --- Cost projection ---
    console.print("\n[bold]Cost projection (at 10 spawns/task, 100 tasks/day):[/bold]")
    spawns_per_day = 1000
    mono_daily = monolithic_tokens * spawns_per_day
    sca_daily = avg_section_tokens * spawns_per_day
    console.print(f"  Monolithic:  {mono_daily:>10,} prompt tokens/day")
    console.print(f"  SCA:         {sca_daily:>10,.0f} prompt tokens/day")
    console.print(
        f"  Saved:       {mono_daily - sca_daily:>10,.0f} prompt tokens/day"
        f"  ([bold green]{savings_pct:.1f}% reduction[/bold green])"
    )

    print("\nEXAMPLE_COST: 0")


if __name__ == "__main__":
    args = sys.argv[1:]
    force_xml = "--xml" in args
    args = [a for a in args if a != "--xml"]

    path = Path(args[0]) if args else _REPO_ROOT / "AGENTS.md"

    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        sys.exit(1)

    run_benchmark(path, force_xml=force_xml)
