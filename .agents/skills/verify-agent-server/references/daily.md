# The daily pass

The weekly pass in [maintenance.md](maintenance.md) proves every family live.
A daily pass cannot: 34 families and 900-odd sub-features take longer than one
agent's day, and most of the map did not change since yesterday. The daily
pass answers a narrower question with the same rigor: **did anything that
merged since the last pass change what an API consumer sees?** It drives only
the features that question touches, and does nothing when nothing merged.

It is the delta pass that [maintenance.md](maintenance.md) allows, and it
moves the same baseline.

This is a procedure, not a scheduler: run it from whatever runs you daily, do
not create automations or post externally unless asked, and hand what you
found to a person.

## Inputs and hand-off

A scheduled pass starts in a fresh session with no memory of yesterday, so
everything it needs from the previous pass lives in the repository or on an
open PR:

- `BASE` is the `Maintenance baseline: main@<sha> (<date>)` line of
  [the map index](feature-map/README.md), the previous pass's `TARGET`.
  `control-agent-server map baseline` reads it and says how many commits
  `HEAD` is past it; `map owners` with no arguments diffs from it. Fetch
  `main` and freeze its full SHA as `TARGET`; deepen a shallow clone until
  `BASE` is present (`map baseline` returns `commits_since: null` until it
  is).
- **Stop early when `BASE` equals `TARGET`**: nothing merged, so there is
  nothing to drive. The one exception is an earlier pass's PR that is still
  open with something pending on it (failing CI, an unanswered review); handle
  that and stop.
- A pass proposes `TARGET` as the next baseline in its PR
  (`control-agent-server map baseline --set "$TARGET"`; the SHA must be on
  `origin/main`) only when step 2 finished: every family `map owners` listed
  was driven, every commit in `BASE..TARGET` was resolved to its PR, and the
  closed-issue rows below were re-driven. Merging that PR accepts the
  baseline. A pass that ran out of time or was blocked in step 2 leaves the
  line where it was, even when its PR carries other map or CLI fixes, and says
  so in its report (maintenance.md's blocked/partial outcome); a pass that
  changes nothing leaves it too, so the next pass covers a longer range. If an
  earlier pass's PR is still open, the next pass continues on that branch and
  takes `BASE` from the line there.
- Yesterday's ledger is an optional input: when the previous run directory or
  its `evidence/ledger.jsonl` is at hand (a CI artifact, a shared directory),
  pass it to `evidence report --baseline`; without it the report has no
  "Changes since baseline" section and the verdict rests on today's fail and
  blocked rows.

Record the UTC time, model and spend budget, and the keys and capabilities
available (`control-agent-server capabilities`), as the weekly pass does. A
pass that runs out of time says so and names what it did not reach; it never
rolls the remainder into tomorrow silently.

## Steps, in order

Run the steps in this order and stop when the budget runs out. A family that
nothing in `BASE..TARGET` touched is not driven: it waits for the weekly pass.

1. **Static (minutes, no server).** `control-agent-server map check` and
   `map coverage`. A recipe that names a route the app no longer serves, a
   verb or flag the CLI no longer parses, or a route no family owns is drift
   found before any server starts. Fix it in the map with a live drive later
   today, or report it.
2. **Changed (the core).** `control-agent-server map owners --target
   "$TARGET"` lists the families whose `Source:` lines own a changed path, and
   `unowned_product_paths` that no family claims. Map each unowned path to a
   family or an explicit non-agent-facing reason, as maintenance.md §2 says.
   Diff the contract too: `map diff --base <BASE worktree>` for routes added,
   removed or newly deprecated, and the REST breakage check's PR comments for
   schema changes. Then drive **every** listed family on a fresh run
   (`map run --file Fnn --fresh --record`), and every changed behavior on each
   entry point the family lists (REST, WebSocket, SDK, TypeScript client).
   Widen shared models (`openhands-sdk` events, settings, LLM config) to every
   family that serializes them rather than sampling one. Resolve each merged
   commit in `BASE..TARGET` to its PR and keep intent (`documented`,
   `undocumented`, `contradictory`) separate from runtime results, as
   maintenance.md §3 describes. The delta pass has a second half: every bullet
   whose label says `known bug`, or whose Gotchas link an issue, is re-driven
   when that issue closed since `BASE` (check each linked issue's state), its
   row recorded and its label or note updated. A fix that landed without
   touching a family's `Source:` paths shows up only this way. So is every
   open `bug` issue opened since `BASE` that names a mapped route or feature:
   it is reproduced and gets its note, so bugs filed outside a pass reach the
   map. Model-backed recipes run only inside the LLM budget; without a key
   they are `blocked` with the missing prerequisite.

## Reading the result

Render `control-agent-server evidence report --runs <every run of the pass>`,
with `--baseline <yesterday's run or ledger>` when that ledger is at hand.
Fail and blocked rows come first; the "Changes since baseline" section, when
present, is the daily verdict:

- **Newly failing** on a family that step 2 drove for a changed path: an
  introduced-in-range candidate. Re-drive the same minimal recipe on `BASE`
  (`map run --file Fnn --only <ID> --fresh --checkout <BASE worktree>`) before
  calling it a regression; otherwise classify it as reproduced-on-both,
  environment or harness gap, or origin-unconfirmed.
- **Newly failing** elsewhere, or **newly passing** without a PR that explains
  it: an undocumented change; cite the commit and ask.
- **Not checked this run** rows are features nothing in the range touched,
  not regressions.
- Without yesterday's ledger, read today's fail rows the same way: a fail on a
  family step 2 drove for a changed path is a candidate to re-drive on
  `BASE`; elsewhere it is a finding to triage, with the family's last known
  state taken from the map (a `known bug` bullet, a linked issue).

Triage as the skill does: map drift (fix, with live evidence), harness gap
(extend the CLI, re-drive), product bug (keep the evidence, search issues,
file it in the owning repository, and mark the bullet `known bug` with the
issue link in the map PR; the fix stays out), blocked (name the prerequisite
and the route). At most one PR of proven map and CLI corrections per pass,
from current `main` (or from the previous pass's branch while its PR is
open), carrying the moved baseline line and saying "delta" in its report;
never edit product code in a pass.

## Several agents

Shard by family, never by bullet: each agent gets whole families, its own
`launch --new` (or `map run --fresh`), its own `AGENT_SERVER_VERIFY_RUN`, and
writes only its own ledger. One coordinator merges the ledgers with
`evidence report --runs` and owns the hand-off. A result that names no run,
revision or entry point does not count; a gap is not a pass.

## What the daily pass is not

- Not the full pass: it does not re-prove every bullet, and the baseline it
  moves says only that the day's changes were covered, which is why an
  unfinished step 2 never moves it. When step 2 finds that most of the map is
  affected (a shared model, a dependency bump such as FastAPI, LiteLLM or
  MCP), say so and run the full procedure instead. A full pass is still needed
  weekly or before a release, because dependency bumps change behavior without
  touching a router.
- Not CI: a green test run or a passing `map check` is an input to a verdict,
  never the verdict.
- Not a bug-fix lane: product bugs are filed and mapped, not fixed, inside the
  pass.
