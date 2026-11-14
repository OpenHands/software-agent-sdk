# One owner for policy, analyzer and secrets: verification record

How the stack #6762 → #6763 → #6764 was verified. This is a temporary PR
artifact (see `AGENTS.md`, "PR-specific artifacts"); the next release PR
removes it.

- Base: `main` at `3c66af1d`, merged into all three branches.
- Code under test: part 1 `ad341922`, part 2 `115395d6`, part 3 `cac2960d`. The commit
  after `cac2960d` adds this record and changes nothing else.
- Every live attempt launches its own agent-server
  (`control-agent-server map run --fresh`); "restart" is a real process
  restart on the same state directory. Runs were wrapped in
  `/usr/libexec/docker/docker-init -s --`, because PID 1 in the sandbox is
  slow to reap orphaned tmux servers.
- Model-backed bullets used DeepSeek flash. The rest use the skill's
  scripted `llm-stub` and need no key.
- `xfail`: the bullet failed with exactly the failure its known-bug marker
  documents. `xpass`: that failure did not reproduce.
- Sections 1, 2 and 4 are one run on the commits named above. Section 3 (the
  UI) was run one merge of `main` earlier, on part 3 at `ae2e835c` against
  `main` at `91e8b5b6`; between `ae2e835c` and `cac2960d` the source of the four
  packages is unchanged (`git diff` is empty), only dependency pins and the
  feature maps moved.

## 1. The bug on `main` and the fix, on live servers

### Part 1 (#6762): a secret is saved the moment it changes

Replayed with `main`'s map (which still has the markers), against a server
from `main` and then from part 1 (`--checkout`).

| Bullet | `main` | this branch |
|---|---|---|
| `F04.create-secrets-get` | xfail, xfail | xpass, xpass |
| `F08.secrets-visible` | xfail, xfail | xpass, xpass |
| `F08.secrets-unsaved-restart` | xfail, xfail | xpass, xpass |

SDK map, `S04.secrets-update` and `S04.secrets-constructor`: fail on `main`
(`saved_after_update: []`), pass on part 1.

### Part 2 (#6763): a fork keeps the state

| Bullet | `main` | this branch |
|---|---|---|
| `F10.fork-keeps-credentials` | xfail, xfail | xpass, xpass |
| `F10.fork-run-memory` | xfail, xfail | xpass, xpass |

SDK map, `S04.fork-keeps-state`: fails 16 checks on `main`, passes on part 2.

### Part 3 (#6764): `meta.json` no longer holds a second copy

Replayed with a copy of `main`'s map in which these recipes carry a
known-bug marker, against a server from `main` and then from part 3.

| Bullet | `main` | this branch |
|---|---|---|
| `F08.state-on-disk` | xfail, xfail | xpass, xpass |
| `F08.policy-restart` | xfail, xfail | xpass, xpass |
| `F08.analyzer-restart` | xfail, xfail | xpass, xpass |
| `F08.analyzer-clear-restart` | xfail, xfail | xpass, xpass |
| `F08.secrets-override-restart` | xfail, xfail | xpass, xpass |
| `F08.fork-after-restart` | xfail, xfail | xpass, xpass |
| `F08.child-inherits-current` | xfail, xfail | xpass, xpass |
| `F08.reload-after-pause` | xfail, xfail | xpass, xpass |

## 2. Whole families on part 3's code, and the same families on `main`

| Family | this branch | `main` (`3c66af1d`), its own map |
|---|---|---|
| `F08` confirmation, security, secrets | 34 of 34 pass | 22 of 24 pass, 2 xfail |
| `F10` fork, navigate, condense, ask | 25 of 27 pass; 2 fail that also fail on `main` (`F10.ask-agent`, `F10.ask-agent-running`) | 22 of 26 pass, 2 fail, 2 xfail |
| `F04` conversation lifecycle | 36 of 37 pass; 1 xfail that `main` has too (`F04.create-initial-message-run-false`) | 35 of 37 pass, 2 xfail |
| `F12` runtime and credentials | 29 of 32 pass; 1 xfail that `main` has too (`F12.sandbox-pause-websocket`); `F12.docker-runtime` blocked (needs a Docker daemon); `F12.docker-startup-timeout` blocked (needs a Docker daemon) | 29 of 32 pass, 1 xfail, 2 blocked |
| `F31` persistence and limits | 22 of 24 pass; 1 xfail that `main` has too (`F31.takeover-keeps-metadata`); `F31.storage-retention` blocked (needs a Docker daemon) | 22 of 24 pass, 1 xfail, 1 blocked |
| `F33` Python `RemoteConversation`: run, fork, confirmation, secrets | 5 of 5 pass | 5 of 5 pass |
| `F34` TypeScript client: create, run, fork/navigate/ask, confirm | 4 of 5 pass; 1 fail that also fails on `main` (`F34.conversation-branching`) | 4 of 5 pass, 1 fail |
| `S04` local conversation (SDK map) | 26 of 27 pass; `S04.factory` labelled blocked in the map | 25 of 26 pass, 1 blocked |
| `S13` security and confirmation (SDK map) | 7 of 9 pass; the live-model bullet (`S13.llm-analyzer`, `S13.confirm-wait`, `S13.confirm-approve-reject`) fail in this run, blocked on `main`: see the note below; `S13.confirm-approve-reject`, `S13.llm-analyzer` labelled blocked in the map | 7 of 9 pass, 2 blocked |

