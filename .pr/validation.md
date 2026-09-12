# Extended regex in the system grep fallback

Base: `9c3571a694547734002518bd94b3cb41a187f9b6`. Tested on macOS with Python 3.13.13 and system grep.

## Reproduce

From the repository root after `make build`:

```sh
.venv/bin/python .pr/repro_grep_regex.py
```

The script gives the public `GrepExecutor` / `GrepAction` a temporary PATH with
system grep but no ripgrep. It executes the real binary against temporary files,
checking extended grouping/alternation/repetition and escaped literal punctuation.
No production functions are replaced.

[baseline.log](baseline.log) and [fixed.log](fixed.log) retain the observed JSON
results, omitting warning/banner noise and replacing the temporary grep path with
an availability boolean. Before the fix one pattern missed a matching file and
the other returned the wrong file. After the fix both return `matching.txt`.
The portable `.pr/` script was rerun successfully after preparing these artifacts.
To verify the baseline independently, copy the script into a checkout of the base
commit, set up that environment, and run the same command (expected exit 1).

## Regression coverage

```sh
uv run pytest tests/tools/grep tests/sdk/agent/test_tool_call_compatibility.py -q
```

Recorded during implementation: 85 passed. The three new cases failed before the
production fix. They execute actual system grep, including a tool call routed
through the SDK terminal compatibility fallback. Existing warnings concern
LiteLLM cost calculation for the test model. The full suite was not rerun when
packaging these artifacts.

## Checks and limits

Ruff format, Ruff lint, PEP8 style, Pyright, import rules, and tool registration: passed.
Full pre-commit: failed in check-forbidden-dynamic-attributes on unchanged files:
openhands-sdk/openhands/sdk/agent/stream_context.py:287
openhands-sdk/openhands/sdk/llm/utils/telemetry.py:264
openhands-sdk/openhands/sdk/llm/utils/telemetry.py:270
openhands-sdk/openhands/sdk/llm/utils/telemetry.py:272
The same four violations were reproduced on the pristine base.

The copied reproduction also passed its applicable Python pre-commit checks.
This corrects common regex syntax; it does not make every Python, ripgrep, and
POSIX grep regex feature identical. Agent benchmarks/evaluations were not run.
The production change is two command arguments, with no public API, schema,
dependency, prompt, or tool-description change.
