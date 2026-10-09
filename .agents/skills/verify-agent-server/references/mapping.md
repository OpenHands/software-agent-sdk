# Creating and extending the feature map

The map is written for the next agent, read cold and mid-task by someone who
has never seen the server. Every entry answers, from an API consumer's point of
view: what the feature is, which routes and sockets carry it, which SDK and
TypeScript client methods reach it, how to drive it with
`control-agent-server`, and what observable end state proves it works. A recipe
that was never executed is a draft, not an entry.

## 0. Build or check the lever first

Before mapping anything, prove the CLI can carry one feature end to end:
`launch --new`, `doctor`, drive one mapped recipe, save an exchange with
`--save`, `evidence add`, `stop`, and confirm the evidence file still exists
after `stop`. If any step fails, fix the CLI or report the environment blocker
before writing entries: a map written against a broken harness teaches wrong
steps.

While mapping, every time you reach for an ad-hoc script, a raw `curl`, a
Python snippet or a manual `websocat` to drive or observe an agent path, stop
and add a verb or flag to `scripts/control_agent_server.py` instead (documented
in its `--help` with an example). The CLI is what makes the map rerunnable. If
the verb exists already, use it.

## 1. Inventory agent-facing surfaces

Work from what a consumer can call, and require a concrete source path for
each candidate. Sources, roughly in order of yield:

1. **Routes**: `control-agent-server map routes` imports this checkout's app and
   lists every HTTP route and WebSocket (`--module <router>` narrows it). The
   OpenAPI document does not describe WebSockets or `include_in_schema=False`
   routes; the route table does. It also builds the app in Docker runtime
   mode and adds the routes that exist only there (marked `config`). Every
   route is owned by exactly one family or listed under "Not mapped" with a
   reason.
2. **Routers and services**: `openhands-agent-server/openhands/agent_server/*_router.py`
   and the services they call. Read request and response models in `models.py`
   and the SDK models they reference: field names in recipes must be real.
3. **WebSocket protocols**: `sockets.py`, `session_socket.py`,
   `session_protocol.py`: auth (first frame, deprecated query parameter,
   header), replay (`resend_mode`, `after_timestamp`), frames in both
   directions, close codes.
4. **Configuration that changes API behavior**: `config.py` (`OH_*`
   variables): webhooks, deferred init, session keys, `max_concurrent_runs`,
   idle TTL, retention, VS Code, browser, CORS. Each is reachable with
   `launch --config-json`, `--env`, or `restart --config-json`.
5. **Consumers**: the SDK's `RemoteConversation` and `RemoteWorkspace`
   (`openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py`,
   `openhands-sdk/openhands/sdk/workspace/remote/`), the TypeScript client
   (`clients/typescript/src`), and `examples/02_remote_agent_server/`. A
   consumer method is an entry point: list it in the family.
6. **Errors and edge states**: `HTTPException` details, status codes in the
   handlers, `deprecated=True` routes and their removal versions. These are the
   states a happy path never shows.
7. **Existing tests** (`tests/agent_server/`, `tests/cross/`) are references for
   request shapes and expected behavior, not proof.

`control-agent-server map coverage` lists routes no family owns
(`unowned`), owned routes no recipe drives (`owned_but_not_driven`), and
routes claimed twice. Drive `unowned` to zero, or list each exclusion with its
reason in the index's "Not mapped" section.

## 2. Group into families

One file per agent-facing job ("Conversation lifecycle", "LLM profiles",
"Bash commands"), not per router. Aim for files an agent can verify in one
sitting: usually 5 to 20 sub-features; split beyond about 30. Name files
`Fnn-short-slug.md` and give the family the ID `Fnn`. Keep IDs stable forever:
when a route or field moves, update the text, not the ID; when a feature is
removed, mark it `retired` with the authorizing PR instead of deleting the row.

A **sub-feature** is one observable behavior with its own pass/fail, such as
"a paused conversation stays paused after a restart" or "an unknown id returns
404". IDs look like `F03.pause`: lowercase, hyphenated, unique across the map,
prefixed by the family ID. Settle the IDs before taking evidence, and write the
`## Sub-features` list into the file first: evidence and ledger rows are filed
under them (`evidence add` and `map check` reject unknown IDs), and renaming
later means re-proving.

