# Maybe Don't Security Gateway Examples

This directory contains example configurations for integrating the Maybe Don't security gateway with OpenHands Agent SDK.

## Overview

Maybe Don't is a security gateway that sits between AI assistants and external tools, providing real-time security policy enforcement for tool executions using the Model Context Protocol (MCP).

## Files in This Directory

### `gateway-config.yaml`

Complete example configuration for Maybe Don't gateway including:

- **Server configuration** - How MCP clients connect (HTTP/STDIO/SSE)
- **Downstream servers** - MCP tool servers that gateway proxies to
- **CEL validation** - Deterministic security rules
- **AI validation** - LLM-based contextual threat analysis
- **Audit logging** - Comprehensive logging configuration
- **Pass-through auth** - Multi-user credential forwarding
- **Custom policies** - Example security rules

## Quick Start

### 1. Install Maybe Don't

Download from [maybedont.ai/download](https://www.maybedont.ai/download/)

### 2. Configure the Gateway

Copy the example configuration:

```bash
cp examples/maybe_dont/gateway-config.yaml ./gateway-config.yaml
```

Edit `gateway-config.yaml` to customize:
- Server listening address
- Downstream MCP servers
- Security policies
- Audit log location

### 3. Set Environment Variables

```bash
# Required for AI validation
export OPENAI_API_KEY="your-openai-api-key"

# Optional: custom tokens for downstream servers
export GITHUB_TOKEN="your-github-token"
```

### 4. Start the Gateway

```bash
maybe-dont --config gateway-config.yaml
```

The gateway will start and listen on the configured address (default: `http://127.0.0.1:8080`).

### 5. Run OpenHands with Gateway

```bash
# Enable Maybe Don't integration
export MAYBE_DONT_ENABLED=true
export LLM_API_KEY="your-llm-api-key"

# Run the example
python examples/01_standalone_sdk/27_maybe_dont_gateway.py
```

## Configuration Options

### Server Types

The gateway supports three transport mechanisms:

**STDIO** - Local process communication
```yaml
server:
  type: stdio
```

**HTTP** - Network-based communication
```yaml
server:
  type: http
  listen_addr: "127.0.0.1:8080"
```

**SSE (Server-Sent Events)** - Streaming with optional TLS
```yaml
server:
  type: sse
  listen_addr: "127.0.0.1:8443"
  tls:
    cert_file: "/path/to/cert.pem"
    key_file: "/path/to/key.pem"
```

### Downstream Servers

Configure which MCP tool servers the gateway proxies to:

```yaml
downstream:
  # Local STDIO server
  - name: "filesystem"
    type: stdio
    command: "uvx"
    args: ["mcp-server-filesystem", "--allowed-directory", "./workspace"]

  # Remote HTTP server
  - name: "github"
    type: http
    url: "https://api.github.com/mcp"
    headers:
      Authorization: "Bearer ${GITHUB_TOKEN}"
```

### Security Policies

#### CEL Rules (Fast, Deterministic)

Built-in protections:
- Mass deletion prevention (`rm -rf`, wildcards)
- System directory access blocking
- Credential file protection
- Dangerous command patterns

Add custom rules:
```yaml
cel_validation:
  enabled: true
  custom_rules:
    - name: "prevent_production_access"
      expression: "!request.arguments.path.contains('/production')"
      message: "Production access not allowed"
```

#### AI Validation (Contextual, Intelligent)

LLM-based analysis for sophisticated threats:
```yaml
ai_validation:
  enabled: true
  endpoint: "https://api.openai.com/v1/chat/completions"
  api_key: "${OPENAI_API_KEY}"
  model: "gpt-4o-mini"
```

Detects:
- Command injection
- Data exfiltration attempts
- Privilege escalation
- Suspicious patterns
- Context-aware threats

### Audit Logging

Comprehensive logging of all operations:

```yaml
audit:
  enabled: true
  file_path: "./maybe-dont-audit.log"
  format: "json"
  log_allowed: true
  log_denied: true
  log_flagged: true
```

Log entries include:
- Timestamp
- Tool name and arguments
- Security decision (ALLOW/DENY/FLAG)
- Reason for decision
- User/client information
- Risk severity

## Example Scenarios

The example configuration includes policies that demonstrate:

### ✓ Allowed Operations

- Reading files in workspace
- Creating files in allowed directories
- Fetching from approved URLs
- Running safe commands

### ⚠️ Flagged Operations

- Bulk file operations
- Modifications to system-adjacent directories
- Network requests to internal domains
- Large file transfers

### ✗ Denied Operations

- Access to system directories (`/etc`, `/sys`)
- Mass deletion operations
- Reading credential files
- Dangerous command patterns
- Privilege escalation attempts

## Monitoring

### Health Check

```bash
curl http://127.0.0.1:8080/health
```

### Metrics (Prometheus format)

```bash
curl http://127.0.0.1:8080/metrics
```

### Audit Log Review

```bash
# View recent denials
grep "DENY" maybe-dont-audit.log | tail -20

# View flagged operations
grep "FLAG" maybe-dont-audit.log | tail -20

# Count operations by decision
jq '.action' maybe-dont-audit.log | sort | uniq -c
```

## Troubleshooting

### Gateway Won't Start

- Check `gateway-config.yaml` syntax (valid YAML)
- Verify port 8080 is not in use: `lsof -i :8080`
- Check environment variables are set
- Review gateway error logs

### OpenHands Can't Connect

- Verify gateway is running: `curl http://127.0.0.1:8080/health`
- Check `MAYBE_DONT_URL` matches gateway address
- Ensure firewall allows connections
- Try STDIO transport for local testing

### All Operations Denied

- Check OPENAI_API_KEY is valid
- Review ai_validation configuration
- Check CEL rules aren't too restrictive
- Look at audit log for specific denial reasons
- Temporarily disable AI validation to isolate issue

### Performance Issues

- AI validation adds ~100-500ms latency
- Consider disabling for low-risk environments
- Use CEL rules only for performance-critical paths
- Monitor AI API response times
- Increase timeout values if needed

## Best Practices

1. **Start Conservative** - Use restrictive policies, then loosen as needed
2. **Monitor Audit Logs** - Review regularly for false positives/negatives
3. **Test Policies** - Validate rules don't block legitimate operations
4. **Use Environment Variables** - Never hardcode secrets in config
5. **Enable Both Validations** - CEL + AI provides comprehensive coverage
6. **Set Up Alerts** - Monitor for high DENY rates or security events
7. **Document Custom Rules** - Maintain clear descriptions of policy decisions
8. **Regular Updates** - Keep Maybe Don't and policies up to date

## Advanced Configuration

### Multi-Tenant Setup

Configure pass-through authentication for multiple users:

```yaml
pass_through:
  enabled: true
  headers:
    - source_header: "X-User-Token"
      target_header: "Authorization"
      format: "Bearer {value}"
```

### Environment-Specific Policies

Use different configurations for dev/staging/prod:

```bash
# Development - permissive
maybe-dont --config gateway-config-dev.yaml

# Production - strict
maybe-dont --config gateway-config-prod.yaml
```

### Custom AI Prompts

Customize the AI validation behavior:

```yaml
ai_validation:
  system_prompt: |
    You are a security validator for AI tool execution in a [healthcare/finance/etc] environment.
    Apply industry-specific security standards when analyzing tool calls.
```

## Resources

- **Complete Guide:** `../../docs/MAYBE_DONT_GATEWAY.md`
- **Example Code:** `../01_standalone_sdk/27_maybe_dont_gateway.py`
- **Maybe Don't Docs:** [maybedont.ai/docs](https://www.maybedont.ai/docs/)
- **MCP Specification:** [modelcontextprotocol.io](https://modelcontextprotocol.io/)

## Support

For issues or questions:
- OpenHands SDK: [GitHub Issues](https://github.com/OpenHands/agent-sdk/issues)
- Maybe Don't: Check official documentation and support channels
- Example questions: Tag issues with `maybe-dont` label
