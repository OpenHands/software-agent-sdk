# GitHub Autospawn Configuration

This document explains how to configure GitHub webhook autospawn for OpenHands Agent Server.

## Overview

The GitHub autospawn feature allows you to automatically spawn OpenHands agents in response to GitHub webhook events (e.g., when a PR is opened, an issue is created, or code is pushed).

## Configuration

### Basic Setup

Add a `github_autospawn` section to your agent-server configuration:

```yaml
# config.yaml
github_autospawn:
  github_secret: "your-webhook-secret-here"
  triggers:
    - event: "pull_request"
      action: "opened"
      repo: "owner/repository"
      agent_config:
        task: "Review this pull request and check for common issues."
        agent:
          llm:
            model: "anthropic/claude-3-5-sonnet-20240620"
            api_key: "${ANTHROPIC_API_KEY}"
          tools:
            - name: "file_editor"
            - name: "terminal"
        max_iterations: 100
```

### Configuration via Environment Variables

GitHub webhook secret can be loaded from environment variables using the `OH_` prefix:

```bash
export OH_GITHUB_AUTOSPAWN__GITHUB_SECRET="your-webhook-secret"
```

### Configuration Fields

#### `GitHubWebhookConfig`

- `github_secret` (SecretStr, optional): GitHub webhook secret for HMAC signature verification. If not set, signature verification is skipped (**not recommended for production**).
- `triggers` (list): List of trigger configurations that define when to spawn agents.
- `workspace_base_dir` (str, optional): Base directory for creating workspaces. Defaults to system temp directory.
- `cleanup_on_success` (bool, default: True): Whether to cleanup workspace after successful execution.
- `cleanup_on_failure` (bool, default: False): Whether to cleanup workspace after failed execution. In DEBUG mode, workspaces are always kept for debugging.

#### `GitHubTriggerConfig`

- `event` (str): GitHub event type (e.g., `pull_request`, `issues`, `push`).
- `action` (str, optional): Event action filter (e.g., `opened`, `labeled`, `closed`).
- `repo` (str): Repository filter in `owner/repo` format.
- `branch` (str, optional): Branch filter for push events.
- `agent_config`: Configuration for the agent to spawn.

#### `GitHubAgentConfig`

- `task` (str): Task prompt to give to the agent.
- `agent` (AgentBase): Full agent configuration including LLM and tools.
- `max_iterations` (int, default: 500): Maximum number of iterations the agent will run.

## GitHub Webhook Setup

1. **Generate a webhook secret**:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

2. **Add webhook to your GitHub repository**:
   - Go to Settings → Webhooks → Add webhook
   - **Payload URL**: `https://your-server.com/webhooks/github`
   - **Content type**: `application/json`
   - **Secret**: Paste the generated secret
   - **Events**: Select events you want to trigger on (e.g., Pull requests, Pushes)

3. **Start agent-server with autospawn configured**:
   ```bash
   python -m openhands.agent_server --config config.yaml
   ```

## Security

### HMAC Signature Verification

When `github_secret` is configured, all incoming webhooks are verified using HMAC SHA-256 signatures. Requests with invalid or missing signatures are rejected with HTTP 401.

**Important**: Always use a webhook secret in production environments to prevent unauthorized requests from triggering agent execution.

### Repository Filtering

Triggers include repository filtering to ensure agents are only spawned for authorized repositories. Always specify exact repository names in trigger configurations.

## Example Configurations

### Automatic PR Review

```yaml
github_autospawn:
  github_secret: "${GITHUB_WEBHOOK_SECRET}"
  triggers:
    - event: "pull_request"
      action: "opened"
      repo: "myorg/myrepo"
      agent_config:
        task: |
          Review the pull request changes:
          1. Check for code quality issues
          2. Verify tests are included
          3. Check documentation updates
          4. Post a review comment with findings
        agent:
          llm:
            model: "anthropic/claude-3-5-sonnet-20240620"
          tools:
            - name: "file_editor"
            - name: "terminal"
```

### Issue Triage

```yaml
github_autospawn:
  github_secret: "${GITHUB_WEBHOOK_SECRET}"
  triggers:
    - event: "issues"
      action: "opened"
      repo: "myorg/myrepo"
      agent_config:
        task: |
          Analyze this issue and:
          1. Classify the issue type (bug, feature request, question)
          2. Add appropriate labels
          3. Suggest which team should handle it
        agent:
          llm:
            model: "gpt-4o"
```

### Automated Testing on Push

```yaml
github_autospawn:
  github_secret: "${GITHUB_WEBHOOK_SECRET}"
  triggers:
    - event: "push"
      repo: "myorg/myrepo"
      branch: "main"
      agent_config:
        task: |
          Run the test suite and report results:
          1. Run all unit tests
          2. Run integration tests
          3. Check code coverage
          4. Report any failures
        agent:
          llm:
            model: "gpt-4o"
          tools:
            - name: "terminal"
```

## Troubleshooting

### Webhook Not Triggering

1. **Check agent-server logs**: Look for "GitHub autospawn router enabled" on startup
2. **Verify signature**: Check for signature verification errors in logs
3. **Inspect trigger match**: Ensure event type, action, repo, and branch match exactly

### Workspace Issues

- **Workspaces not cleaned up**: Check `cleanup_on_success` and `cleanup_on_failure` settings
- **Permission errors**: Ensure agent-server has write permissions to `workspace_base_dir`

### Common Errors

- **401 Unauthorized**: Signature verification failed - check webhook secret
- **400 Bad Request**: Missing `X-GitHub-Event` header
- **No agents spawned**: No triggers matched - review trigger configuration

## Testing

To test your webhook configuration without GitHub, you can use `curl`:

```bash
# Generate HMAC signature
PAYLOAD='{"repository":{"full_name":"myorg/myrepo","clone_url":"https://github.com/myorg/myrepo.git"},"action":"opened"}'
SECRET="your-webhook-secret"
SIGNATURE=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')

# Send test webhook
curl -X POST http://localhost:3000/webhooks/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: pull_request" \
  -H "X-Hub-Signature-256: sha256=$SIGNATURE" \  -d "$PAYLOAD"
```

## Limitations

- **Rate limiting**: Not currently implemented. Consider implementing rate limiting in production.
- **Workspace lifecycle**: Workspaces are cleaned up immediately after conversation starts, not after completion.
- **Concurrent webhooks**: Multiple webhooks are processed in parallel via FastAPI BackgroundTasks.