Global and conversation-scoped variants of the same operation (for example
`/api/bash/...` and `/api/conversations/{id}/bash/...`) belong to the family
that owns the operation; the scoped variant is a second entry point with its
own sub-feature when its behavior differs (which directory or container it
acts on).

## 3. Write each entry

Each file starts with an H1 title, one paragraph describing the agent-visible
behavior, then:

- one `Source:` line with the main implementation paths in backticks (routers,
  services, models; `map owners` uses these prefixes to route changed files to
  families, so list directories or files, not symbols), and
- an optional `Needs:` line naming machine prerequisites from a fixed
  vocabulary (`llm`, `tmux`, `git`, `node`, `uvx`, `docker`, `chromium`,
  `vscode`, `network` for outbound HTTPS to github.com or a provider), so
  `control-agent-server capabilities` and `map run --all` can tell a blocked
  family from a failing one,
- an optional `Launch:` line with the `launch --new` flags the family needs
  (`` Launch: `--deferred-init` `` or `` Launch: `--webhook-sink --config-json '{"max_concurrent_runs": 1}'` ``);
  `map run --fresh` and `map run --all --fresh` launch the family's run with
  them, and
- one `Routes:` line listing every route this family owns, each in backticks
  exactly as `map routes` prints it (`GET /api/conversations/{conversation_id}`,
  `WS /sockets/events/{conversation_id}`), or `Routes: none` for a
  behavior-only family (webhooks, SDK lane). The `Routes:` paragraph may wrap
  over several lines; it ends at the first blank line.

Then exactly four H2 sections, in this order:

1. `## Sub-features`: one bullet per ID:
   `` - `F03.pause`: pausing a running conversation stops it and reports `paused`. ``
2. `## How to get to it (agent POV)`: every entry point, as a consumer meets
   it: the REST route, the WebSocket, the SDK method
   (`RemoteConversation.pause()`), the TypeScript client method, the config
   field or environment variable, and (for context only) the Agent Canvas
   surface that calls it. Note which entry point each recipe exercises.
3. `## Driving it with control-agent-server`: starts with `Preconditions:`
   (run flags, LLM profile, fixtures, earlier bullets) as bullets, then
   labeled bullets that pair one consumer action with its commands, in a
   fenced `sh` block inside the bullet, and the observable result in prose:

   ````markdown
   - **Pause (`F04.pause`).** Pause a running conversation.
     ```sh
     CID=$(control-agent-server conversation start --prompt 'Run: sleep 30' --print-id)
     control-agent-server conversation wait "$CID" --until running --timeout 60
     control-agent-server api POST "/api/conversations/$CID/pause" --expect 200 --save F04.pause/pause
     control-agent-server conversation wait "$CID" --until paused --timeout 30
     control-agent-server api GET "/api/conversations/$CID" --check execution_status eq paused
     ```
     The wait reports `paused` and a GET confirms it.
   ````

   The map is executable: `control-agent-server map run --file F04` runs every
   `sh` block of the family in order, in one run, carrying shell variables
   (`CID=...`) from bullet to bullet, and fails a bullet when any command exits
   non-zero. So encode each expectation in the command: `--expect` for the
   status, `--check FIELD OP VALUE` for the body (`eq`, `ne`, `lt`, `ge`,
   `contains`, `matches`, `exists`, `missing`, `len-eq`, ...),
   `--expect-kind`/`--contains` for events and frames, `--expect-min` for sink
   deliveries, and plain `test` for files. Prose states what the reader should
   see; the commands prove it. Inline code in prose is never executed, so
   expected values and partial commands can stay inline. A `sh` block under
   `Preconditions:` is executed first.

   Two label markers keep the map honest without turning it red forever:
   a bullet whose bold label says **known bug** (for example
   `**Stale secrets after update (`F08.secrets-visible`), known bug #1234.**`)
   asserts the correct behavior and is reported as an expected failure
   (`xfail`) while the bug lasts; when it starts passing (`xpass`) `map run`
   fails so the marker gets removed. The command that fails because of the
   bug ends its line with `# bug`; `map run` counts the bullet as reproduced
   only when that command is the one that fails, and reports `fail` when an
   arrange step breaks first (`map check` requires the marker). Write a
   negative bug assertion on one line, as `if cmd; then false; fi  # bug`.
   A bullet whose label says **blocked**
   names its prerequisite in prose, is reported as `blocked`, and is not
   executed. Later bullets never depend on variables set in either kind.

   Include the read-only second view for every mutation (a GET, a WebSocket
   frame, a file on disk, a sink delivery), the error cases that matter (404
   for unknown ids, 422 for bad bodies, 401 without a key, 409 for
   conflicts), and `--save` on the exchange that proves the result.
