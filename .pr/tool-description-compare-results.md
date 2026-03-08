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

## Summary of differences
The comparison shows several byte-level text changes (mostly whitespace), plus a couple of substantive ordering/escaping changes:

- **apply_patch**: single-line description in `main` becomes two lines in PR (line break inserted).
- **grep**: example regex string now renders with double backslashes (`\\s+\\w+`) instead of single (`\s+\w+`).
- **file_editor / planning_file_editor (vision enabled)**: the image-viewing bullet moved **above** the text-file bullet compared to `main` (ordering change). Also extra blank lines appear around the conditional block.
- **file_editor / planning_file_editor (vision disabled)**: an extra blank line is inserted where the conditional block was removed.
- **gemini read/write/edit/list + glob**: blank line before the “Your current working directory…” footer is removed compared to `main`.
- **browser_use tools + terminal**: trailing whitespace/newline differences only (content text otherwise identical).

Full diffs are in `.pr/tool-description-diff.md`.
