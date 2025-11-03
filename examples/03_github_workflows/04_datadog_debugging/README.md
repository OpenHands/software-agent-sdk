# Datadog Error Debugging Workflow

This example demonstrates how to use OpenHands agents to automatically debug errors from Datadog in a GitHub Actions workflow.

## Overview

The workflow:
1. Fetches errors from Datadog based on configurable queries
2. Searches for or creates GitHub issues to track errors
3. Clones relevant repositories for comprehensive analysis
4. Uses OpenHands AI agents to analyze code and identify root causes
5. Posts debugging insights as comments on GitHub issues

## Files

- `workflow.yml` - GitHub Actions workflow with manual trigger
- `agent_script.py` - Link to the main debugging script (see `examples/01_standalone_sdk/26_datadog_debugging.py`)

## Features

### Manual Trigger
Run on-demand via GitHub Actions UI with configurable inputs:
- Datadog query (e.g., `service:deploy ClientDisconnect`)
- Repository list to analyze
- Issue repository for tracking
- Parent issue for organization
- Max errors to fetch
- LLM model selection

### Smart Issue Management
- Searches for existing issues before creating duplicates
- Uses URL encoding for proper GitHub API queries
- Selects oldest matching issue when duplicates exist
- Links to parent tracking issue

### Multi-Repository Analysis
- Clone multiple repositories for comprehensive context
- Agent has full view of all relevant codebases
- Identifies root causes across repository boundaries

### AI-Powered Debugging
- Automatic code analysis using OpenHands agents
- Identifies error locations and root causes
- Provides actionable fix recommendations
- Posts detailed findings as GitHub comments

## Setup

### Required Secrets

Configure these in your repository Settings → Secrets and variables → Actions:

```yaml
DD_API_KEY: Your Datadog API key
DD_APP_KEY: Your Datadog Application key
DD_SITE: Your Datadog site (e.g., us5.datadoghq.com)
LLM_API_KEY: API key for LLM service
LLM_BASE_URL: Base URL for LLM service (optional)
```

**Note**: `GITHUB_TOKEN` is automatically provided by GitHub Actions.

### Installation

1. Copy `workflow.yml` to your repository's `.github/workflows/` directory
2. Configure the required secrets
3. Ensure `examples/01_standalone_sdk/26_datadog_debugging.py` is accessible in your repository

## Usage

### Via GitHub Actions UI

1. Go to the **Actions** tab in your repository
2. Select **Datadog Error Debugging** workflow
3. Click **Run workflow**
4. Configure inputs:
   - **Datadog Query**: Error search query (default: `service:deploy ClientDisconnect`)
   - **Repository List**: Comma-separated repos to analyze (default: `OpenHands/OpenHands,All-Hands-AI/infra`)
   - **Issue Repository**: Where to create issues (default: `All-Hands-AI/infra`)
   - **Parent Issue**: Optional parent issue URL for tracking
   - **Issue Prefix**: Prefix for issue titles (default: `DataDog Error: `)
   - **Max Errors**: Maximum errors to fetch (default: `5`)
   - **LLM Model**: Model to use (default: `openhands/claude-sonnet-4-5-20250929`)
5. Click **Run workflow**

### Via GitHub CLI

```bash
gh workflow run datadog-debugging.yml \
  -f datadog_query="service:deploy ClientDisconnect" \
  -f repo_list="OpenHands/OpenHands,All-Hands-AI/infra" \
  -f issue_repo="All-Hands-AI/infra" \
  -f max_errors="5"
```

## Example

### Input
```yaml
datadog_query: "service:deploy ClientDisconnect"
repo_list: "OpenHands/OpenHands,All-Hands-AI/infra,All-Hands-AI/deploy"
issue_repo: "All-Hands-AI/infra"
issue_parent: "https://github.com/All-Hands-AI/infra/issues/672"
max_errors: 5
```

### Output
- **Console**: Progress logs showing error fetching, repository cloning, and agent analysis
- **GitHub Issue**: Created or updated with error details
- **GitHub Comment**: AI-generated analysis with root cause and recommendations
- **Artifacts**: Debugging data and logs saved for 7 days

### Real Example

See a real run with production data:
- Error: `starlette.requests.ClientDisconnect` (1,526 occurrences)
- Issue: https://github.com/All-Hands-AI/infra/issues/703
- AI Analysis: https://github.com/All-Hands-AI/infra/issues/703#issuecomment-3480707049

