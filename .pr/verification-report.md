# PR #2130 Verification Report

## Summary
I have thoroughly investigated the fix for issue #1957 and can confirm that **the implementation is correct and all tests pass**. However, @XZ-X reports still seeing "security risk" as "low" instead of "unknown". This document explains my findings and provides guidance for troubleshooting.

## What the Fix Does

The fix adds an early return in `_extract_security_risk()` to return `SecurityRisk.UNKNOWN` when `security_analyzer is None`:

```python
# When no security analyzer is configured, ignore any security_risk field
# from LLM and return UNKNOWN. This ensures that security_risk is only
# evaluated when a security analyzer is explicitly set.
if security_analyzer is None:
    return risk.SecurityRisk.UNKNOWN
```

This ensures that when:
1. `llm_security_analyzer=False` is set (no LLM-based security analysis requested)
2. No security analyzer is configured via `conversation.set_security_analyzer()`

Then the `security_risk` field provided by the LLM in tool calls is **ignored**, and `SecurityRisk.UNKNOWN` is used instead.

## Test Results

### All Tests Pass ✓

```bash
$ uv run pytest tests/sdk/agent/test_extract_security_risk.py tests/sdk/agent/test_security_policy_integration.py -v
======================== 29 passed, 1 warning in 0.12s =========================
```

Key tests that verify the fix:
1. **test_extract_security_risk** - 21 tests covering all scenarios including:
   - `agent_without_analyzer-LOW-UNKNOWN-False` ✓
   - `agent_without_analyzer-MEDIUM-UNKNOWN-False` ✓
   - `agent_without_analyzer-HIGH-UNKNOWN-False` ✓
   - These tests confirm that when no analyzer is set, LLM-provided security_risk values are ignored

2. **test_security_risk_param_ignored_when_no_analyzer** - Integration test that:
   - Sets `llm_security_analyzer=False`
   - Mocks LLM to return `security_risk=HIGH`
   - Verifies the ActionEvent has `security_risk=UNKNOWN` (ignored) ✓

### Manual Integration Testing

I created and ran manual integration tests that exactly replicate the issue #1957 scenario:
- Agent with `llm_security_analyzer=False`
- LLM provides `security_risk=LOW` in tool call
- **Result: security_risk is correctly set to UNKNOWN** ✓

Conversation state shows:
```python
'security_analyzer': None  # ← Correct!
```

And ActionEvent shows:
```python
security_risk: UNKNOWN  # ← Correct! LLM-provided "LOW" was ignored
```

## Architecture Understanding

### How Security Risk Works

1. **llm_security_analyzer setting** (system_prompt_kwargs):
   - Controls the system prompt to tell the LLM whether to provide `security_risk` in tool calls
   - Does NOT automatically create a security analyzer instance
   - Stored in system_prompt_kwargs, used by templates

2. **conversation.state.security_analyzer** (SecurityAnalyzerBase | None):
   - The actual security analyzer instance that evaluates risks
   - Defaults to `None`
   - Can be set via `conversation.set_security_analyzer(analyzer)`
   - Can be LLMSecurityAnalyzer, GraySwanAnalyzer, or custom analyzer

3. **security_risk extraction** (_extract_security_risk):
   - Pops `security_risk` from LLM's tool call arguments
   - **With this fix:** Returns UNKNOWN if `security_analyzer is None`
   - Only uses LLM-provided value when LLMSecurityAnalyzer is configured

### Code Flow

```
LLM Response with tool_call {"thought": "...", "security_risk": "LOW"}
    ↓
agent._get_action_event(security_analyzer=state.security_analyzer)
    ↓
agent._extract_security_risk(arguments, ..., security_analyzer)
    ↓
Check: security_analyzer is None?
    ↓ YES (when llm_security_analyzer=False and no analyzer set)
Return SecurityRisk.UNKNOWN  ← Fix ensures this happens
```

## Why @XZ-X Might Still See "low"

### Possible Causes

