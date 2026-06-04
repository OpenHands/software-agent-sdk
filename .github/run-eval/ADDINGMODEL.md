# Adding Models to resolve_model_config.py

## Overview

This file (`resolve_model_config.py`) defines models available for evaluation. Models must be added here before they can be used in integration tests or evaluations.

## Critical Rules

**ONLY ADD NEW CONTENT - DO NOT MODIFY EXISTING CODE**

### What NOT to Do

1. **Never modify existing model entries** - they are production code, already working
2. **Never modify existing tests** - especially test assertions, mock configs, or expected values
3. **Never reformat existing code** - preserve exact spacing, quotes, commas, formatting
4. **Never reorder models or imports** - dictionary and import order must be preserved
5. **Never "fix" existing code** - if it's in the file and tests pass, it works
6. **Never change test assertions** - even if they "look wrong" to you
7. **Never replace real model tests with mocked tests** - weakens validation
8. **Never fix import names** - if `test_model` exists, don't change it to `check_model`

### What These Rules Prevent

**Example violations** (all found in real PRs):
- Changing `assert result[0]["id"] == "claude-sonnet-4-5-20250929"` to `"gpt-4"` ❌
- Replacing real model config tests with mocked/custom model tests ❌
- "Fixing" `from resolve_model_config import test_model` to `check_model` ❌
- Adding "Fixed incorrect assertions" without explaining what was incorrect ❌
- Claiming to "fix test issues" when tests were already passing ❌

### What TO Do

**When adding a model**:
- Add ONE new entry to the MODELS dictionary
- Add ONE new test function (follow existing pattern exactly)
- Add to feature lists in model_features.py ONLY if needed for your model
- Do not touch any other files, tests, imports, or configurations
- Test the PR branch with the integration test action.
- Add a link to the integrations test to the PR.
- If you think something is broken, it's probably not - add a comment to the PR.

## Files to Modify

1. **Always required**:
   - `.github/run-eval/resolve_model_config.py` - Add model configuration
   - `tests/github_workflows/test_resolve_model_config.py` - Add test

2. **Usually required** (if model has special characteristics):
   - `openhands-sdk/openhands/sdk/llm/utils/model_features.py` - Add to feature categories

3. **Sometimes required**:
   - `openhands-sdk/openhands/sdk/llm/utils/model_prompt_spec.py` - GPT models only (variant detection)
   - `openhands-sdk/openhands/sdk/llm/utils/verified_models.py` - Production-ready models

   > ⛔ **Do NOT add a model to `verified_models.py` unless explicitly asked to.**
   > "Verified" means the model has been validated against the OpenHands integration
   > test suite **and** an OpenHands maintainer has approved it for the production UI.
   > A passing integration run is *necessary but not sufficient*. New models should be
   > added to `MODELS` in `resolve_model_config.py` (and `model_features.py` if
   > applicable) only — leave `verified_models.py` alone until a maintainer requests it
   > in the PR.
   >
   > ⚠️ **When you are explicitly asked to edit `verified_models.py`**: If you add a
   > model to `VERIFIED_OPENHANDS_MODELS`, you **must also** add it to its
   > provider-specific list (e.g. `VERIFIED_ANTHROPIC_MODELS`, `VERIFIED_GEMINI_MODELS`,
   > `VERIFIED_MOONSHOT_MODELS`, etc.). If no list exists for the provider yet, create
   > one and add it to the `VERIFIED_MODELS` dict. This ensures the model appears under
   > its actual provider in the UI, not just under "openhands".

## Step 1: Add to resolve_model_config.py

Add entry to `MODELS` dictionary:

```python
"model-id": {
    "id": "model-id",  # Must match dictionary key
    "display_name": "Human Readable Name",
    "llm_config": {
        "model": "litellm_proxy/provider/model-name",
        "temperature": 0.0,  # See temperature guide below
    },
},
```

### Temperature Configuration

| Value | When to Use | Provider Requirements |
|-------|-------------|----------------------|
| `0.0` | Standard deterministic models | Most providers |
| `1.0` | Reasoning models | Kimi K2, MiniMax M2.5 |
| `None` | Use provider default | When unsure |

### Special Parameters

