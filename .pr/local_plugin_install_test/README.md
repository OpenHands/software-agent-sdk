# Local plugin install smoke test (PR-only)

This folder contains **temporary PR artifacts** used to validate the installed
plugin utilities end-to-end in this PR.

## Guarantee: this will not be merged

The `.pr/` directory is PR-only. When this PR is approved, a workflow will
automatically remove the entire `.pr/` directory so it does not get merged to
`main`. So you can approve this PR so that it gets cleaned up.

- Cleanup workflow: `.github/workflows/pr-artifacts.yml` (job: `cleanup-on-approval`).

## How this smoke test works (summary)

This smoke test uses `TestLLM` (a scripted LLM) to run a real `Conversation`
loop without external network calls, and verifies that an installed plugin’s
skill can be loaded and triggered.

See `console_log.md` for the full explanation (verbatim) and console output.

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
>
> Cleanup workflow: `.github/workflows/pr-artifacts.yml` (job: `cleanup-on-approval`).
>
> See `console_log.md` for a detailed explanation + the console output.
