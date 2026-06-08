# Model Configuration for OpenHands SDK

See the [project root AGENTS.md](../../AGENTS.md) for repository-wide policies and workflows.

This directory contains model configuration and evaluation setup for the OpenHands SDK.

## Key Files

- **`resolve_model_config.py`** - Model registry and configuration
  - Defines all models available for evaluation
  - Contains model IDs, display names, LiteLLM paths, and parameters
  - Used by integration tests and evaluation workflows

- **`tests/github_workflows/test_resolve_model_config.py`** - Tests for model configurations
  - Validates model entries are correctly structured
  - Tests preflight check functionality

- **`ADDINGMODEL.md`** - Detailed guide for adding models (see below)

## Common Tasks

### Adding a New Model

**→ See [ADDINGMODEL.md](./ADDINGMODEL.md) for complete instructions**

This is the most common task in this directory. The guide covers:
- Required steps and files to modify
- Model feature categories and when to use them
- Integration testing requirements
- Common issues and troubleshooting
- Critical rules to prevent breaking existing models

### Debugging Model Issues

If a model is failing in evaluations:
1. Check the model configuration in `resolve_model_config.py`
2. Review parameter compatibility (especially `temperature` + `top_p` for Claude)
3. Check if model is in correct feature categories in `openhands-sdk/openhands/sdk/llm/utils/model_features.py`
4. Run preflight check: `MODEL_IDS="model-id" python resolve_model_config.py`

### Updating Existing Models

**Warning**: Only update existing models if there's a confirmed issue. Working configurations should not be changed.

If you must update:
1. Document why the change is needed (link to issue/PR showing the problem)
2. Test thoroughly before and after the change
3. Run integration tests to verify no regressions

## Reviewing Model PRs (Reviewers / QA bot)

When reviewing a PR that adds an entry to `resolve_model_config.py` (or the
matching test in `tests/cross/test_resolve_model_config.py`), apply the
following rules. They apply equally to human reviewers and to automated
review / QA agents.

### The local preflight check is NOT authoritative for new models

`resolve_model_config.py` ends with a live `litellm.completion(...)` call
against the LiteLLM proxy at `LLM_BASE_URL` (default
`https://llm-proxy.eval.all-hands.dev`). Registering a brand-new model name on
that proxy is done **out-of-band** by infra/maintainers, not in the PR. So a
local run like:

```bash
MODEL_IDS="brand-new-model" uv run python .github/run-eval/resolve_model_config.py
```

can legitimately fail with `Invalid model name passed in model=<provider>/<name>`
**even when the PR itself is correct**. The failure simply means the proxy has
not been provisioned with the new model yet — it does **not** mean the PR is
broken.

**Do not** mark such a PR as failing preflight on this basis, and do not post
"❌ QA Report: FAIL" purely because the live preflight rejected the new model
name. Re-running the same command on every new commit will keep producing the
same false negative until proxy provisioning catches up.

### What IS authoritative for new model PRs

For PRs adding a new model, treat the following as authoritative validation:

1. The **integration-runner workflow** (`.github/workflows/integration-runner.yml`)
   run for this PR, surfaced in the PR description / comments as a GitHub
   Actions run URL.
2. A run for this model on the
   [eval monitor](https://openhands-eval-monitor.vercel.app/) (e.g. a SWE-bench
   or other benchmark run that exercises the same model identifier end-to-end).
3. The PR author's explicit confirmation (e.g. screenshot) that the model is
   reachable via the proxy.

If at least one of (1)–(3) is present and looks healthy, the live preflight
result against the eval proxy can be ignored as a transient
proxy-provisioning lag. See the
"Preflight Check: 'Invalid model name' for a newly-added model" entry in
[`ADDINGMODEL.md`](./ADDINGMODEL.md) for the author-side counterpart.

### What still IS a real preflight blocker

Don't conflate the lag above with real configuration problems. The following
preflight failures **are** PR-blocking and should still be flagged:

- `Cannot specify both temperature and top_p` for Claude models
  (configuration error in the PR — see ADDINGMODEL.md).
- Bad request errors caused by parameter shapes the PR introduced
  (e.g. an unsupported `reasoning_effort` or `litellm_extra_body` value).
- The PR fails the **unit test** in
  `tests/cross/test_resolve_model_config.py` (independent of any proxy).
- The PR breaks resolution of *existing* models (`MODEL_IDS=<existing-model>`
  used to succeed and now fails) — that is a real regression.

## Directory Purpose

This directory bridges model definitions with the evaluation system:
- Models defined here are available for integration tests
- Configuration includes LiteLLM routing and SDK-specific parameters
- Preflight checks validate model accessibility before expensive evaluation runs
- Tests ensure all models are correctly structured and resolvable
