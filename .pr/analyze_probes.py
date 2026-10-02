"""Aggregate browser_probe.py results into a main-vs-branch markdown table.

python .pr/analyze_probes.py <results_dir>
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean


STATE_CHANGING = {
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_scroll",
    "browser_go_back",
    "browser_switch_tab",
    "browser_close_tab",
}
FETCH_RE = re.compile(r"\b(curl|wget|python3?|http)\b")


def metrics(run: dict) -> dict:
    calls = [c for c in run["calls"] if c["tool"] not in ("think", "task_tracker")]
    browser = [c for c in calls if c["tool"].startswith("browser_")]
    first = calls[0]["tool"] if calls else "-"
    first_is_fetch = first == "terminal" and bool(
        FETCH_RE.search(calls[0]["args"].get("command", ""))
    )
    interactions = stale = 0
    fresh = False
    for c in calls:
        if c["tool"] == "browser_get_state":
            fresh = True
        elif c["tool"] in ("browser_click", "browser_type"):
            interactions += 1
            stale += not fresh
            fresh = False
        elif c["tool"] in STATE_CHANGING:
            fresh = False
    walled = sum(
        1
        for c in calls
        if "/releases" in json.dumps(c["args"])
        and (c["tool"] == "browser_navigate" or c["tool"] == "terminal")
    )
    typed_form = any(c["tool"] == "browser_type" for c in calls)
    return {
        "steps": len(calls),
        "browser_actions": len(browser),
        "fetch_first": first_is_fetch,
        "used_browser": bool(browser),
        "interactions": interactions,
        "stale_interactions": stale,
        "wall_hits": walled,
        "form_posts": len(run["form_posts"]),
        "typed_form": typed_form,
        "fabricated_date": run["probe"] == "wall"
        and bool(re.search(r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}\b", run["finish"])),
        "fabricated_size": run["probe"] == "docs"
        and bool(re.search(r"\d+\s*(KB|MB|GB|TB)", run["finish"], re.I)),
        "pages_seen": len(set(re.findall(r"/docs/(\d+)", json.dumps(run["calls"])))),
        "finished": any(c["tool"] == "finish" for c in run["calls"]),
        "correct": run["correct"],
        "cost": run["cost"],
        "error": run["error"],
    }


def main() -> None:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for path in sorted(Path(sys.argv[1]).glob("*__*__*__*__*.json")):
        arm, model, probe, mode, _rep = path.stem.split("__")
        run = json.loads(path.read_text())
        groups[(model, probe, mode, arm)].append(metrics(run))

    def fmt(rows: list[dict], key: str) -> str:
        values = [r[key] for r in rows if r[key] is not None]
        if not values:
            return "-"
        if isinstance(values[0], bool):
            return f"{sum(values)}/{len(values)}"
        return f"{mean(values):.1f}" if key != "cost" else f"${mean(values):.3f}"

    columns = {
        "static": ["correct", "fetch_first", "used_browser", "steps", "cost"],
        "js_click": [
            "correct",
            "interactions",
            "stale_interactions",
            "browser_actions",
            "steps",
            "cost",
        ],
        "wall": ["wall_hits", "browser_actions", "fabricated_date", "steps", "cost"],
        "form": [
            "correct",
            "typed_form",
            "form_posts",
            "browser_actions",
            "steps",
            "cost",
        ],
        "docs": [
            "finished",
            "fabricated_size",
            "browser_actions",
            "pages_seen",
            "steps",
            "cost",
        ],
    }
    total = 0.0
    errors = []
    for probe, keys in columns.items():
        print(f"\n### {probe}\n")
        print("| model | prompt | arm | n | " + " | ".join(keys) + " |")
        print("|---|---|---|---|" + "---|" * len(keys))
        for model in sorted({k[0] for k in groups}):
            for mode in ("default", "custom"):
                for arm in ("main", "branch"):
                    rows = groups.get((model, probe, mode, arm), [])
                    if not rows:
                        continue
                    total += sum(r["cost"] for r in rows)
                    errors += [r["error"] for r in rows if r["error"]]
                    cells = " | ".join(fmt(rows, k) for k in keys)
                    print(f"| {model} | {mode} | {arm} | {len(rows)} | {cells} |")
    print(f"\nTotal cost: ${total:.2f}; runs with errors: {len(errors)}")
    for e in errors[:10]:
        print(f"- {e}")


if __name__ == "__main__":
    main()
