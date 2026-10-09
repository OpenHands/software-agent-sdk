---
name: verify-agent-server
description: >
  This skill should be used to "verify Agent Server APIs", "test the REST or
  WebSocket API like an agent would", "drive the agent-server", "check an API
  change against a real server", "create or update the agent-server feature
  map", or "run the weekly API audit". Ships control-agent-server (launch,
  doctor, api, ws, llm, conversations, fixtures, exec, evidence, map) and the
  maintained map of every agent-facing feature.
triggers:
- /verify-agent-server
- agent-server feature map
---

# Verify the Agent Server through its real API

Prove what an API consumer observes, not that a handler returned or CI passed.
The consumers are agents and the programs that carry them: the SDK's
`RemoteConversation`/`RemoteWorkspace`, the TypeScript client, Agent Canvas and
automations. Three parts work together:

1. **`control-agent-server`** ([scripts/](scripts/)) is the lever. It launches
   this checkout as an isolated real server (private `HOME`, persistence dir,
   config file, session key, secret key, port), and turns every API step into a
   command you can rerun: REST calls, WebSocket captures, LLM-backed
   conversations, fixtures, SDK programs.
2. **The feature map** ([references/feature-map/](references/feature-map/README.md))
   lists every agent-facing behavior with stable IDs, the routes each family
   owns, entry points (REST, WebSocket, SDK and TypeScript client methods),
   exact `control-agent-server` recipes, observable results and gotchas.
3. **Evidence**: saved request/response exchanges, WebSocket frames, webhook
   deliveries and program transcripts, plus a pass/fail/blocked/not-run ledger
   that survives cleanup.

Neither this skill nor the map authorizes external writes, paid models beyond
the budget you were given, or product fixes the user did not ask for.

## The CLI comes first

Put it on `PATH` and read its help before anything else:

```sh
export PATH="$PWD/.agents/skills/verify-agent-server/scripts:$PATH"
control-agent-server --help              # then: control-agent-server <command> --help
```

Requirements: `make build` (or `uv sync --dev`) in the repository root; the
CLI runs on the repository's `.venv` and needs nothing else. `git` is needed
for fixtures, `tmux` for bash and terminal tools, and a DeepSeek key for
model-backed recipes. Every command prints one JSON object; exit 0 ok,
1 action failed, 2 usage, 3 environment.

If an agent-facing path cannot be driven with the CLI, that is a **harness
gap**: extend `scripts/control_agent_server.py` (document the verb in its
`--help` with examples and in this file), prove it live, then write the recipe.
Never work around a gap with an untracked one-off script the next agent cannot
rerun. `api` and `ws` reach every route, so most gaps are about waiting,
fixtures or observing side effects, not about calling an endpoint. Other agents
may be running the same script: edit a copy, run its tests and
`map check`, then move it into place in one step.

## Launch → doctor → drive → evidence → cleanup

```sh
export AGENT_SERVER_VERIFY_RUN=$(control-agent-server launch --new --print-run)
control-agent-server doctor                     # read-only; must be ok before driving
control-agent-server llm preset deepseek        # deepseek-flash (active) + deepseek-pro from $DEEPSEEK_API_KEY
CID=$(control-agent-server conversation start --prompt 'Create hello.txt containing hi' --wait --print-id)
control-agent-server conversation events "$CID" --kinds ObservationEvent --contains hello.txt
control-agent-server api GET "/api/conversations/$CID" --expect 200 --save F03.get/after-run
control-agent-server evidence add --feature F03.get --result pass --entry "GET /api/conversations/{id}" \
  --expected "execution_status finished" --actual "finished" --artifact evidence/F03.get/after-run.json
control-agent-server stop                       # stops only this run; evidence stays
```

- **Which run.** Commands use `--run DIR` (anywhere on the line), else
  `$AGENT_SERVER_VERIFY_RUN`, else the only live run. With several live runs
  they refuse to guess, so export `AGENT_SERVER_VERIFY_RUN` in every shell when
  other agents share the machine: an agent that drives someone else's run
  corrupts both evidence ledgers. Never `pkill -f` or `killall` by pattern;
  `control-agent-server stop` ends only your run's process groups.
