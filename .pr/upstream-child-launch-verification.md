# Child launches in fresh workspaces — upstream verification

Verified 2026-10-24 on macOS with Python 3.13.11, SDK/Agent Server 1.58.1 and frozen development dependencies.

Base: `acd96e6fa424c02432304395b9208f99be24b54d`.
Verified head: `05e56fdc0e919c5a6898c44293649a84086997d5`.

## Reproduction

The source regression was first applied by itself to the upstream base. It starts a real uvicorn server, creates a normal parent over HTTP in an empty workspace, confirms that startup initialized Git without a commit, and posts a default child request. This fails with HTTP 500 from `git worktree add ... HEAD`; the base run reports one failed test in 4.52 seconds.

On the fixed head, the same HTTP workflow returns 201 and shared isolation, finishes the child, preserves the parent/child relationship, original file, Git status and unborn HEAD, and deletes both conversations. The repository's fixture patches the LLM; HTTP, server routing, lifecycle and Git are real. This pass does not claim a new external-model, Docker or benchmark run. Original Linux/model evidence remains in `issue-42-evidence.md` at its recorded source revisions.

## Validation

```sh
uv sync --frozen --dev
uv run --frozen pytest \
  tests/cross/test_remote_conversation_live_server.py::test_child_launch_in_fresh_workspace_preserves_unborn_repository \
  tests/agent_server/test_parent_child_conversations.py \
  tests/tools/child_conversation/test_start_child_conversation.py \
  tests/agent_server/test_conversation_service.py -k 'child or worktree' -q
```

Result: **45 passed, 128 deselected, two existing deprecation warnings**, in 6.10 seconds. All changed-code pre-commit checks passed, including Ruff, pycodestyle, Pyright, import rules and tool registration. The added explicit-ref regression also passed its independent 11-test worktree run.

The newer upstream worktree base options and custom branch prefix are preserved. Tests cover default/head/explicit-ref branch selection, committed repositories, subdirectories, remote-default tracking despite unborn local HEAD, and a missing explicit ref raising without creating conversation or worktree artifacts. The fallback only handles a missing usable default/head commit; other Git failures propagate. A separate reviewer found no material correctness or compatibility issue.

The final evidence commit changes no product or test code. Test fixtures owned and stopped their server; no persistent service was started by this pass.
