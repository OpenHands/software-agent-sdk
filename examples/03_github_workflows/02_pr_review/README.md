# PR Review Workflow

This example demonstrates how to set up a GitHub Actions workflow for automated pull request reviews using the OpenHands agent SDK. When a PR is labeled with `review-this` or when openhands-agent is added as a reviewer, OpenHands will analyze the changes and provide detailed, constructive feedback.

## Files

- **`action.yml`**: Symlink to the composite GitHub Action (`.github/actions/pr-review/action.yml`)
- **`workflow.yml`**: Example GitHub Actions workflow file that uses the composite action
- **`agent_script.py`**: Python script that runs the OpenHands agent for PR review
- **`prompt.py`**: The prompt asking the agent to write the PR review
- **`evaluate_review.py`**: Script to evaluate review effectiveness when PR is closed
- **`README.md`**: This documentation file

## Features

- **Two Review Modes**:
  - **SDK Mode** (default): Runs the agent locally in GitHub Actions
  - **Cloud Mode**: Launches the review in OpenHands Cloud for faster CI completion
- **Automatic Trigger**: Reviews are triggered when:
  - The `review-this` label is added to a PR, OR
  - openhands-agent is requested as a reviewer
- **Inline Review Comments**: Posts review comments directly on specific lines of code in the PR diff, rather than a single giant comment. This makes it easier to:
  - See exactly which lines the feedback refers to
  - Address issues one by one
  - Have focused discussions on specific code sections
- **Review Context Awareness**: The agent considers previous review history:
  - **Previous reviews**: Sees all past review decisions (APPROVED, CHANGES_REQUESTED, etc.)
  - **Review threads**: Fetches all review threads including their resolution status
  - **Smart commenting**: Avoids repeating issues that have already been raised and addressed
  - **Unresolved focus**: Prioritizes unresolved threads that may still need attention
  - **Pagination limits**: Fetches up to 100 threads per page (with pagination) and up to 50 comments per thread. For PRs with extensive review history exceeding these limits, older threads/comments may be omitted.
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

