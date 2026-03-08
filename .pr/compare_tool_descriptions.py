from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def diff_strings(a: str, b: str) -> str:
    import difflib

    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)
    diff = difflib.unified_diff(
        a_lines,
        b_lines,
        fromfile="main",
        tofile="pr",
        lineterm="",
    )
    return "".join(diff).strip()


def compare_values(path: str, a: Any, b: Any, diffs: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        keys = sorted(set(a.keys()) | set(b.keys()))
        for key in keys:
            compare_values(
                f"{path}.{key}" if path else key, a.get(key), b.get(key), diffs
            )
        return

    if isinstance(a, str) and isinstance(b, str):
        if a != b:
            diffs.append(
                f"### {path}\n\n" + "```diff\n" + diff_strings(a, b) + "\n```\n"
            )
        return

    if a != b:
        diffs.append(f"### {path}\n\n" + f"- main: {a!r}\n- pr: {b!r}\n")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    main_path = repo_root / ".pr" / "tool_descriptions_main.json"
    pr_path = repo_root / ".pr" / "tool_descriptions_pr.json"

    main_json = load_json(main_path)
    pr_json = load_json(pr_path)

    diffs: list[str] = []
    compare_values("tools", main_json.get("tools"), pr_json.get("tools"), diffs)

    report_path = repo_root / ".pr" / "tool-description-diff.md"
    if not diffs:
        report_path.write_text(
            "# Tool description comparison\n\n"
            "All checked tool descriptions match exactly between main and PR.\n"
        )
        return

    report = "# Tool description comparison\n\n"
    report += "Differences detected in the following descriptions:\n\n"
    report += "\n".join(diffs)
    report_path.write_text(report)


if __name__ == "__main__":
    main()
