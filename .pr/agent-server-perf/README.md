# Agent-server lock-starvation / cold-load repro

Runs a real `python -m openhands.agent_server` plus a mock OpenAI-compatible LLM
(`mock_llm.py`), driven over HTTP/WebSocket. `SDK_REPO=<checkout>` picks the tree whose
`.venv` runs the server (default: this repo).

```bash
# deadlock: cpu_count=2 (6-thread default executor), 5 WS clients, 3 s tools, 2 tools/step
.venv/bin/python .pr/agent-server-perf/bench_lock_starvation.py 2 5 3 label 2

# cold load: build a 300-step fixture, then measure /alive during the load (3 reps)
.venv/bin/python .pr/agent-server-perf/bench_cold_load.py build 300 500
.venv/bin/python .pr/agent-server-perf/bench_cold_load.py measure 300 500 3 label
```

`results_*.jsonl` are the runs quoted in the PR (main = 8dc712f, fix12 = this branch).