- **Launch** runs `python -m openhands.agent_server` from the checkout in its
  own process group, with cwd, `HOME`, `OH_PERSISTENCE_DIR`, the config file
  (`OPENHANDS_AGENT_SERVER_CONFIG_PATH`), tmux dir, worktree root, session key
  (`OH_SESSION_API_KEYS_0`) and secret key (`OH_SECRET_KEY`) all private to the
  run. The caller's `OH_*`, LLM and cloud variables are not inherited; pass
  what a recipe needs with `--env K=V` or `--pass-env NAME`. Useful variants:
  `--no-auth` (unauthenticated mode), `--deferred-init` (dormant until
  `POST /api/init`), `--webhook-sink` (a recording webhook receiver wired into
  the config), `--config-json '{...}'` (any other config field), `--vscode`,
  `--preload-tools`, and `--checkout PATH` (another worktree, for a baseline).
  `attach --url ... --key-env VAR` drives a server you did not launch (a Docker
  image, the PyInstaller binary, a remote sandbox); `stop` never kills it.
  Launch refuses to start with less than about 1 GB free: a run with a model
  conversation holds 300 to 600 MB.
- **Doctor** checks the process group, that this run owns its port, `/alive`,
  `/health`, `/ready`, `/server_info` (version, and that `build_git_sha` is the
  checkout's HEAD), that a missing key is rejected (401) and the run's key
  accepted, that a rotated-out key is rejected, the OpenAPI document, WebSocket
  first-frame auth (bad key closes with 4001), and tracebacks in the log. Run it
  first, after every surprising failure, and before blaming the product.
- **Drive** with `api METHOD PATH` (any REST route, `OPTIONS` and `HEAD`
  included; `--auth none|bad|previous|bearer|bearer-bad|init` for auth cases,
  `--expect` for the status, `--check FIELD OP VALUE` and `--check-header` for
  the body and headers, `--field` to chain values into shell variables, `--jar`
  for cookies, `--until-ok` to poll, `--save FEATURE/NAME` for evidence),
  `ws listen|start|read|stop` (any WebSocket;
  `--auth first-frame|query|header|none|bad|bad-query|bad-header|previous|key`,
  `--expect-reject` for close codes, replay with `--query resend_mode=all`,
  background captures that span other commands, `--wait` on `read|stop` to
  poll until the expectations hold),
  `conversation start|wait|send|events` (the essential agent pathways),
  `sink read` (what a webhook, telemetry or credential sink received),
  `state ls|cat|grep` (what the server persisted, and that secrets are not in
  it), `config` and `capabilities`, and `exec -- <program>` (an SDK or
  TypeScript-client program with `AGENT_SERVER_URL` and `SESSION_API_KEY`
  injected). `logs --grep` reads the server log with secrets redacted.
- **Arrange, don't fake.** `llm`, `fixture` and setup calls through `api` exist
  to reach preconditions (a configured profile, a git repository, a skill, a
  stdio MCP server, an HTTP sink). They never count as proof of the feature
  that consumes them; the map says which steps are proof. Never patch the
  server or mock a server response to make a live check pass. The one stand-in
  is `fixture llm-stub`: an OpenAI-compatible provider on loopback that
  replays scripted steps (`reply`, `tool`, `status`, `hang`) through the
  server's real LLM client. Use it only for provider behavior a real model
  cannot produce on demand (a 401, a 500, a hang, an exact tool call such as
  `finish` or a risky command); every happy path runs on a real model.
- **State control** reaches states a happy path never shows, without mocks:
  `restart` (same state after a restart: persistence, restore, reconnection),
  `restart --hard` (SIGKILL: crash recovery), `restart --rotate-key` (stale
  session key), `launch --share-conversations-from RUN` (a second instance on
  the same conversations: leases and takeover), `restart --env K=V` or
  `--config-json` (config changes such as `max_concurrent_runs` or idle TTL),
  `launch --deferred-init`, `launch --no-auth`, `conversation start --no-run`,
  and `ws start` before an action so its events are captured live.
- **Evidence** goes under `<run>/evidence/<feature-id>/` and the append-only
  ledger `<run>/evidence/ledger.jsonl`. Nothing is overwritten: a repeated name
  is saved as `<name>-2.json`, so cite the path the command prints;
  `evidence report` renders [the report contract](references/report.md). The
  CLI redacts the run's keys, `$DEEPSEEK_API_KEY` and cipher-encrypted values
  from all output and evidence; keys, logs and state stay in `<run>/private/`.
  Evidence is not automatically public: read it before publishing it.
- **Cleanup** with `control-agent-server stop` (add `--purge-private` to delete
  keys, state and fixtures once the evidence is checked). It signals only the
  process groups this run started (server, HTTP sinks, background captures)
  and verifies the port closed.

## LLM budget

Use `deepseek-flash` (DeepSeek V4.1 Flash) for everything that needs a model;
switch to `deepseek-pro` only for checks that need a second profile or a
stronger model. Keep prompts tiny, deterministic and confined to the
conversation's workspace ("Create hello.txt containing exactly: hi"), and
assert on tool observations and files, never on the model's wording. Stop or
delete conversations a recipe no longer needs. Without a key, run every
credential-free recipe and record model-dependent ones as `blocked` with the
missing prerequisite; never substitute a mock and call it a pass.

## Choose the job

- **Verify a change or a PR**: map the changed paths to feature IDs with
  `control-agent-server map owners --changed origin/main...HEAD`, drive every
  entry point those features list (REST, WebSocket, SDK, TypeScript client),
  and report with [the report contract](references/report.md). A behavior
  change needs a regression test as well (usually in
  `tests/cross/test_remote_conversation_live_server.py`); the recipe is the
  live proof, not a substitute for that test.
- **Verify an existing fix** (an open PR or a merged commit that claims to fix
  a `known bug` bullet): reproduce on the baseline twice and the fix twice,
  with the same flags and fixtures:
  `control-agent-server map run --file Fnn --only Fnn.x --fresh --repeat 2 --checkout <base worktree>`
  must report the bullet `xfail` in both attempts, and the same command
  without `--checkout` (the fix) `xpass` in both. Then drop the marker in the
  same PR. Baseline not reproducing twice means "inconclusive", never
  "fixed".
- **Create or extend the map**: follow [references/mapping.md](references/mapping.md).
  `control-agent-server map coverage` measures which routes no family owns or
  drives.
- **Re-prove the map**: every family's recipes are fenced `sh` blocks whose
  commands assert their own expectations, so
  `control-agent-server map run --file Fnn --fresh --record` replays a family
  on its own fresh run (with the family's `Launch:` flags) and writes ledger
  rows, and `control-agent-server map run --all --fresh --record` replays the
  whole map. Only failing bullets need judgment; `known bug` bullets report as
  expected failures until the bug is fixed.
- **Maintain the map (periodic or weekly)**: follow
  [references/maintenance.md](references/maintenance.md): a frozen BASE/TARGET,
  a route diff, a PR-intent ledger, a source wave, one live pass over every
  family, and at most one PR of proven corrections.

## Triage what you find

- **Map drift**: the map describes something the server no longer does by
  design. Fix the entry with current source and live evidence.
- **Harness gap**: the server works but the CLI cannot drive or observe it.
  Fix the CLI, re-drive.
- **Product bug**: the server is broken. Keep the evidence, search existing
  issues, and report it separately: Agent Server, SDK and the TypeScript client
  here (`OpenHands/agent-sdk`), Agent Canvas UI in `OpenHands/OpenHands`,
  scheduling and dispatch in `OpenHands/automation`. Never rewrite an expected
  result to bless broken behavior.
- **Blocked**: name the missing prerequisite (key, account, Docker, binary, OS)
  and the route you attempted. Unreachable is never a pass.
  `control-agent-server capabilities` says what this machine can drive and
  which families' `Needs:` are unmet.

## How this fits with the tests

Unit tests (`tests/agent_server/`), the in-process live-server tests
(`tests/cross/test_remote_conversation_live_server.py`), the stress suite, the
REST breakage check (oasdiff against the last release) and the TypeScript
client's endpoint audit prove contracts in isolation, with scripted LLMs. This
skill proves the assembled server as agents use it: one real process, real
persistence and restarts, real sockets and webhooks, and a real model. It does
not replace any of them.

See [references/adaptation.md](references/adaptation.md) for where these ideas
come from and what was deliberately left out.
