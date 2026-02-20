# API Compliance Tests Plan

## Executive Summary

This document outlines a plan for creating a new category of tests that systematically probe LLM API behavior in response to malformed message patterns. These tests are designed to help us understand how different providers respond to event log corruption scenarios, enabling us to build more robust defensive mechanisms in the SDK.

## Background: The Three Issues

Analysis of GitHub issues #2127, #1841, and #1782 reveals common patterns where the event log gets corrupted, causing LLM API calls to fail.

### Issue #2127: Missing tool_result After tool_use

**Root Cause:** An `ActionEvent` (tool_use) was recorded at 19:31:13, but its corresponding `ObservationEvent` (tool_result) wasn't recorded until 20:56:16 (87 minutes later). When a user message arrived at 20:56:14 (between the action and observation timestamps), the system built an incomplete conversation history.

**Malformed Pattern:**
```
[assistant: tool_use (toolu_016...)] → [user: "just tell me..."] → API CALL
                                                                   ↑ Missing tool_result!
```

**API Error (Anthropic):**
> `tool_use` ids were found without `tool_result` blocks immediately after: toolu_016byxnyfRGeWDr8MUVDeTnS. Each `tool_use` block must have a corresponding `tool_result` block in the next message.

### Issue #1841: User Message During Pending Tool Execution

**Root Cause:** User sends message via `send_message()` while a tool call is pending. The message is appended immediately to the event list, breaking the required ordering.

**Malformed Pattern:**
```
[assistant: tool_use] → [user: new message] → [tool: result]
                        ↑ Inserted here!
```

**Expected (Valid) Pattern:**
```
[assistant: tool_use] → [tool: result] → [user: new message]
```

### Issue #1782: Duplicate ObservationEvent

**Root Cause:** Conversation resumed after being paused/finished. On resume, a duplicate `ObservationEvent` was created with the same `tool_call_id` as an existing observation.

**Malformed Pattern:**
```
Event 94:  ActionEvent (tool_call_id=toolu_01CG...)
Event 95:  ObservationEvent (tool_call_id=toolu_01CG...)  ← First result
...
Event 116: ObservationEvent (tool_call_id=toolu_01CG...)  ← DUPLICATE!
```

**API Error (Anthropic):**
> `tool_use` ids were found without `tool_result` blocks immediately after

## Identified Malformed Patterns

Based on the issues, we've identified the following patterns that can break LLM APIs:

| # | Pattern Name | Description | Related Issue |
|---|--------------|-------------|---------------|
| 1 | Unmatched tool_use | A `tool_use` without corresponding `tool_result` | #2127 |
| 2 | Unmatched tool_result | A `tool_result` referencing non-existent `tool_call_id` | — |
| 3 | Interleaved user message | User message between `tool_use` and `tool_result` | #1841 |
| 4 | Interleaved assistant message | Assistant message between `tool_use` and `tool_result` | — |
| 5 | Duplicate tool_call_id | Multiple `tool_result` with same `tool_call_id` | #1782 |
| 6 | Wrong tool_call_id | `tool_result` references wrong `tool_use`'s ID | — |
| 7 | Parallel - missing result | Multiple tool_calls but not all results provided | — |
| 8 | Parallel - wrong order | Tool results sent before combined assistant message | — |

## Proposed Test Framework

### Test Category: `a##_*.py` (API Compliance Tests)

Located in `tests/integration/tests/`, these tests will have a distinct prefix `a` (for "API") to differentiate them from:
- `t##_*.py` - Integration tests (required, must pass)
- `b##_*.py` - Behavior tests (optional, track quality)
- `c##_*.py` - Condenser tests (optional, stress testing)

### Key Characteristics

1. **Non-blocking**: These tests are EXPECTED to fail (they intentionally send malformed data). They should never block PRs or releases.

2. **Documentary**: The purpose is to document and understand API behavior, not enforce correctness.

