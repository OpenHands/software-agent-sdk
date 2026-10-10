# Live verification

Started the fixed agent-server. Created a non-repository workspace root with a
repository one level below at `proj/`, then requested changes through both API
forms.

Observed:

```text
global 200 [{'status': 'UPDATED', 'path': 'proj/README.md'}, {'status': 'ADDED', 'path': 'proj/notes.txt'}]
scoped 200 [{'status': 'UPDATED', 'path': 'proj/README.md'}, {'status': 'ADDED', 'path': 'proj/notes.txt'}]
```

A non-repository parent whose only child `.git` directory is invalid still
raises `GitRepositoryError`, so `LocalWorkspace.git_changes` does not silently
report no changes.
