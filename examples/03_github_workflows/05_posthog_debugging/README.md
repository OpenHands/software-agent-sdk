# PostHog Error Debugging Workflow

This example demonstrates how to use OpenHands agents to automatically debug errors from PostHog in a GitHub Actions workflow.

## Overview

The workflow:
1. Fetches events from PostHog based on configurable queries
2. Searches for or creates GitHub issues to track errors
3. Clones relevant repositories for comprehensive analysis
4. Uses OpenHands AI agents to analyze code and identify root causes
5. Posts debugging insights as comments on GitHub issues

## Files

- `workflow.yml` - GitHub Actions workflow with manual trigger
- `posthog_debugging.py` - Main debugging script
- `debug_prompt.jinja` - Template for AI debugging prompts

## Features

### Manual Trigger
Run on-demand via GitHub Actions UI with configurable inputs:
- **Query Type**: Choose between `event-query` (event name) or `event-id` (specific event ID)
- **PostHog Query**:
  - For `event-query`: Event name like `$exception`, `error`, or custom event names
  - For `event-id`: Specific event ID
- Repository list to analyze
- Issue repository for tracking
- Parent issue for organization
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
POSTHOG_API_KEY: Your PostHog Personal API key
POSTHOG_PROJECT_ID: Your PostHog project ID
POSTHOG_HOST: PostHog host (e.g., us.posthog.com, eu.posthog.com)
LLM_API_KEY: API key for LLM service
LLM_BASE_URL: Base URL for LLM service (optional)
```

**Note**: `GITHUB_TOKEN` is automatically provided by GitHub Actions.

### Getting PostHog Credentials

1. **API Key**: Go to your PostHog instance → Settings → Personal API Keys → Create new key
   - Ensure the key has `query:read` scope
2. **Project ID**: Found in your project URL: `https://app.posthog.com/project/{PROJECT_ID}/...`
3. **Host**: 
   - US Cloud: `us.posthog.com`
   - EU Cloud: `eu.posthog.com`
   - Self-hosted: Your instance hostname

### Installation

1. Copy `workflow.yml` to your repository's `.github/workflows/` directory (e.g., `.github/workflows/posthog-debugging.yml`)
2. Configure the required secrets in repository Settings → Secrets and variables → Actions
3. Optionally, customize the workflow inputs and defaults in the YAML file

**Note**: The workflow automatically downloads the latest version of `posthog_debugging.py` and `debug_prompt.jinja` from the SDK repository at runtime. No need to copy these files to your repository unless you want to customize them.

## Usage

### Via GitHub Actions UI

1. Go to the **Actions** tab in your repository
2. Select **PostHog Error Debugging** workflow
3. Click **Run workflow**
4. Configure inputs:
   - **Query Type**: Choose `event-query` or `event-id` (default: `event-query`)
   - **PostHog Query**: 
     - For `event-query`: Event name (default: `$exception`)
     - For `event-id`: Event ID
   - **Repository List**: Comma-separated repos to analyze (default: `OpenHands/OpenHands,All-Hands-AI/infra`)
   - **Issue Repository**: Where to create issues (default: `All-Hands-AI/infra`)
   - **Parent Issue**: Optional parent issue URL for tracking
   - **Issue Prefix**: Prefix for issue titles (default: `PostHog Error: `)
   - **LLM Model**: Model to use (default: `anthropic/claude-sonnet-4-5-20250929`)
5. Click **Run workflow**

### Via GitHub CLI

**Search for exception events:**
```bash
gh workflow run posthog-debugging.yml \
  -f query_type="event-query" \
  -f posthog_query="$exception" \
  -f repo_list="OpenHands/OpenHands,All-Hands-AI/infra" \
  -f issue_repo="All-Hands-AI/infra"
```

**Debug a specific event by ID:**
```bash
gh workflow run posthog-debugging.yml \
  -f query_type="event-id" \
  -f posthog_query="01234567-89ab-cdef-0123-456789abcdef" \
  -f repo_list="OpenHands/OpenHands,All-Hands-AI/infra,All-Hands-AI/deploy" \
  -f issue_repo="All-Hands-AI/infra"
```

