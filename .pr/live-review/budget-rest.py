"""Run focused real-server budget verification through control-agent-server."""

import json
import subprocess
import sys
import uuid
from pathlib import Path

run = Path(sys.argv[1])
cli = Path(".agents/skills/verify-agent-server/scripts/control-agent-server").resolve()
summary = {}


def metric_values(stats):
    return {
        key: {
            "cost": value["accumulated_cost"],
            "tokens": value["accumulated_token_usage"],
        }
        for key, value in stats["usage_to_metrics"].items()
    }


def command(*args):
    proc = subprocess.run(
        [str(cli), *args, "--run", str(run)], text=True, capture_output=True
    )
    if proc.returncode:
        raise RuntimeError(f"{args}: {proc.stdout} {proc.stderr}")
    return proc.stdout


def api(method, path, name, body=None, expect="200"):
    args = ["api", method, path, "--expect", expect, "--save", name]
    if body is not None:
        args += ["--json", json.dumps(body)]
    return json.loads(command(*args))["response"]["body"]


def evidence(feature, entry, expected, actual, artifact):
    command(
        "evidence",
        "add",
        "--feature",
        feature,
        "--result",
        "pass",
        "--entry",
        entry,
        "--expected",
        expected,
        "--actual",
        actual,
        "--artifact",
        artifact,
        "--allow-unknown",
    )


base = {
    "workspace": {"working_dir": str(run / "fixtures")},
    "agent": {"llm": {"model": "deepseek/deepseek-flash"}, "tools": []},
    "autotitle": False,
}
for name, value in [
    ("zero", 0),
    ("negative", -1),
    ("nonfinite", "Infinity"),
    ("nan", "NaN"),
]:
    response = api(
        "POST",
        "/api/conversations",
        f"F04.budget/{name}",
        {**base, "max_budget_per_run": value},
        expect="422",
    )
    assert any("max_budget_per_run" in item["loc"] for item in response["detail"])
for name, extra in [("null", {"max_budget_per_run": None}), ("omitted", {})]:
    response = api(
        "POST",
        "/api/conversations",
        f"F04.budget/{name}",
        {**base, **extra},
        expect="201",
    )
    assert response["max_budget_per_run"] is None
    summary[name] = response["id"]
evidence(
    "F04.budget-validation",
    "POST /api/conversations",
    "Positive finite cap; null/omission unbounded",
    "0, -1, Infinity and NaN rejected422; null and omission returned null201",
    "evidence/F04.budget",
)

for label, cap, prompt, expected in [
    (
        "under",
        0.05,
        "Call finish immediately with message BUDGET_UNDER_OK. Do nothing else.",
        "finished",
    ),
    (
        "over",
        0.00000001,
        'First call think with thought "budget probe". Then in a separate later response call finish with message BUDGET_OVER_DONE. Do not call finish in the first response.',
        "error",
    ),
]:
    cid = command(
        "conversation",
        "start",
        "--prompt",
        prompt,
        "--tools",
        "none",
        "--no-autotitle",
        "--body-json",
        json.dumps({"max_budget_per_run": cap, "max_iterations": 4}),
        "--llm-json",
        '{"max_output_tokens":128,"num_retries":0}',
        "--no-run",
        "--print-id",
        "--save",
        f"F04.budget/{label}-create",
    ).strip()
    capture = label + "-" + uuid.uuid4().hex[:8]
    command(
        "ws", "start", f"/sockets/events/{cid}", "--name", capture, "--duration", "600"
    )
    api("POST", f"/api/conversations/{cid}/run", f"F05.budget/{label}-run")
    waited = json.loads(
        command(
            "conversation", "wait", cid, "--until", "finished,error", "--timeout", "60"
        )
    )
    assert waited["status"] == expected, waited
    info = api("GET", f"/api/conversations/{cid}", f"F05.budget/{label}-stats")
    assert info["max_budget_per_run"] == cap
    cost = sum(
        m["accumulated_cost"] for m in info["stats"]["usage_to_metrics"].values()
    )
    assert cost > 0
    assert (cost < cap) if label == "under" else (cost > cap)
    command(
        "ws",
        "read",
        capture,
        "--contains",
        "stats",
        "--expect-min",
        "2",
        "--save",
        f"F06.budget/{label}-ws",
    )
    command("ws", "stop", capture)
    meta = json.loads(
        command(
            "state",
            "cat",
            "meta.json",
            "--conversation",
            cid,
            "--check",
            "max_budget_per_run",
            "eq",
            str(cap),
        )
    )
    stored = json.loads(
        command(
            "state",
            "cat",
            "base_state.json",
            "--conversation",
            cid,
            "--max-chars",
            "100000",
        )
    )["content"]
    assert metric_values(stored["stats"]) == metric_values(info["stats"])
    frames = [
        json.loads(line)
        for line in (run / "captures" / f"{capture}.jsonl").read_text().splitlines()
    ]
    stats_updates = []
    for frame in frames:
        value = frame.get("frame")
        if isinstance(value, str):
            value = json.loads(value)
        if isinstance(value, dict) and value.get("key") == "stats":
            stats_updates.append(value["value"])
    assert metric_values(info["stats"]) in [metric_values(s) for s in stats_updates], (
        "REST/persisted stats absent from live WS stats updates"
    )
    events = api(
        "GET", f"/api/conversations/{cid}/events/search", f"F06.budget/{label}-events"
    )
    if label == "over":
        assert "MaxBudgetReached" in json.dumps(events)
    fork = api(
        "POST",
        f"/api/conversations/{cid}/fork",
        f"F10.budget/{label}-fork",
        {"title": f"Budget {label} fork"},
        expect="201",
    )
    assert fork["max_budget_per_run"] == cap
    summary[label] = {
        "id": cid,
        "fork": fork["id"],
        "cap": cap,
        "cost": cost,
        "status": expected,
        "live_stats_updates": len(stats_updates),
    }
    evidence(
        "F05.budget-" + label,
        "REST run + real DeepSeek flash",
        expected,
        f"{expected}; cap={cap}; reported cost={cost}",
        f"evidence/F05.budget/{label}-stats.json",
    )
    evidence(
        "F06.budget-" + label,
        "WebSocket stats + REST + base_state.json",
        "Matching cumulative costs",
        f"{len(stats_updates)} live stats updates; REST and persisted cost/token values identical",
        f"evidence/F06.budget/{label}-ws.json",
    )
    evidence(
        "F10.budget-" + label,
        "POST /api/conversations/{id}/fork",
        "Cap retained",
        f"fork cap={cap}",
        f"evidence/F10.budget/{label}-fork.json",
    )

command("restart")
assert json.loads(command("doctor"))["ok"]
for label in ["under", "over"]:
    for which in ["id", "fork"]:
        info = api(
            "GET",
            "/api/conversations/" + summary[label][which],
            f"F31.budget/{label}-{which}-restart",
        )
        assert info["max_budget_per_run"] == summary[label]["cap"]
evidence(
    "F31.budget-restart",
    "Restart + GET original and forks",
    "Cap retained",
    "Both original and both fork caps retained after graceful restart",
    "evidence/F31.budget",
)
Path(".pr/live-review/rest-summary.json").write_text(
    json.dumps(summary, indent=2) + "\n"
)
print(json.dumps(summary, indent=2))