**For SDK Mode (default):**
- **`LLM_API_KEY`** (required): Your LLM API key
  - Get one from the [OpenHands LLM Provider](https://docs.all-hands.dev/openhands/usage/llms/openhands-llms)
- **`GITHUB_TOKEN`** (auto-available): Used for PR diff and posting comments

**For Cloud Mode:**
- **`OPENHANDS_CLOUD_API_KEY`** (required): Your OpenHands Cloud API key
  - Get one from your [OpenHands Cloud account settings](https://app.all-hands.dev/settings/api-keys)
- **`GITHUB_TOKEN`** (auto-available): Used to post initial comment with conversation URL
- **`LLM_API_KEY`** (optional): Your LLM API key. If not provided, uses the LLM configured in your OpenHands Cloud account.

**Note**: Cloud mode uses the LLM settings configured in your OpenHands Cloud account by default. You can optionally override this by providing `LLM_API_KEY`. The workflow uses `GITHUB_TOKEN` to post a comment linking to the conversation URL. The agent running in cloud uses your account's GitHub access for the actual review.

### 3. Customize the workflow (optional)

Edit `.github/workflows/pr-review-by-openhands.yml` to customize the inputs.

**SDK Mode Configuration (default):**

```yaml
- name: Run PR Review
  uses: ./.github/actions/pr-review
  with:
      # Review mode: 'sdk' runs the agent locally in GitHub Actions
      mode: sdk
      # LLM configuration
      llm-model: anthropic/claude-sonnet-4-5-20250929
      llm-base-url: ''
      # Review style: roasted (other option: standard)
      review-style: roasted
      # SDK git ref to use (tag, branch, or commit SHA, e.g., 'v1.0.0', 'main', or 'abc1234')
      sdk-version: main
      # Optional: override the SDK repo (owner/repo) if you forked it
      sdk-repo: OpenHands/software-agent-sdk
      # Secrets
      llm-api-key: ${{ secrets.LLM_API_KEY }}
      github-token: ${{ secrets.GITHUB_TOKEN }}
```

**Cloud Mode Configuration:**

```yaml
- name: Run PR Review
  uses: ./.github/actions/pr-review
  with:
      # Review mode: 'cloud' runs in OpenHands Cloud
      mode: cloud
      # Review style: roasted (other option: standard)
      review-style: roasted
      # SDK git ref to use
      sdk-version: main
      # Cloud mode secrets
      openhands-cloud-api-key: ${{ secrets.OPENHANDS_CLOUD_API_KEY }}
      github-token: ${{ secrets.GITHUB_TOKEN }}
      # Optional: Override the cloud's default LLM with your own
      # llm-api-key: ${{ secrets.LLM_API_KEY }}
      # llm-model: anthropic/claude-sonnet-4-5-20250929
      # Optional: custom cloud API URL
      # openhands-cloud-api-url: https://app.all-hands.dev
```

**Cloud Mode Benefits:**
- **Faster CI completion**: Starts the review and exits immediately
- **Track progress in UI**: Posts a comment with a link to the conversation URL
- **Interactive**: Users can interact with the review conversation in the cloud UI

**Cloud Mode Prerequisites:**
> ⚠️ The OpenHands Cloud account that owns the `OPENHANDS_CLOUD_API_KEY` must have GitHub access to the repository you want to review. The agent running in cloud uses your account's GitHub credentials to fetch the PR diff and post review comments.
>
> Follow the [GitHub Installation Guide](https://docs.openhands.dev/openhands/usage/cloud/github-installation) to connect your GitHub account to OpenHands Cloud.

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

**Note**: Adding labels or requesting a *new* reviewer requires write access. GitHub may still allow PR authors to use "Re-request review" for a reviewer who has already reviewed.

## Customizing the Code Review

Instead of forking the `agent_script.py`, you can customize the code review behavior by adding a `.agents/skills/code-review.md` file to your repository. This is the **recommended approach** for customization.

### How It Works

The PR review agent uses skills from the [OpenHands/skills](https://github.com/OpenHands/skills) repository by default. When you add a `.agents/skills/code-review.md` file to your repository, it **overrides** the default skill with your custom guidelines.

### Example: Custom Code Review Skill

Create `.agents/skills/code-review.md` in your repository:

```markdown
---
name: code-review
description: Custom code review guidelines for my project
triggers:
- /codereview
---

# My Project Code Review Guidelines

You are a code reviewer for this project. Follow these guidelines:

## Review Decisions

- **APPROVE** straightforward changes (config updates, typo fixes, documentation)
- **COMMENT** when you have feedback or concerns

## What to Check

- Code follows our project conventions
- Tests are included for new functionality
- No security vulnerabilities introduced
- Documentation is updated if needed

## Communication Style

- Be direct and constructive
- Use GitHub suggestion syntax for code fixes
- Approve quickly when code is good
```

### Benefits of Custom Skills

1. **No forking required**: Keep using the official SDK while customizing behavior
2. **Version controlled**: Your review guidelines live in your repository
3. **Easy updates**: SDK updates don't overwrite your customizations
4. **Team alignment**: Everyone uses the same review standards

### Reference Example

See the [software-agent-sdk's own code-review skill](https://github.com/OpenHands/software-agent-sdk/blob/main/.agents/skills/code-review.md) for a complete example of a custom code review skill.

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
| `llm-model` | LLM model to use | No | `anthropic/claude-sonnet-4-5-20250929` |
| `llm-base-url` | LLM base URL (optional for custom endpoints) | No | `''` |
| `review-style` | Review style: 'standard' or 'roasted' | No | `roasted` |
| `sdk-version` | Git ref for SDK (tag, branch, or commit SHA) | No | `main` |
| `sdk-repo` | SDK repository (owner/repo) | No | `OpenHands/software-agent-sdk` |
| `llm-api-key` | LLM API key (required for SDK mode, optional for cloud mode) | SDK mode | - |
| `github-token` | GitHub token for API access | Yes | - |
| `openhands-cloud-api-key` | OpenHands Cloud API key (cloud mode only) | cloud mode | - |
| `openhands-cloud-api-url` | OpenHands Cloud API URL | No | `https://app.all-hands.dev` |
| `lmnr-api-key` | Laminar API key for observability (sdk mode only) | No | - |

## Review Evaluation (Observability)

When Laminar observability is enabled (`lmnr-api-key` input is provided), the workflow captures trace data that enables delayed evaluation of review effectiveness.

### How It Works

1. **During Review**: The agent script captures the Laminar trace ID and stores it as a GitHub artifact
2. **On PR Close/Merge**: The evaluation workflow (`pr-review-evaluation.yml`) runs automatically:
   - Downloads the trace ID from the artifact
   - Fetches all PR comments and the final diff from GitHub
   - Creates an evaluation trace in Laminar with the review context
   - Optionally scores the original review trace

### Evaluation Metrics

The evaluation script provides:
- **Review Engagement Score**: Preliminary score based on human responses to agent comments
- **Comment Analysis**: Structured data for signal processing (which comments were addressed)
- **Final Diff Context**: The actual code changes for comparison

### Laminar Signal Integration

Configure a Laminar signal to analyze the evaluation traces:

1. Create a signal named `pr_review_effectiveness`
2. Filter by tag: `pr-review-evaluation`
3. Use the signal prompt to analyze:
   - Which agent comments were addressed in the final patch
   - Which comments received human responses
   - Overall review effectiveness score

See [GitHub Issue #1953](https://github.com/OpenHands/software-agent-sdk/issues/1953) for the full implementation details.