### Via Command Line

```bash
# Search for exception events
python posthog_debugging.py \
  --query-type event-query \
  --query '$exception' \
  --repos "OpenHands/OpenHands,All-Hands-AI/infra" \
  --issue-repo "All-Hands-AI/infra" \
  --issue-prefix "PostHog Error: "

# Debug custom error events
python posthog_debugging.py \
  --query-type event-query \
  --query 'application_error' \
  --repos "OpenHands/OpenHands,All-Hands-AI/infra,All-Hands-AI/deploy" \
  --issue-repo "All-Hands-AI/infra"
```

## Example

### Input (Search Query)
```yaml
query_type: "event-query"
posthog_query: "$exception"
repo_list: "OpenHands/OpenHands,All-Hands-AI/infra,All-Hands-AI/deploy"
issue_repo: "All-Hands-AI/infra"
issue_parent: "https://github.com/All-Hands-AI/infra/issues/672"
```

### Input (Specific Event ID)
```yaml
query_type: "event-id"
posthog_query: "01234567-89ab-cdef-0123-456789abcdef"
repo_list: "OpenHands/OpenHands,All-Hands-AI/infra,All-Hands-AI/deploy"
issue_repo: "All-Hands-AI/infra"
issue_parent: "https://github.com/All-Hands-AI/infra/issues/672"
```

### Output
- **Console**: Progress logs showing event fetching, repository cloning, and agent analysis
- **GitHub Issue**: Created or updated with event details
- **GitHub Comment**: AI-generated analysis with root cause and recommendations
- **Artifacts**: Debugging data and logs saved for 7 days

## Configuration

### PostHog Event Query Examples

```yaml
# Exception events (PostHog automatically captures these)
$exception

# Page view errors
$pageview

# Custom error events
application_error

# API error events
api_error

# User action errors
checkout_error
```

### Using HogQL for Advanced Queries

For more complex queries, you can modify the script to use HogQL:

```python
# Query events with specific properties
hogql_query = """
SELECT * FROM events 
WHERE event = '$exception' 
  AND properties.$exception_type = 'ValueError'
ORDER BY timestamp DESC 
LIMIT 10
"""

# Query events in a time range
hogql_query = """
SELECT * FROM events 
WHERE event = 'application_error'
  AND timestamp > now() - INTERVAL 7 DAY
ORDER BY timestamp DESC
"""
```

### Repository List Format

Comma-separated list of `owner/repo`:
```
OpenHands/OpenHands,All-Hands-AI/infra,All-Hands-AI/deploy
```

### LLM Model Options

- `anthropic/claude-sonnet-4-5-20250929` - Best quality (default)
- `anthropic/claude-haiku-4-5-20251001` - Faster, cheaper
- `anthropic/claude-3-5-sonnet-20241022` - Alternative

## Workflow Details

### Inputs

| Input | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `posthog_query` | string | Yes | `$exception` | PostHog event name or event ID |
| `query_type` | string | No | `event-query` | Type of query: `event-query` or `event-id` |
| `repo_list` | string | Yes | `OpenHands/OpenHands,All-Hands-AI/infra` | Comma-separated list of repositories |
| `issue_repo` | string | Yes | `All-Hands-AI/infra` | Repository to create/update issues in |
| `issue_parent` | string | No | - | Parent GitHub issue URL for tracking |
| `issue_prefix` | string | No | `PostHog Error: ` | Prefix for issue titles |
| `max_events` | string | No | `5` | Maximum number of events to fetch |
| `llm_model` | string | No | `anthropic/claude-sonnet-4-5-20250929` | LLM model to use |

### Outputs

- **GitHub Issues**: Created or updated with event details
- **GitHub Comments**: AI analysis posted to issues
- **Artifacts**: Debugging data and logs (retained for 7 days)

### Permissions