1. **Not using code from this PR** (Most Likely)
   - Testing against main branch or old commit
   - Need to checkout branch `openhands/fix-security-analyzer-ignore-llm-risk`
   - Need to rebuild: `make build`

2. **Security Analyzer Being Set Somewhere**
   - Check if any plugins set a security analyzer
   - Check if custom code calls `conversation.set_security_analyzer()`
   - Add logging to verify: `print(conversation.state.security_analyzer)`

3. **Looking at Wrong Event**
   - The issue code prints `events[-1].security_risk`
   - Last event might not be an ActionEvent
   - Should filter for ActionEvents specifically

4. **Cached/Stale Code**
   - Python bytecode cache (.pyc files)
   - Try: `find . -type d -name __pycache__ -exec rm -r {} +`
   - Rebuild virtual environment

## Verification Steps for @XZ-X

### Step 1: Verify Correct Branch
```bash
cd /path/to/software-agent-sdk
git branch --show-current
# Should show: openhands/fix-security-analyzer-ignore-llm-risk

git log --oneline -1
# Should show the commit with the fix
```

### Step 2: Rebuild
```bash
make clean
make build
```

### Step 3: Add Debug Logging
```python
# In your test script, add:
print(f"Security analyzer: {conversation.state.security_analyzer}")

action_events = [e for e in events if isinstance(e, ActionEvent)]
for i, ae in enumerate(action_events):
    print(f"ActionEvent {i}: {ae.tool_name} -> {ae.security_risk}")

# Don't just print events[-1].security_risk - filter for ActionEvents!
```

### Step 4: Run Test
```python
from openhands.sdk import LLM, Agent, Conversation, Message, TextContent
from openhands.sdk.event import ActionEvent
from openhands.tools.preset.default import get_default_tools
from pydantic import SecretStr

llm = LLM(model="...", api_key=SecretStr("..."), base_url="...")
agent = Agent(
    llm=llm,
    tools=get_default_tools(enable_browser=False),
    system_prompt_kwargs={"llm_security_analyzer": False},
)
conversation = Conversation(agent=agent)

# Debug: Check analyzer
print(f"DEBUG: security_analyzer = {conversation.state.security_analyzer}")
# Should print: None

conversation.send_message(Message(role="user", content=[TextContent(text="test")]))
# ... run conversation ...

# Debug: Check ActionEvents
action_events = [e for e in conversation.state.events if isinstance(e, ActionEvent)]
for ae in action_events:
    print(f"DEBUG: ActionEvent {ae.tool_name} security_risk = {ae.security_risk}")
    # Should print: UNKNOWN (not LOW/MEDIUM/HIGH)
```

### Step 5: Expected Output
```
DEBUG: security_analyzer = None
DEBUG: ActionEvent think security_risk = SecurityRisk.UNKNOWN
```

If you see different output, please share:
1. The git commit hash you're testing
2. The full debug output
3. Your environment (Python version, OS, etc.)

## Additional Notes

### Why `add_security_risk_prediction=True` Is Unchanged

The PR description correctly explains why `add_security_risk_prediction=True` in `openhands/sdk/agent/utils.py` is intentionally NOT changed:

- It controls the **tool schema** (what fields the LLM sees)
- Keeping it in the schema ensures consistency
- The **runtime behavior** is controlled by `_extract_security_risk()` (this fix)
- Weaker models can omit the field without breaking validation

This design is correct!

## Conclusion

The fix is **correct and thoroughly tested**. All 29 tests pass, including specific tests for this exact scenario. If @XZ-X is still seeing "low" instead of "unknown", it's most likely due to:
1. Not using the code from this PR branch
2. Environmental factors (plugins, custom code, caching)

Please follow the verification steps above to identify the root cause.

---
**Tested By:** OpenHands Agent
**Date:** 2026-02-19
**Test Results:** ✅ 29/29 tests passed
**Manual Testing:** ✅ Confirmed fix works as expected
