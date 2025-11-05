# OpenHands Agent SDK

Build AI agents that write software. A clean, modular SDK with production-ready tools.

The OpenHands SDK allows you to build applications with agents that write software. This SDK also powers [OpenHands](https://github.com/OpenHands/OpenHands), an all-batteries-included coding agent that you can access through a GUI, CLI, or API.

## Features

- **Single Python API**: Unified interface for building coding agents with minimal boilerplate
- **Pre-defined Tools**: Built-in tools for bash commands, file editing, task tracking, and web browsing
- **REST-based Agent Server**: Deploy agents as scalable web services with WebSocket support for real-time interactions

## Why OpenHands Agent SDK?

- **Emphasis on coding**: Purpose-built for software development tasks with specialized tools and workflows
- **State-of-the-Art Performance**: Powered by advanced LLMs and optimized for real-world coding scenarios
- **Free and Open Source**: MIT licensed with an active community and transparent development

## Quick Start

Here's what building with the SDK looks like:

```python
from openhands.sdk import LLM, Conversation
from openhands.tools.preset.default import get_default_agent

# Configure LLM and create agent
llm = LLM(model="openhands/claude-sonnet-4-5-20250929", api_key=api_key)
agent = get_default_agent(llm=llm)

# Start a conversation
conversation = Conversation(agent=agent, workspace="/path/to/project")
conversation.send_message("Write 3 facts about this project into FACTS.txt.")
conversation.run()
```

For installation instructions and detailed setup, see the [Getting Started Guide](https://docs.openhands.dev/sdk/getting-started).

## Documentation

For detailed documentation, tutorials, and API reference, visit:

**[https://docs.openhands.dev/sdk](https://docs.openhands.dev/sdk)**

The documentation includes:
- [Getting Started Guide](https://docs.openhands.dev/sdk/getting-started) - Installation and setup
- [Architecture & Core Concepts](https://docs.openhands.dev/sdk/arch/overview) - Agents, tools, workspaces, and more
- [Guides](https://docs.openhands.dev/sdk/guides/hello-world) - Hello World, custom tools, MCP, skills, and more
- [API Reference](https://docs.openhands.dev/sdk/guides/agent-server/api-reference/server-details/alive) - Agent Server REST API documentation

## Examples

The `examples/` directory contains comprehensive usage examples:

- **Standalone SDK** (`examples/01_standalone_sdk/`) - Basic agent usage, custom tools, and microagents
- **Remote Agent Server** (`examples/02_remote_agent_server/`) - Client-server architecture and WebSocket connections
- **GitHub Workflows** (`examples/03_github_workflows/`) - CI/CD integration and automated workflows

## Contributing

For development setup, testing, and contribution guidelines, see [DEVELOPMENT.md](DEVELOPMENT.md).

## Community

- [Join Slack](https://openhands.dev/joinslack) - Connect with the OpenHands community
- [GitHub Repository](https://github.com/OpenHands/agent-sdk) - Source code and issues
- [Documentation](https://docs.openhands.dev/sdk) - Complete documentation

## License

MIT License - see [LICENSE](LICENSE) for details.
