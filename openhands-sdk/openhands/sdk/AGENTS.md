# Package Guidelines

## Package Structure & Module Organization

- This directory (`openhands-sdk/openhands/sdk/`) contains the core Python SDK under the `openhands.sdk.*` namespace.
- Keep new modules within the closest existing subpackage (e.g., `llm/`, `tool/`, `event/`, `agent/`) and follow local naming patterns.
- Add/adjust unit tests under `tests/sdk/` mirroring the SDK path (for example, changes to `openhands-sdk/openhands/sdk/tool/tool.py` should be covered in `tests/sdk/tool/test_tool.py`).

## Build, Test, and Development Commands

- `make build`: sets up the dev environment (runs `uv sync --dev` and installs pre-commit hooks).
- `make lint` / `make format`: run Ruff linting and formatting.
- `uv run pre-commit run --files <path>`: run the pre-commit checks for files you changed.
- `uv run pytest tests/sdk -k <pattern>`: run targeted SDK tests; prefer running the smallest relevant test set first.

## Coding Style & Naming Conventions

- Python target is 3.12; keep code Ruff-compliant (line length 88).
- Prefer explicit, accurate type annotations; use Pyright for type checking (do not add mypy).
- Avoid `# type: ignore` unless there is no reasonable typing fix.
- Keep imports at the top of files; avoid `sys.path` hacks and in-line imports unless required for circular dependencies.
- When changing Pydantic models or serialized event shapes, preserve backward compatibility so older persisted data can still load.

## Testing Guidelines

- Prefer real code paths over mocks; introduce fixtures in `tests/conftest.py` when setup is repeated.
- Keep tests minimal and focused on the changed behavior; avoid adding broad integration tests unless required.

## Commit & Pull Request Guidelines

- Follow the repository’s existing commit style (short, imperative subjects; use scope prefixes like `fix(sdk):` when helpful).
- Keep PRs focused; update docs and tests when changing public APIs or user-facing behavior.
