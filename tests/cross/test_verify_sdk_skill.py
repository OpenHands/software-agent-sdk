"""Tests for the verify-sdk skill's sdk-map tool and the committed SDK map.

The tool lives at `.agents/skills/verify-sdk/scripts/sdk_map.py`. These tests
run it against throwaway maps and validate the committed map's structure and
cited paths, without running any recipe from the real map.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".agents/skills/verify-sdk/scripts/sdk_map.py"


def _load():
    spec = importlib.util.spec_from_file_location("sdk_map", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["sdk_map"] = module
    spec.loader.exec_module(module)
    return module


sdk_map = _load()

FAMILY = """# Demo family

What a program sees.

Source: `openhands-sdk/openhands/sdk/event/`

Examples: none

## Sub-features

- `S98.ok`: works. Unit: `tests/cross/test_verify_sdk_skill.py`.
- `S98.broken`: fails. Unit: **none (untested)**. Example: none. Real-LLM: none.
- `S98.model`: needs a model. Example: `examples/01_standalone_sdk/01_hello_world.py`.
- `S98.gated`: needs Docker. Unit: none. Example: none. Real-LLM: none.

## How to get to it (agent POV)

- Call it.

## Driving it

Preconditions:
- none.

- **Works (`S98.ok`).**
  ```sh
  test 1 = 1
  ```
- **Fails (`S98.broken`).**
  ```sh
  echo before
  false
  ```
- **Model (`S98.model`).**
  ```sh
  uv run python examples/01_standalone_sdk/01_hello_world.py
  ```
- **Docker (`S98.gated`), blocked here.**
  ```sh
  docker ps
  ```

## Gotchas