The agent identified:
- Error locations in `github.py` and `gitlab.py`
- Root cause: Unhandled `ClientDisconnect` exceptions
- Recommendations: Add proper error handling for client disconnections

## Configuration

### Datadog Query Examples

```yaml
# ClientDisconnect errors
service:deploy ClientDisconnect

# Server errors (5xx)
service:deploy http.status_code:5*

# Database errors
service:deploy (database OR postgresql) status:error

# Authentication errors
service:deploy (authentication OR authorization) status:error

# Rate limit errors
service:deploy rate_limit status:error
```

### Repository List Format

Comma-separated list of `owner/repo`:
```
OpenHands/OpenHands,All-Hands-AI/infra,All-Hands-AI/deploy
```

### LLM Model Options

- `openhands/claude-sonnet-4-5-20250929` - Best quality (default)
- `openhands/claude-haiku-4-5-20251001` - Faster, cheaper
- `anthropic/claude-3-5-sonnet-20241022` - Alternative

## Workflow Details

### Inputs

| Input | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `datadog_query` | string | Yes | `service:deploy ClientDisconnect` | Datadog query to search for errors |
| `repo_list` | string | Yes | `OpenHands/OpenHands,All-Hands-AI/infra` | Comma-separated list of repositories |
| `issue_repo` | string | Yes | `All-Hands-AI/infra` | Repository to create/update issues in |
| `issue_parent` | string | No | - | Parent GitHub issue URL for tracking |
| `issue_prefix` | string | No | `DataDog Error: ` | Prefix for issue titles |
| `max_errors` | string | No | `5` | Maximum number of errors to fetch |
| `llm_model` | string | No | `openhands/claude-sonnet-4-5-20250929` | LLM model to use |

### Outputs

- **GitHub Issues**: Created or updated with error details
- **GitHub Comments**: AI analysis posted to issues
- **Artifacts**: Debugging data and logs (retained for 7 days)

### Permissions

```yaml
permissions:
  contents: read   # Clone repositories
  issues: write    # Create/update issues and comments
```

## Customization

### For Production Use

Consider creating a separate configuration repository with:
- Scheduled runs (daily for critical, weekly for comprehensive)
- Predefined error query categories
- Repository group definitions
- Environment-specific settings

See the All-Hands-AI/infra example for a production-ready implementation.

### Adding Scheduled Runs

Add to the workflow's `on:` section:

```yaml
on:
  workflow_dispatch:
    # ... existing inputs ...
  
  schedule:
    # Daily at 09:00 UTC for critical errors
    - cron: '0 9 * * *'
    # Weekly on Monday at 09:00 UTC for full scan
    - cron: '0 9 * * 1'
```

### Matrix Strategy

Run multiple queries in parallel:

```yaml
jobs:
  debug-errors:
    strategy:
      matrix:
        query:
          - "service:deploy ClientDisconnect"
          - "service:deploy http.status_code:5*"
          - "service:deploy database status:error"
      fail-fast: false
```

## Troubleshooting

### Workflow Fails to Start
- Verify all required secrets are configured
- Check `GITHUB_TOKEN` has necessary permissions
- Review workflow syntax with `yamllint`

### No Issues Created
- Verify issue repository exists and is accessible
- Check `GITHUB_TOKEN` has `issues: write` permission
- Review workflow logs for API errors

### Agent Analysis Incomplete
- Increase workflow timeout if needed
- Check `LLM_API_KEY` is valid and has quota
- Try a different LLM model
- Reduce number of repositories to analyze

### Repository Clone Failures
- Verify repository names use `owner/repo` format
- Check `GITHUB_TOKEN` has access to private repos
- Ensure repositories exist and are accessible

## Related Examples

- **Standalone Script**: `examples/01_standalone_sdk/26_datadog_debugging.py` - Core debugging logic
- **Basic Action**: `examples/03_github_workflows/01_basic_action/` - Simple workflow example
- **PR Review**: `examples/03_github_workflows/02_pr_review/` - PR automation example

## Benefits

1. **Automated Debugging**: AI analyzes code without manual intervention
2. **Reduced MTTR**: Faster root cause identification
3. **Context-Aware**: Multi-repo analysis for complete picture
4. **No Duplicates**: Smart issue tracking prevents clutter
5. **Actionable Insights**: Clear recommendations for fixes
6. **Scalable**: Easy to add new error categories

## Learn More

- [Datadog API Documentation](https://docs.datadoghq.com/api/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [OpenHands SDK Documentation](https://github.com/OpenHands/software-agent-sdk)
