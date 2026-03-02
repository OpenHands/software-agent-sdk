# Local plugin install smoke test (PR-only)

This folder contains **temporary PR artifacts** used to validate the installed
plugin utilities end-to-end in this PR.

## What this tests

- Installs a small **local plugin** into an `installed_dir` (without touching the
  real `~/.openhands/` home directory).
- Loads the installed plugin, merges its `skills/` into an `Agent`.
- Runs a minimal `Conversation` using `TestLLM` with **persistence enabled** and
  writes persisted state + events under this directory.

## How to (re)generate

This reuses the committed example script, but writes artifacts into `.pr/` via an env var:

```bash
OPENHANDS_EXAMPLE_ARTIFACT_DIR=.pr/local_plugin_install_test \
  uv run python examples/05_skills_and_plugins/03_local_plugin_install/main.py
```

## Artifacts

- `plugin_src/`: the local plugin source directory used for installation
- `installed_root/`: the install root containing `.installed.json` and the copied plugin
- `persistence/`: persisted `base_state.json` and `events/` from the smoke-test conversation

> Note: `.pr/` content is automatically cleaned up on PR approval.