- none.
"""


def _index(baseline: list[str], rows: str) -> str:
    return (
        "# Map\n\n"
        + "\n".join(baseline)
        + "\n\nThe map has **4 sub-features**, **1 of them untested**.\n\n"
        + "## Families\n\n"
        + rows
        + "\n"
    )


@pytest.fixture
def demo_map(tmp_path, monkeypatch):
    feature_map = tmp_path / "map"
    feature_map.mkdir()
    (feature_map / "S98-demo.md").write_text(FAMILY)
    (feature_map / "README.md").write_text(
        _index(
            ["Maintenance baseline: main@abc1234 (2026-10-01). Keep me."],
            "| S98 | [Demo](S98-demo.md) | x | x | none | 4 | 1 |",
        )
    )
    monkeypatch.setattr(sdk_map, "FEATURE_MAP_DIR", feature_map)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    return feature_map


def _run(capsys, *argv):
    code = sdk_map.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_committed_map_is_valid(capsys):
    """Structure, IDs, counts, baseline line, and every cited path exists."""
    code, out = _run(capsys, "check")
    assert code == 0, out["problems"]


def test_check_reports_dead_paths_counts_and_undriven_ids(demo_map, capsys):
    family = demo_map / "S98-demo.md"
    family.write_text(
        FAMILY.replace("test_verify_sdk_skill.py`", "test_gone.py`").replace(
            "- **Works (`S98.ok`).**", "- **Works.**"
        )
    )
    code, out = _run(capsys, "check")
    assert code == 1
    problems = out["problems"]["S98-demo.md"]
    assert any("tests/cross/test_gone.py does not exist" in p for p in problems)
    assert "S98.ok has no `Driving it` bullet" in problems

    family.write_text(
        FAMILY.replace(
            "- `S98.gated`:",
            "- `S98.extra`: new. Unit: **none (untested)**.\n- `S98.gated`:",
        ).replace("- **Works (`S98.ok`).**", "- **Works (`S98.ok`, `S98.extra`).**")
    )
    code, out = _run(capsys, "check", "--fix-counts")
    assert out["ok"], out["problems"]
    text = (demo_map / "README.md").read_text()
    assert "| 5 | 2 |" in text
    assert "**5 sub-features**, **2 of them untested**" in text


def test_run_records_pass_fail_blocked_and_skips_model_blocks(
    demo_map, tmp_path, capsys
):
    out_dir = tmp_path / "run"
    code, out = _run(capsys, "run", "--file", "S98", "--out", str(out_dir))
    assert code == 1
    results = {r["ids"][0]: r for r in out["results"]}
    assert results["S98.ok"]["result"] == "pass"
    assert results["S98.broken"]["result"] == "fail"
    assert "before" in Path(results["S98.broken"]["transcript"]).read_text()
    assert results["S98.model"]["result"] == "blocked"
    assert "no DEEPSEEK_API_KEY" in results["S98.model"]["note"]
    assert results["S98.gated"]["result"] == "blocked"
    ledger = [
        json.loads(line) for line in (out_dir / "ledger.jsonl").read_text().splitlines()
    ]
    assert {r["feature"] for r in ledger} == set(results)

    # A feature proven by one bullet and blocked in another counts as proven.
    family = demo_map / "S98-demo.md"
    family.write_text(
        FAMILY.replace(
            "- **Model (`S98.model`).**", "- **Model (`S98.model`, `S98.ok`).**"
        )
    )
    code, out = _run(capsys, "run", "--file", "S98", "--out", str(tmp_path / "two"))
    rows = {
        r["feature"]: r
        for r in map(
            json.loads, (tmp_path / "two/ledger.jsonl").read_text().splitlines()
        )
    }
    assert rows["S98.ok"]["result"] == "pass"
    assert "bullet 3: blocked" in rows["S98.ok"]["note"]
    family.write_text(FAMILY)

    code, out = _run(capsys, "run", "--file", "S98", "--only", "S98.ok", "--dry-run")
    assert [r["ids"] for r in out["results"]] == [["S98.ok"]]
    code, out = _run(capsys, "run", "--file", "S98", "--only", "S98.nope")
    assert code == 2


def test_report_compares_with_a_baseline_run(demo_map, tmp_path, capsys):
    before, after = tmp_path / "before", tmp_path / "after"
    for path, rows in [
        (before, {"S98.ok": "pass", "S98.broken": "pass", "S98.gated": "blocked"}),
        (after, {"S98.ok": "pass", "S98.broken": "fail", "S98.model": "pass"}),
    ]:
        path.mkdir()
        (path / "ledger.jsonl").write_text(
            "".join(
                json.dumps({"feature": f, "result": r}) + "\n" for f, r in rows.items()
            )
        )
    code, out = _run(
        capsys,
        "report",
        "--runs",
        str(after),
        "--baseline",
        str(before),
        "--print-markdown",
    )
    assert code == 0
    assert [c["feature"] for c in out["changes"]["newly_failing"]] == ["S98.broken"]
    assert [c["feature"] for c in out["changes"]["not_checked"]] == ["S98.gated"]
    assert [c["feature"] for c in out["changes"]["other"]] == ["S98.model"]
    assert out["markdown"].index("S98.broken") < out["markdown"].index("S98.ok")


def test_baseline_reads_and_moves_the_line(demo_map, capsys):
    head = sdk_map.git("rev-parse", "HEAD")
    code, out = _run(capsys, "baseline")
    assert out["sha"] == "abc1234"
    code, out = _run(
        capsys, "baseline", "--set", head, "--date", "2026-10-12", "--main", "HEAD"
    )
    assert code == 0, out
    text = (demo_map / "README.md").read_text()
    assert f"Maintenance baseline: main@{head} (2026-10-12). Keep me." in text


def test_baseline_problems_are_reported(demo_map, capsys):
    (demo_map / "README.md").write_text(
        _index(
            [
                "Maintenance baseline: main@abc1234 (2026-10-01).",
                "Maintenance baseline: main@def5678 (2026-10-02).",
            ],
            "| S98 | [Demo](S98-demo.md) | x | x | none | 4 | 1 |",
        )
    )
    code, out = _run(capsys, "baseline")
    assert code == 1
    assert "2 `Maintenance baseline:` lines" in out["problems"][0]


def test_owners_routes_paths_and_flags_unowned_ones(demo_map, capsys):
    code, out = _run(
        capsys,
        "owners",
        "openhands-sdk/openhands/sdk/event/base.py",
        "openhands-sdk/openhands/sdk/utils/redact.py",
        "docs/readme.md",
    )
    assert out["families"] == ["S98-demo.md"]
    assert out["unowned_product_paths"] == [
        "openhands-sdk/openhands/sdk/utils/redact.py"
    ]


def test_statements_split_on_top_level_lines_only():
    block = (
        "uv run pytest tests/a.py -q\n"
        'uv run python -c "\nimport os\nprint(1)\n"\n'
        "LLM_MODEL=x uv run python examples/01_standalone_sdk/01_hello_world.py \\\n"
        "  --flag\n"
        "uv run pytest tests/integration/test_tool_presets.py -q"
    )
    statements = sdk_map.split_statements(block)
    assert len(statements) == 4
    assert [sdk_map.needs_model(st) for st in statements] == [False, False, True, False]
    assert sdk_map.needs_model("uv run pytest tests/examples/test_examples.py")
    assert sdk_map.needs_model("uv run python tests/integration/run_infer.py --x 1")


def _bullet_map(demo_map, block, ids="`S98.ok`"):
    (demo_map / "S98-demo.md").write_text(
        FAMILY.replace(
            "- **Works (`S98.ok`).**\n  ```sh\n  test 1 = 1\n  ```",
            f"- **Works ({ids}).**\n  ```sh\n"
            + "\n".join("  " + line for line in block.splitlines())
            + "\n  ```",
        )
    )


def test_a_block_that_needs_a_model_is_skipped_whole_without_a_key(
    demo_map, tmp_path, capsys
):
    """The block's commands share one run; half of it would prove nothing."""
    _bullet_map(
        demo_map,
        "export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)\n"
        "echo ran-the-scripted-half",
    )
    code, out = _run(
        capsys, "run", "--file", "S98", "--only", "S98.ok", "--out", str(tmp_path / "r")
    )
    (row,) = out["results"]
    assert row["result"] == "blocked"
    assert "1 model-backed block(s) skipped" in row["note"]