4. `## Gotchas`: traps that waste or invalidate a run: async transitions that
   need a wait, fields that only appear after the agent runs, behavior that
   depends on config, platform or installed binaries, and known open issues
   (link them; they are repro candidates, not exemptions).

Treat commands as literal: quote JSON with single quotes, keep names unique to
the run (prefix fixtures with `qa-` or `QA_`), and restore shared state after
mutations (delete the secret, re-activate the default profile) unless the next
recipe depends on it, in which case say so. Values that differ per run
(conversation ids, event ids, command ids) are captured into shell variables
with `--print-id`, `--field` or `--print-path`, never pasted as literals:
`CID=$(control-agent-server conversation start ... --print-id)`.

The driving bullets must run top to bottom on one run in one shell, because
the next agent will run them that way:

- Put every arrange step in `Preconditions:`, in execution order. A
  precondition with a short life (a conversation that is running) says when to
  create it: "right before the Pause bullet".
- A bullet never depends on state that a later bullet creates, and never
  deletes a fixture that a later bullet needs.
- Write environment-dependent results as conditionals ("if `tmux` is
  installed, ...; otherwise 503 with ...") and check them, not from memory.
- After any mutation, read the stored value back through a second route; a
  write that returned 200 but stored nothing is a classic API bug.
- Async results (a run finishing, a webhook flush, a background bash command)
  are awaited with `conversation wait`, `ws start`/`ws stop`, or `sink read
  --expect-min`, never with a bare `sleep` unless the server's own timer is
  the thing under test (say so).
- Assert deterministic facts (status codes, fields, files the agent wrote, tool
  observations, event kinds), not the model's wording.
- Secret checks assert what the server returns or stores, not what the model
  says: models refuse to echo secrets. Compare inside the command
  (`control-agent-server api GET ... --check value eq "$EXPECTED"`, or
  `state cat ... --not-contains "$SECRET"` for storage at rest) and never print a
  real key.

### Recipe pitfalls that reviews keep finding

- `! cmd` never fails under `set -e`. Write `if cmd; then false; fi`, or
  assert with `--check`/`--expect` (`map check` flags it).
- A bold label must close on its own line; a wrapped `- **...` silently
  merges the bullet into the previous one (`map check` flags it).
- A negative assertion needs a positive control on the same server: show the
  check can pass (the right key gets in, the cookie loads the file) before
  showing the wrong input is refused.
- "Stayed open" proves nothing when the server waits for a frame. Send the
  frame that would be refused if the behavior were wrong, and wait for the
  reply (`--until-kind`, `--expect-kind`).
- Timing needs both bounds: a timeout check with only a lower bound passes
  for any longer timeout (`--expect-max-ms`, or `test $((SECONDS - T0)) -le N`).
- A refused request must also leave no trace: no `Set-Cookie` on a 401
  (`--check-header set-cookie missing`), no new file, no event.
- A mutation is proven by a second read, not by its own response.
- Long or racy waits use the CLI's waits (`conversation wait`,
  `ws stop --wait`, `api --until-ok`, `sink read --expect-min`), never a bare
  `sleep` before an assertion.
- Large bodies: `--quiet` keeps output small while `--check` still sees the
  whole body; `--raw-out` keeps the bytes.
