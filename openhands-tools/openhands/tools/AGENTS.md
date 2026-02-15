# Repository Guidelines

## Project Structure & Module Organization

- This directory (`openhands-tools/openhands/tools/`) contains runtime tool implementations under the `openhands.tools.*` namespace.
- Most tools live in dedicated subpackages (for example `terminal/`, `file_editor/`, `browser_use/`) and typically split:
  - `definition.py`: public schema/metadata/registration
  - `impl.py` / `core.py`: runtime implementation
- Treat `openhands-tools/openhands/tools/__init__.py` as the published surface for `openhands-tools`; `__all__` is considered public API.

## Build, Test, and Development Commands

- `make build`: set up the dev environment (`uv sync --dev`) and install pre-commit hooks.
- `uv run pre-commit run --files <path>`: run checks only for the files you touched.
- `uv run pytest tests/tools -k <pattern>`: run the tools test suite; prefer running a focused subset first (e.g. `uv run pytest tests/tools/terminal`).

## Coding Style & Naming Conventions

- Python target is 3.12; keep code Ruff-compliant (line length 88) and Pyright-friendly.
- Tool names, parameter schemas, and output schemas are user-facing and often referenced in tests like `tests/tools/test_tool_name_consistency.py`; avoid breaking changes. If a schema must change, provide a backward-compatible loading path.
- When adding runtime-loaded assets (Jinja `.j2` templates or JS under `browser_use/js/`), ensure they are included as package data (and update the agent-server PyInstaller spec when needed).

## Testing Guidelines

- Add/adjust unit tests under `tests/tools/`, mirroring the tool package. Keep tests focused on the behavior you changed.
- Prefer real code paths over mocks; when mocking is unavoidable (e.g. external processes), centralize setup in `tests/conftest.py` or `tests/tools/<tool>/conftest.py`.

## Commit & Pull Request Guidelines

- Keep changes scoped to the tool(s) touched, and run the smallest relevant tests before running broader suites.
