---
title: LLM Test Data Fixtures
description: Real LLM completion data collected for comprehensive testing of the LLM class and related components. Includes function calling and non-function calling data.
---

# LLM Test Data Fixtures

This directory contains real LLM completion data collected from `examples/hello_world.py` for comprehensive testing of the LLM class and related components.

## Structure

```
tests/fixtures/llm_data/
├── README.mdx                     # This file
├── fncall-llm-message.json       # Function calling conversation messages
├── nonfncall-llm-message.json    # Non-function calling conversation messages
├── llm-logs/                     # Raw function calling completion logs
│   └── *.json                    # Individual completion log files
└── nonfncall-llm-logs/           # Raw non-function calling completion logs
    └── *.json                    # Individual completion log files
```

## Data Sources

### Function Calling Data
- **Model**: `litellm_proxy/anthropic/claude-sonnet-4-20250514`
- **Features**: Native function calling support
- **Files**: `fncall-llm-message.json`, `llm-logs/*.json`

### Non-Function Calling Data
- **Model**: `litellm_proxy/deepseek/deepseek-chat`
- **Features**: Prompt-based function calling mocking
- **Files**: `nonfncall-llm-message.json`, `nonfncall-llm-logs/*.json`

## File Formats

### Message Files (`*-llm-message.json`)
Contains conversation messages in OpenHands format:
```json
[
  {
    "role": "system",
    "content": "System prompt..."
  },
  {
    "role": "user", 
    "content": "User message..."
  },
  {
    "role": "assistant",
    "content": "Assistant response...",
    "tool_calls": [...]  // Only in function calling data
  },
  {
    "role": "tool",
    "content": "Tool result...",
    "tool_call_id": "..."  // Only in function calling data
  }
]
```

### Raw Log Files (`*/logs/*.json`)
Contains complete LiteLLM completion logs:
```json
{
  "messages": [...],           // Request messages
  "tools": [...],             // Tool definitions (if any)
  "kwargs": {...},            // Request parameters
  "context_window": 200000,   // Model context window
  "response": {               // LiteLLM response
    "id": "...",
    "model": "...",
    "choices": [...],
    "usage": {...}
  },
  "cost": 0.016626,          // API cost
  "timestamp": 1757003287.33, // Unix timestamp
  "latency_sec": 3.305       // Response latency
}
```


## Regenerating Test Data

Use the test data generator utility to create new test fixtures:

```bash
# Generate new test data
python tests/fixtures/llm_data/test_data_generator.py --api-key YOUR_API_KEY

# Validate existing test data
python tests/fixtures/llm_data/test_data_generator.py --api-key YOUR_API_KEY --validate-only

# Custom models and messages
python tests/fixtures/llm_data/test_data_generator.py \
  --api-key YOUR_API_KEY \
  --fncall-model "litellm_proxy/openai/gpt-5.5" \
  --nonfncall-model "litellm_proxy/deepseek/deepseek-chat" \
  --user-message "Create a Python script that calculates fibonacci numbers"
```