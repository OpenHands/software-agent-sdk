# Maintaining the map (periodic or weekly)

A feature map rots the moment the SDK changes. This pass keeps it honest and
answers three separate questions about the interval since the last pass:
**Is each feature present? Does it work as a program using the SDK sees it?
Was each change documented and intended?** A merged PR proves none of the last
two.

The unit of rigor is the feature: every family file gets source coverage and
a replay, without re-proving every sentence. This is a procedure, not a
scheduler: do not create automations, merge, or post externally unless asked.

## Outcomes

Pick one and say which:

- **clean**: every family got source coverage and a replay, intent is
  reconciled, nothing worth shipping. No branch, no PR.
- **changed**: one PR of proven map and `sdk-map` corrections, separate from
  product fixes.
- **blocked/partial**: coverage could not finish. Say exactly what and why; do
  not call it clean or move the baseline.

Edit scope is this skill's directory only (SKILL.md, references/, scripts/).
Never edit product code or tests during a maintenance pass.

## 1. Freeze the comparison

Record the UTC run time, budget (model, spend), the keys and tools available
(`DEEPSEEK_API_KEY`, `tmux`, Docker, Chromium, `node`, `uvx`), and permitted side
effects. Fetch `main` and freeze its full SHA as TARGET. Check
`git rev-parse --is-shallow-repository`; deepen with a bounded
`git fetch --depth=...` until BASE is present (an empty log from a shallow
clone is not "no changes"). BASE is the `Maintenance baseline: main@<sha>
(<date>)` line of [the map index](feature-map/README.md) (`sdk-map baseline`
reads it). A pass that changes the map moves that line to its TARGET in its
PR (`sdk-map baseline --set "$TARGET"`), so merging the PR accepts it.

```sh
git merge-base --is-ancestor "$BASE" "$TARGET"
git log --first-parent --format='%H %cI %s' "$BASE..$TARGET"
sdk-map owners --target "$TARGET"
```

A daily pass is a **delta pass**: [daily.md](daily.md). A full pass is still
needed weekly or before a release, because dependency bumps (LiteLLM, MCP,
pydantic, provider SDKs) change behavior without touching a family's
`Source:` paths.

For an apparent regression, use a second worktree at BASE and replay the same
bullet there (`sdk-map run --file Snn --only <ID>` from that worktree's own
`sdk-map`), so the two runs never share a checkout.

## 2. Route every change

`sdk-map owners --target "$TARGET"` routes every changed file to the families
whose `Source:` prefixes cover it and lists `unowned_product_paths` under
`openhands-sdk/`, `openhands-tools/` and `openhands-workspace/` that no family
claims. Map each of those to a family or an explicit reason it is not a
feature a program calls (an internal refactor, `utils/`, `io/`). Paths the
index's "Not mapped" section names come back as `not_mapped`, already
explained; a path that stays unowned needs a family or a line there. For shared models (events,
LLM config, settings) expand to every family that uses them rather than
sampling one. A new public API (an export in `openhands.sdk.__init__`, a new
tool, a new example) needs a sub-feature; add it per the family entry
contract in the index, with its `Unit:`, `Example:` and `Real-LLM:` links.

## 3. Build the PR-intent ledger

Resolve every commit in BASE..TARGET to its PR through the API
(`repos/OpenHands/software-agent-sdk/commits/<sha>/pulls`; commit-message
`(#123)` regexes miss rebases and direct pushes). Read each PR's description,
linked issue acceptance criteria, relevant review discussion and file list.

| Commit / PR | Changed paths | Families | Intended result + quoted evidence | Proof needed |
|---|---|---|---|---|

Record direct commits and unresolvable PRs as unknown intent. Keep intent
(`documented`, `undocumented`, `contradictory`) independent of runtime
(`pass`, `fail`, `blocked`, `not-run`). A working change can still be
undocumented. Never edit a PR description to justify a change after the fact.

## 4. Index hygiene and source wave

Run `sdk-map check`; fix missing, duplicate and dead entries
(`sdk-map check --fix-counts` refreshes the index counts). A cited test,
example or source path that is gone is drift: find where it moved
(`git log --follow --diff-filter=R`) or retire the row. Then give one
read-only reader per family file (parallel if delegation is available). Each
reads current source for that family and returns: summary, source entry
points, likely drift with citations (or none), new public behavior missing
from the map, rows whose `Unit:` link no longer tests what the row says, and
one recipe. Readers never edit files and never run model-backed recipes.

Re-check stale claims whenever tests or examples were added: grep the map for
`untested`, `blocked` and `no test`, and turn a row's link into a real one
when a new test or example now covers it.

## 5. Replay

Required even when source looks clean. `sdk-map run --all` replays every
family's scripted blocks; with `DEEPSEEK_API_KEY` set it also runs the live
blocks, examples and integration commands. Exercise every family at least
once and every changed behavior through each entry point the family lists
(the API call, the example, the integration task). Hold three invariants:

1. Never trust a replay of a checkout you have not doctored since its last
   surprise (`sdk-map check`, `uv sync --dev`, the import line in SKILL.md).
2. Evidence survives every cleanup: the run directory is outside the
   checkout; check it after you clean up.
3. Nothing a recipe started outlives it: no stray processes (MCP servers,
   tmux sessions, browsers), no files left in the checkout.

A `check` failure caused by skill drift is drift: fix it and retry once
before calling the pass blocked. A feature that cannot be reached is
`blocked` only with the concrete prerequisite and the command attempted; if
the map omits that prerequisite from the family's `Needs:` line, that is
drift too.

## 6. Triage and ship

- **Map drift** (the map is wrong about intended behavior): fix the entry,
  replay it, cite the PR that changed it.
- **Harness gap**: fix `sdk-map` or the recipe, replay.
- **Product bug**: record `fail` with the transcript and a minimal command
  list; file it in this repository, and add the `Known failure` note in the
  map PR. The fix stays out.
- **Intent gap** (behavior changed without documentation): report it to the
  user with the PR link; do not "fix" the map to bless it silently.

For **changed**: one PR of proven corrections. Re-read every changed file,
run `sdk-map check` and the skill's tests
(`uv run pytest tests/cross/test_verify_sdk_skill.py tests/cross/test_control_sdk.py`), move the baseline line
to TARGET, and put the report (fail and blocked first) and the intent ledger
in the PR description. For **clean** or **blocked**: no PR; report the outcome
and coverage honestly.

Keep concise run notes (families covered, unreachable prerequisites, confirmed
drift, outcome) in a scratch location; do not commit them.