- A request that must be in flight while another command probes the server
  runs in the background (`control-agent-server api ... &`, then `wait`);
  `map check` parses it. Values from shell variables (`--expect-count "$N"`)
  are type-checked only when the recipe runs.
- A model may answer with a message or through the `finish` tool. Prove a
  model turn by what it leaves either way: completion tokens in
  `stats.usage_to_metrics`, or an agent `MessageEvent` or `ActionEvent`.
- Never point a run at a shared path (`--env TMUX_TMPDIR=/tmp/x`, a fixed
  port, a workspace outside `$AGENT_SERVER_VERIFY_RUN`): every run already
  has its own, and a shared one couples parallel replays.

## 4. Prove every recipe live

Execute every command you wrote, in order, on a fresh `launch --new` (or a run
you have doctored since its last surprise). Before you hand the file over, run
the whole file once more from the top, exactly as written, with
`control-agent-server map run --file Fnn --fresh --record`; it launches a
fresh run with the family's `Launch:` flags, replays every bullet, stops the
run, and must pass end to end. Model-backed families run
`control-agent-server llm preset deepseek` in a `sh` block under
`Preconditions:`, so a fresh replay needs nothing but the key in the
environment. A bullet that needs a second server launches it inside its own
`sh` block (`B=$(control-agent-server launch --new --print-run ...)`, then
`--run "$B"`) and stops it before the bullet ends.

For each sub-feature record
`control-agent-server evidence add --feature <ID> --result pass|fail|blocked|not-run`
with the entry point, expected and actual result and the saved artifacts.

- A recipe that fails because the instructions are wrong: fix the
  instructions and re-drive (map drift).
- A recipe the CLI cannot express: check `control-agent-server --help` and the
  sub-command help first, then extend the CLI and re-drive (harness gap).
- A recipe that fails because the server is broken: keep the expected result,
  record `fail` with evidence and report the product bug separately.
- A recipe that needs something the run cannot have (a Docker daemon, a cloud
  account, an OAuth provider, a GitHub token, macOS): mark it `blocked`, name
  the prerequisite in `Preconditions:` and keep the recipe as far as it can be
  written.

`blocked` is the most common wrong verdict. Before you write it, try the
cheaper routes earlier mappers missed:

- A fixture may already exist: `control-agent-server fixture list` (git
  repository, skill, stdio MCP server, HTTP sink).
- A local stand-in often exercises the same code path as the external service:
  a local git repository instead of GitHub, a stdio MCP server instead of a
  hosted one, a local directory instead of a marketplace URL, an HTTP sink
  instead of an automation service.
- Most server modes are a launch flag away: `--no-auth`, `--deferred-init`,
  `--config-json`, `restart --env`.
- When a path is "blocked because every X needs Y", list every X by its real
  attributes first; one of them usually does not need Y.
- Translate the environment before giving up: restate the behavior without
  platform nouns ("a Docker sandbox" may only mean "a runtime that is not
  local"; "GitHub" may only mean "a git remote") and test it with the
  stand-in when the stand-in exercises the same code path. Label such
  bullets as translated in prose; when the missing environment is itself the
  behavior under test, it is genuinely blocked.

## 5. Check the map

```sh
control-agent-server map check --file F03   # one entry while others are being written
control-agent-server map check              # whole map: structure, IDs, index, commands parse, routes exist
control-agent-server map coverage           # routes no family owns or drives
control-agent-server map ids                # every ID with its file, for the index table
```

`map check` parses every `control-agent-server ...` command in the map with the
real argument parser (so a renamed flag fails the check), verifies every route
on a `Routes:` line and every `api METHOD PATH` call against the app's route
table, and checks the index counts. Whoever owns the index
([feature-map/README.md](feature-map/README.md)) adds the new file to the
families table with its entry points, prerequisites and ID count, and lists
"Not mapped" reasons; `map check --fix-counts` rewrites the counts and the
total. When several agents map families in parallel, each edits only its own
file and checks it with `--file`; one coordinator updates the index afterwards.
Then hand the evidence ledger (`control-agent-server evidence report`) to
whoever asked, with fail and blocked rows first.
