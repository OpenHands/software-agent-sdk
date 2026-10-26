# Embedded-path skill source migration verification

Verified 2026-10-24 for [upstream #6184](https://github.com/OpenHands/software-agent-sdk/issues/6184), migrated from [enyst/agent-sdk#39](https://github.com/enyst/agent-sdk/issues/39) and [source PR #44](https://github.com/enyst/agent-sdk/pull/44).

## Revision provenance

- Upstream base: `acd96e6fa424c02432304395b9208f99be24b54d`.
- Source product commit: `31897798e8bd8e62ef56bdb86dba6588efbbc557`; source evidence commit: `1973797e6a6d8ff7ecc9993cf990663d918c7aa1`.
- Migrated product commit: `3536c7e06`; migrated source-evidence commit and tested head: `a92344a54`.
- Both source commits applied cleanly with `git cherry-pick -x`. Original author and coauthor remain intact.
- Subsequent migration-evidence files contain no product changes. The source fork issue, PR, and branch were not modified.

## Current upstream reproduction

The real source-run Agent Server, Python 3.13.11 on macOS arm64, handled requests over HTTP and cloned the public GitHub repository. Each run used its own temporary persistence, conversations, workspace, bash-event directory, process and unused loopback port; the owned server process was stopped afterward. No model calls were needed.

Both source forms pinned the same public extensions commit, `26047067ecd0230d6ce4e15016cbdf786798cba5`. The exact POST bodies were:

```json
{"source":"github:OpenHands/extensions","repo_path":"skills/github","ref":"26047067ecd0230d6ce4e15016cbdf786798cba5"}
{"source":"github:OpenHands/extensions/skills/github","ref":"26047067ecd0230d6ce4e15016cbdf786798cba5"}
```

The workflow POSTs `/api/skills/install`; a successful response is followed by GET then DELETE of `/api/skills/installed/github` before trying the next source form.

| Revision | Fresh run | Explicit path control | Embedded path |
| --- | --- | --- | --- |
| Upstream base | 1 | 200, `github` | 422, invalid skill |
| Upstream base | 2 | 200, `github` | 422, invalid skill |
| Migrated head | 1 | 200, `github` | 200, `github` |
| Migrated head | 2 | 200, `github` | 200, `github` |

All successful installs also returned 200 for readback and uninstall. [Sanitized request/response evidence](migration-39-live-evidence.json) ties each exchange to its tested revision, source form and resolved extensions commit.

Reproduce with `make build`, then start `uv run python -m openhands.agent_server --host 127.0.0.1 --port <unused-port>` with `OH_PERSISTENCE_DIR`, `OH_CONVERSATIONS_PATH`, `OH_WORKSPACE_PATH` and `OH_BASH_EVENTS_DIR` pointing inside a fresh temporary directory. Set `OH_TELEMETRY_CONSENT=false`. Send the JSON bodies above to the install endpoint, reading and deleting successful installs between them. Repeat with a fresh process and directory on base and head.

The original source PR's separate `fix-39-*` artifacts record Linux/Python 3.13.5 evidence. This migration independently exercises the same public entry point on macOS. Neither run claims packaged binary or Docker-image validation. No packaging, dependency, external executable requirement, public request shape, or persisted schema changes.

## Focused verification on migrated head

`make build` completed, using an isolated worktree virtual environment.

```sh
uv run pytest tests/sdk/extensions tests/sdk/skills/test_installed_skills.py \
  tests/sdk/plugin/test_installed_plugins.py -m 'not network' -q
# 204 passed, 3 network tests deselected

uv run pytest tests/agent_server/test_skills_router.py \
  tests/agent_server/test_skills_service.py -q
# 98 passed

uv run pre-commit run --files \
  openhands-sdk/openhands/sdk/extensions/fetch.py \
  openhands-sdk/openhands/sdk/extensions/installation/manager.py \
  tests/sdk/skills/test_installed_skills.py
# all applicable hooks passed, including Pyright
```

The installed-skill regression matrix covers real Git clone/update behavior for embedded paths (including trailing slash), explicit paths and repository roots. It preserves names, original source metadata and disabled state through updates. Existing extension/plugin coverage checks conflicting paths, containment, local paths, manifestless repository names and legacy update names. The network-marked tests are complemented by the real GitHub HTTP workflows above.

## Duplicate and predecessor review

Open and closed upstream search covered `subpath`, `embedded-path`, `repo_path`, `Invalid skill`, repository-root naming, the introduced resolver name and source issue/PR URLs. No competing report or patch for this post-fetch 422 regression was found before opening #6184.

- #5895 / #5909 fixed the earlier 400 parser failure. #5909 merged as `98c97e6f18cecdaec96c011efb00f43db5b63bcc`.
- #5896 / #5905 corrected names for actual remote repository roots. #5905 merged later as `7bb4be18ca42bf0e44748183baa6a56eb329621a` and introduced the naming path that misclassifies the selected skill directory.
- #6008 / #6009 concern manifestless marketplace discovery, not installation of an embedded skill source.

Source PR #44 has no review submissions or inline review comments. Its posted notes are draft-review skipping, temporary-artifact notice and successful REST compatibility results. Source checks do not substitute for this migration's checks; upstream CI still needs to run on the published head.
