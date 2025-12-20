# Development Guide

## Setup

```bash
git clone https://github.com/OpenHands/agent-sdk.git
cd agent-sdk
make build
```

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
agent-sdk/
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
