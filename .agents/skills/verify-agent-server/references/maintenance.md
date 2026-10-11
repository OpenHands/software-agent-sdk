# Maintaining the map (periodic or weekly)

A feature map rots the moment the server changes. This pass keeps it honest
and answers three separate questions about the interval since the last pass:
**Is each feature present? Does it work as an API consumer sees it? Was each
change documented and intended?** A merged PR proves none of the last two.

The unit of rigor is the feature: every family file gets source coverage and
live coverage, without re-proving every sentence. This is a procedure, not a
scheduler: do not create automations, merge, or post externally unless asked.

## Outcomes

Pick one and say which:

- **clean**: every family got source and live coverage, intent is reconciled,
  nothing worth shipping. No branch, no PR.
- **changed**: one PR of proven map/CLI corrections, separate from product
  fixes.
- **blocked/partial**: coverage could not finish. Say exactly what and why; do
  not call it clean or advance the accepted baseline.

Edit scope is this skill's directory only (SKILL.md, references/, scripts/).
Never edit product code during a maintenance pass.

## 1. Freeze the comparison

Record the UTC run time, budget (model, spend), accounts and keys available,
and permitted side effects. Fetch `main` and freeze its full SHA as TARGET.
Check `git rev-parse --is-shallow-repository`; deepen with a bounded
`git fetch --depth=...` until BASE is present (an empty log from a shallow
clone is not "no changes"). BASE is the previous completed pass's recorded
TARGET, kept on the `Maintenance baseline` line of
[the map index](feature-map/README.md); on a first run, the last first-parent
commit before the agreed cutoff
(`git rev-list --first-parent -1 --before="$CUTOFF" "$TARGET"`). A pass that
changes the map updates that line to its TARGET in its PR, so merging the PR
accepts the new baseline.

```sh
git merge-base --is-ancestor "$BASE" "$TARGET"
git log --first-parent --format='%H %cI %s' "$BASE..$TARGET"
git diff --name-status "$BASE" "$TARGET"
```

A daily pass may be a **delta pass**: source and live coverage only for the
families whose paths or routes changed in BASE..TARGET (§3) and for rows that
link an issue closed since BASE. It says "delta" in its report. A full pass is
still needed now and then (weekly, or before a release), because dependency
bumps (LiteLLM, FastAPI, MCP) change behavior without touching a router.

Use separate worktrees for BASE and TARGET and `control-agent-server launch
--new --checkout <worktree>` for each (after `uv sync --dev` in each worktree),
so state and ports never mix. Never rebuild the baseline from today's source or
overwrite last pass's evidence.

## 2. Diff the contract, not just the files

The agent-facing contract is the route table plus the request and response
schemas. Compare both ends:

```sh
control-agent-server map diff --base "$BASE_WORKTREE" --target "$TARGET_WORKTREE"
control-agent-server map owners --changed "$BASE..$TARGET"
```

Every added, removed or newly deprecated route is a map change: a new route
needs an owner (`map coverage` fails until it has one), a removed route needs
its sub-features marked `retired` with the authorizing PR, and a deprecated
route needs its removal version in the family's Gotchas. For schema changes,
the CI's REST breakage check (oasdiff against the last release) tells you what
changed incompatibly; read its PR comment rather than re-deriving it, and
re-drive the affected recipes.

`map owners --changed` routes every changed file to the families whose
`Source:` prefixes cover it and lists changed product paths no family claims.
Map each of those to a family or an explicit non-agent-facing reason (tests,
CI, docs, internal refactor with no contract change). For shared models
(`openhands-sdk` events, settings, LLM config) expand to every family that
serializes them rather than sampling one.

## 3. Build the PR-intent ledger

Resolve every commit in BASE..TARGET to its PR through the API
(`repos/OpenHands/agent-sdk/commits/<sha>/pulls`; commit-message `(#123)`
regexes miss rebases and direct pushes). Read each PR's description, linked
issue acceptance criteria, relevant review discussion and file list.

| Commit / PR | Changed paths | Families / routes | Intended result + quoted evidence | Runtime proof needed |
|---|---|---|---|---|

Record direct commits and unresolvable PRs as unknown intent. Keep intent
(`documented`, `undocumented`, `contradictory`) independent of runtime
(`pass`, `fail`, `blocked`, `not-run`). A working change can still be
undocumented. Never edit a PR description (the `HUMAN:` section is human-only)
to justify a change after the fact.

## 4. Index hygiene and source wave

Run `control-agent-server map check` and `map coverage`; fix missing,
duplicate and dead entries (`map check --fix-counts` refreshes the index
counts). Then give one read-only reader per family file (parallel if
delegation is available). Each reads current source for that family and
returns: summary, source entry points, likely drift with citations (or none),
new routes or behaviors missing from the map, consumer changes
(`RemoteConversation`, `RemoteWorkspace`, TypeScript client), and one live
recipe. Readers never edit files and never launch servers.

Re-check stale harness claims whenever the CLI gained verbs: grep the map for
`blocked`, `not run`, `harness gap`, `no verb` and `cannot be driven`, and
re-drive any that a current verb can now reach (compare with
`control-agent-server --help`).

## 5. Live pass

Required even when source looks clean. One coordinator owns each launched run
and drives it serially; parallel live work needs one `launch --new` per worker,
each with its own exported `AGENT_SERVER_VERIFY_RUN`. Exercise every family at
least once and every changed behavior on all its entry points (REST,
WebSocket, SDK, TypeScript client). Hold three invariants:

1. Never drive a run not doctored since its last surprise
   (`control-agent-server doctor`; `restart` or relaunch when a run is wedged).
2. Evidence captured so far survives every cleanup (check it after `stop`).
3. Nothing a drive started outlives its usefulness: conversations a recipe no
   longer needs are deleted, background captures stopped, runs stopped.

A doctor failure caused by skill drift is drift: fix it and retry once before
calling the pass blocked. A feature that cannot be reached is `blocked` only
with the concrete prerequisite and the route attempted.

When the TypeScript client's pinned server image (`clients/typescript/package.json`
→ `config.agentServerImage`) lags TARGET, note which recipes' consumer methods
are not yet in the client; that is client drift, owned here, not map drift.

## 6. Triage and ship

- **Map drift** (the map is wrong about intended behavior): fix the entry,
  re-drive, cite the PR that changed it.
- **Harness gap**: fix the CLI, prove the new verb live, re-drive.
- **Product bug**: record `fail` with evidence and a minimal repro command list;
  report it in this repository (or the owning one), not in the map PR.
- **Intent gap** (behavior changed without documentation): report it to the
  user with the PR link; do not "fix" the map to bless it silently.

For **changed**: one PR of proven corrections. Re-read every changed file,
run `map check`, `map coverage` and the CLI tests, update the `Maintenance
baseline` line to TARGET, and put the evidence report (fail/blocked first) and
the intent ledger in the PR description or under `.pr/`. For **clean** or
**blocked**: no PR; report the outcome and coverage honestly.

Keep concise run notes (families covered, unreachable prerequisites, confirmed
drift, outcome) in a scratch location; do not commit them.