def test_a_no_model_block_runs_its_example_without_a_key(demo_map, tmp_path, capsys):
    _bullet_map(demo_map, "# no model\ntest -n examples/ && echo ran-the-example")
    code, out = _run(
        capsys, "run", "--file", "S98", "--only", "S98.ok", "--out", str(tmp_path / "r")
    )
    (row,) = out["results"]
    assert row["result"] == "pass", row


def test_exit_3_means_blocked_not_failed(demo_map, tmp_path, capsys):
    _bullet_map(demo_map, "echo missing key\nexit 3")
    code, out = _run(
        capsys, "run", "--file", "S98", "--only", "S98.ok", "--out", str(tmp_path / "r")
    )
    (row,) = out["results"]
    assert row["result"] == "blocked"
    assert "exit 3" in row["note"]


def test_check_rejects_pytest_and_broken_control_sdk_calls(demo_map, capsys):
    _bullet_map(
        demo_map,
        "uv run pytest tests/sdk -q\n"
        "control-sdk conversation sned x\n"
        "control-sdk program S98/nowhere --check ok eq true\n"
        "control-sdk conversation new --script S98/missing.json --field id\n"
        'control-sdk conversation send "$CID" --text hi --run \\\n'
        "  --check execution_status eq finished",
    )
    code, out = _run(capsys, "check", "--file", "S98")
    assert code == 1
    problems = "\n".join(out["problems"]["S98-demo.md"])
    assert "runs pytest" in problems
    assert "`control-sdk conversation sned x` does not parse" in problems
    assert "no program programs/S98/nowhere.py" in problems
    assert "no script programs/S98/missing.json" in problems
    assert "conversation send" not in problems


