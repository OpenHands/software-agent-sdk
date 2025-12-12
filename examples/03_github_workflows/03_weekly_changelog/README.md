# Changelog Generator

This example demonstrates how to automate changelog generation using the OpenHands agent SDK. The agent analyzes git commit history and creates/updates a CHANGELOG.md file following the [Keep a Changelog](https://keepachangelog.com/) format.

## Overview

**Use Case:** *"A documentation system that checks the changes made to your codebase and updates the changelog"*

The agent:
- Analyzes git commits within a specified range (dates, commits, or tags)
- Categorizes changes into: Added, Changed, Fixed, Removed, Security, Deprecated
- Generates a well-formatted CHANGELOG.md with commit links
- Optionally creates a pull request with the changes

## Files

- **`workflow.yml`**: GitHub Actions workflow file for automated/scheduled runs
- **`agent_script.py`**: Python script that runs the OpenHands agent for changelog generation
- **`prompt.py`**: The prompt template instructing the agent on changelog format
- **`test_local.py`**: Local testing script for quick validation
- **`README.md`**: This documentation file

## Features

- **Automatic Categorization**: Intelligently categorizes commits into changelog sections
- **Keep a Changelog Format**: Follows industry-standard changelog formatting
- **Commit Links**: Each entry includes a link to the commit on GitHub
- **Flexible Range Types**: Generate changelogs using dates, commit SHAs, or tags
- **Version Release Support**: Generate changelogs between version tags (e.g., v1.0.0..v1.1.0)
- **PR Creation**: Optionally creates a pull request with the changelog
- **Scheduled Runs**: Can run automatically on a schedule (e.g., weekly)
- **Manual Triggers**: Run on-demand with custom ranges

## Setup

### 1. Copy the workflow file

Copy `workflow.yml` to `.github/workflows/weekly-changelog.yml` in your repository:

```bash
cp examples/03_github_workflows/03_weekly_changelog/workflow.yml .github/workflows/weekly-changelog.yml
```

### 2. Configure secrets

Set the following secret in your GitHub repository settings:

- **`LLM_API_KEY`** (required): Your LLM API key
  - Get one from the [OpenHands LLM Provider](https://docs.all-hands.dev/openhands/usage/llms/openhands-llms)

**Note**: The workflow automatically uses the `GITHUB_TOKEN` secret for PR creation.

### 3. Customize the workflow (optional)

Edit `.github/workflows/weekly-changelog.yml` to customize configuration:

```yaml
env:
    # Optional: Use a different LLM model
    LLM_MODEL: openhands/claude-sonnet-4-5-20250929
    # Optional: Use a custom LLM base URL
    # LLM_BASE_URL: 'https://custom-api.example.com'
```

### 4. Enable scheduled runs (optional)

To enable automatic weekly runs, uncomment the schedule section in the workflow:

```yaml
on:
  schedule:
    - cron: '0 9 * * 1'  # Every Monday at 9 AM UTC
```

## Usage

### Manual Trigger via GitHub Actions

1. Go to your repository → Actions → "Weekly Changelog Generation"
2. Click "Run workflow"
3. (Optional) Customize the range:
   - **Start Ref**: Date (YYYY-MM-DD), commit SHA, or tag (leave empty for 7 days ago)
   - **End Ref**: Date (YYYY-MM-DD), commit SHA, or tag (leave empty for today)
   - **Create PR**: Whether to create a pull request (default: true)
4. Click "Run workflow"

The workflow will generate a CHANGELOG.md and optionally create a PR with the changes.

**Examples:**
- Weekly changelog: Leave both empty (defaults to last 7 days)
- Version release: `start_ref=v1.0.0`, `end_ref=v1.1.0`
- Since last release: `start_ref=v1.0.0`, `end_ref=` (empty for HEAD)

### Local Testing

Test the changelog generator locally before deploying:

```bash
# Set your API key
export LLM_API_KEY="your-api-key"

# Run the test script
cd /path/to/your/repo
uv run python /path/to/agent-sdk/examples/03_github_workflows/03_weekly_changelog/test_local.py
```

**Optional environment variables:**
```bash
# Date-based range (default behavior)
export START_REF="2025-01-01"   # Default: 7 days ago
export END_REF="2025-01-17"     # Default: today

# Tag-based range (for version releases)
export START_REF="v1.0.0"
export END_REF="v1.1.0"

# Commit-based range
export START_REF="abc1234"
export END_REF="def5678"

# Other options
export LLM_MODEL="openhands/claude-sonnet-4-5-20250929"  # Default model
export CREATE_PR="false"         # Default: false (for local testing)
```

**Note:** `START_DATE` and `END_DATE` are still supported for backward compatibility.

### Direct Script Execution

You can also run the agent script directly:

```bash
export LLM_API_KEY="your-api-key"

# Example: Generate changelog between two tags
export START_REF="v1.0.0"
export END_REF="v1.1.0"

cd /path/to/your/repo
uv run python /path/to/agent-sdk/examples/03_github_workflows/03_weekly_changelog/agent_script.py
```

## Testing

### Local Testing Procedure

1. **Prerequisites**:
   ```bash
   # Ensure you have uv installed
   uv --version
   
   # Set required environment variable
   export LLM_API_KEY="your-api-key"
   ```

2. **Run the test**:
   ```bash
   cd /path/to/your/repository
   uv run python examples/03_github_workflows/03_weekly_changelog/test_local.py
   ```

3. **Verify the output**:
   ```bash
   # Check that CHANGELOG.md was created
   ls -la CHANGELOG.md
   
   # Review the generated content
   cat CHANGELOG.md
   ```

### What to Verify

**File Creation**: CHANGELOG.md exists in your repository root

**Format Compliance**: 
- Starts with "# Changelog"
- Includes Keep a Changelog reference
- Has version/date header (e.g., `## [Unreleased] - 2025-01-17`)

**Categorization**:
- Changes are grouped into sections (Added, Fixed, Changed, etc.)
- Only relevant sections are included

**Content Quality**:
- Commit descriptions are clear and concise
- Each entry includes a commit hash with GitHub link
- Changes are user-facing and meaningful

**Date Range**:
- Only commits from the specified date range are included
- Default is last 7 days if no dates specified

### Expected Output Structure

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased] - 2025-01-17

### Added
- Add new feature for user authentication ([a1b2c3d](https://github.com/owner/repo/commit/a1b2c3d))
- Add API endpoint for data export ([e4f5g6h](https://github.com/owner/repo/commit/e4f5g6h))

### Fixed
- Fix memory leak in data processor ([i7j8k9l](https://github.com/owner/repo/commit/i7j8k9l))

### Changed
- Update dependencies to latest versions ([m0n1o2p](https://github.com/owner/repo/commit/m0n1o2p))
```

### Testing Edge Cases

Test the tool with different scenarios:

1. **Empty date range** (no commits):
   ```bash
   export START_DATE="2020-01-01"
   export END_DATE="2020-01-02"
   ```
   Expected: CHANGELOG.md with headers but no entries

2. **Large date range** (many commits):
   ```bash
   export START_DATE="2024-01-01"
   export END_DATE="2025-01-17"
   ```
   Expected: All commits categorized properly

3. **Custom date range**:
   ```bash
   export START_DATE="2025-01-10"
   export END_DATE="2025-01-15"
   ```
   Expected: Only commits in that specific range

### Troubleshooting

**Issue**: "LLM_API_KEY environment variable is not set"
- **Solution**: Export your API key: `export LLM_API_KEY="your-key"`

**Issue**: No CHANGELOG.md created
- **Solution**: Check the logs for errors. Ensure git history is available.

**Issue**: Incomplete changelog entries
- **Solution**: Try increasing the date range or check if commits have meaningful messages.

**Issue**: PR creation fails
- **Solution**: Ensure `GITHUB_TOKEN` is set and has write permissions.

## Configuration

### Prompt customization (optional)

- To override the built-in prompt at runtime, set `CHANGELOG_PROMPT_FILE` to a file path. The file may use `{start_ref}`, `{end_ref}`, `{start_type}`, `{end_type}`, `{repo_name}`.
- To append extra instructions, set `CHANGELOG_PROMPT_APPEND`.

Example:
```bash
export CHANGELOG_PROMPT_FILE="/tmp/changelog_prompt.txt"
cat > /tmp/changelog_prompt.txt <<'PROMPT'
You are a changelog generator.
Range: {start_ref} .. {end_ref} ({start_type}->{end_type})
Produce a minimal Keep a Changelog update with only sections that have entries.
If end_ref is a tag, use it as the version header; otherwise use Unreleased.
PROMPT

# Optional extra note
export CHANGELOG_PROMPT_APPEND=$'\nNote: prefer user-facing changes; omit internal refactors.'
```

### Range Options

The changelog generator supports three types of references:

| Type | Format | Example |
|------|--------|----------|
| Date | YYYY-MM-DD | `2025-01-15` |
| Commit | 7-40 hex chars | `abc1234` or full SHA |
| Tag | Any other string | `v1.0.0`, `1.0.0a1`, `release-1.0` |

**Common configurations:**
- **Weekly changelog**: Leave both empty (defaults to last 7 days)
- **Version release**: `START_REF=v1.0.0 END_REF=v1.1.0`
- **Since last release**: `START_REF=v1.0.0` (END_REF defaults to today)
- **Custom date range**: `START_REF=2025-01-01 END_REF=2025-01-15`

### LLM Model Options

The default model is `openhands/claude-sonnet-4-5-20250929`. You can use any model supported by the OpenHands SDK:

```bash
export LLM_MODEL="gpt-4"
export LLM_MODEL="claude-opus-4-20250514"
export LLM_MODEL="openhands/gpt-4o"
```

### PR Creation

Control whether a pull request is created:

```bash
# Local testing - don't create PR
export CREATE_PR="false"

# GitHub Actions - create PR
export CREATE_PR="true"
```

## Example Use Cases

1. **Weekly Documentation Updates**
   - Run every Monday to generate changelog for the past week
   - Automatically create PR for review

2. **Version Release Changelog**
   - Generate changelog between version tags: `START_REF=v1.0.0 END_REF=v1.1.0`
   - Perfect for release notes preparation
   - Version header automatically uses the tag name

3. **Release Preparation**
   - Generate changelog from last release tag to HEAD: `START_REF=v1.0.0`
   - Review and polish before creating release

4. **Sprint Retrospectives**
   - Generate changelog for the sprint period
   - Share with team in standup or retrospective

5. **Changelog Maintenance**
   - Run monthly to keep changelog up to date
   - Ensure no commits are forgotten

## Output Example

See the generated [CHANGELOG.md](../../../../CHANGELOG.md) in this repository for a real example of the agent's output.

## References

- [Keep a Changelog Format](https://keepachangelog.com/)
- [OpenHands SDK Documentation](https://docs.all-hands.dev/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [LLM Provider Setup](https://docs.all-hands.dev/openhands/usage/llms/openhands-llms)
