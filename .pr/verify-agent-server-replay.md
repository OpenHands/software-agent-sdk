# Full fresh replay of the verify-agent-server map

Commit: `f688c8d` (this PR's head), 9 October 2026, Linux container without Docker, with a DeepSeek key for the model-backed bullets.

Command, run family by family so each one gets its own fresh server with its `Launch:` flags:

```sh
control-agent-server map run --all --fresh --keep-going --record
```

Result: **no failures and no unexpected passes** in any of the 34 families. Counts are evidence-ledger rows (one per sub-feature ID a bullet names):

| Family | Pass | Expected failure (known bug) | Blocked |
|---|---:|---:|---:|
| F01 | 11 | 0 | 0 |
| F02 | 19 | 2 | 0 |
| F03 | 19 | 1 | 0 |
| F04 | 25 | 7 | 0 |
| F05 | 27 | 4 | 0 |
| F06 | 23 | 5 | 0 |
| F07 | 22 | 2 | 0 |
| F08 | 20 | 3 | 0 |
| F09 | 19 | 9 | 0 |
| F10 | 19 | 6 | 0 |
| F11 | 23 | 2 | 0 |
| F12 | 26 | 3 | 1 |
| F13 | 24 | 5 | 0 |
| F14 | 23 | 7 | 0 |
| F15 | 22 | 5 | 1 |
| F16 | 22 | 3 | 1 |
| F17 | 21 | 3 | 0 |
| F18 | 24 | 8 | 0 |
| F19 | 25 | 3 | 0 |
| F20 | 29 | 6 | 0 |
| F21 | 28 | 4 | 0 |
| F22 | 19 | 5 | 0 |
| F23 | 21 | 5 | 0 |
| F24 | 17 | 2 | 1 |
| F25 | 26 | 7 | 0 |
| F26 | 21 | 4 | 0 |
| F27 | 26 | 5 | 0 |
| F28 | 24 | 5 | 0 |
| F29 | 23 | 6 | 0 |
| F30 | 22 | 4 | 0 |
| F31 | 18 | 4 | 1 |
| F32 | 24 | 6 | 0 |
| F33 | 36 | 6 | 1 |
| F34 | 23 | 9 | 0 |
| **Total** | **771** | **156** | **6** |

- **Expected failure** means a known-bug bullet reproduced its bug at the command marked `# bug`. Each one has an issue; they are listed in #5653.
- **Blocked** bullets are marked blocked in the map and are not executed: `F12.docker-runtime` (needs Docker), `F15.repo-search-github` (needs a GitHub token), `F16.vscode-running` (needs the VS Code server binary), `F24.login-complete` (needs a ChatGPT account and a human to sign in), `F31.storage-retention` (needs Docker), `F33.workspace-runtime-lifecycle` (needs Docker).
- `map coverage`: 193 of 203 routes owned and driven, 10 excluded with reasons. `map check`: 930 sub-feature IDs, all recipes parse.
- `uv run pytest tests/cross/test_verify_agent_server_skill.py`: 11 passed.
