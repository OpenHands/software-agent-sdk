# PR Review Workflow

This example demonstrates how to set up a GitHub Actions workflow for automated pull request reviews using the OpenHands agent SDK. When a PR is labeled with `review-this` or when openhands-agent is added as a reviewer, OpenHands will analyze the changes and provide detailed, constructive feedback.

## Files

- **`action.yml`**: Symlink to the composite GitHub Action (`.github/actions/pr-review/action.yml`)
- **`workflow.yml`**: Example GitHub Actions workflow file that uses the composite action
- **`agent_script.py`**: Python script that runs the OpenHands agent for PR review
- **`prompt.py`**: The prompt asking the agent to write the PR review
- **`README.md`**: This documentation file

## Features

- **Automatic Trigger**: Reviews are triggered when:
  - The `review-this` label is added to a PR, OR
  - openhands-agent is requested as a reviewer
- **Inline Review Comments**: Posts review comments directly on specific lines of code in the PR diff, rather than a single giant comment. This makes it easier to:
  - See exactly which lines the feedback refers to
  - Address issues one by one
  - Have focused discussions on specific code sections
- **Skills-Based Review**: Uses public skills from <https://github.com/OpenHands/skills>:
  - **`/codereview`**: Standard pragmatic code review focusing on simplicity, type safety, and backward compatibility
  - **`/codereview-roasted`**: Linus Torvalds style brutally honest review with emphasis on "good taste" and data structures
- **Complete Diff Upfront**: The agent receives the complete git diff in the initial message for efficient review
  - Large file diffs are automatically truncated to 10,000 characters per file
  - Total diff is capped at 100,000 characters
  - The agent can still access the repository for additional context if needed
- **Comprehensive Analysis**: Analyzes code changes in context of the entire repository
- **Detailed Feedback**: Provides structured review comments covering:
  - Overall assessment of changes
  - Code quality and best practices
  - Potential issues and security concerns
  - Specific improvement suggestions
- **GitHub API Integration**: Uses the GitHub API to post inline review comments directly on specific lines of code
- **Version Control**: Use `sdk-version` to pin to a specific version tag or branch

## Setup

### 1. Copy the workflow file

Copy `workflow.yml` to `.github/workflows/pr-review-by-openhands.yml` in your repository:

```bash
cp examples/03_github_workflows/02_pr_review/workflow.yml .github/workflows/pr-review-by-openhands.yml
```

### 2. Configure secrets

Set the following secrets in your GitHub repository settings:

- **`LLM_API_KEY`** (required): Your LLM API key
  - Get one from the [OpenHands LLM Provider](https://docs.all-hands.dev/openhands/usage/llms/openhands-llms)

**Note**: The workflow automatically uses the `GITHUB_TOKEN` secret that's available in all GitHub Actions workflows.

### 3. Customize the workflow (optional)

Edit `.github/workflows/pr-review-by-openhands.yml` to customize the inputs:

```yaml
- name: Run PR Review
  uses: ./.github/actions/pr-review
  with:
      # LLM configuration
      llm-model: anthropic/claude-sonnet-4-5-20250929
      llm-base-url: ''
      # Review style: 'standard' or 'roasted' (Linus Torvalds style)
      review-style: standard
      # SDK git ref to use (tag, branch, or commit SHA, e.g., 'v1.0.0', 'main', or 'abc1234')
      sdk-version: main
      # Secrets
      llm-api-key: ${{ secrets.LLM_API_KEY }}
      github-token: ${{ secrets.GITHUB_TOKEN }}
```

### 4. Create the review label

Create a `review-this` label in your repository:

1. Go to your repository → Issues → Labels
2. Click "New label"
3. Name: `review-this`
4. Description: `Trigger OpenHands PR review`
5. Color: Choose any color you prefer
6. Click "Create label"

## Usage

### Triggering a Review

There are two ways to trigger an automated review of a pull request:

#### Option 1: Using Labels

1. Open the pull request you want reviewed
2. Add the `review-this` label to the PR
3. The workflow will automatically start and analyze the changes
4. Review comments will be posted to the PR when complete

#### Option 2: Requesting a Reviewer (Recommended)

1. Open the pull request you want reviewed
2. Click on "Reviewers" in the right sidebar
3. Search for and select "openhands-agent" as a reviewer
4. The workflow will automatically start and analyze the changes
5. Review comments will be posted to the PR when complete

**Note**: Both methods require write access to the repository, ensuring only authorized users can trigger the AI review.

## Composite Action

This workflow uses a reusable composite action located at `.github/actions/pr-review/action.yml` in the software-agent-sdk repository. The composite action handles:

- Checking out the SDK at the specified version
- Setting up Python and dependencies
- Running the PR review agent
- Uploading logs as artifacts

### Action Inputs

| Input | Description | Required | Default |
|-------|-------------|----------|---------|
| `llm-model` | LLM model to use | No | `anthropic/claude-sonnet-4-5-20250929` |
| `llm-base-url` | LLM base URL (optional) | No | `''` |
| `review-style` | Review style: 'standard' or 'roasted' | No | `standard` |
| `sdk-version` | Git ref for SDK (tag, branch, or commit SHA) | No | `main` |
| `llm-api-key` | LLM API key | Yes | - |
| `github-token` | GitHub token for API access | Yes | - |
