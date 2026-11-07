# Extended regex in the system grep fallback

Original base: `9c3571a694547734002518bd94b3cb41a187f9b6`.
Updated with upstream `76e9e25078ed0ff7970f2c75e451274d3ed32bf2` on 2026-09-13.
Local validation uses macOS, Python 3.13.13, and system grep.

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

Latest local result: **86 passed**. The original three new cases failed before
the production fix. They execute actual system grep, including a tool call routed
through the SDK terminal compatibility fallback. The added empty-pattern case
verifies that reading the pattern from stdin still matches a nonempty file.
Existing warnings concern LiteLLM cost calculation for the test model.

## Windows CI follow-up

The original [Windows job](https://github.com/OpenHands/software-agent-sdk/actions/runs/34676600523/job/103610803533)
matched `foobar` for `^foo\\(bar\\)\\+$`, losing the literal punctuation escapes.
Git for Windows' MSYS argument processing can remove those backslashes when
native Python launches grep. Reading the pattern with `-f -` bypasses that parsing.
Binary stdin also avoids Python's Windows LF-to-CRLF translation. A final LF
preserves grep's empty-pattern semantics; returned paths use filesystem decoding.

An independent [native Windows probe](https://github.com/BORAN002/software-agent-sdk/actions/runs/34731738940)
reproduced the original wrong-file result and demonstrated that text-mode stdin
also fails. The [binary-input validation run](https://github.com/BORAN002/software-agent-sdk/actions/runs/34731853726)
checks the corrected transport with escaped punctuation, ERE operators, empty
patterns, and trailing newlines, then runs the upstream Windows tools test group.
The validation workflow lives only on the fork's `ci/grep-regex-windows` branch.

## Checks and limits

Full `uv run pre-commit run --all-files --show-diff-on-failure`: **passed**,
including Ruff format/lint, PEP8, Pyright, dynamic attributes, import rules, and
tool registration. The original four dynamic-attribute failures were in the
base and were fixed upstream in #4968; merging that upstream commit resolves
them without adding exceptions to the checker.

`uv run python scripts/check_forbidden_dynamic_attributes.py --baseline-ref origin/main`:
**passed**. Checker, stream-context, and telemetry regression suites: **83 passed**.

The copied reproduction also passed its applicable Python pre-commit checks.
This corrects common regex syntax; it does not make every Python, ripgrep, and
POSIX grep regex feature identical. Agent benchmarks/evaluations were not run.
No public API, schema, dependency, prompt, or tool-description change.