FAKE_RUN_INFER = """
import json, os, sys
args = sys.argv[1:]
out = args[args.index("--output-dir") + 1]
ids = args[args.index("--eval-ids") + 1].split(",")
os.makedirs(os.path.join(out, "run"), exist_ok=True)
outcome = os.environ.get("FAKE_OUTCOME", "fail")
instances = [
    {"instance_id": i, "test_result": {
        "success": outcome == "pass", "skipped": outcome == "skip"}}
    for i in ids[: 1 if outcome == "missing" else None]
]
with open(os.path.join(out, "run", "results.json"), "w") as f:
    json.dump({"test_instances": instances}, f)
print("Overall Success rate: 0.00% (0/1)")
"""


@pytest.mark.parametrize(
    "outcome, result",
    [("fail", "fail"), ("missing", "fail"), ("skip", "blocked"), ("pass", "pass")],
)
def test_integration_runs_are_judged_by_their_results_not_exit_code(
    demo_map, tmp_path, monkeypatch, capsys, outcome, result
):
    repo = tmp_path / "repo"
    (repo / "tests/integration").mkdir(parents=True)
    (repo / "tests/integration/run_infer.py").write_text(FAKE_RUN_INFER)
    monkeypatch.setattr(sdk_map, "REPO_ROOT", repo)
    monkeypatch.setenv("FAKE_OUTCOME", outcome)
    _bullet_map(
        demo_map,
        f"{sys.executable} tests/integration/run_infer.py --eval-ids t01_a,t02_b",
    )
    code, out = _run(
        capsys,
        "run",
        "--file",
        "S98",
        "--only",
        "S98.ok",
        "--with-llm",
        "--out",
        str(tmp_path / "r"),
    )
    (row,) = out["results"]
    assert row["result"] == result, row["note"]
    if result == "pass":
        assert row["proof"] == ["integration"]


def test_timeouts_kill_everything_the_recipe_started(demo_map, tmp_path, capsys):
    pidfile = tmp_path / "child.pid"
    _bullet_map(demo_map, f"sleep 60 &\necho $! > {pidfile}\nsleep 60")
    code, out = _run(
        capsys,
        "run",
        "--file",
        "S98",
        "--only",
        "S98.ok",
        "--timeout",
        "1",
        "--out",
        str(tmp_path / "r"),
    )
    (row,) = out["results"]
    assert row["result"] == "fail"
    assert "(timeout)" in row["note"]
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_reports_take_the_newest_attempt_whatever_the_argument_order(
    demo_map, tmp_path, capsys
):
    def ledger(name, result, at):
        path = tmp_path / name
        path.mkdir()
        row = {"feature": "S98.ok", "result": result, "at": at}
        (path / "ledger.jsonl").write_text(json.dumps(row) + "\n")
        return str(path)

    baseline = ledger("baseline", "pass", "2026-10-12T00:00:00+00:00")
    early = ledger("early-pass", "pass", "2026-10-13T01:00:00+00:00")
    late = ledger("late-fail", "fail", "2026-10-13T02:00:00+00:00")
    for runs in ([late, early], [early, late]):
        code, out = _run(
            capsys,
            "report",
            "--runs",
            *runs,
            "--baseline",
            baseline,
            "--print-markdown",
        )
        assert out["counts"] == {"fail": 1}
        assert [c["feature"] for c in out["changes"]["newly_failing"]] == ["S98.ok"]
        assert out["conflicting"][0]["feature"] == "S98.ok"
        assert "## Conflicting attempts" in out["markdown"]


def test_every_command_documents_examples(capsys):
    parser = sdk_map.build_parser()
    for name in ("check", "ids", "owners", "baseline", "run", "report"):
        with pytest.raises(SystemExit):
            parser.parse_args([name, "--help"])
        assert "Examples:" in capsys.readouterr().out, name