```yaml
permissions:
  contents: read   # Clone repositories
  issues: write    # Create/update issues and comments
```

## Understanding PostHog Events

### Common Event Types

PostHog automatically captures several event types:

- **`$exception`**: JavaScript errors and exceptions
- **`$pageview`**: Page views
- **`$pageleave`**: When users leave pages
- **`$autocapture`**: Automatically captured user interactions
- **Custom events**: Events you manually track in your application

### Event Properties

Exception events typically include:

```json
{
  "$exception_type": "Error",
  "$exception_message": "Cannot read property 'x' of undefined",
  "$exception_list": [...],
  "$exception_stack_trace_raw": "...",
  "$current_url": "https://example.com/page",
  "$browser": "Chrome",
  "$os": "Mac OS X"
}
```

Custom events can include any properties you define.

## Customization

### For Production Use

Consider creating a separate configuration repository with:
- Scheduled runs (daily for critical errors, weekly for comprehensive analysis)
- Predefined event categories
- Repository group definitions
- Environment-specific settings

### Adding Scheduled Runs

Add to the workflow's `on:` section:

```yaml
on:
  workflow_dispatch:
    # ... existing inputs ...
  
  schedule:
    # Daily at 09:00 UTC for exception events
    - cron: '0 9 * * *'
    # Weekly on Monday at 09:00 UTC for full scan
    - cron: '0 9 * * 1'
```

### Matrix Strategy

Run multiple queries in parallel:

```yaml
jobs:
  debug-events:
    strategy:
      matrix:
        query:
          - "$exception"
          - "application_error"
          - "api_error"
      fail-fast: false
```

## Troubleshooting

### Workflow Fails to Start
- Verify all required secrets are configured
- Check `GITHUB_TOKEN` has necessary permissions
- Review workflow syntax with `yamllint`

### No Events Found
- Verify the event name is correct (case-sensitive)
- Check your PostHog project has events of that type
- Try querying PostHog UI first to confirm events exist
- Ensure API key has `query:read` scope

### API Authentication Errors
- Verify `POSTHOG_API_KEY` is a Personal API Key (not Project API Key)
- Check the API key hasn't expired
- Ensure `POSTHOG_PROJECT_ID` is correct
- Verify `POSTHOG_HOST` matches your PostHog instance

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

## Comparing with DataDog Example

This example is analogous to the DataDog debugging example but adapted for PostHog:

| Feature | DataDog | PostHog |
|---------|---------|---------|
| **Data Source** | Logs & Error Tracking | Events & Custom Tracking |
| **Query Types** | Log queries, Error IDs | Event names, Event IDs |
| **Authentication** | API Key + App Key | Personal API Key |
| **Query Language** | Datadog Query Syntax | HogQL (SQL-like) |
| **Time Range** | Filter timestamps | Filter timestamps |
| **Use Cases** | Server errors, logs | User errors, custom events |

## Related Examples

- **Basic Action**: `examples/03_github_workflows/01_basic_action/` - Simple workflow example
- **PR Review**: `examples/03_github_workflows/02_pr_review/` - PR automation example
- **TODO Management**: `examples/03_github_workflows/03_todo_management/` - Automated TODO tracking
- **DataDog Debugging**: `examples/03_github_workflows/04_datadog_debugging/` - Similar debugging for DataDog

## Benefits

1. **Automated Debugging**: AI analyzes code without manual intervention
2. **Reduced MTTR**: Faster root cause identification
3. **Context-Aware**: Multi-repo analysis for complete picture
4. **No Duplicates**: Smart issue tracking prevents clutter
5. **Actionable Insights**: Clear recommendations for fixes
6. **Scalable**: Easy to add new event categories
7. **User-Centric**: Track errors as users experience them

## Learn More

- [PostHog API Documentation](https://posthog.com/docs/api)
- [PostHog HogQL Documentation](https://posthog.com/docs/hogql)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [OpenHands SDK Documentation](https://github.com/OpenHands/software-agent-sdk)
