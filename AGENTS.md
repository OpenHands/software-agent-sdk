# Repository Guidelines

## Project Structure & Modules
- `openhands-sdk/` core SDK; `openhands-tools/` built-in tools; `openhands-workspace/` workspace management; `openhands-agent-server/` server runtime; `examples/` runnable patterns; `tests/` split by domain (`tests/sdk`, `tests/tools`, `tests/agent_server`, etc.).
- Python namespace is `openhands.*` across packages; keep new modules within the matching package and mirror test paths under `tests/`.

## Setup, Build, and Local Runs
- `make build` installs dev deps with `uv sync --dev` and sets up pre-commit (requires Python 3.12).
- `make build-server` builds the agent-server executable via PyInstaller into `dist/agent-server/`.
- Use `uv run ...` for tool execution inside the managed venv (e.g., `uv run python examples/01_standalone_sdk/main.py`).

## Coding Style, Linting, and Types
- Formatting/linting: Ruff (via `make lint` / `make format`, and `uv ruff`).
- Type checking: Pyright via pre-commit. Do **not** use `mypy` in this repo.
- Avoid hacky dependency tricks like `sys.path.insert`.
- Avoid inline imports unless necessary (e.g., circular imports).
- Prefer direct access to typed attributes; avoid `getattr`/`hasattr` shape-guard patterns. Convert inputs up front into a canonical typed shape if needed.
- Avoid `# type: ignore` unless it’s a last resort; avoid `# type: ignore[attr-defined]` when a small assertion or better typing would work.

## Testing Guidelines
- Framework: `pytest` (`uv run pytest` for full suite; target slices like `uv run pytest tests/sdk/`).
- After editing a file, run the pre-commit hooks for just that file: `uv run pre-commit run --files <path>`.
- Keep tests focused; avoid test classes unless necessary.
- Behavior tests (prefix `b##_*`) live in `tests/integration/tests/`; see `tests/integration/BEHAVIOR_TESTS.md` before modifying them.

## Documentation
- Docs live in `https://github.com/OpenHands/docs` under the `sdk/` folder. If a change affects public SDK APIs, update docs in a paired PR and cross-link both PRs.

## Commit & Pull Request Guidelines
- Commit only the relevant files you changed.
- Include this line in commit messages:
  - `Co-authored-by: openhands <openhands@all-hands.dev>`
- Before opening a PR: run `make format`, `make lint`, and relevant `uv run pytest ...`; mention results in the PR description.

## Line length (E501)
- Prefer splitting statements across multiple lines.
- If it’s a single-line string, split it into a parenthesized multi-line string.
- If it’s a long docstring and you must silence the warning, add the ignore comment **after** the closing `"""` (never inside the docstring).
