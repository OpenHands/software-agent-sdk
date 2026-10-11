# Follow-up fixes after the second review pass

Four gaps were still open after `2f1aa08`: three from the earlier reviews and
one from the bot's review of `5c34e09`. `fc01624` closes the first three and the
commit after it closes the fourth. All runs below used this checkout on fresh
servers launched by `map run --fresh`, on a machine with tmux, git, node and
network but no model key.

## 1. Background fixtures died at the end of their bullet

`execute_recipe` stops each bullet's process group when the bullet ends. F19's
Preconditions started three MCP HTTP servers with `nohup ... &`, and later
bullets connect to them, so they were gone before the first of those ran.

The fix adds `control-agent-server helper start --name NAME -- CMD` (its own
session, registered with the run like sinks and captures) and
`helper stop NAME`; `stop` ends any helper left. F19 now starts its servers
with it and stops them in its last bullet.

Paired run of `map run --file F19 --only F19.test-remote-bearer --fresh`, with
one temporary local edit on both sides that was not committed:
`llm preset deepseek --no-validate` and a placeholder key, because this machine
has no DeepSeek key and neither bullet calls the model.

| Head | Preconditions | `F19.test-remote-bearer` |
|---|---|---|
| `5c34e09` (before) | pass | **fail**: `POST /api/mcp/test` answered `{"ok": false, "error": "Client failed to connect: All connection attempts failed"}` |
| this commit | pass | pass |

After the run, `run.json` lists the three `qa-f19-http-*` helpers with
`stopped_at`, and no `qa_f19_http.py` process remains.

## 2. `--only` still dropped setup for many recipes

`map check` now reports a recipe that reads a variable whose last assignment
is in an earlier recipe it does not `Require` (the runner carries line-start
assignments between bullets, which is exactly what the check follows; quoted
heredoc bodies are skipped). On `5c34e09` this flagged 175 dependencies across
29 families, 16 of them on known-bug recipes. For example,
`F04.create-secrets-get` reads `$SECID`, and with an empty value its `# bug`
line fails for the wrong reason. Those recipes now declare `Requires:`, and
`map check` is green: 34 families, 931 IDs, 0 problems. File dependencies are
not traced; F25's are declared by hand (`F25.sync`, `F25.public-repo`,
`F25.install-github`).

`map run --file F25 --only F25.sync-offline --fresh` now runs Preconditions,
`F25.sync` (pass) and `F25.sync-offline` (xfail at its marked line).

A full F19 replay then showed the check missed a recipe that reads a variable
before assigning it itself (`F19.oauth-refresh-writeback` checks `$TOKEN`,
then sets it), and `--env CID="$CID"` looked like a local assignment. The
check now counts a read as outside when it comes before the recipe's own
binding. That found six more (`F19.oauth-refresh-writeback` and five F33
recipes that pass `$CID` to a program), now declared. F19 also had
server-state dependencies (the stored `qa-env`, `qa-oauth` and `qa-f19`
servers and the OAuth webhook sink), declared by hand:
`map run --file F19 --only F19.conversation-secrets-at-rest,F19.restart --fresh`
runs 10 recipes, all pass. Before, the same selection failed at
`F19.oauth-refresh-writeback`.

## 3. Machine needs were family-wide

A recipe can declare its own `Needs:` line next to `Requires:`. `map run`
reports it, and every recipe that requires it, as `blocked` with the missing
capability, and drives the rest of the family. `capabilities` lists these per
family. F02 moves `tmux` to the seven recipes that run commands, and F25 keeps
`git` at family level with `llm`, `network` and `node` on the recipes that use
them.

`map run --file F25 --fresh --record` without a model key: **25 pass,
7 xfail (every known bug reproduced at its marked line), 2 blocked**
(`F25.conversation-activation` and `F25.invoke-skill`, missing `llm`), 0 fail.
Before this commit, `capabilities` and `map run --all` reported all of F25 as
blocked on such a machine.

## 4. Encrypted round trips in F09 and F19 lost their token

Since `2f1aa08`, `--field` redacts by default, so a recipe that reads a
`gAAAAA` token to post it back gets `<redacted:encrypted>` instead. F18 and
F20 opted in with `--unsafe-unredacted`; F09 and F19 did not. The review on
`5c34e09` named four reads (F09 `config.api_key`, `config`, `agent_settings`
and F19's `QA_F19_TOKEN`). Two more in F19 read the `oauth_state` of a
finished OAuth job, whose tokens are also `gAAAA` ciphertext. The two
`plaintext` reads of the stored OAuth access token opt in too: redacted, a
regression that returned ciphertext would turn into `<redacted:encrypted>`
and slip past the `test "${TOKEN#gAAAA}" = "$TOKEN"` check. Evidence stays
redacted (no raw token in any saved file of these runs).

Paired runs with `--fresh` and the same temporary preset edit as in 1:

| Recipe (`--only`) | `fc01624` (before) | this commit |
|---|---|---|
| `F09.auth-required` | Preconditions **fail** at `test "${ENC#gAAAAA}" != "$ENC"` | pass |
| `F19.test-encrypted-env` | **fail** at `test "${ENC#gAAAA}" != "$ENC"` | pass |
| `F19.oauth-stored-state` | **fail**: `/api/mcp/test` with the redacted state times out on tool listing | pass |

Also passing after the change: `F09.load-plugin` (posts the encrypted
settings back with `secrets_encrypted: true`), `F09.switch-llm-registered-usage-id`
(xfail at its marked line), and `F19.oauth-inline-writeback` and
`F19.oauth-refresh-writeback`.

Full replays without a model key (`--keep-going`): F19 has 24 pass, 3 xfail
and 2 fail, and F09 has 13 pass, 6 xfail and 10 fail. Every failure is a
recipe that needs a model turn (or an ACP session), or one that reuses such a
recipe's conversation. F19's `F19.restart` failed in that full run only:
after the restart, the stored `qa-oauth` state had no tokens.
`F19.conversation-tool` had stopped mid-turn and left its conversation
open. The server log shows MCP OAuth flow errors ("OAuth client not found")
right after SIGTERM, and `settings.json` was rewritten at that moment, so
shutting down with that conversation open appears to have written the state
back without its tokens (inferred from the log and the file's write time,
not reproduced on its own). With a working key the recipe deletes its
conversation first, and without that conversation the same restart passes
(the selection above). It may deserve its own recipe later.

## Checks

- `uv run pytest tests/cross/test_verify_agent_server_skill.py`: 39 passed
  (5 new: helpers outlive their bullet until `stop`; helper errors; the
  dependency check; reads before a recipe's own binding; recipe-level needs
  and `capabilities`).
- `pre-commit` on the changed Python files: ruff format and lint,
  pycodestyle, pyright all pass.
- `map check`: ok, 34 families, 931 IDs. `map coverage`: ok, 194 of 204
  routes owned and driven.
