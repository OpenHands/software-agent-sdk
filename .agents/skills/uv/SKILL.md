---
name: uv
description: If the project uses uv, use this skill. Use this skill to create/manage Python projects and environments with `uv`, add/remove dependencies, sync a project from `uv.lock`, and run commands in the project environment.
triggers:
- uv
- uv.lock
- .python-version
- pyproject.toml
- astral uv
- python package manager
---

# uv (Python)

Use `uv` as the default tool for Python dependency + environment management when the repo has `uv.lock`, mentions `uv` in its docs/Makefile, or already uses a `.venv` created by `uv`.

## Quick decision rules

- If the repo has `uv.lock` and `pyproject.toml`: treat it as a uv-managed project.
- If the repo has only `requirements.txt`: you can still use `uv pip` for fast installs.
- Prefer **project commands** (`uv add/remove/sync/run/lock`) over raw `pip` unless the repo explicitly uses `uv pip`.

## Installation (if needed)

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## Common operations

### Initialize a new project

```bash
uv init
# or
uv init my-project
```

### Create / use a virtual environment

```bash
uv venv              # creates .venv
uv venv --python 3.11

# optional activation (not required for uv commands)
source .venv/bin/activate  # macOS/Linux
# .venv\\Scripts\\activate   # Windows
```

### Add / remove dependencies (updates pyproject.toml and uv.lock)

```bash
uv add requests
uv add 'requests==2.31.0'
uv add -r requirements.txt

uv remove requests
```

### Lock + sync (reproducible installs)

```bash
uv lock   # (re)generate uv.lock
uv sync   # create/update .venv to match uv.lock
```

If you pulled new changes and `uv.lock` changed, run `uv sync`.

### Run commands inside the project environment

```bash
uv run python -m pytest -q
uv run python main.py
uv run ruff check .
```

### Using uv as a fast pip replacement (requirements workflows)

```bash
uv venv
uv pip install -r requirements.txt
uv pip freeze
uv pip list
```

## Notes / pitfalls

- `uv` will usually auto-detect and use `.venv` in the project root.
- In CI/containers you may see `uv pip install --system`, but prefer virtualenvs for local dev.
- If a command mutates deps, prefer `uv add/remove/lock/sync` so `uv.lock` stays correct.
