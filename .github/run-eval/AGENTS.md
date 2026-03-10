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

## Directory Purpose

This directory bridges model definitions with the evaluation system:
- Models defined here are available for integration tests
- Configuration includes LiteLLM routing and SDK-specific parameters
- Preflight checks validate model accessibility before expensive evaluation runs
- Tests ensure all models are correctly structured and resolvable
