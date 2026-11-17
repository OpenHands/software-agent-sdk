# Where this workflow comes from

This is an independently written adaptation of ideas from poteto's
MIT-licensed pstack ([cursor/plugins/pstack](https://github.com/cursor/plugins/tree/main/pstack),
mirrored harness-neutrally at [backnotprop/pstack](https://github.com/backnotprop/pstack)),
by way of the sibling skills
[`verify-agent-server`](../../verify-agent-server/references/adaptation.md)
in this repository and `verify-openhands` in
[`OpenHands/OpenHands`](https://github.com/OpenHands/OpenHands/tree/main/.agents/skills/verify-openhands):

- [create-verification-skill](https://github.com/cursor/plugins/blob/main/pstack/skills/create-verification-skill/SKILL.md):
  launch / doctor / drive / evidence / cleanup sections and a feature map
  with one file per feature and four sections.
- [maintain-verification-skill](https://github.com/cursor/plugins/blob/main/pstack/skills/maintain-verification-skill/SKILL.md):
  the three outcomes (clean, changed, blocked), edit scope limited to the
  skill, index hygiene, a parallel read-only source wave, a required live
  pass with three invariants (doctor first, evidence survives cleanup, no
  leftovers), and triage into map drift, harness gaps and product bugs that
  stay out of the PR.
- [principle-prove-it-works](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-prove-it-works/SKILL.md):
  check the real thing (run the feature, read the actual value), not a proxy
  or a self-report.

## SDK decisions

- **A driver, not the unit tests.** The SDK runs inside the caller's
  process, so there is no server to launch, but the recipes still drive the
  product: `control-sdk` gives each scenario an isolated run, persisted
  conversations that each command resumes, short programs that use the public
  API like the examples, and `--check` on their JSON. Rerunning the unit
  tests would only repeat CI. A scripted `TestLLM` drives the real agent loop
  where the model cannot matter; DeepSeek drives it where it can.
  `sdk-map run` replays the map's `sh` blocks and records a ledger.
- **Three links per row.** Each sub-feature names its deterministic proof
  (`Unit:`), its real-model demonstration (`Example:`) and its integration
  task (`Real-LLM:`), so a reader sees at once which rows CI checks on every
  PR and which only a labeled run or this skill checks.
- **The same baseline and daily pass as the agent-server map.** BASE is a
  line in the index, the daily pass replays only the families changed since
  it and stops early when nothing merged, and only a pass that
  finished its changed families moves it. Family IDs are `Snn` so they never
  collide with the agent-server map's `Fnn`.
- Not imported: Cursor plugin configuration, `disable-model-invocation`, and
  autonomous shipping or merge authority.

These are contributor workflows for this repository under `.agents/skills`
([AgentSkills format](https://docs.openhands.dev/sdk/guides/skill)). They
complement the unit, cross, example and integration tests, and do not replace
them.
