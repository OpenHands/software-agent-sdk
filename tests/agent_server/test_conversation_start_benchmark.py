from scripts.agent_server_benchmarks.conversation_start import render_report


def _run(cold: bool, first_agent_event_s: float) -> dict:
    return {
        "startup_s": 0.1,
        "creates": [
            {
                "cold": cold,
                "create_s": 0.1,
                "first_agent_event_s": first_agent_event_s,
                "max_alive_s": 0.001,
            }
        ],
    }


def test_report_includes_first_agent_event_for_each_scenario() -> None:
    runs = {
        "default": {
            "base": [_run(False, 0.2), _run(True, 0.3)],
            "head": [_run(False, 0.2), _run(True, 0.3)],
        },
        "pause": {"base": [_run(True, 3.2)], "head": [_run(True, 3.2)]},
        "worktree": {"base": [_run(False, 0.7)], "head": [_run(False, 0.7)]},
        "mcp": {"base": [_run(False, 3.2)], "head": [_run(False, 3.2)]},
    }

    report = render_report(
        runs,
        {
            "base_ref": "base",
            "head_ref": "head",
            "rounds": 1,
            "runner": "test",
            "env_vars": 0,
        },
    )

    assert "First agent event right after server start" in report
    assert "First agent event 3 s after server start" in report
    assert "New worktree create → first agent event" in report
    assert "MCP create → first agent event" in report
    assert "receipt; they do not measure browser rendering" in report
