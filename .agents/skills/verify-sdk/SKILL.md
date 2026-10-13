---
name: verify-sdk
description: >
  This skill should be used to "verify SDK behavior", "check an SDK change
  against the feature map", "run the SDK feature map", "maintain the SDK
  feature map", or "run the daily SDK map pass". It holds the maintained map
  of what the OpenHands SDK does in-process (agent loop, LLMs, conversations,
  events, condensers, skills, plugins, MCP, hooks, sub-agents, critic,
  security, tools, workspaces, settings, observability, ACP) and ships
  sdk-map (check, owners, baseline, run, report).
triggers:
- /verify-sdk
- sdk feature map
---

# Verify the OpenHands SDK in-process

The "user" here is a Python program that imports `openhands.sdk` and runs an
agent in its own process. This skill proves that program's view of the SDK:
each feature as the public API exposes it, with a deterministic test where one
exists, an example script with a real model, and the integration runner. The
agent server is a separate surface with its own skill,
[`verify-agent-server`](../verify-agent-server/SKILL.md); what the server adds
on top of the SDK belongs there.

## Launch

There is no server. Prepare the checkout once:

```sh
uv sync --dev
export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"
```

Model-backed recipes read `LLM_API_KEY`, `LLM_MODEL` and, behind a proxy,
`LLM_BASE_URL`. Without a key they are skipped and reported `blocked`, never
passed.

## Doctor

`sdk-map check` is the read-only health check: it validates the map's
structure and IDs, the index counts, the baseline line, and that every path
the map cites still exists. Run it first and whenever a recipe surprises you.
For the environment, `uv run python -c "import openhands.sdk, openhands.tools,
openhands.workspace"` must succeed.

## Drive

Each family file in [the map](references/feature-map/README.md) lists its
sub-features and a `Driving it` section of bullets, each naming the IDs it
proves and carrying `sh` blocks. Run them literally from the repository root,
or let the tool replay them:

```sh
sdk-map run --file S05                                  # one family
sdk-map run --file S05 --only S05.event-base            # the bullets that name these IDs
sdk-map run --all --out "$SDK_VERIFY_HOME/full"         # the whole map
```

A bullet passes when every block it ran exits 0. Commands that run an
example script, `tests/examples/` or the integration runner
(`tests/integration/run_infer.py`) run only with a model key; the
deterministic commands in the same block run either way. An integration run
passes only when its `results.json` shows every requested test succeeded,
because the runner exits 0 on failed tests. Each block runs in its own
process group, which is killed when the block ends or times out.

## Evidence

`sdk-map run` writes one ledger row per sub-feature to `<out>/ledger.jsonl`
and each bullet's transcript to `<out>/transcripts/`. `<out>` is a new
directory under `$SDK_VERIFY_HOME` (default `$TMPDIR/sdk-verify`) unless
`--out` names one. `sdk-map report --runs <out> [...]` renders the latest
result per sub-feature, fail first; `--baseline <earlier run>` lists what
changed since then.

Proof standards: drive the public API a program uses, not internal setters;
assert state, files and events, never a model's wording; a scripted LLM
(`TestLLM`) is the deterministic proof, an example or integration run the
real-model one, and a row says which it got. Unreachable is `blocked` with the
missing prerequisite, never a pass.

## Cleanup

Recipes write into temporary directories and `.agent_tmp/`-style working
directories the examples create. Remove what a recipe created in the
checkout (`git status` shows it) and keep the run directory: the evidence
lives there, not in the checkout.

## Workflows

- **Verify a change or a PR**: `sdk-map owners --changed origin/main...HEAD`
  maps the changed paths to the families whose `Source:` lines own them, and
  lists product paths no family claims (a map gap). Run those families with
  `sdk-map run`, plus their examples when a key is available, and report per
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
- **Harness gap**: the SDK works but no recipe can show it (no test, example
  or runner reaches it). Mark the row untested, and add a recipe when one is
  cheap; adding tests to the product is a separate PR.
- **Product bug**: the SDK is broken. Keep the transcript, search existing
  issues, and file it in this repository; mark the bullet's expected result
  with `Known failure (reproduced <date>, #<issue>)`. Never rewrite an expected
  result to bless broken behavior, and never fix product code inside a pass.
- **Blocked**: name the missing prerequisite (key, Docker, Chromium, account,
  interactive login) and the command attempted.

See [references/adaptation.md](references/adaptation.md) for where these
ideas come from.
