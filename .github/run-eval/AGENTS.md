# Agent Guide: Adding Models to OpenHands SDK

This guide provides step-by-step instructions for adding new models to the OpenHands SDK evaluation configuration.

> **Critical**: Always run integration tests on your branch BEFORE creating a PR. 30% of model issues are only caught during integration testing.

## Quick Start: Add a Model in 10 Steps

### Step 1: Research Model Characteristics

Before coding, determine:

- **Vision support**: Does the model support images? Is LiteLLM reporting it correctly?
- **Reasoning capabilities**: Is this a reasoning model (o1, o3, GPT-5, Claude Opus 4.5+, Gemini 3+)?
- **Parameter compatibility**: Can it accept both `temperature` AND `top_p`? (Claude models CANNOT)
- **Recommended temperature**: 0.0 for standard models, 1.0 for reasoning models
- **Special requirements**: `reasoning_effort`, `enable_thinking`, `max_tokens`, etc.

### Step 2: Add Model to resolve_model_config.py

Add to the `MODELS` dictionary:

```python
"model-id": {
    "id": "model-id",  # Must match dictionary key
    "display_name": "User Friendly Name",
    "llm_config": {
        "model": "litellm_proxy/provider/model-name",
        "temperature": 0.0,  # or 1.0 for reasoning, or None
        # Add ONLY if needed:
        # "top_p": 0.95,  # NEVER with temperature for Claude!
        # "reasoning_effort": "high",  # For reasoning models
        # "disable_vision": True,  # If LiteLLM reports vision incorrectly
        # "max_tokens": 4096,  # To prevent hangs
    },
},
```

**Critical Rules**:
- Model ID must match dictionary key
- Always use `litellm_proxy/` prefix
- **For Claude models**: NEVER set both `temperature` and `top_p` together
- Add `disable_vision: True` only if LiteLLM incorrectly reports vision support

### Step 3: Update Model Features (if applicable)

**File**: `openhands-sdk/openhands/sdk/llm/utils/model_features.py`

Add model to appropriate lists:

#### REASONING_EFFORT_MODELS (64% of models)
For models supporting `reasoning_effort` parameter:
- OpenAI o1/o3/o4, GPT-5 series
- Claude Opus 4.5/4.6, Sonnet 4.6
- Gemini 2.5/3.0/3.1 series
- Nova 2 Lite

```python
REASONING_EFFORT_MODELS: list[str] = [
    # ... existing ...
    "your-model-id",
]
```

#### EXTENDED_THINKING_MODELS (11% of models)
For models supporting extended thinking:
- Claude Sonnet 4.5/4.6
- Claude Haiku 4.5

```python
EXTENDED_THINKING_MODELS: list[str] = [
    # ... existing ...
    "your-model-id",
]
```

**Important**: Models in REASONING_EFFORT_MODELS or EXTENDED_THINKING_MODELS automatically have `temperature` and `top_p` stripped to avoid API conflicts.

#### PROMPT_CACHE_MODELS (43% of models)
For prompt caching support:
- Claude 3.5+, 4+ series

```python
PROMPT_CACHE_MODELS: list[str] = [
    # ... existing ...
    "your-model-id",
]
```

#### Other Feature Categories

Add to these if applicable:

- **PROMPT_CACHE_RETENTION_MODELS**: GPT-5 family, GPT-4.1
- **SUPPORTS_STOP_WORDS_FALSE_MODELS**: o1/o3, Grok, DeepSeek R1
- **RESPONSES_API_MODELS**: GPT-5 family
- **FORCE_STRING_SERIALIZER_MODELS**: DeepSeek, GLM, some others
- **SEND_REASONING_CONTENT_MODELS**: Kimi K2, MiniMax-M2, DeepSeek Reasoner

### Step 4: Update Model Variant Detection (GPT models only)

**File**: `openhands-sdk/openhands/sdk/llm/utils/model_prompt_spec.py`

**Only if** this is a GPT model needing a specific prompt template variant:

```python
_MODEL_VARIANT_PATTERNS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "openai_gpt": (
        (
            "gpt-5-codex",  # More specific pattern FIRST
            ("gpt-5-codex", "gpt-5.1-codex", "gpt-5.2-codex", "gpt-5.3-codex"),
        ),
        ("gpt-5", ("gpt-5", "gpt-5.1", "gpt-5.2")),  # General pattern LAST
    ),
}
```

**Order matters**: More specific patterns must come before general ones.

### Step 5: Add Test

**File**: `tests/github_workflows/test_resolve_model_config.py`

