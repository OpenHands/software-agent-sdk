# Situations a happy path never shows

A family can be fully mapped, every bullet passing, and someone else still
files a bug on it the same week. Reading the fixes other people sent after
this map landed (2026-10-12 to 2026-11-11), the route was almost always
mapped already, and often its bullet passed. What the recipe lacked was the
*situation*: settings that held more than the fixture, two features switched
on together, a value shaped like a user's, a model or provider that is not
the scripted one, a failure halfway through, or a server that is not a fresh
local process from this checkout. A few times the recipe saw the bug and
wrote it down as the way things are.

This file lists those situations as questions. Ask them in three places:

- **Mapping** ([mapping.md](mapping.md) §3): after a sub-feature's happy path
  is written, walk the list and add a bullet for each question that has an
  answer on this surface.
- **The daily pass** ([daily.md](daily.md) step 2): for each merged change,
  ask the questions of the behavior it touched, not only whether it does what
  its PR says.
- **A full pass** ([maintenance.md](maintenance.md) §4): each source reader
  returns, with its drift list, the questions its family has no bullet for.

A question with no answer on a surface is skipped, not recorded. A question
whose answer needs something the run lacks is `blocked` with the
prerequisite, as everywhere else. Every bullet added this way is driven live
before it is written.

These questions are illustrative, not exhaustive. Ask them where a wrong
answer would hurt someone using the server, not to collect theoretical edge
cases; unit tests own those. When a report fits none of them, add the
question.

## 1. Did we write a bug down as the contract?

The most expensive miss is the one the map already saw. `F18.sdk-clone-repos`
stored the token as `github_token`, the code's spelling, while users store
`GITHUB_TOKEN` (#5729). `F26.install-agent-plugins` asserted that a
`plugin.json` without `$schema` is 422, the shape every Copilot-style plugin
ships. `F13.env-config-credentials` noticed that `sanitized_env` was the only
guard on the session key and called it "the current contract, not an
endorsement" (the tmux backend skipped it: #6149). `F06` pinned `TZ=UTC` so
naive timestamps read right, and recorded "timestamps are naive server-local"
(#6525).

- A Gotcha, an expected result or a "this records the current contract" note
  that a user would call wrong is a finding. File it (or find the issue) and
  mark the bullet `known bug`, or link the PR, doc or issue comment that
  decided it is intended. "The code does this" is not a decision.
- Take names, shapes and defaults from what users have: the documented name
  (`GITHUB_TOKEN`), what other tools ship (`plugin.json` without `$schema`),
  the example in the route's own docstring or the tool's own description. Not
  from the spelling the implementation happens to read.
- A `Launch:` flag or fixture that pins the environment to make a recipe
  deterministic (`TZ=UTC`, a locale, a short path) hides whatever depends on
  it. Pair it with one bullet on a second `launch --new` without the pin
  (`F06.timestamp-offset`).

## 2. Were two features on at once?

Each family is driven with its own features and the others at their
defaults. Bugs live where one family's state flows through another's route:
a `${NAME}` secret reference in `POST /api/mcp/test` (#6156); an
`OH_SECRET_KEY`-encrypted LLM profile loaded by a sub-agent (#5564); a
`worktree: true` conversation on the Docker runtime (#5307); Laminar/OTEL
variables with the Docker runtime (#5562); a sub-agent under a profile with
MCP servers (#5743) or a scoped tool list (#5519); client-supplied skills on
an ACP agent (#6152); a 500 response to a cross-origin request (#6011).

For each sub-feature, ask which of these reaches it, and drive the ones that
do with that axis switched on:

- secrets: stored, referenced as `${NAME}`, encrypted at rest (`OH_SECRET_KEY`),
  scoped by a profile;
- the agent: `agent_kind` `acp`, a sub-agent, a non-default profile, a
  switched LLM;
- the runtime: `conversation_runtime=docker`, `worktree: true`,
  `deferred_init`;
- the caller: a browser origin (CORS), the SDK `RemoteConversation`, the
  TypeScript client;
- the outcome: an error response, not only a 200.

The twin links (`Same promise:`) and the families' `Source:` overlap are a
cheap index of which axes meet where.

## 3. Was there something there before?

Recipes start from empty settings and create what they read. Users change
settings that already hold their work. Switching `agent_kind` wiped the MCP
catalog, persistent memory and the critic key (#6390, #6440):
`F18.agent-kind-switch` passed because nothing was stored before the switch.
Seeding the default agent profile reset `disabled_skills` (#5744). The
TypeScript client served a stale file the second time it downloaded a path
that had changed (#6181).

- Before a switch, migration, seed or upgrade, fill every optional field the
  user may have set (MCP servers, secrets, disabled skills, profiles,
  `load_memory`, the critic key), then read *every* field back, not only the
  one the change is about.
- Do the thing a second time after the underlying data changed: download,
  list, fork, install.

## 4. Was the data like a user's?

Fixtures are short, few, ASCII and unique. Give each value a bullet with one
that is not:

- **Secrets** that are short or common substrings (`a1b`, `iron`), so masking
  meets ids and timestamps (#6160, #6258); two secrets where one is a prefix
  of the other; tokens with `/`, `@`, `:` (base64, #6509).
- **Ids** in every form the parser accepts (braced, URN, hex without dashes),
  through every dispatcher (#6124).
- **Sizes** past the transport defaults: a state or skill catalog over 1 MiB
  on the WebSocket (#6548).
- **Providers and layouts** users have: Azure DevOps and Bitbucket Data
  Center URLs (#5986, #6509), a skills repository without a marketplace
  manifest (#6008), symlinks inside a served tree (#6316).

## 5. What happens when something fails halfway?

Recipes drive the success path and the 4xx table. The reports are about a
dependency failing in the middle and what is left over: a marketplace clone
that fails (#6460), a plugin that fails to load (#6439), a conversation
closed while a terminal command runs (#6139), an MCP stdio close that times
out (#5722), SIGTERM with tracing on (#6565), a persistence error inside an
executor thread (#4386).

- Make the dependency fail for real (an unreachable source, a missing key, a
  process killed with `kill -TERM`, `restart` mid-run) and assert what the
  user is told and what is left running or unflushed.

## 6. Was it run where users run it?

`launch` starts `python -m openhands.agent_server` from this checkout, on
Linux, in a container with a UTF-8 locale. Users run the PyInstaller binary
and the Docker image (#5989: schemas missing from the bundle), on macOS and
Windows, with another locale (#6261), under load from several conversations
(#4575), and in OpenHands Cloud (#6479).

- When a change touches files that must ship (schemas, JS, templates),
  check that the build includes them (`agent-server.spec`, the wheel's
  package data), or drive the built image, or mark the bullet `blocked`
  on it.
- A row `blocked` on an account or OS was not checked. Keep it visible in
  the report (report.md, Blocked), not folded into coverage.