3. **Direct API calls**: These tests bypass the agent loop and call the LLM completion API directly with crafted message sequences.

4. **Multi-model**: Each test should run against multiple LLM providers to compare behavior.

5. **Rich output**: Tests should capture and record:
   - Whether the API accepts or rejects the request
   - The exact error message returned
   - Any patterns in error handling across providers

### Test Harness Design

```python
# tests/integration/api_compliance/__init__.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

class APIResponse(Enum):
    ACCEPTED = "accepted"       # API processed the request
    REJECTED = "rejected"       # API returned an error
    UNEXPECTED = "unexpected"   # Unexpected behavior

@dataclass
class ComplianceTestResult:
    pattern_name: str
    model: str
    provider: str
    response_type: APIResponse
    error_message: str | None
    error_type: str | None
    raw_response: dict | None
    notes: str | None

class BaseAPIComplianceTest(ABC):
    """Base class for API compliance tests."""
    
    @property
    @abstractmethod
    def pattern_name(self) -> str:
        """Human-readable name for the malformed pattern being tested."""
        pass
    
    @property
    @abstractmethod
    def pattern_description(self) -> str:
        """Description of what malformed pattern this test sends."""
        pass
    
    @abstractmethod
    def build_malformed_messages(self) -> list[dict]:
        """Construct the malformed message sequence to send to the API."""
        pass
    
    def run_test(self, llm: LLM) -> ComplianceTestResult:
        """Execute the test against the given LLM and record results."""
        messages = self.build_malformed_messages()
        try:
            response = llm.completion(messages=messages, tools=[...])
            return ComplianceTestResult(
                pattern_name=self.pattern_name,
                model=llm.model,
                provider=self._extract_provider(llm.model),
                response_type=APIResponse.ACCEPTED,
                error_message=None,
                error_type=None,
                raw_response=response,
                notes="API accepted malformed input"
            )
        except Exception as e:
            return ComplianceTestResult(
                pattern_name=self.pattern_name,
                model=llm.model,
                provider=self._extract_provider(llm.model),
                response_type=APIResponse.REJECTED,
                error_message=str(e),
                error_type=type(e).__name__,
                raw_response=None,
                notes=None
            )
```

### Test File Structure

```
tests/integration/
├── api_compliance/                    # New directory
│   ├── __init__.py                    # Exports
│   ├── base.py                        # BaseAPIComplianceTest
│   ├── result.py                      # ComplianceTestResult, APIResponse
│   ├── report.py                      # Report generation utilities
│   └── run_compliance.py              # Test runner
├── tests/
│   ├── a01_unmatched_tool_use.py      # Test file for pattern 1
│   ├── a02_unmatched_tool_result.py   # Test file for pattern 2
│   ├── a03_interleaved_user_msg.py    # Test file for pattern 3
│   ├── a04_interleaved_asst_msg.py    # Test file for pattern 4
│   ├── a05_duplicate_tool_call_id.py  # Test file for pattern 5
│   ├── a06_wrong_tool_call_id.py      # Test file for pattern 6
│   ├── a07_parallel_missing_result.py # Test file for pattern 7
│   └── a08_parallel_wrong_order.py    # Test file for pattern 8
```

### Example Test: Unmatched tool_use

```python
# tests/integration/tests/a01_unmatched_tool_use.py
"""
API Compliance Test: Unmatched tool_use

Tests how different LLM APIs respond when a tool_use message is sent
without a corresponding tool_result.

Related Issues: #2127
"""

from tests.integration.api_compliance.base import BaseAPIComplianceTest

PATTERN_NAME = "unmatched_tool_use"
DESCRIPTION = """
Sends a conversation where an assistant message contains a tool_use,
but no tool_result follows. This pattern can occur when:
- ObservationEvent is delayed/lost
- User message arrives before observation is recorded
"""

class UnmatchedToolUseTest(BaseAPIComplianceTest):
    
    @property
    def pattern_name(self) -> str:
        return PATTERN_NAME
    
    @property
    def pattern_description(self) -> str:
        return DESCRIPTION
    
    def build_malformed_messages(self) -> list[dict]:
        """Build message sequence with unmatched tool_use."""
        return [
            {
                "role": "system",
                "content": "You are a helpful assistant."
            },
            {
                "role": "user", 
                "content": "List the files in the current directory."
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_abc123",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command": "ls -la"}'
                    }
                }]
            },
            # NOTE: No tool_result follows!
            {
                "role": "user",
                "content": "What was the result?"
            }
        ]
```

