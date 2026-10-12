# Where this workflow comes from

This is an independently written adaptation of ideas from poteto's
MIT-licensed pstack ([backnotprop/pstack](https://github.com/backnotprop/pstack),
a harness-neutral mirror of
[cursor/plugins/pstack](https://github.com/cursor/plugins/tree/main/pstack)) and
the [cursor-team-kit](https://github.com/cursor/plugins/tree/main/cursor-team-kit)
control skills, by way of the sibling `verify-openhands` skill in
[`OpenHands/OpenHands`](https://github.com/OpenHands/OpenHands/tree/main/.agents/skills/verify-openhands),
which applies the same method to the Agent Canvas UI:

- [create-verification-skill](https://github.com/cursor/plugins/blob/main/pstack/skills/create-verification-skill/SKILL.md):
  interview the repository, then generate launch / doctor / drive / evidence /
  cleanup and a feature map, and prove one recipe end to end before handing
  it over.
- [The feature-map example](https://github.com/cursor/plugins/tree/main/pstack/skills/create-verification-skill/references/feature-map-example):
  one file per feature with four sections, stable IDs, and every step written
  against a `control-<app>` CLI so it is a literal, rerunnable command.
- [maintain-verification-skill](https://github.com/cursor/plugins/blob/main/pstack/skills/maintain-verification-skill/SKILL.md):
  index hygiene, a parallel source wave, one serial live pass, and triage into
  map drift, harness gaps and product bugs.
- [Build the Lever](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-build-the-lever/SKILL.md)
  and [control-cli](https://github.com/cursor/plugins/blob/main/cursor-team-kit/skills/control-cli/SKILL.md):
  build the tool that drives and proves the work; one action per step,
  deterministic waits over sleeps, clean up what you started.
- [Benny's control-adapter contract and verify-existing-fix](https://github.com/backnotprop/pstack/tree/main/automations/benny/skills/reproduce-and-fix-issues/references):
  report capabilities and which map sections are drivable or blocked before
  starting, translate the environment before declaring a block, and confirm a
  fix only when the baseline reproduces twice and the patched build resolves
  it twice.
- [cli-for-agents](https://github.com/cursor/plugins/blob/main/cli-for-agent/skills/cli-for-agents/SKILL.md):
  non-interactive flags, layered `--help` with examples, actionable errors,
  idempotent commands, machine-readable output.

## Agent-server decisions

The "user" of this product is an agent, or the program that carries one, so
the surface under test is the REST and WebSocket API rather than a screen.

- **A CLI over raw HTTP and WebSocket, not the TypeScript client.** The
  TypeScript client is a consumer under test: it is pinned to a released
  server image, it intentionally does not wrap every route, and it would hide
  server behavior (status codes, error bodies, close codes, headers) behind
  its own. Driving the wire keeps every route reachable on the day it lands.
  Consumers are still covered: each family lists the SDK and TypeScript
  client methods that reach it, and `exec` runs SDK or client programs against
  the same isolated run.
- **The route table is the inventory.** `map routes` imports the checkout's
  app (in the default configuration and in Docker runtime mode), so it sees
  WebSockets, config-only routes and routes that OpenAPI omits. Each family
  declares the routes it owns on a `Routes:` line; `map coverage` fails until
  every route has exactly one owner, and `map check` fails when a recipe or a
  `Routes:` line names a route the app no longer serves.
- **Recipes are parsed, not just read.** `map check` runs every
  `control-agent-server ...` command in the map through the real argument
  parser, so a renamed verb or flag breaks the check instead of the next agent.
- **Maintenance starts from the contract.** The weekly pass diffs the route
  table between BASE and TARGET and routes changed files to families with
  `map owners`, then relies on the existing oasdiff breakage check for schema
  changes instead of re-deriving it.
- **Isolation by configuration.** Every run gets a private `HOME`,
  `OH_PERSISTENCE_DIR`, config file, conversations and workspace paths,
  worktree root, tmux dir, session key, secret key and port, and inherits none
  of the caller's `OH_*` or LLM variables, so a run never touches a developer's
  `~/.openhands` or another agent's run.
- **States without mocks.** Restarts (persistence and restore), key rotation,
  deferred init, unauthenticated mode, config changes and a recording webhook
  sink are CLI verbs, so recipes reach those states with the real server.
- **Model-backed recipes use DeepSeek V4.1 Flash** from an environment key,
  validated with the server's own pre-flight before it is saved, so a CI agent
  can run them without typing secrets into argv or logs.
- Not imported: the browser daemon, screenshots and viewport checks of the UI
  skill (there is no UI here), Cursor plugin configuration, model routing,
  `disable-model-invocation`, and autonomous shipping or merge authority.

These are contributor workflows for this repository under `.agents/skills`
([AgentSkills format](https://docs.openhands.dev/sdk/guides/skill)). They
complement the unit, live-server, stress and contract tests described in
[SKILL.md](../SKILL.md#how-this-fits-with-the-tests), and do not replace them.