```python
def test_your_model_id_config():
    """Test that your-model-id has correct configuration."""
    model = MODELS["your-model-id"]
    
    assert model["id"] == "your-model-id"
    assert model["display_name"] == "Your Model Name"
    assert model["llm_config"]["model"] == "litellm_proxy/provider/model-name"
    # Add assertions for special parameters:
    # assert model["llm_config"]["temperature"] == 0.0
    # assert model["llm_config"]["disable_vision"] is True
```

### Step 6: Run Local Tests

```bash
# Run pre-commit checks
pre-commit run --all-files

# Run tests
pytest tests/github_workflows/test_resolve_model_config.py -v

# Test model resolution manually
cd .github/run-eval
MODEL_IDS="your-model-id" GITHUB_OUTPUT=/tmp/output.txt python resolve_model_config.py
cat /tmp/output.txt
```

### Step 7: Commit and Push

```bash
git add .github/run-eval/resolve_model_config.py
git add openhands-sdk/openhands/sdk/llm/utils/model_features.py  # if modified
git add tests/github_workflows/test_resolve_model_config.py
git commit -m "Add your-model-id to resolve_model_config.py

- Added model configuration with appropriate parameters
- Added to [feature categories] in model_features.py
- Added test in test_resolve_model_config.py"
git push origin your-branch-name
```

### Step 8: Run Integration Tests BEFORE Creating PR

**THIS IS MANDATORY - DO NOT SKIP!**

1. Go to: https://github.com/OpenHands/software-agent-sdk/actions/workflows/integration-runner.yml
2. Click "Run workflow"
3. Fill in:
   - **Branch**: Select your branch
   - **Reason**: "Testing your-model-id"
   - **model_ids**: `your-model-id`
4. Click "Run workflow"
5. Wait for completion (5-10 minutes per model)
6. **Verify 100% success rate**
7. **Copy the run URL** - you MUST include this in your PR

**Do NOT create PR until integration tests pass!**

### Step 9: Create Pull Request

**Title**: `Add [model-id] to resolve_model_config.py`

**Description**:

```markdown
## Summary
Adds the `your-model-id` model to the evaluation model configuration.

## Model Information
- **Model ID**: your-model-id
- **Display Name**: Your Model Name
- **Provider**: Provider Name
- **Model Path**: litellm_proxy/provider/model-name

## Configuration Details
- **Temperature**: [value] - [reasoning]
- **Special Parameters**: [list any and explain why]

## Model Features
- Added to: [list categories from model_features.py]
- Reasoning: [explain why]

## Integration Test Results
✅ **Integration tests passed**: [PASTE GITHUB ACTIONS RUN URL HERE]

[Paste test results summary showing 100% success]

## Testing
- [x] Pre-commit hooks pass
- [x] All tests pass locally
- [x] Integration tests pass on correct branch
- [x] Added test in test_resolve_model_config.py

Fixes #[issue-number]
```

**CRITICAL**: Must include link to successful integration test run!

### Step 10: Monitor and Iterate

- Watch for CI results
- Address any review comments
- Be ready for follow-up issues (complex models may need multiple PRs)

## Common Issues & Solutions

### Issue: Integration tests hang indefinitely
**Symptoms**: Tests run for 6-8+ hours

**Solutions**:
- Add `"max_tokens": [appropriate value]` to llm_config
- Check for temperature + top_p conflict (Claude models)
- Add to REASONING_EFFORT_MODELS or EXTENDED_THINKING_MODELS

### Issue: Preflight check fails with parameter error
**Symptoms**: Error like "You cannot specify both temperature and top_p"

**Solutions**:
- Remove `top_p` from llm_config if `temperature` is set (Claude models)
- Add to REASONING_EFFORT_MODELS or EXTENDED_THINKING_MODELS to auto-strip both
- Verify SDK_ONLY_PARAMS includes SDK-specific parameters

### Issue: Vision tests fail despite model supporting vision
**Symptoms**: Multimodal tests fail

**Solutions**:
- Add `"disable_vision": True` to llm_config
- LiteLLM incorrectly reports vision support for some models (GLM series)

### Issue: Wrong prompt template (GPT models)
**Symptoms**: Malformed tool calls, unexpected output

**Solutions**:
- Update `model_prompt_spec.py` variant detection
- Ensure more specific patterns come before general ones
- Add explicit version entries (e.g., gpt-5.2-codex, gpt-5.3-codex)

### Issue: Model uses wrong content format
**Symptoms**: "Input should be a valid string" errors