### Example Test: Interleaved User Message

```python
# tests/integration/tests/a03_interleaved_user_msg.py
"""
API Compliance Test: Interleaved User Message

Tests how different LLM APIs respond when a user message appears
between tool_use and tool_result.

Related Issues: #1841
"""

PATTERN_NAME = "interleaved_user_message"
DESCRIPTION = """
Sends a conversation where a user message appears between tool_use and
tool_result. This pattern can occur when:
- User sends message during pending tool execution
- Events are appended in incorrect order
"""

class InterleavedUserMessageTest(BaseAPIComplianceTest):
    
    @property
    def pattern_name(self) -> str:
        return PATTERN_NAME
    
    @property
    def pattern_description(self) -> str:
        return DESCRIPTION
    
    def build_malformed_messages(self) -> list[dict]:
        """Build message sequence with interleaved user message."""
        return [
            {
                "role": "system",
                "content": "You are a helpful assistant."
            },
            {
                "role": "user", 
                "content": "List the files in the current directory."
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_abc123",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command": "ls -la"}'
                    }
                }]
            },
            # Interleaved user message (WRONG!)
            {
                "role": "user",
                "content": "Actually, can you also show hidden files?"
            },
            # Tool result comes after user message
            {
                "role": "tool",
                "tool_call_id": "call_abc123",
                "content": "file1.txt\nfile2.txt"
            }
        ]
```

### Test Runner Integration

The test runner should be modified to handle API compliance tests differently:

```python
# tests/integration/api_compliance/run_compliance.py

def run_compliance_tests(
    patterns: list[str] | None = None,
    models: list[dict[str, Any]] | None = None,
) -> ComplianceReport:
    """Run compliance tests across multiple models and generate report.
    
    Args:
        patterns: List of pattern names to test (None = all)
        models: List of LLM configs to test against (None = default set)
    
    Returns:
        ComplianceReport with all results
    """
    
    if models is None:
        models = [
            {"model": "anthropic/claude-sonnet-4-5-20250929"},
            {"model": "openai/gpt-4o"},
            {"model": "google/gemini-2.0-flash"},
            {"model": "deepseek/deepseek-chat"},
        ]
    
    results: list[ComplianceTestResult] = []
    
    for test_file in load_compliance_tests(patterns):
        test = load_test_from_file(test_file)
        for llm_config in models:
            llm = create_llm(llm_config)
            result = test.run_test(llm)
            results.append(result)
            log_result(result)
    
    return ComplianceReport(results=results)
```

### Output Format

```json
{
  "test_run_id": "compliance_20250101_120000",
  "run_timestamp": "2025-01-01T12:00:00Z",
  "patterns_tested": 8,
  "models_tested": ["claude-sonnet-4", "gpt-4o", "gemini-2.0-flash"],
  "summary": {
    "total_tests": 24,
    "accepted": 3,
    "rejected": 21
  },
  "results": [
    {
      "pattern_name": "unmatched_tool_use",
      "description": "A tool_use without corresponding tool_result",
      "results_by_model": {
        "anthropic/claude-sonnet-4": {
          "response_type": "rejected",
          "error_type": "BadRequestError",
          "error_message": "tool_use ids were found without tool_result blocks immediately after: call_abc123"
        },
        "openai/gpt-4o": {
          "response_type": "rejected",
          "error_type": "BadRequestError", 
          "error_message": "An assistant message with 'tool_calls' must be followed by tool messages"
        },
        "google/gemini-2.0-flash": {
          "response_type": "accepted",
          "notes": "Gemini accepted the malformed input"
        }
      }
    }
  ]
}
```

