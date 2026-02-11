# Multi-Model PR Review with Debate

This example demonstrates an experimental multi-model code review workflow where multiple AI models review a pull request and then debate to produce a consolidated, well-reasoned final review.

## Overview

The workflow consists of two phases:

### Phase 1: Parallel Reviews
Three AI models independently review the PR:
- **GPT-5.2** (`openai/gpt-5.2`)
- **Claude Sonnet 4.5** (`anthropic/claude-sonnet-4-5-20250929`)
- **Gemini 3 Flash** (`google/gemini-3-flash`)

Each model provides its own code review with findings, suggestions, and concerns.

### Phase 2: Debate
The reviewers are given each other's reviews and can communicate using inter-agent tools to:
- Discuss disagreements
- Clarify positions
- Work toward consensus
- Produce a final consolidated review

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        main.py                              │
│                   (Entry Point)                             │
└─────────────────────────┬───────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌─────────────────┐ ┌───────────────┐ ┌─────────────────────┐
│  github_utils   │ │ review_runner │ │ debate_orchestrator │
│  (GitHub API)   │ │ (Multi-Model) │ │   (Coordination)    │
└─────────────────┘ └───────────────┘ └──────────┬──────────┘
                                                 │
                                    ┌────────────┼────────────┐
                                    ▼            ▼            ▼
                              ┌──────────┐ ┌──────────┐ ┌──────────┐
                              │  GPT-5.2 │ │  Claude  │ │  Gemini  │
                              │  Agent   │ │  Agent   │ │  Agent   │
                              └────┬─────┘ └────┬─────┘ └────┬─────┘
                                   │            │            │
                                   └────────────┼────────────┘
                                                │
                                    ┌───────────▼───────────┐
                                    │    debate_tools.py    │
                                    │  - SendToReviewer     │
                                    │  - ConcludeDebate     │
                                    │  - MessageQueue       │
                                    └───────────────────────┘
```

## Files

- **`main.py`**: Main entry point that orchestrates the entire workflow
- **`github_utils.py`**: GitHub API utilities for fetching PR data
- **`review_runner.py`**: Runs parallel reviews with multiple models
- **`debate_orchestrator.py`**: Coordinates the debate between reviewers
- **`debate_tools.py`**: Tools for inter-agent communication
- **`models.py`**: Data models (PRInfo, ReviewResult, DebateState, etc.)
- **`prompt.py`**: Prompt templates for reviews and debate

## Debate Tools

Each reviewer agent has access to:

### SendToReviewer Tool
Send a message to another reviewer during the debate.

```
recipient: "claude" | "gpt" | "gemini" | "all"
message: "Your message content"
```

### ConcludeDebate Tool
Conclude participation in the debate with a final position.

```
final_position: "Your final summary and position"
consensus_points: "Points where reviewers agree"
remaining_disagreements: "Points still disputed"
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LLM_API_KEY` | Yes | API key for the LLM provider |
| `LLM_BASE_URL` | No | Custom base URL for LLM API |
| `GITHUB_TOKEN` | Yes | GitHub token for API access |
| `PR_NUMBER` | Yes | Pull request number |
| `PR_TITLE` | Yes | Pull request title |
| `PR_BODY` | No | Pull request description |
| `PR_BASE_BRANCH` | Yes | Base branch name |
| `PR_HEAD_BRANCH` | Yes | Head branch name |
| `REPO_NAME` | Yes | Repository in format owner/repo |
| `REVIEW_STYLE` | No | 'standard' or 'roasted' (default: standard) |
| `MAX_DEBATE_ROUNDS` | No | Maximum debate rounds (default: 3) |

## Usage

### Local Testing

```bash
# Set environment variables
export LLM_API_KEY="your-api-key"
export LLM_BASE_URL="https://llm-proxy.eval.all-hands.dev"
export GITHUB_TOKEN="your-github-token"
export PR_NUMBER="123"
export PR_TITLE="Add new feature"
export PR_BODY="Description of changes"
export PR_BASE_BRANCH="main"
export PR_HEAD_BRANCH="feature-branch"
export REPO_NAME="owner/repo"
export REVIEW_STYLE="standard"
export MAX_DEBATE_ROUNDS="3"

# Run the review
cd examples/03_github_workflows/03_pr_review_debate
python main.py
```

### GitHub Actions Integration

This workflow can be integrated into GitHub Actions. See the parent PR review workflow (`02_pr_review`) for an example of how to set up the GitHub Actions workflow.

## Synchronization Mechanism

The debate uses a turn-based synchronization approach:

1. **MessageQueue**: Manages communication between agents
2. **Thread-safe operations**: Uses locks to prevent race conditions
3. **Blocking communication**: When an agent sends a message, it blocks until receiving a response
4. **Round tracking**: Tracks debate rounds to limit discussion length

## Cost Considerations

Running multiple models in parallel and then having them debate can be expensive. Consider:

- Using fewer debate rounds for cost savings
- Running with fewer models (2 instead of 3)
- Using smaller/cheaper models for the debate phase
- Setting `MAX_DEBATE_ROUNDS=0` to skip debate entirely

## Experimental Status

⚠️ **This is an experimental workflow** meant for exploring multi-model collaboration patterns. It may:

- Have higher latency than single-model review
- Incur significantly higher costs
- Produce verbose output
- Not always reach meaningful consensus

Use the standard `02_pr_review` workflow for production use cases.

## Customization

### Using Different Models

Edit `models.py` to change the `ReviewerModel` enum:

```python
class ReviewerModel(Enum):
    MODEL_A = "provider/model-a"
    MODEL_B = "provider/model-b"
    # ...
```

### Custom Debate Prompts

Edit `prompt.py` to customize the debate prompts:
- `DEBATE_INITIAL_PROMPT`: Initial prompt with consolidated reviews
- `DEBATE_ROUND_PROMPT`: Prompt for each debate round
- `FINAL_CONSOLIDATION_PROMPT`: Prompt for synthesizing final review

### Custom Debate Tools

Extend `debate_tools.py` to add more inter-agent communication options:
- Add voting/polling tools
- Add priority tagging
- Add reference tools for citing specific code

## Output

The workflow produces:
1. Console output with progress and final review
2. `debate_review_results.json` with structured results

Example output structure:
```json
{
  "pr_number": "123",
  "pr_title": "Add new feature",
  "repo_name": "owner/repo",
  "models_used": ["openai/gpt-5.2", "anthropic/claude-sonnet-4-5-20250929"],
  "debate_rounds": 2,
  "total_cost": 0.045,
  "final_review": "## Overall Assessment\n..."
}
```