Every bullet that does not pass on part 3 does not pass on `main` either
(compared bullet by bullet). `main` has five more that do not pass: the bullets its map marks as known bugs, which this stack fixes (`F04.create-secrets-get`, `F08.secrets-visible`, `F08.secrets-unsaved-restart`, `F10.fork-keeps-credentials`, `F10.fork-run-memory`). The three failures the two share are recipes about a side question: after `ask_agent` the event count is one higher than `F10.ask-agent`, `F10.ask-agent-running` and `F34.conversation-branching` expect, on `main` as here. I left those recipes alone; they are not about this change.

SDK maps on parts 1 and 2:

| Family | Result |
|---|---|
| `S04` local conversation (SDK map) | 25 of 26 pass; `S04.factory` labelled blocked in the map |
| `S13` security and confirmation (SDK map) | 8 of 9 pass; `S13.confirm-approve-reject`, `S13.llm-analyzer` labelled blocked in the map |

| Family | Result |
|---|---|
| `S04` local conversation (SDK map) | 26 of 27 pass; `S04.factory` labelled blocked in the map |
| `S13` security and confirmation (SDK map) | 8 of 9 pass; `S13.confirm-approve-reject`, `S13.llm-analyzer` labelled blocked in the map |

The `S13` bullet that did not pass is the live-model one (`S13.llm-analyzer`): it needs DeepSeek to rate `rm -rf scratch` as `HIGH`, and the model often says `MEDIUM`. Repeated alone four times on each side, it passed once, failed twice and was blocked once on this stack's top branch, and exactly the same on `main`. The stack does not touch how a model rates a command.

## 3. Through the Agent Canvas UI

The full OpenHands UI (Agent Canvas, built from the `OpenHands/OpenHands` repository) was run with that repository's `verify-openhands` skill (`control-openhands launch --sdk-path <checkout>`), once on `main`'s SDK and agent-server (`91e8b5b6`) and once on this branch's (`ae2e835c`), with a real model (DeepSeek flash). That is one merge of `main` before this branch's current code commit; the source of the four packages is the same in both (`git diff ae2e835c..cac2960d` is empty for them). The Canvas has no control that changes confirmation mode on an existing conversation, and it only ever sends lookup secrets, so two steps were made through the REST API: that switch, and the replacement of the secret's value. Everything else was typed and clicked in the UI, and each stack was restarted for real.

| Scenario, observed in the UI | `main` | this branch |
|---|---|---|
| Conversation switched to `AlwaysConfirm`, then a backend restart, then a new command | ran without asking, in 2 of 2 attempts | waited for approval, in 2 of 2 attempts; Continue ran it, Cancel rejected the next one |
| Secret replaced at runtime (18 bytes → 25), then a restart; length read by the agent | 18: the old value is back | 25 |
| New conversation started in confirmation mode: reject, approve, restart, next command | waits (creation-time value) | waits |
| `meta.json` read back at every step | had `confirmation_policy`, `security_analyzer`, `secrets` in 29 of 29 read-backs | had none of them in 31 of 31 |

![policy after a restart](ui-policy-after-restart.png)

![replaced secret after a restart](ui-secret-after-restart.png)

On `main` the revert is also written back: once the conversation is loaded after the restart, `base_state.json` holds the creation-time policy again. While the Add-secret form is being filled, the typed value is in that masked field; otherwise no secret value appears in the page (text and DOM), in the API events or in the conversation's state directory, on either side, and the screenshots show only lengths. An independent pass recounted every number in this table from the saved read-backs and step logs.

## 4. Checks CI runs only for PRs into `main`, run on part 3

- The exported OpenAPI document (`export_agent_server_openapi.py`) is byte-identical to `main`'s.
- REST API breakage check (`check_agent_server_rest_api_breakage.py`, with oasdiff 1.19.1): passes. What it lists against the 1.62.0 release are `main`'s own additive changes (the reaction events of #6609); with an identical OpenAPI document, this branch adds nothing to them.
- Persisted settings compatibility (`check_persisted_settings_compat.py`): passes, the payloads of the 1.62.0 release included.
- SDK API breakage check (`check_sdk_api_breakage.py`): no breaking changes in the three packages.
- `make test-server-schema`: passes.
- The agent-server code of the 1.62.0 release (tag `v1.62.0`) starts a conversation on this branch's SDK.

## 5. Reproduce

```sh
export PATH="$PWD/.agents/skills/verify-agent-server/scripts:$PWD/.agents/skills/verify-sdk/scripts:$PATH"

# the fix, from a checkout of part 3
control-agent-server map run --file F08 --fresh
control-agent-server map run --file F10 --fresh
sdk-map run --file S04 --only S04.secrets-update,S04.secrets-constructor,S04.fork-keeps-state

# the same bullets against main: a clean main worktree with its own `uv sync --dev`
control-agent-server map run --file F08 --only F08.state-on-disk,F08.policy-restart,F08.secrets-override-restart \
  --fresh --repeat 2 --checkout ../main
```