Add only if needed:

- **`disable_vision: True`** - Model doesn't support vision despite LiteLLM reporting it does (GLM-4.7, GLM-5)
- **`reasoning_effort: "high"`** - For OpenAI reasoning models that support this parameter
- **`max_tokens: <value>`** - To prevent hangs or control output length
- **`top_p: <value>`** - Nucleus sampling (cannot be used with `temperature` for Claude models)
- **`litellm_extra_body: {...}`** - Provider-specific parameters (e.g., `{"enable_thinking": True}`)

### Vision and Reasoning Capability Check

Before finalizing the entry, explicitly check both capabilities against
the **provider's official documentation** (don't rely only on LiteLLM):

1. **Vision (multimodal input)**
   - Does the model accept image / video input?
   - **Critical first check — proxy `model_name` alignment.** The eval
     LiteLLM proxy stores capability metadata (`supports_vision`,
     `supports_function_calling`, token limits, etc.) under each
     `model_name` registered in its config. The SDK's lookup
     (`_get_model_info_from_litellm_proxy`) does an **exact string match**
     of `model.removeprefix("litellm_proxy/")` against the proxy's
     `model_name`. If the strings don't match, `supports_vision` is
     silently ignored and the SDK falls back to LiteLLM's static metadata.
     So: **use the proxy's exact registered `model_name` in your
     `llm_config["model"]`**, not a longer provider-prefixed alias, e.g.
     prefer `litellm_proxy/step-3.7-flash` over
     `litellm_proxy/openrouter/stepfun/step-3.7-flash` if the proxy entry
     is registered as `step-3.7-flash`. Ask infra (or check the proxy
     config) for the canonical `model_name` — and confirm `model_info`
     has the capability flags you expect.
   - Cross-check LiteLLM static metadata: in a Python shell, run
     ```python
     from litellm import supports_vision
     supports_vision(model="<litellm_proxy_target_without_litellm_proxy_prefix>")
     ```
   - Decision matrix:
     | Provider docs | Proxy `model_info.supports_vision` | LiteLLM static | Action |
     |---------------|------------------------------------|----------------|--------|
     | Vision ✅      | True (and `model_name` matches your config) | any | Do nothing — vision auto-enables via the proxy metadata. |
     | Vision ✅      | True, but `model_name` does **not** match your config | any | **Fix the model path** in your `llm_config` to match the proxy's `model_name` exactly. Don't add `disable_vision`. |
     | Vision ✅      | not set / proxy entry has no `model_info` | any | Coordinate with infra to add `supports_vision: true` to the proxy entry (one line of YAML). Vision integration test will skip until that lands — non-blocking. |
     | Vision ❌      | True | True | Add `"disable_vision": True` (proxy/LiteLLM are wrong). |
     | Vision ❌      | any | any | Do nothing. |
   - Note the result in the PR description, including the exact proxy
     `model_name` your `llm_config["model"]` matches.

