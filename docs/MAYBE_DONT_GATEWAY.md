# Maybe Don't Security Gateway Integration

This guide explains how to integrate the Maybe Don't security gateway with OpenHands Agent SDK to add runtime security policy enforcement for tool executions.

## Overview

[Maybe Don't](https://www.maybedont.ai/) is a security gateway that sits between AI assistants and external tools/servers, monitoring and blocking potentially dangerous AI actions before they execute. It operates as a transparent Model Context Protocol (MCP) gateway, intercepting tool calls and applying security policies in real-time.

## Architecture

The integration follows a three-tier architecture:

```
┌─────────────────────┐
│  OpenHands Agent    │
│   (MCP Client)      │
└──────────┬──────────┘
           │ MCP Protocol
           ▼
┌─────────────────────┐
│  Maybe Don't        │
│  Security Gateway   │
│  (MCP Middleware)   │
└──────────┬──────────┘
           │ MCP Protocol
           ▼
┌─────────────────────┐
│  Downstream MCP     │
│  Servers (Tools)    │
└─────────────────────┘
```

**Key Points:**
- OpenHands Agent connects to Maybe Don't as if it were a regular MCP server
- Maybe Don't intercepts all tool calls, applies security policies, and proxies to downstream servers
- All communication uses standard MCP protocol (no custom APIs)
- Maybe Don't runs as a separate process/service

## Prerequisites

1. **Install Maybe Don't:**
   - Download from [maybedont.ai/download](https://www.maybedont.ai/download/)
   - Or build from source if available

2. **OpenHands Agent SDK:**
   - Already includes MCP client support via `fastmcp`
   - No additional SDK changes needed

## Setup Guide

### Step 1: Configure Maybe Don't Gateway

Create a `gateway-config.yaml` file (in current directory or `~/.maybe-dont/`):

```yaml
# Server configuration - how MCP clients connect to Maybe Don't
server:
  type: http
  listen_addr: "127.0.0.1:8080"
  # Alternatives:
  # type: stdio  # for local process communication
  # type: sse    # for server-sent events with TLS

# Downstream MCP servers that Maybe Don't proxies to
downstream:
  - name: "filesystem"
    type: stdio
    command: "uvx"
    args: ["mcp-server-filesystem", "--allowed-directory", "/workspace"]

  - name: "fetch"
    type: stdio
    command: "uvx"
    args: ["mcp-server-fetch"]

  # Example HTTP downstream server
  # - name: "remote-api"
  #   type: http
  #   url: "https://api.example.com/mcp"
  #   headers:
  #     Authorization: "Bearer ${API_TOKEN}"

# Security validation (CEL-based rules)
cel_validation:
  enabled: true
  # Built-in rules prevent dangerous operations like:
  # - Mass deletion (rm -rf, wildcards)
  # - System directory access
  # - Credential file exposure

# AI-powered validation (optional but recommended)
ai_validation:
  enabled: true
  endpoint: "https://api.openai.com/v1/chat/completions"
  api_key: "${OPENAI_API_KEY}"
  model: "gpt-4o-mini"
  # AI validation detects:
  # - Dangerous command patterns
  # - Suspicious file operations
  # - Network security risks
  # - Large-scale modifications

# Audit logging
audit:
  file_path: "./maybe-dont-audit.log"
  # Logs all tool calls with timestamps, decisions, and reasons
```

### Step 2: Start Maybe Don't Gateway

```bash
# Set required environment variables
export OPENAI_API_KEY="your-api-key-here"

# Start the gateway (assuming binary is in PATH)
maybe-dont --config gateway-config.yaml

# Gateway will start listening on the configured address (e.g., http://127.0.0.1:8080)
```

### Step 3: Configure OpenHands to Use Maybe Don't

Update your OpenHands agent configuration to connect to Maybe Don't instead of direct MCP servers:

```python
import os
from pydantic import SecretStr
from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.tool import Tool
from openhands.tools.execute_bash import BashTool
from openhands.tools.file_editor import FileEditorTool

# Configure LLM
llm = LLM(
    model="openhands/claude-sonnet-4-5-20250929",
    api_key=SecretStr(os.getenv("LLM_API_KEY")),
)

# Regular OpenHands tools
tools = [
    Tool(name=BashTool.name),
    Tool(name=FileEditorTool.name),
]

# MCP configuration pointing to Maybe Don't gateway
mcp_config = {
    "mcpServers": {
        # Connect to Maybe Don't gateway
        # The gateway will proxy to downstream MCP servers (filesystem, fetch, etc.)
        "secured-tools": {
            "url": "http://127.0.0.1:8080",
            "transport": "sse"  # or "http" depending on gateway config
        }
    }
}

# Create agent with MCP gateway configuration
agent = Agent(
    llm=llm,
    tools=tools,
    mcp_config=mcp_config,
)

# Use the agent normally - all MCP tool calls now go through Maybe Don't
conversation = Conversation(agent=agent, workspace="./workspace")
conversation.send_message("List files in the current directory")
conversation.run()
```

## Security Policies

Maybe Don't enforces security through two complementary mechanisms:

### 1. CEL-Based Rules (Deterministic)

Common Expression Language rules provide fast, deterministic validation:

**Built-in protections:**
- Prevents `rm -rf` and wildcard deletion operations
- Blocks access to system directories (`/etc`, `/sys`, `/proc`)
- Prevents reading credential files (`.env`, `credentials.json`, etc.)
- Validates command arguments for dangerous patterns

**Custom rules** can be added in `gateway-config.yaml` to match your security requirements.

### 2. AI-Powered Validation (Contextual)

LLM-based analysis detects sophisticated attack patterns:

- **Mass operations:** Identifies attempts to delete/modify many files
- **Command injection:** Detects suspicious command chaining
- **Data exfiltration:** Flags unusual network access patterns
- **Privilege escalation:** Identifies attempts to modify system settings
- **Context-aware:** Understands intent beyond simple pattern matching

## Configuration Options

### Transport Types

Maybe Don't supports three MCP transport mechanisms:

| Transport | Use Case | Configuration |
|-----------|----------|---------------|
| **STDIO** | Local development, single process | `command` and `args` |
| **HTTP** | Network deployments, containers | `url` with base URL |
| **SSE** | Streaming, long-lived connections | `url` with optional TLS |

### Pass-Through Authentication

For multi-user scenarios, Maybe Don't can extract and forward client credentials:

```yaml
pass_through:
  enabled: true
  headers:
    - source_header: "X-GitHub-Token"
      target_header: "Authorization"
      format: "Bearer {value}"
```

This allows individual users to authenticate through the gateway to downstream services.

## Example: Complete Integration

See `examples/01_standalone_sdk/27_maybe_dont_gateway.py` for a complete working example demonstrating:

- Gateway setup and configuration
- Agent configuration with MCP gateway
- Safe operations (allowed)
- Risky operations (flagged or denied)
- Error handling and logging

## Monitoring and Auditing

### Audit Logs

Maybe Don't writes detailed audit logs to the configured file:

```
2025-01-15T10:30:45Z [ALLOW] tool=read_file path=/workspace/README.md user=alice
2025-01-15T10:31:12Z [FLAG] tool=write_file path=/etc/hosts reason="System directory access" user=alice
2025-01-15T10:32:03Z [DENY] tool=bash_command cmd="rm -rf *" reason="Mass deletion detected" user=bob
```

### Monitoring

Review audit logs regularly to:
1. Identify security incidents
2. Tune security policies
3. Understand agent behavior patterns
4. Comply with audit requirements

## Troubleshooting

### Connection Issues

**Symptom:** OpenHands cannot connect to MCP tools

**Solutions:**
- Verify Maybe Don't is running: `curl http://127.0.0.1:8080/health` (if HTTP)
- Check gateway-config.yaml server configuration matches mcp_config URL
- Review Maybe Don't logs for startup errors

### Tools Not Available

**Symptom:** MCP tools not showing up in agent

**Solutions:**
- Verify downstream servers are configured correctly in gateway-config.yaml
- Check downstream server processes are starting (review Maybe Don't logs)
- Test downstream servers directly without gateway to isolate issues

### All Actions Denied

**Symptom:** Maybe Don't blocks all tool executions

**Solutions:**
- Review ai_validation configuration (API key, endpoint)
- Check CEL rules aren't overly restrictive
- Temporarily disable AI validation to test CEL rules in isolation
- Review audit logs for specific denial reasons

### Performance Issues

**Symptom:** Slow tool execution

**Solutions:**
- AI validation adds latency (~100-500ms per call)
- Consider disabling AI validation for low-risk environments
- Use CEL rules only for performance-critical deployments
- Monitor AI validation API response times

## Security Best Practices

1. **Defense in Depth:** Use both CEL rules and AI validation for comprehensive coverage
2. **Least Privilege:** Configure downstream servers with minimal required permissions
3. **Regular Audits:** Review audit logs at least weekly
4. **Policy Tuning:** Adjust rules based on false positive/negative rates
5. **Credential Management:** Use environment variables for API keys, never hardcode
6. **Network Isolation:** Run Maybe Don't in a trusted network segment
7. **Access Control:** Implement authentication on the gateway (pass-through or gateway-level)

## Comparison with Other Approaches

| Approach | Integration Point | Advantages | Disadvantages |
|----------|------------------|------------|---------------|
| **Maybe Don't Gateway** | MCP Protocol | Transparent, no SDK changes, works with all MCP tools | Requires separate gateway process |
| **SDK-level Hooks** | Before tool execution | Integrated, no external dependencies | Requires SDK modifications, bypassed if SDK is replaced |
| **LLM Security Analyzer** | LLM prompt | Preventative, catches issues early | No runtime enforcement, depends on LLM compliance |

**Recommendation:** Use Maybe Don't Gateway + LLM Security Analyzer for comprehensive security.

## Advanced Configuration

### Multiple Downstream Servers

Configure different security policies for different server groups:

```yaml
downstream:
  # Low-risk read-only servers
  - name: "filesystem-readonly"
    type: stdio
    command: "uvx"
    args: ["mcp-server-filesystem", "--readonly"]

  # High-risk write servers with strict validation
  - name: "filesystem-write"
    type: stdio
    command: "uvx"
    args: ["mcp-server-filesystem", "--allowed-directory", "/workspace"]
    cel_rules: "strict"  # Apply stricter rules to this server
```

### Custom CEL Rules

Add custom security rules in gateway configuration:

```yaml
cel_validation:
  enabled: true
  custom_rules:
    - name: "prevent_production_access"
      expression: |
        !request.arguments.path.contains('/production') &&
        !request.arguments.path.contains('/prod')
      message: "Access to production paths is not allowed"
```

## Resources

- **Maybe Don't Documentation:** [maybedont.ai/docs](https://www.maybedont.ai/docs/)
- **MCP Specification:** [modelcontextprotocol.io](https://modelcontextprotocol.io/)
- **OpenHands MCP Guide:** [docs.openhands.dev/sdk/guides/mcp](https://docs.openhands.dev/sdk/guides/mcp)
- **Example Code:** `examples/01_standalone_sdk/27_maybe_dont_gateway.py`

## Support

For issues or questions:
- OpenHands SDK: [GitHub Issues](https://github.com/OpenHands/agent-sdk/issues)
- Maybe Don't: Check their official documentation and support channels
- MCP Protocol: [GitHub Discussions](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions)
