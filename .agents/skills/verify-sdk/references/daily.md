# The daily pass

The weekly pass in [maintenance.md](maintenance.md) replays every family.
A daily pass need not: most of the map did not change since yesterday, and
the model-backed recipes cost money. The daily pass answers a narrower
question with the same rigor: **did anything that merged since the last pass
change what a program using the SDK sees?** It replays only the families that
question touches, and does nothing when nothing merged. It is the delta pass
that maintenance.md allows, and it moves the same baseline.

This is a procedure, not a scheduler: run it from whatever runs you daily, do
not create automations or post externally unless asked, and hand what you
found to a person.

## Inputs and hand-off

A scheduled pass starts in a fresh session with no memory of yesterday, so
everything it needs lives in the repository or on an open PR:

- `BASE` is the `Maintenance baseline: main@<sha> (<date>)` line of
  [the map index](feature-map/README.md); `sdk-map baseline` reads it and
  says how many commits `HEAD` is past it, and `sdk-map owners` with no
  arguments diffs from it. Fetch `main` and freeze its full SHA as `TARGET`;
  deepen a shallow clone until `BASE` is present.
- **Stop early when `BASE` equals `TARGET`**: nothing merged, so there is
  nothing to replay. The one exception is an earlier pass's PR that is still
  open with something pending on it (failing CI, an unanswered review); handle
  that and stop.
- A pass proposes `TARGET` as the next baseline in its PR
  (`sdk-map baseline --set "$TARGET"`) only when step 2 finished: every
  family `sdk-map owners` listed was replayed, every commit in `BASE..TARGET`
  was resolved to its PR, and the closed-issue rows below were re-driven.
  Merging that PR accepts the baseline. A pass that ran out of time or was
  blocked in step 2 leaves the line where it was and says so. If an earlier
  pass's PR is still open, the next pass continues on that branch and takes
  `BASE` from the line there.
- Yesterday's run directory is an optional input to
  `sdk-map report --baseline`.

Record the UTC time, model and spend budget, and the keys and tools
available. A pass that runs out of time says so and names what it did not
reach.

## Steps, in order

A family that nothing in `BASE..TARGET` touched is not replayed: it waits for
the weekly pass.

1. **Static (seconds).** `sdk-map check`. A cited test, example or source
   path that is gone, or a sub-feature with no bullet, is drift found before
   anything runs.
2. **Changed (the core).** `sdk-map owners --target "$TARGET"`. Replay every
   listed family in full (`sdk-map run --file Snn`), with the model-backed
   blocks when a key is set, and map each `unowned_product_paths` entry to a
   family or a reason (maintenance.md §2). Resolve each merged commit in
   `BASE..TARGET` to its PR and keep intent separate from results
   (maintenance.md §3). Then the second half: every row whose Gotchas or
   `Known failure` note links an issue that closed since `BASE` is replayed
   and its note updated, and every open `bug` issue opened since `BASE` that
   names a mapped feature is reproduced and gets its note. Model-backed blocks
   run only inside the budget; without a key they are `blocked`.

## Reading the result

`sdk-map report --runs <every run of the pass>`, with `--baseline
<yesterday's run>` when at hand. Fail and blocked rows come first.

- **Newly failing** on a family step 2 replayed for a changed path: an
  introduced-in-range candidate. Replay the same bullet on a `BASE` worktree
  before calling it a regression.
- **Newly failing** elsewhere, or **newly passing** without a PR that
  explains it: cite the commit and ask. A dependency bump is a common cause.
- **Not checked this run**: features nothing in the range touched, not a
  regression.

Triage as the skill does, at most one PR of proven map and `sdk-map`
corrections per pass, carrying the moved baseline line and saying "delta" in
its report.

## What the daily pass is not

- Not the full pass: when step 2 finds most of the map affected (a shared
  model, a LiteLLM bump), say so and run maintenance.md instead.
- Not CI: CI already runs the unit tests on every PR. The pass adds what CI
  skips (examples, integration tasks, the map's own claims) and the
  judgment of whether a change was intended.
- Not a bug-fix lane: product bugs are filed and noted in the map, not fixed,
  inside the pass.
