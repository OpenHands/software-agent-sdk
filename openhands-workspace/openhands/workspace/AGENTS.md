# Repository Guidelines

## Project Structure & Module Organization

- This directory (`openhands-workspace/openhands/workspace/`) contains workspace implementations under the `openhands.workspace.*` namespace (Docker, Apptainer, cloud, and API-remote).
- Each backend lives in its own subpackage (e.g. `docker/`, `cloud/`) and typically exposes a `*Workspace` class from `workspace.py`.
- The published import surface is `openhands-workspace/openhands/workspace/__init__.py` (`__all__` is treated as public API). Keep imports lightweight so `import openhands.workspace` does not pull in build-time dependencies.
- These classes should remain compatible with the SDK workspace interfaces and types (for example `openhands.sdk.workspace.RemoteWorkspace`, `TargetType`, `PlatformType`).

## Build, Test, and Development Commands

- `make build`: set up the dev environment (`uv sync --dev`) and install pre-commit hooks.
- `uv run pre-commit run --files <path>`: run checks for only the files you changed.
- `uv run pytest tests/workspace -k <pattern>`: run workspace tests; start with the narrowest file/directory that covers your change.

## Coding Style & Naming Conventions

- Python target is 3.12; keep code Ruff-compliant (line length 88) and Pyright-friendly.
- Prefer small, explicit wrappers around external interactions (Docker/Apptainer/HTTP). Validate inputs early and keep side-effecting operations out of module import time.

## Testing Guidelines

- Tests live under `tests/workspace/` and generally validate import behavior, model fields, and command invocation. Prefer patching command executors instead of requiring real Docker in unit tests.
- Add focused coverage for backend-specific behavior and for any changes that affect the public import surface.

## Commit & Pull Request Guidelines

- Avoid breaking changes to exported workspace classes/symbols; deprecate before removal when changing the public surface.