### GitHub Actions Integration

A new workflow for API compliance tests:

```yaml
# .github/workflows/api-compliance-runner.yml
name: API Compliance Tests

on:
  workflow_dispatch:
    inputs:
      reason:
        description: 'Reason for running compliance tests'
        required: true
      patterns:
        description: 'Comma-separated patterns to test (empty = all)'
        required: false
  pull_request:
    types: [labeled]
  schedule:
    - cron: '0 6 * * 0'  # Weekly on Sunday at 6 AM UTC

jobs:
  compliance-tests:
    if: |
      github.event_name == 'workflow_dispatch' || 
      github.event_name == 'schedule' ||
      (github.event_name == 'pull_request' && github.event.label.name == 'api-compliance-test')
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false  # Continue even if one model fails
      matrix:
        model:
          - anthropic/claude-sonnet-4-5-20250929
          - openai/gpt-4o
          - google/gemini-2.0-flash
          - deepseek/deepseek-chat
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      
      - name: Install dependencies
        run: |
          pip install uv
          make build
      
      - name: Run compliance tests
        continue-on-error: true  # Tests are expected to fail
        env:
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
          LLM_BASE_URL: ${{ secrets.LLM_BASE_URL }}
        run: |
          uv run python tests/integration/api_compliance/run_compliance.py \
            --model "${{ matrix.model }}" \
            --output-dir compliance-results/
      
      - name: Upload results
        uses: actions/upload-artifact@v4
        with:
          name: compliance-results-${{ matrix.model | replace('/', '-') }}
          path: compliance-results/

  generate-report:
    needs: compliance-tests
    runs-on: ubuntu-latest
    steps:
      - name: Download all results
        uses: actions/download-artifact@v4
        with:
          path: all-results/
      
      - name: Generate consolidated report
        run: |
          uv run python tests/integration/api_compliance/report.py \
            --input-dir all-results/ \
            --output compliance-report.md
      
      - name: Post report to PR
        if: github.event_name == 'pull_request'
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const report = fs.readFileSync('compliance-report.md', 'utf8');
            github.rest.issues.createComment({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: context.issue.number,
              body: report
            });
```

## Implementation Phases

### Phase 1: Foundation (Week 1)
- [ ] Create `tests/integration/api_compliance/` directory structure
- [ ] Implement `BaseAPIComplianceTest` base class
- [ ] Implement `ComplianceTestResult` data class
- [ ] Create minimal test runner for single pattern/model
- [ ] Add documentation for the test framework

### Phase 2: Core Patterns (Week 2)
- [ ] Implement Pattern 1: Unmatched tool_use (a01)
- [ ] Implement Pattern 2: Unmatched tool_result (a02)
- [ ] Implement Pattern 3: Interleaved user message (a03)
- [ ] Implement Pattern 5: Duplicate tool_call_id (a05)
- [ ] Run tests against Anthropic Claude

### Phase 3: Multi-Model Support (Week 3)
- [ ] Extend runner to support multiple LLM configurations
- [ ] Run tests against OpenAI GPT-4o
- [ ] Run tests against Google Gemini
- [ ] Generate comparison report
- [ ] Document initial findings

### Phase 4: Additional Patterns (Week 4)
- [ ] Implement Pattern 4: Interleaved assistant message (a04)
- [ ] Implement Pattern 6: Wrong tool_call_id (a06)
- [ ] Implement Pattern 7: Parallel - missing one result (a07)
- [ ] Implement Pattern 8: Parallel - out of order results (a08)

