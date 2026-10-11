# Real Agent comparison for #4984

On 2026-10-05, two real OpenHands `Agent` / `LocalConversation` runs used
DeepSeek's public API (`https://api.deepseek.com`, `deepseek-flash`, thinking
disabled). Both reproduced the expected behavior for their respective revision.

| Case | Base result | PR head result |
| --- | --- | --- |
| Extended regex `^(foo\|bar)+[0-9]{2}$` | No matches: missed the correct file | `extended/matching.txt` |
| Escaped punctuation `^foo\(bar\)\+$` | `escaped/other.txt`: matched the wrong file | `escaped/matching.txt` |

The fixtures contain `FooBAR12` and `foo12 extra` for the extended case, and
`foo(bar)+` and `foobar` for the escaped case. GrepTool searches case-insensitively.
The exact fixture contents and prompt are included in each `environment.json`.

## Environment and isolation

- Base: `76e9e25078ed0ff7970f2c75e451274d3ed32bf2`.
- PR head tested: `486b46fb8c9a0fadb544fbc1854f3a177c1022dc`.
- macOS 15.6, arm64; Python 3.13.13; OpenHands SDK 1.47.0; LiteLLM 1.93.0.
- System grep: `/usr/bin/grep`, `grep (BSD grep, GNU compatible) 2.6.0-FreeBSD`.
- Each checkout had its own virtual environment, installed with
  `uv sync --frozen --dev`; both used the same `uv.lock` and harness hashes.
- The only tracked local change during the head run was the evidence harness
  preserved below. Production source was unchanged from the tested head.
- `PATH` contained only a symlink to `/usr/bin/grep`; `rg` resolved to `null` in
  both runs. The tool's startup log confirms selection of the system-grep backend.

Both runs used the same model, service settings, prompt, fixture contents, and
absolute fixture path. After the base run, its fixture directory was moved aside
and the head run recreated it at the same path. Each conversation had at most
five iterations, a 1,024-token output limit, no LLM retries, a 60-second request
timeout, and the harness's $1 SDK budget (not a guaranteed provider billing cap).

The [frozen harness](repro_live_grep_agent.py) is the exact script used for these
conversations, with SHA-256
`7c7e1ae7a304a6206cda316325829674ba14d9ff2813bde17db07fe88706bdbc`.
This matches `harness_sha256` in both environment files. Later validation
improvements are in [the current harness](../repro_live_grep_agent.py); the
recorded events, summaries, environment files, and historical hashes are unchanged.

## Evidence

| Run | Environment | Actual tool calls and observations | Validation | Runtime log |
| --- | --- | --- | --- | --- |
| Base | [environment.json](base/environment.json) | [events.jsonl](base/events.jsonl) | [summary.json](base/summary.json) | [stderr.log](base/stderr.log) |
| Head | [environment.json](head/environment.json) | [events.jsonl](head/events.jsonl) | [summary.json](head/summary.json) | [stderr.log](head/stderr.log) |

Each conversation made exactly two `grep` calls with the original requested
patterns and `*.txt` include, received both observations, then made a `finish`
call in a subsequent model response. Both finished normally, with no validation
errors, conversation error events, or logged system-grep failures/Python fallback.
The final messages accurately reported the returned filenames. No model
responses or tool observations were mocked.

The saved base/head streams also pass the current validator's stricter checks
for errors, extra calls, call/observation pairing, and a finish in a later model
response after both grep observations. This is an offline recheck of the same
two conversations. The [offline tests](../test_live_grep_event_sequence.py) cover
both recorded streams and invalid event sequences: 12 passed.

From the checkout root:

```sh
UV_OFFLINE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --frozen pytest \
  -o addopts='' .pr/test_live_grep_event_sequence.py
```

In the base summary, `matches_expected_revision_behavior: true` means the
original defect was reproduced, not that the base returned correct matches.
SDK-reported costs were approximately $0.002203 for base and $0.001196 for head;
these are SDK accounting values, not a provider invoice.

For publication, only local checkout and temporary-directory prefixes were
replaced with `/checkouts/base`, `/checkouts/head`, and `/tmp/...` consistently.
Trailing whitespace in stderr logs was removed. The raw files remain local.
Patterns, results, response IDs, timestamps, hashes,
and validation outcomes are unchanged. No API key or authorization header is
included.

## Scope

This demonstrates the real `GrepTool` path on macOS with system grep. It does not
claim a live Windows run, exercise the terminal-command compatibility fallback,
or replace a repository benchmark or human maintainer review. The previous native
Windows transport evidence remains separate.

Reproduction instructions: [live-grep-agent.md](../live-grep-agent.md).
