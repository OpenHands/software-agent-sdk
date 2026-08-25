# Development Guide

## Setup

```bash
git clone https://github.com/OpenHands/software-agent-sdk.git
cd software-agent-sdk
make build
```

## Repository boundaries

This repository owns the Python SDK and Agent Server. Put agent/tool behavior, conversations, workspaces, events, and new REST/WebSocket endpoints here. The API contract flows through [`OpenHands/typescript-client`](https://github.com/OpenHands/typescript-client) to Agent Canvas in [`OpenHands/OpenHands`](https://github.com/OpenHands/OpenHands); automation scheduling, webhooks, run history, and dispatch belong in [`OpenHands/automation`](https://github.com/OpenHands/automation). If a PR is opened in the wrong repository, recommend closing and moving it to the owning repository. Follow [`.agents/skills/custom-codereview-guide.md`](.agents/skills/custom-codereview-guide.md) for every PR.

## Code Quality

```bash
make format                              # Format code
make lint                                # Lint code
uv run pre-commit run --all-files        # Run all checks
```

Pre-commit hooks run automatically on commit with type checking and linting.

## Testing

```bash
uv run pytest                            # All tests
uv run pytest tests/sdk/                 # SDK tests only
uv run pytest tests/tools/               # Tools tests only
```

## Project Structure

```
software-agent-sdk/
├── openhands-sdk/          # Core SDK package
├── openhands-tools/        # Built-in tools
├── openhands-workspace/    # Workspace management
├── openhands-agent-server/ # Agent server
├── examples/               # Usage examples
└── tests/                  # Test suites
```

## Contributing

1. Create a new branch
2. Make your changes
3. Run tests and checks
4. Push and create a pull request

For questions, join our [Slack community](https://openhands.dev/joinslack).