**Solutions**:
- Add to FORCE_STRING_SERIALIZER_MODELS
- Use appropriate pattern (model name or provider/model)

## Quick Reference: Feature Categories Decision Tree

```
Is this a reasoning model? (o1, o3, GPT-5, Claude Opus 4.5+, Gemini 3+)
└─ Yes → Add to REASONING_EFFORT_MODELS

Is this Claude Sonnet 4.5+/4.6 or Haiku 4.5?
└─ Yes → Add to EXTENDED_THINKING_MODELS

Does it support prompt caching? (Claude 3.5+, 4+)
└─ Yes → Add to PROMPT_CACHE_MODELS

Is it GPT-5 family or GPT-4.1?
└─ Yes → Add to PROMPT_CACHE_RETENTION_MODELS

Does it NOT support stop words? (o1/o3, Grok, DeepSeek R1)
└─ Yes → Add to SUPPORTS_STOP_WORDS_FALSE_MODELS

Is it GPT-5 family?
└─ Yes → Add to RESPONSES_API_MODELS

Does it need string serialization? (DeepSeek, GLM)
└─ Yes → Add to FORCE_STRING_SERIALIZER_MODELS

Should reasoning content be in messages? (Kimi K2, MiniMax-M2, DeepSeek Reasoner)
└─ Yes → Add to SEND_REASONING_CONTENT_MODELS
```

## Key Statistics (Based on Recent Additions)

- **64%** of models need REASONING_EFFORT_MODELS
- **43%** of models need PROMPT_CACHE_MODELS
- **30%** of model issues only caught in integration testing
- **Temperature**: 0.0 for standard, 1.0 for reasoning models

## What NOT to Do

❌ **NEVER**:
- Create PR before running integration tests
- Set both `temperature` and `top_p` for Claude models
- Skip local testing
- Forget to add test cases
- Omit integration test link from PR description

## Temperature Configuration Guide

| Value | Use Case | Examples |
|-------|----------|----------|
| `0.0` | Standard models, deterministic | Most Claude, GPT, Gemini |
| `1.0` | Reasoning models, exploration | Kimi K2, MiniMax M2.5 |
| `None` | Use provider defaults | Rare cases |

## Files Reference

**Always modified**:
1. `.github/run-eval/resolve_model_config.py` - Model config
2. `tests/github_workflows/test_resolve_model_config.py` - Tests

**Usually modified (80%)**:
3. `openhands-sdk/openhands/sdk/llm/utils/model_features.py` - Features

**Sometimes modified**:
4. `openhands-sdk/openhands/sdk/llm/utils/verified_models.py` - Verified list
5. `openhands-sdk/openhands/sdk/llm/utils/model_prompt_spec.py` - GPT variants

## Example Recent Additions

### Simple Addition
**Claude Sonnet 4.6** (#2102): Standard addition, later needed follow-ups for parameter conflicts and feature flags

### With Feature Classification
**Claude Opus 4.6** (#1941): Added to REASONING_EFFORT_MODELS and PROMPT_CACHE_MODELS

### With Parameter Fixes
**GLM-5** (#2111, #2194): Added `disable_vision: True`, ensured SDK_ONLY_PARAMS filtering

### With Variant Detection
**GPT-5.2-codex, GPT-5.3-codex** (#2238): Added explicit variant entries to prevent wrong prompt template

## Integration Test Workflow

Integration tests run 8 tests per model:
1. Basic command execution
2. File operations
3. Code editing
4. Multi-step reasoning
5. Error handling
6. Tool usage
7. Context management
8. Image viewing (skipped for text-only models)

**Expected**:
- Success Rate: 100% (or 87.5% if vision test skipped)
- Duration: 5-10 minutes per model
- Cost: $0.10-$0.50 per model

## Resources

- **Integration Test Workflow**: `.github/workflows/integration-runner.yml`
- **Example PRs**:
  - Simple: #2102 (Claude Sonnet 4.6)
  - With fixes: #2111 (GLM-5 vision), #2138 (Claude Sonnet 4.6 features)
  - Variant detection: #2238 (GPT-5.2/5.3 Codex)
- **Related Issues**:
  - Integration hangs: #2147
  - Parameter conflicts: #2137
  - Vision misreporting: #2110
  - Preflight failures: #2193

---

**Remember**: Integration testing BEFORE PR is mandatory. It catches 30% of issues that don't appear in local testing.

**When in doubt**: Research the model thoroughly, add it conservatively (minimal parameters), run integration tests, then iterate based on results.
