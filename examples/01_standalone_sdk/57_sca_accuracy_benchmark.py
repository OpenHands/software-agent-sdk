"""Benchmark: SCA vs monolithic — accuracy comparison via Ollama.

For each AGENTS.md section:
  1. Ask Ollama to generate factual questions answerable from that section
  2. Run each question against both monolithic and SCA agents
  3. Use Ollama as judge to score correctness against the section content

No files are created. Cleanup:
    git clean -f examples/01_standalone_sdk/57_sca_accuracy_benchmark.py

Usage:
    uv run python examples/01_standalone_sdk/57_sca_accuracy_benchmark.py
    uv run python examples/01_standalone_sdk/57_sca_accuracy_benchmark.py --verbose
    uv run python examples/01_standalone_sdk/57_sca_accuracy_benchmark.py --questions-per-section 5

EXAMPLE_COST: 0
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

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
_DEFAULT_QUESTIONS_PER_SECTION = 3
_NUM_CTX = 32768  # 128GB RAM — no need to be stingy


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_QUESTION_GEN_USER = """\
I have a documentation section titled "{title}". Here is its content:

---
{content}
---

Write {n} factual quiz questions about this documentation. Each question must be \
answerable from the text above. Output only the numbered questions, nothing else.\
"""

_JUDGE_SYSTEM = """\
You are an accuracy judge for documentation Q&A.
Given a documentation section (ground truth), a question, and a response, \
score the response as CORRECT, PARTIAL, or INCORRECT.

CORRECT   — response accurately addresses the question using information from the section
PARTIAL   — response is on-topic but misses key details or is vague
INCORRECT — response is wrong, contradicts the section, or doesn't address the question

Output ONLY one word: CORRECT, PARTIAL, or INCORRECT.\
"""

_JUDGE_USER = """\
Documentation section (ground truth):
{section}

Question: {question}

Response to judge:
{response}

Score:\
"""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class QuestionResult:
    question: str
    section_name: str
    mono_response: str
    sca_response: str
    mono_score: str   # CORRECT / PARTIAL / INCORRECT
    sca_score: str
    mono_tokens: int
    sca_tokens: int


@dataclass
class SectionResults:
    agent_name: str
    title: str
    questions: list[QuestionResult] = field(default_factory=list)

    def mono_accuracy(self) -> float:
        return _score_pct([q.mono_score for q in self.questions])

    def sca_accuracy(self) -> float:
        return _score_pct([q.sca_score for q in self.questions])


def _score_pct(scores: list[str]) -> float:
    if not scores:
        return 0.0
    weights = {"CORRECT": 1.0, "PARTIAL": 0.5, "INCORRECT": 0.0}
    return sum(weights.get(s, 0.0) for s in scores) / len(scores) * 100


# ---------------------------------------------------------------------------
# Ollama helpers
# ---------------------------------------------------------------------------

def _complete(system: str, user: str, max_tokens: int = 512) -> tuple[str, int]:
    """Returns (response_text, prompt_tokens)."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    resp = litellm.completion(
        model=_MODEL,
        api_base=_OLLAMA_BASE,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.1,
        num_ctx=_NUM_CTX,
        extra_body={"think": False},
    )
    text = resp.choices[0].message.content or ""
    return text.strip(), resp.usage.prompt_tokens


def generate_questions(title: str, content: str, n: int) -> list[str]:
    # No system prompt — gemma4 silences itself when system+content conflict.
    # 4096 max_tokens: gemma4:26b burns thinking tokens first, needs headroom.
    text, _ = _complete(
        "",
        _QUESTION_GEN_USER.format(title=title, content=content, n=n),
        max_tokens=4096,
    )
    questions = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip leading numbering "1." "1)" or bullets "-" "*"
        cleaned = re.sub(r"^[\d]+[.)]\s*|^[-*]\s*", "", line).strip()
        # Accept any non-trivial line — model may omit "?" in some outputs
        if len(cleaned) > 15:
            questions.append(cleaned)
    return questions[:n]


