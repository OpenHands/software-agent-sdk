---
name: verify-sdk
description: >
  This skill should be used to "verify SDK behavior", "check an SDK change
  against the feature map", "run the SDK feature map", "maintain the SDK
  feature map", or "run the daily SDK map pass". It holds the maintained map
  of what the OpenHands SDK does in-process (agent loop, LLMs, conversations,
  events, condensers, skills, plugins, MCP, hooks, sub-agents, critic,
  security, tools, workspaces, settings, observability, ACP), the control-sdk
  driver its recipes use, and sdk-map (check, owners, baseline, run, report).
triggers:
- /verify-sdk
- sdk feature map
---

# Verify the OpenHands SDK in-process

The "user" here is a Python program that imports `openhands.sdk` and runs an
agent in its own process. This skill proves that program's view of the SDK:
each recipe builds an agent with the public API, runs it, and checks what came
out (state, files, events, payloads), with a scripted model where the model
cannot matter and a real one where it can. It never reruns the unit tests: CI
does that on every PR, and each row links them on its `Unit:` line. The agent
server is a separate surface with its own skill,
[`verify-agent-server`](../verify-agent-server/SKILL.md); what the server adds
on top of the SDK belongs there.

## Launch

There is no server. Prepare the checkout once, then start a run per scenario:

```sh
uv sync --dev
export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"
export SDK_VERIFY_RUN=$(control-sdk new --field run)                     # scripted
export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run) # live
```

A run has its own HOME, `OH_PERSISTENCE_DIR`, conversation store, workspace
and tmux server, and drops the caller's `LLM_*` and `OH_*` variables, so it
never touches a developer's `~/.openhands`. Live runs read `DEEPSEEK_API_KEY`
when a model is called; without it they exit 3 and report `blocked`.

## Doctor

`sdk-map check` is the read-only health check: it validates the map's
structure and IDs, the index counts, the baseline line, and that every path
the map cites still exists. Run it first and whenever a recipe surprises you.
For the environment, `uv run python -c "import openhands.sdk, openhands.tools,
openhands.workspace"` must succeed.

## Drive

`control-sdk` does one thing per command and prints JSON; `--check FIELD OP
VALUE` asserts on it and `--field PATH` prints one value for the shell:

```sh
CID=$(control-sdk conversation new --script S05/write_and_read.json --field id)
control-sdk conversation send "$CID" --text "write notes.txt" --run --check execution_status eq finished
control-sdk conversation events "$CID" --kind ActionEvent --check count eq 4
control-sdk program S05/reload_events --arg "$CID" --check roundtrip_equal eq true
control-sdk exec --model-env -- python examples/01_standalone_sdk/01_hello_world.py
```

Conversations persist in the run, so each command resumes the one before.
Programs under [`programs/`](programs/) are short scripts like the examples
that print one JSON result. [references/recipes.md](references/recipes.md)
explains scripts, programs, live runs and conventions.

Each family file in [the map](references/feature-map/README.md) lists its
sub-features and a `Driving it` section of bullets, each naming the IDs it
proves and carrying `sh` blocks. Run them literally from the repository root,
or let the tool replay them:

```sh
sdk-map run --file S05                                  # one family
sdk-map run --file S05 --only S05.event-base            # the bullets that name these IDs
sdk-map run --all --out "$SDK_VERIFY_HOME/full"         # the whole map
```

A bullet passes when every block it ran exits 0; exit 3 means blocked. A block
that needs a real model (a live run, `--model-env`, an example script or the
integration runner) runs only with `DEEPSEEK_API_KEY` set and is skipped whole
otherwise. An integration run passes only when its `results.json` shows every
requested test succeeded, because the runner exits 0 on failed tests. Each
block runs in its own process group, which is killed when the block ends or
times out.

## Evidence

`sdk-map run` writes one ledger row per sub-feature to `<out>/ledger.jsonl`
and each bullet's transcript to `<out>/transcripts/`. `<out>` is a new
directory under `$SDK_VERIFY_HOME` (default `$TMPDIR/sdk-verify`) unless
`--out` names one. `sdk-map report --runs <out> [...]` renders the latest
result per sub-feature, fail first; `--baseline <earlier run>` lists what
changed since then.

Proof standards: drive the public API a program uses, not internal setters
or the unit tests; assert state, files and events, never a model's wording; a
scripted model (`TestLLM`) through the real agent loop is the deterministic
proof, a live run, example or integration run the real-model one, and a row
says which it got (`scripted`, `live`, `example`, `integration`). Unreachable
is `blocked` with the missing prerequisite, never a pass.

## Cleanup

Recipes write into their run's workspace, under `$SDK_VERIFY_HOME` (default
`$TMPDIR/sdk-verify`), never the checkout. Keep the run directories while the
evidence matters; `git status` should show nothing a recipe created.

## Workflows

- **Verify a change or a PR**: `sdk-map owners --changed origin/main...HEAD`
  maps the changed paths to the families whose `Source:` lines own them, and
  lists product paths no family claims (a map gap). Run those families with
  `sdk-map run`, with `DEEPSEEK_API_KEY` set for the live bullets, and report per
  [the agent-server report contract](../verify-agent-server/references/report.md).
- **Run the daily pass**: follow [references/daily.md](references/daily.md):
  `sdk-map check`, the families changed since the `Maintenance baseline` line
  (`sdk-map owners` with no arguments), and nothing else: families nothing
  in the range touched wait for the weekly pass, and a pass with no new
  commits on `main` stops early.
- **Maintain the map (periodic or weekly)**: follow
  [references/maintenance.md](references/maintenance.md): a frozen
  BASE/TARGET, a PR-intent ledger, a source wave, a full replay with a model
  key, and at most one PR of proven corrections.

## Triage what you find

- **Map drift**: the map describes something the SDK no longer does by design.
  Fix the entry with current source and a passing recipe.
- **Harness gap**: the SDK works but no recipe shows it yet. Write the program
  or `control-sdk` step that does; adding unit tests to the product is a
  separate PR.
- **Product bug**: the SDK is broken. Keep the transcript, search existing
  issues, and file it in this repository; mark the bullet's expected result
  with `Known failure (reproduced <date>, #<issue>)`. Never rewrite an expected
  result to bless broken behavior, and never fix product code inside a pass.
- **Blocked**: name the missing prerequisite (key, Docker, Chromium, account,
  interactive login) and the command attempted.

See [references/adaptation.md](references/adaptation.md) for where these
ideas come from.