2. **Reasoning (thinking / reasoning_effort)**
   - Does the model expose adjustable reasoning levels or extended thinking?
   - Cross-check LiteLLM:
     ```python
     from litellm import get_supported_openai_params
     "reasoning_effort" in (get_supported_openai_params(
         model="<litellm_target>", custom_llm_provider=None) or [])
     ```
   - Decision matrix:
     | Provider style | LiteLLM `reasoning_effort` support | Action |
     |----------------|-------------------------------------|--------|
     | OpenAI-style reasoning items (e.g. GPT-5, OpenRouter reasoning levels) | ✅ | Pin `"reasoning_effort": "high"` in `llm_config`. If pinned, remove `temperature` / `top_p` (they'll be auto-stripped). |
     | OpenAI-style reasoning items, provider has a non-standard reasoning param | ❌ | Pin via provider-specific passthrough: `"litellm_extra_body": {<provider-key>: <value>}` (e.g. `{"reasoning": {"effort": "high"}}` for OpenRouter, `{"enable_thinking": True}` for Qwen). **Confirm which upstream the proxy actually routes to** before choosing the key — the proxy's `litellm_params.model` (e.g. `openai/...` vs `openrouter/...`) decides what extra-body keys the upstream understands. The top-level `reasoning_effort` would otherwise be dropped by `drop_params=True`. |
     | Anthropic extended thinking (Claude Sonnet 4.5+, Haiku 4.5) | n/a | Add the model identifier to `EXTENDED_THINKING_MODELS` in `model_features.py` (see Step 2). |
     | Non-reasoning model | n/a | Do nothing. |
   - Note the result in the PR description (e.g., "Reasoning: OpenRouter exposes high/medium/low; LiteLLM does not yet expose `reasoning_effort`, so opting in via `litellm_extra_body`; condenser thinking-block test will skip — expected for non-Anthropic reasoning models").

The integration runner correctly **skips** vision / extended-thinking tests
when the SDK can't detect support. A skipped test for one of these reasons
is **not** a failure of the PR and doesn't need to be fixed in the same PR;
it usually means LiteLLM metadata needs to be updated upstream.

### Critical Rules

1. Model ID must match dictionary key
2. Model path must start with `litellm_proxy/`
3. **Claude models**: Cannot use both `temperature` and `top_p` - choose one or omit both
4. Parameters like `disable_vision` must be in `SDK_ONLY_PARAMS` constant (they're filtered before sending to LiteLLM)

## Step 2: Update model_features.py (if applicable)

Check provider documentation to determine which feature categories apply:

### REASONING_EFFORT_MODELS
Models that support `reasoning_effort` parameter:
- OpenAI: o1, o3, o4, GPT-5 series
- Anthropic: Claude Opus 4.5+, Claude Sonnet 4.6
- Google: Gemini 2.5+, Gemini 3.x series
- AWS: Nova 2 Lite

```python
REASONING_EFFORT_MODELS: list[str] = [
    "your-model-identifier",  # Add here
]
```

**Effect**: Automatically strips `temperature` and `top_p` parameters to avoid API conflicts.

### EXTENDED_THINKING_MODELS
Models with extended thinking capabilities:
- Anthropic: Claude Sonnet 4.5+, Claude Haiku 4.5

```python
EXTENDED_THINKING_MODELS: list[str] = [
    "your-model-identifier",  # Add here
]
```

**Effect**: Automatically strips `temperature` and `top_p` parameters.

### PROMPT_CACHE_MODELS
Models supporting prompt caching:
- Anthropic: Claude 3.5+, Claude 4+ series

```python
PROMPT_CACHE_MODELS: list[str] = [
    "your-model-identifier",  # Add here
]
```

### SUPPORTS_STOP_WORDS_FALSE_MODELS
Models that **do not** support stop words:
- OpenAI: o1, o3 series
- xAI: Grok-4, Grok-code-fast-1
- DeepSeek: R1 family

```python
SUPPORTS_STOP_WORDS_FALSE_MODELS: list[str] = [
    "your-model-identifier",  # Add here
]
```

### FORCE_STRING_SERIALIZER_MODELS
Models requiring string format for tool messages (not structured content):
- DeepSeek models
- GLM models  
- Groq: Kimi K2-Instruct
- OpenRouter: MiniMax

Use pattern matching:
```python
FORCE_STRING_SERIALIZER_MODELS: list[str] = [
    "deepseek",  # Matches any model with "deepseek" in name
    "groq/kimi-k2-instruct",  # Provider-prefixed
]
```

### Other Categories

- **PROMPT_CACHE_RETENTION_MODELS**: GPT-5 family, GPT-4.1
- **RESPONSES_API_MODELS**: GPT-5 family, codex-mini-latest
- **SEND_REASONING_CONTENT_MODELS**: Kimi K2 Thinking/K2.5, MiniMax-M2, DeepSeek Reasoner

See `model_features.py` for complete lists and additional documentation.

## Step 3: Add Test

**File**: `tests/github_workflows/test_resolve_model_config.py`

**Important**: 
- Python function names cannot contain hyphens. Convert model ID hyphens to underscores.
- **Do not modify any existing test functions** - only add your new one at the end of the file
- **Do not change existing imports** - use what's already there
- **Do not fix "incorrect" assertions** in other tests - they are correct

**Test template** (copy and modify for your model):

```python
def test_your_model_id_config():  # Replace hyphens with underscores in function name
    """Test that your-model-id has correct configuration."""
    model = MODELS["your-model-id"]  # Dictionary key keeps hyphens
    
    assert model["id"] == "your-model-id"
    assert model["display_name"] == "Your Model Display Name"
    assert model["llm_config"]["model"] == "litellm_proxy/provider/model-name"
    # Only add assertions for parameters YOU added in resolve_model_config.py
    # assert model["llm_config"]["temperature"] == 0.0
    # assert model["llm_config"]["disable_vision"] is True
```

**What NOT to do in tests**:
- Don't change assertions in other test functions (even if model names "look wrong")
- Don't replace real model tests with mocked tests
- Don't change `test_model` to `check_model` in imports
- Don't modify mock_models dictionaries in other tests
- Don't add "fixes" to existing tests - they work as-is

## Step 4: Update GPT Variant Detection (GPT models only)

**File**: `openhands-sdk/openhands/sdk/llm/utils/model_prompt_spec.py`

Required only if this is a GPT model needing specific prompt template.

**Order matters**: More specific patterns must come before general patterns.

```python
_MODEL_VARIANT_PATTERNS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "openai_gpt": (
        (
            "gpt-5-codex",  # Specific variant first
            ("gpt-5-codex", "gpt-5.1-codex", "gpt-5.2-codex", "gpt-5.3-codex"),
        ),
        ("gpt-5", ("gpt-5", "gpt-5.1", "gpt-5.2")),  # General variant last
    ),
}
```

## Step 5: Run Tests Locally

```bash
# Pre-commit checks
pre-commit run --all-files

# Unit tests
pytest tests/github_workflows/test_resolve_model_config.py::test_your_model_config -v

# Manual verification
cd .github/run-eval
MODEL_IDS="your-model-id" GITHUB_OUTPUT=/tmp/output.txt python resolve_model_config.py
```

## Step 6: Create Draft PR

Push your branch and create a draft PR. Note the PR number returned - you'll need it for the integration tests.

## Step 7: Run Integration Tests

Trigger integration tests on your PR branch:

```bash
gh workflow run integration-runner.yml \
  -f model_ids=your-model-id \
  -f reason="Testing new model from PR #<pr-number>" \
  -f issue_number=<pr-number> \
  --ref your-branch-name
```

Results will be posted back to the PR as a comment.

### Expected Results

- Success rate: 100% (or 87.5% if vision test skipped)
- Duration: 5-10 minutes per model
- Tests: 8 total (basic commands, file ops, code editing, reasoning, errors, tools, context, vision)

## Step 8: Fix Issues and Rerun (if needed)

If tests fail, see [Common Issues](#common-issues) below. After fixing:

1. Push the fix: `git add . && git commit && git push`
2. Rerun integration tests with the same command from Step 7 (using the same PR number)

## Step 9: Mark PR Ready

When tests pass, mark the PR as ready for review:

```bash
gh pr ready <pr-number>
```

### Required in PR Description

```markdown
## Summary
Adds the `model-id` model to resolve_model_config.py.

## Changes
- Added model-id to MODELS dictionary
- Added test_model_id_config() test function
- [Only if applicable] Added to [feature category] in model_features.py

## Configuration
- Model ID: model-id
- Provider: Provider Name  
- Temperature: [value] - [reasoning for choice]
- [List any special parameters and why needed]

## Integration Test Results
✅ Integration tests passed: [PASTE GITHUB ACTIONS RUN URL]

[Summary table showing test results]

Fixes #[issue-number]
```

### What NOT to Include in PR Description

**Do not claim to have "fixed" things unless they were actually broken**:
- ❌ "Fixed test_model import issue" (if tests were passing, there was no issue)
- ❌ "Fixed incorrect assertions in existing tests" (they were correct)
- ❌ "Improved test coverage" (unless you actually added new test cases)
- ❌ "Cleaned up code" (you shouldn't be cleaning up anything)
- ❌ "Updated test approach" (you shouldn't be changing testing approach)

**Only describe what you actually added**:
- ✅ "Added gpt-5.3-codex model configuration"
- ✅ "Added test for gpt-5.3-codex"
- ✅ "Added gpt-5.3-codex to REASONING_EFFORT_MODELS"

## Common Issues

### Integration Tests Hang (6-8+ hours)
**Causes**:
- Missing `max_tokens` parameter
- Claude models with both `temperature` and `top_p` set
- Model not in REASONING_EFFORT_MODELS or EXTENDED_THINKING_MODELS

**Solutions**: Add `max_tokens`, remove parameter conflicts, add to appropriate feature category.

**Reference**: #2147

### Preflight Check: "Cannot specify both temperature and top_p"
**Cause**: Claude models receiving both parameters

**Solutions**:
- Remove `top_p` from llm_config if `temperature` is set
- Add model to REASONING_EFFORT_MODELS or EXTENDED_THINKING_MODELS (auto-strips both)

**Reference**: #2137, #2193

### Preflight Check: "Invalid model name" for a newly-added model
**Cause**: The local preflight in `resolve_model_config.py` performs a *live*
`litellm.completion(...)` against the LiteLLM proxy at `LLM_BASE_URL` (default
`https://llm-proxy.eval.all-hands.dev`). Registering a new model name on that
proxy is done **out-of-band** by infra/maintainers — it is not part of this PR.
So immediately after adding a new entry, the proxy may not yet know the model
name and respond with `Invalid model name passed in model=<provider>/<name>`.

**This is not a bug in the PR** — it is a transient proxy-provisioning lag.

**What to do**:
- Author: keep the PR open, link evidence that the model works (see Step 7 — the
  integration-runner workflow run and/or the
  [eval monitor](https://openhands-eval-monitor.vercel.app/) run for this model
  are the authoritative checks).
- Reviewers / QA bot: do **not** block the PR on this specific failure. Treat
  successful integration-runner results or an eval monitor link as authoritative
  validation, and re-run the local preflight only after the proxy has been
  updated. See the "Reviewing model PRs" section in
  [`AGENTS.md`](./AGENTS.md) for the full review-side rule.

**Reference**: #3472

### Vision Tests Fail
**Cause**: LiteLLM reports vision support but model doesn't actually support it

**Solution**: Add `"disable_vision": True` to llm_config

**Reference**: #2110 (GLM-5), #1898 (GLM-4.7)

### Wrong Prompt Template (GPT models)
**Cause**: Model variant not detected correctly, falls through to wrong template

**Solution**: Add explicit entries to `model_prompt_spec.py` with correct pattern order

**Reference**: #2233 (GPT-5.2-codex, GPT-5.3-codex)

### SDK-Only Parameters Sent to LiteLLM
**Cause**: Parameter like `disable_vision` not in `SDK_ONLY_PARAMS` set

**Solution**: Add to `SDK_ONLY_PARAMS` in `resolve_model_config.py`

**Reference**: #2194

## Model Feature Detection Criteria

### How to Determine if Model Needs Feature Category

**Reasoning Model**:
- Check provider documentation for "reasoning", "thinking", or "o1-style" mentions
- Model exposes internal reasoning traces
- Examples: o1, o3, GPT-5, Claude Opus 4.5+, Gemini 3+

**Extended Thinking**:
- Check if model is Claude Sonnet 4.5+ or Claude Haiku 4.5
- Provider documents extended thinking capabilities

**Prompt Caching**:
- Check provider documentation for prompt caching support
- Anthropic Claude 3.5+ and 4+ series support this

**Vision Support**:
- Check provider documentation (don't rely solely on LiteLLM)
- If LiteLLM reports vision but provider docs say text-only, add `disable_vision: True`

**Stop Words**:
- Most models support stop words
- o1/o3 series, some Grok models, DeepSeek R1 do not

**String Serialization**:
- If tool message errors mention "Input should be a valid string"
- DeepSeek, GLM, some provider-specific models need this

## Reference

- Recent model additions: #2102, #2153, #2207, #2233, #2269
- Common issues: #2147 (hangs), #2137 (parameters), #2110 (vision), #2233 (variants), #2193 (preflight)
- Integration test workflow: `.github/workflows/integration-runner.yml`
- Integration tests can be triggered via: `gh workflow run integration-runner.yml --ref <branch>`
