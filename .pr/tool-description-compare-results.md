# Tool description comparison results

## Scope
Compared tool description strings rendered on `main` vs `feat/tools-j2-descriptions`.

## Commands
```
# Snapshot tool descriptions on PR branch
REPO_ROOT=/workspace/project/software-agent-sdk \
WORKSPACE_DIR=/workspace/project/software-agent-sdk/.pr/description_workspace_shared \
OUTPUT_PATH=/workspace/project/software-agent-sdk/.pr/tool_descriptions_pr.json \
/workspace/project/software-agent-sdk/.venv/bin/python .pr/tool_description_snapshot.py

# Snapshot tool descriptions on main branch (worktree)
REPO_ROOT=/workspace/project/software-agent-sdk/.pr/worktree-main \
WORKSPACE_DIR=/workspace/project/software-agent-sdk/.pr/description_workspace_shared \
OUTPUT_PATH=/workspace/project/software-agent-sdk/.pr/tool_descriptions_main.json \
/workspace/project/software-agent-sdk/.venv/bin/python .pr/tool_description_snapshot.py

# Compare snapshots and write report
/workspace/project/software-agent-sdk/.venv/bin/python .pr/compare_tool_descriptions.py
```

## Summary
All checked tool descriptions match exactly between `main` and the PR branch.

Full diffs are in `.pr/tool-description-diff.md`.
