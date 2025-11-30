# ACPAgent Implementation

This document describes the minimal implementation of `ACPAgent` for the OpenHands SDK.

## Overview

`ACPAgent` is an Agent-Client Protocol (ACP) client implementation that allows OpenHands to communicate with ACP servers like Claude-Code or Gemini CLI. It acts as a bridge between the OpenHands SDK and ACP servers.

## Architecture

```
┌─────────────────────────┐
│  OpenHands SDK          │
│  (Conversation)         │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  ACPAgent               │
│  (ACP Client)           │
└───────────┬─────────────┘
            │ JSON-RPC 2.0
            │ via stdin/stdout
            ▼
┌─────────────────────────┐
│  ACP Server             │
│  (claude-code, etc.)    │
└─────────────────────────┘
```

## Key Features

### Implemented

1. **Basic ACP Protocol Support**
   - JSON-RPC 2.0 communication
   - Session initialization (`session/new`)
   - Prompt/response flow (`session/prompt`)
   - Subprocess management for ACP servers

2. **SDK Integration**
   - Inherits from `AgentBase`
   - Implements `step()` method
   - Converts SDK conversation state to ACP messages
   - Translates ACP responses back to SDK events

3. **Feature Validation**
   - Checks for unsupported features at initialization
   - Raises `NotImplementedError` for:
     - Custom tools
     - MCP configuration
     - Agent context (microagents)
     - Security analyzers
     - Context condensers

### Not Yet Supported

The following SDK features are not currently supported by ACPAgent:
- Custom tools (ACP server manages its own tools)
- MCP configuration
- Microagents
- Security analyzers
- Context condensers
- Tool filtering

## Usage Example

```python
from openhands.sdk import LLM, Conversation
from openhands.sdk.agent import ACPAgent

# Create LLM configuration
llm = LLM(model="claude-sonnet-4-20250514", api_key="your-key")

# Create ACPAgent with ACP server command
agent = ACPAgent(
    llm=llm,
    acp_command=["npx", "-y", "claude-code-acp"],
    acp_args=[],
)

# Create conversation
conversation = Conversation(agent=agent, workspace="/tmp/workspace")

# Send a message
conversation.send_message("Hello! Can you help me write a Python script?")

# Run the conversation
conversation.run()

# Check the response
for event in conversation.state.events:
    if isinstance(event, MessageEvent):
        print(f"Message: {event.content}")
```

## Implementation Details

### File Structure

```
openhands-sdk/openhands/sdk/agent/
├── acp_agent.py          # ACPAgent implementation
└── __init__.py           # Export ACPAgent

examples/01_standalone_sdk/
└── 27_acp_agent_example.py  # Usage example

tests/sdk/agent/
└── test_acp_agent.py     # Unit tests
```

### ACP Protocol Flow

1. **Initialization**
   - Start ACP server subprocess
   - Send `initialize` request
   - Create new session with `session/new`

2. **Message Exchange**
   - Convert SDK messages to ACP format
   - Send `session/prompt` request
   - Parse ACP response
   - Create SDK MessageEvent

3. **Cleanup**
   - Terminate ACP server subprocess

### JSON-RPC Message Format

Request:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "session/prompt",
  "params": {
    "sessionId": "session-uuid",
    "prompt": "User message here"
  }
}
```

Response:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "response": "Agent response here"
  }
}
```

## Testing

Run the ACPAgent tests:
```bash
uv run pytest tests/sdk/agent/test_acp_agent.py -v
```

Tests cover:
- Agent initialization
- Feature validation
- Subprocess management
- Error handling

## Future Work

### Short Term
1. Complete JSON-RPC implementation
2. Handle all ACP protocol events
3. Implement proper error handling
4. Add more comprehensive tests

### Long Term
1. Support microagents through ACP
2. Tool translation layer
3. Security policy integration
4. Context management

## References

- [Agent-Client Protocol Specification](https://agentclientprotocol.com/protocol/overview)
- [Claude-Code ACP](https://github.com/zed-industries/claude-code-acp)
- [OpenHands SDK Documentation](https://github.com/OpenHands/docs/tree/main/sdk)