### Phase 5: CI Integration (Week 5)
- [ ] Create GitHub Actions workflow
- [ ] Set up scheduled runs
- [ ] Create human-readable report generation
- [ ] Document findings in SDK documentation
- [ ] Create runbook for interpreting results

## Success Criteria

1. **Comprehensiveness**: All 8 identified patterns are tested
2. **Multi-model coverage**: At least 4 major LLM providers tested
3. **Documentation**: Clear documentation of API behaviors
4. **Extensibility**: Easy to add new patterns and models
5. **Non-blocking**: Tests never block development workflow
6. **Actionable**: Findings inform SDK defensive mechanisms

## Open Questions

1. **Should we test with real tools or mock tool definitions?**
   - Recommendation: Use minimal mock tool definitions to reduce test complexity

2. **How often should compliance tests run?**
   - Recommendation: Weekly scheduled + on-demand via workflow_dispatch + on PR label

3. **Should we track historical results to detect API changes?**
   - Recommendation: Yes, store results in a persistent location for trend analysis

4. **Should compliance test results influence SDK defensive behavior?**
   - Recommendation: Yes, use findings to inform `View.enforce_properties()` and similar mechanisms

5. **What about rate limits and costs?**
   - Since we're sending malformed messages that get rejected early, costs should be minimal
   - Add rate limiting between tests if needed

## Related Work

- `tests/cross/test_event_loss_repro.py` - Existing test for event loss race condition
- `openhands-sdk/openhands/sdk/context/view/properties/tool_call_matching.py` - Existing property enforcement
- `openhands-sdk/openhands/sdk/context/view/view.py` - View.enforce_properties()
- Issue #2127, #1841, #1782 - The motivating issues

## Appendix: Message Format Reference

### Anthropic Claude Tool Calling Format
```json
// Assistant message with tool_use
{
  "role": "assistant",
  "content": [
    {"type": "text", "text": "I'll list the files."},
    {"type": "tool_use", "id": "toolu_xxx", "name": "terminal", "input": {"command": "ls"}}
  ]
}
// tool_result (in user message)
{
  "role": "user",
  "content": [
    {"type": "tool_result", "tool_use_id": "toolu_xxx", "content": "file1.txt\nfile2.txt"}
  ]
}
```

### OpenAI Tool Calling Format
```json
// Assistant message with tool_calls
{
  "role": "assistant",
  "content": null,
  "tool_calls": [
    {"id": "call_xxx", "type": "function", "function": {"name": "terminal", "arguments": "{\"command\": \"ls\"}"}}
  ]
}
// Tool response
{
  "role": "tool",
  "tool_call_id": "call_xxx",
  "content": "file1.txt\nfile2.txt"
}
```

### Key Differences by Provider

| Provider | tool_result Role | tool_call_id Field | Notes |
|----------|-----------------|-------------------|-------|
| Anthropic | `user` (with content type) | `tool_use_id` | Strict ordering required |
| OpenAI | `tool` | `tool_call_id` | Strict ordering required |
| Google Gemini | `function` | `name` | More lenient |
| DeepSeek | `tool` (OpenAI-style) | `tool_call_id` | OpenAI-compatible |

## Appendix: Existing SDK Defensive Mechanisms

The SDK already has some defensive mechanisms for event ordering:

### ToolCallMatchingProperty (view/properties/tool_call_matching.py)

This property enforces that actions and observations are paired:
- Actions without observations are removed
- Observations without actions are removed

However, it doesn't handle:
- Interleaved messages (it only checks existence, not order)
- Duplicate tool_call_ids (it uses sets)

### Potential Improvements Based on Compliance Test Findings

1. **Reordering in events_to_messages()**: Could detect and fix message ordering
2. **Duplicate detection**: Could detect and remove duplicate tool_results
3. **Message queueing**: Could queue user messages until tool execution completes

These improvements should be informed by the compliance test results showing which patterns are actually rejected by which providers.
