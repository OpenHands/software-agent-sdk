# Real Agent validation for #4984

**Status: not run against a real model.** The contributor has no cloud LLM API
available. A maintainer with a configured service needs to execute the two runs
below and attach the resulting evidence. Preparation and object-construction
checks do not count as live Agent or benchmark validation.

This script exercises the real `Agent` / `LocalConversation` / `GrepTool` path.
It does **not** exercise the terminal-command compatibility fallback. Existing
Windows evidence covers the lower-level transport separately. Any separately
requested benchmark and human maintainer review also remain separate.

## Run the same conversation before and after the fix

Use the same machine, model/service, fixture path, and script for both runs. The
merge-base is `76e9e25078ed0ff7970f2c75e451274d3ed32bf2`; the reviewed fix is
`2a5d21633050ea05b54fe14f9241b2c6917e571b`. Running the latest PR head is also
appropriate: `environment.json` records the actual checked-out commit and the
grep implementation hash. Avoid unrelated local source modifications.

Configure `LLM_MODEL`, `LLM_API_KEY`, and, if needed, `LLM_BASE_URL` securely in the
maintainer's environment. Do not put credentials in commands, logs, or artifacts.
The model must support tool calling. Each conversation permits at most five
iterations and sets a $1 SDK cost budget; provider-reported pricing determines
cost accounting, so this is not a guaranteed provider billing cap.

From the PR checkout, in a POSIX shell:

```sh
GREP_PR_CHECKOUT="$(pwd)"
GREP_BASE_CHECKOUT="$(dirname "$GREP_PR_CHECKOUT")/software-agent-sdk-grep-live-base"
GREP_EVIDENCE="$(mktemp -d "${TMPDIR:-/tmp}/openhands-grep-live.XXXXXX")"
GREP_FIXTURES="$GREP_EVIDENCE/fixtures"

git worktree add --detach "$GREP_BASE_CHECKOUT" 76e9e25078ed0ff7970f2c75e451274d3ed32bf2

cd "$GREP_BASE_CHECKOUT"
uv sync --frozen --dev
"$GREP_BASE_CHECKOUT/.venv/bin/python" "$GREP_PR_CHECKOUT/.pr/repro_live_grep_agent.py" \
  --expect base --workspace "$GREP_FIXTURES" --output "$GREP_EVIDENCE/base"

# Keep the first fixtures as evidence, freeing the same absolute fixture path.
mv "$GREP_FIXTURES" "$GREP_EVIDENCE/base-fixtures"

cd "$GREP_PR_CHECKOUT"
uv sync --frozen --dev
"$GREP_PR_CHECKOUT/.venv/bin/python" "$GREP_PR_CHECKOUT/.pr/repro_live_grep_agent.py" \
  --expect head --workspace "$GREP_FIXTURES" --output "$GREP_EVIDENCE/head"
```

The script is always loaded by its absolute path from the PR checkout; each run
uses its own checkout's interpreter and working directory. It rejects imports of
`Agent`, `LocalConversation`, or `GrepExecutor` from another checkout. Do not set
`PYTHONPATH` or reuse the other worktree's virtual environment.

`PATH` is temporarily limited to a directory containing only a symlink to the
host's `grep`, so `rg` cannot be found. The original path is restored afterward.
Both cases use the identical prompt and real fixture contents; no model responses
or tool results are mocked. Exactly one grep call per requested pattern is
required, with the specified absolute directory, `*.txt` include, a successful
observation, and the expected full matching paths. Retries or altered patterns
fail validation. Review any nonzero exit before treating the run as evidence.

| Pattern | Expected base result | Expected fixed result |
| --- | --- | --- |
| `^(foo\|bar)+[0-9]{2}$` | No matches | `matching.txt` |
| `^foo\(bar\)\+$` | Incorrectly matches `other.txt` | `matching.txt` |

Attach each run's `environment.json`, `events.jsonl`, and `summary.json` to the
PR after checking them for private data. Events contain the actual model tool
calls, LLM response IDs, and the observations returned to the Agent. The summary
must have an empty `validation_errors` list and
`matches_expected_revision_behavior: true` in both runs (base success means the
original defect was reproduced). Review the final Agent response as well.

Use fresh workspace and output directories for every attempt. The optional
`--prepare-only` flag checks fixture creation, checkout imports, and PATH isolation
without constructing a model or running a conversation; it produces no live
validation evidence and cannot share directories with a later live run.