_JUDGE_USER_ONLY = """\
I need you to judge a documentation Q&A answer.

Documentation (ground truth):
---
{section}
---

Question: {question}

Answer to judge:
{response}

Based only on the documentation above, is this answer CORRECT, PARTIAL, or INCORRECT?
Output one word only: CORRECT, PARTIAL, or INCORRECT.\
"""


def judge(section_content: str, question: str, response: str) -> str:
    # No system prompt + 2048 tokens for gemma4 thinking headroom; parse last word
    text, _ = _complete(
        "",
        _JUDGE_USER_ONLY.format(
            section=section_content[:2000],
            question=question,
            response=response,
        ),
        max_tokens=2048,
    )
    # Model may think aloud then output verdict; take the last capitalised word
    words = [w.strip(".,;:") for w in text.upper().split() if w.strip(".,;:")]
    for word in reversed(words):
        if word in ("CORRECT", "PARTIAL", "INCORRECT"):
            return word
    return "INCORRECT"


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

def run_benchmark(
    agents_md_path: Path,
    questions_per_section: int = _DEFAULT_QUESTIONS_PER_SECTION,
    verbose: bool = False,
) -> None:
    console = Console()
    content = agents_md_path.read_text(encoding="utf-8")
    monolithic_system = content

    console.print(f"\n[bold cyan]SCA Accuracy Benchmark — Ollama[/bold cyan]")
    console.print(f"File:  [dim]{agents_md_path}[/dim]")
    console.print(f"Model: [dim]{_MODEL}[/dim]")
    console.print(f"Questions per section: {questions_per_section}\n")

    sections = parse_xml_sections(content, source_path=str(agents_md_path))
    if not sections:
        sections = parse_sections(content, source_path=str(agents_md_path))
    if not sections:
        console.print("[red]No sections found.[/red]")
        sys.exit(1)

    console.print(f"Sections to benchmark: {len(sections)}")
    calls_estimate = len(sections) * (1 + questions_per_section * 3)
    console.print(f"Estimated Ollama calls: ~{calls_estimate} (gen + 2 answers + judge per Q)\n")

    all_section_results: list[SectionResults] = []

    for agent in sections:
        title = agent.description
        section_content = agent.system_prompt
        console.rule(f"[cyan]{agent.name}[/cyan]")

        # 1. Generate questions from this section
        console.print(f"  Generating {questions_per_section} questions…", end=" ")
        t0 = time.perf_counter()
        questions = generate_questions(title, section_content, questions_per_section)
        console.print(f"done ({time.perf_counter()-t0:.1f}s, {len(questions)} Qs)")

        if not questions:
            console.print("  [yellow]No questions generated, skipping.[/yellow]")
            continue

        sr = SectionResults(agent_name=agent.name, title=title)

        for qi, question in enumerate(questions, 1):
            if verbose:
                console.print(f"\n  Q{qi}: [italic]{question}[/italic]")
            else:
                console.print(f"  Q{qi}/{len(questions)}: {question[:70]}…" if len(question) > 70 else f"  Q{qi}/{len(questions)}: {question}")

            # 2a. Monolithic answer
            mono_resp, mono_tokens = _complete(monolithic_system, question, max_tokens=4096)
            # 2b. SCA answer
            sca_resp, sca_tokens = _complete(section_content, question, max_tokens=4096)

            # 3. Judge both
            mono_verdict = judge(section_content, question, mono_resp)
            sca_verdict = judge(section_content, question, sca_resp)

            if verbose:
                console.print(f"     Mono [{mono_verdict}]: {mono_resp[:100]}…")
                console.print(f"     SCA  [{sca_verdict}]: {sca_resp[:100]}…")
            else:
                console.print(f"     Mono: [{'green' if mono_verdict=='CORRECT' else 'yellow' if mono_verdict=='PARTIAL' else 'red'}]{mono_verdict}[/] | SCA: [{'green' if sca_verdict=='CORRECT' else 'yellow' if sca_verdict=='PARTIAL' else 'red'}]{sca_verdict}[/]   (tokens: {mono_tokens:,} → {sca_tokens:,})")

            sr.questions.append(QuestionResult(
                question=question,
                section_name=agent.name,
                mono_response=mono_resp,
                sca_response=sca_resp,
                mono_score=mono_verdict,
                sca_score=sca_verdict,
                mono_tokens=mono_tokens,
                sca_tokens=sca_tokens,
            ))

        all_section_results.append(sr)

    # ---------------------------------------------------------------------------
    # Summary table
    # ---------------------------------------------------------------------------
    console.rule("[bold]Results[/bold]")
    table = Table(title="Accuracy by Section", box=box.ROUNDED)
    table.add_column("Section", style="cyan", no_wrap=True)
    table.add_column("Qs", justify="right")
    table.add_column("Mono accuracy", justify="right")
    table.add_column("SCA accuracy", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Avg token saving", justify="right")

    total_mono_scores: list[str] = []
    total_sca_scores: list[str] = []
    total_mono_tokens: list[int] = []
    total_sca_tokens: list[int] = []

    for sr in all_section_results:
        mono_acc = sr.mono_accuracy()
        sca_acc = sr.sca_accuracy()
        delta = sca_acc - mono_acc
        delta_str = f"[green]+{delta:.0f}%[/green]" if delta >= 0 else f"[red]{delta:.0f}%[/red]"

        avg_mono_t = sum(q.mono_tokens for q in sr.questions) / max(len(sr.questions), 1)
        avg_sca_t = sum(q.sca_tokens for q in sr.questions) / max(len(sr.questions), 1)
        saving = (1 - avg_sca_t / avg_mono_t) * 100 if avg_mono_t else 0

        table.add_row(
            sr.agent_name,
            str(len(sr.questions)),
            f"{mono_acc:.0f}%",
            f"{sca_acc:.0f}%",
            delta_str,
            f"[green]{saving:.0f}%[/green]",
        )

        total_mono_scores.extend(q.mono_score for q in sr.questions)
        total_sca_scores.extend(q.sca_score for q in sr.questions)
        total_mono_tokens.extend(q.mono_tokens for q in sr.questions)
        total_sca_tokens.extend(q.sca_tokens for q in sr.questions)

    console.print(table)

    if total_mono_scores:
        overall_mono = _score_pct(total_mono_scores)
        overall_sca = _score_pct(total_sca_scores)
        delta = overall_sca - overall_mono
        avg_mono_t = sum(total_mono_tokens) / len(total_mono_tokens)
        avg_sca_t = sum(total_sca_tokens) / len(total_sca_tokens)
        token_saving = (1 - avg_sca_t / avg_mono_t) * 100

        summary = Table(title="Overall", box=box.SIMPLE)
        summary.add_column("Metric", style="bold")
        summary.add_column("Value", justify="right")
        summary.add_row("Total questions", str(len(total_mono_scores)))
        summary.add_row("Monolithic accuracy", f"{overall_mono:.1f}%")
        summary.add_row("SCA accuracy", f"{overall_sca:.1f}%")
        delta_color = "green" if delta >= 0 else "red"
        summary.add_row(
            "Accuracy delta (SCA − mono)",
            f"[{delta_color}]{delta:+.1f}%[/{delta_color}]",
        )
        summary.add_row("Avg prompt token reduction", f"[bold green]{token_saving:.1f}%[/bold green]")
        console.print(summary)

    print("\nEXAMPLE_COST: 0")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SCA accuracy benchmark via Ollama")
    parser.add_argument("agents_md", nargs="?", type=Path, default=_REPO_ROOT / "AGENTS.md")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--questions-per-section", type=int, default=_DEFAULT_QUESTIONS_PER_SECTION)
    args = parser.parse_args()

    if not args.agents_md.exists():
        print(f"Error: {args.agents_md} not found", file=sys.stderr)
        sys.exit(1)

    run_benchmark(args.agents_md, args.questions_per_section, args.verbose)
