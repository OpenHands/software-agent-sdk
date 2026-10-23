# Issue #42: default child launch in a fresh workspace

Product commit tested: `b2c0490647963cb877d1f0c20394311fa245d6d4`. Baseline: `5966c781ed014efc96c028a99bddd4ff78426741`. The later evidence commit changes no product code. [Structured observations](issue-42-evidence.json).

Normal parent creation initializes Git without a commit. Before this patch, the default child request returned 500 from `git worktree add ... HEAD` on two fresh servers. Explicit shared children succeeded in the same runs. The new real HTTP regression also failed with that exact error before the patch.

The fix validates that the selected default branch or HEAD resolves to a commit before planning a worktree. It retains the selected ref when creating the worktree so Git preserves remote upstream tracking. If no commit resolves, it uses the existing shared-workspace fallback and reports `isolation: shared`; it creates no commit, branch, or worktree. Other Git errors still propagate. A usable remote default branch still creates an isolated worktree even when local HEAD is unborn. Explicit shared isolation bypasses planning. The request and tool descriptions explain the fallback.

## Automated regression checks

```sh
make build
export PATH=/workspace/.tools/tmux/usr/bin:$PATH
docker-init -s -- uv run pytest \
  tests/cross/test_remote_conversation_live_server.py::test_child_launch_in_fresh_workspace_preserves_unborn_repository \
  tests/agent_server/test_parent_child_conversations.py \
  tests/tools/child_conversation/test_start_child_conversation.py \
  tests/agent_server/test_conversation_service.py -k 'child or worktree' -q
```

Result: **41 passed**. Coverage includes actual HTTP parent startup/child completion, unchanged parent file/index/unborn HEAD, committed repositories, subdirectory workspaces, a usable remote default with unborn local HEAD, explicit shared isolation, capacity, and tool serialization/dispatch. File-scoped pre-commit checks pass.

## Real server and model verification

The maintenance CLI from [PR #43](https://github.com/enyst/agent-sdk/pull/43) drove the fixed checkout after the product commit was created. Each run's `run.json` recorded the exact product SHA above. Run with that CLI on PATH:

```sh
docker-init -s -- control-agent-server map run --file F04 \
  --only F04.child-start,F04.child-unborn,F04.child-errors,F04.child-capacity \
  --checkout /workspace/agent-sdk-fix-42 --fresh --repeat 2 --record --keep-going
```

| Revision | Run | Default child | Other child controls |
|---|---|---|---|
| Baseline | `20261023-060500-1c1a-map-f04` | HTTP 500 | Shared child succeeds |
| Baseline | `20261023-060518-691c-map-f04` | HTTP 500 | Shared child succeeds |
| Fixed | `20261023-071012-2856-map-f04` | HTTP 201, shared, finishes | All pass |
| Fixed | `20261023-071101-a644-map-f04` | HTTP 201, shared, finishes | All pass |

The fixed runs report **XPASS** for `F04.child-unborn`, as expected because its maintained known-bug marker is intentionally unchanged in this product PR. The process exit code is therefore 1; all recipe assertions succeed. The other recipes pass, including real-model worktree/non-Git/shared launches and full-capacity rejection before filesystem side effects.

An additional fresh real-model run (`20261023-071023-f9ef-fix42-default-child-tool`) attached `start_child_conversation` explicitly. The parent called it once without an isolation argument. Its observation reported a successful child with `isolation: shared`; that child finished and appeared in the parent's child list. The parent repository remained unborn, and its preexisting file survived deletion of both conversations.

All live runs used isolated local Linux agent-server processes, `docker-init -s` for process reaping, Git 2.52.0, and DeepSeek flash. The change uses existing Git commands and requires no newer Git feature. The real-model checks cover the local server/tool path; they do not claim a benchmark evaluation or a Docker runtime test.

## Independent review and superseded first version

The first product version, `46ba8bda118cdb35dd596f8f9f275a7fdfe7face`, passed the unborn-child live checks (`20261023-065915-6ccd-map-f04`, `20261023-070005-f6ae-map-f04`) and the first tool check (`20261023-070042-9bc3-fix42-default-child-tool`). Independent review then caught that using a resolved commit hash to create a worktree dropped the remote branch's upstream tracking. Those earlier observations remain under `earlier_revision` in the structured evidence and are not the final proof.

An upstream-tracking assertion added to the existing remote-default regression failed on that first version with “no upstream configured.” The final correction validates the commit but passes the original selected ref to Git; the assertion now passes, together with all 41 targeted tests. Independent final source review found no remaining material findings. The two REST runs and tool run reported above were repeated after committing that correction, at the final product SHA.
