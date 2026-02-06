# PR Review Workflow

This example demonstrates how to set up a GitHub Actions workflow for automated pull request reviews using the OpenHands agent SDK. When a PR is labeled with `review-this` or when openhands-agent is added as a reviewer, OpenHands will analyze the changes and provide detailed, constructive feedback.

## Files

- **`workflow.yml`**: Example GitHub Actions workflow file that uses the composite action
- **`agent_script.py`**: Python script that runs the OpenHands agent for PR review
- **`prompt.py`**: The prompt asking the agent to write the PR review
- **`README.md`**: This documentation file

## Features

- **Automatic Trigger**: Reviews are triggered when:
  - The `review-this` label is added to a PR, OR
  - openhands-agent is requested as a reviewer
- **Two Review Modes**:
  - **SDK Mode** (`mode: sdk`): Runs the agent locally in the GitHub Actions runner. The review completes within the CI job. Requires `llm-api-key`, `llm-model`, and optionally `llm-base-url`.
  - **Cloud Mode** (`mode: cloud`): Launches the review in [OpenHands Cloud](https://app.all-hands.dev). The CI job posts a comment with a link to track progress and exits immediately, allowing the review to run asynchronously. Only requires `openhands-cloud-api-key` - the model configured in your cloud account will be used.
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

Set the following secrets in your GitHub repository settings based on your chosen mode:

**For SDK Mode (`mode: sdk`):**
- **`LLM_API_KEY`** (required): Your LLM API key
  - Get one from the [OpenHands LLM Provider](https://docs.all-hands.dev/openhands/usage/llms/openhands-llms)

**For Cloud Mode (`mode: cloud`):**
- **`OPENHANDS_CLOUD_API_KEY`** (required): Your OpenHands Cloud API key
  - Get one from [OpenHands Cloud Settings](https://app.all-hands.dev/settings/api-keys)
  - Note: `llm-model` and `llm-base-url` are ignored in cloud mode - the model configured in your cloud account will be used

**Note**: The workflow automatically uses the `GITHUB_TOKEN` secret that's available in all GitHub Actions workflows.

### 3. Customize the workflow (optional)

Edit `.github/workflows/pr-review-by-openhands.yml` to customize the inputs:

```yaml
- name: Run PR Review
  uses: ./.github/actions/pr-review
  with:
      # Mode: 'sdk' runs locally, 'cloud' launches in OpenHands Cloud
      # In 'cloud' mode, llm-model and llm-base-url are ignored
      mode: sdk
      # LLM configuration (only used in 'sdk' mode)
      llm-model: anthropic/claude-sonnet-4-5-20250929
      llm-base-url: ''
      # Review style: 'standard' or 'roasted' (Linus Torvalds style)
      review-style: standard
      # SDK version to use (version tag or branch name, e.g., 'v1.0.0' or 'main')
      sdk-version: main
      # Secrets
      llm-api-key: ${{ secrets.LLM_API_KEY }}
      openhands-cloud-api-key: ${{ secrets.OPENHANDS_CLOUD_API_KEY }}
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

### Review Modes

#### SDK Mode (Default)

In SDK mode (`mode: sdk`), the review runs entirely within the GitHub Actions runner:

- The agent analyzes the PR diff and posts inline comments
- The CI job waits for the review to complete
- Cost and token usage are logged in the CI output
- Requires `llm-api-key`, `llm-model`, and optionally `llm-base-url`

#### Cloud Mode

In Cloud mode (`mode: cloud`), the review is launched in OpenHands Cloud:

- A new conversation is created in OpenHands Cloud
- The CI job posts a comment with a link to track progress
- The CI job exits immediately (fast CI completion)
- The review continues asynchronously in the cloud
- When complete, the agent posts inline comments to the PR
- Only requires `openhands-cloud-api-key` - `llm-model` and `llm-base-url` are ignored
- The model configured in your OpenHands Cloud account will be used

Cloud mode is useful when:
- You want faster CI completion times
- You want to track review progress in the OpenHands Cloud UI
- You want to interact with the review conversation
- You want to use the model configured in your cloud account

## Composite Action

This workflow uses a reusable composite action located at `.github/actions/pr-review/action.yml` in the software-agent-sdk repository. The composite action handles:

- Checking out the SDK at the specified version
- Setting up Python and dependencies
- Running the PR review agent
- Uploading logs as artifacts

### Action Inputs

| Input | Description | Required | Default |
|-------|-------------|----------|---------|
| `mode` | Review mode: 'sdk' or 'cloud' | No | `sdk` |
| `llm-model` | LLM model (sdk mode only) | No | `anthropic/claude-sonnet-4-5-20250929` |
| `llm-base-url` | LLM base URL (sdk mode only) | No | `''` |
| `review-style` | Review style: 'standard' or 'roasted' | No | `standard` |
| `sdk-version` | SDK version tag or branch name | No | `main` |
| `llm-api-key` | LLM API key (sdk mode) | No | - |
| `openhands-cloud-api-key` | OpenHands Cloud API key (cloud mode) | No | - |
| `github-token` | GitHub token for API access | Yes | - |
