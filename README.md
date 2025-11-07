<a name="readme-top"></a>

<div align="center">
  <img src="https://raw.githubusercontent.com/OpenHands/docs/main/openhands/static/img/logo.png" alt="Logo" width="200">
  <h1 align="center">OpenHands Software Agent SDK </h1>
</div>


<div align="center">
  <a href="https://github.com/OpenHands/software-agent-sdk/blob/main/LICENSE"><img src="https://img.shields.io/github/license/OpenHands/software-agent-sdk?style=for-the-badge&color=blue" alt="MIT License"></a>
  <a href="https://all-hands.dev/joinslack"><img src="https://img.shields.io/badge/Slack-Join%20Us-red?logo=slack&logoColor=white&style=for-the-badge" alt="Join our Slack community"></a>
  <br>
  <a href="https://docs.openhands.dev/sdk"><img src="https://img.shields.io/badge/Documentation-000?logo=googledocs&logoColor=FFE165&style=for-the-badge" alt="Check out the documentation"></a>
  <a href="https://arxiv.org/abs/2511.03690"><img src="https://img.shields.io/badge/Paper-000?logoColor=FFE165&logo=arxiv&style=for-the-badge" alt="Tech Report"></a>
  <a href="https://docs.google.com/spreadsheets/d/1wOUdFCMyY6Nt0AIqF705KN4JKOWgeI4wUGUP60krXXs/edit?gid=811504672#gid=811504672"><img src="https://img.shields.io/badge/SWEBench-72.8-000?logoColor=FFE165&style=for-the-badge" alt="Benchmark Score"></a>
  <br>
  <!-- Keep these links. Translations will automatically update with the README. -->
  <a href="https://www.readme-i18n.com/OpenHands/software-agent-sdk?lang=de">Deutsch</a> |
  <a href="https://www.readme-i18n.com/OpenHands/software-agent-sdk?lang=es">Español</a> |
  <a href="https://www.readme-i18n.com/OpenHands/software-agent-sdk?lang=fr">français</a> |
  <a href="https://www.readme-i18n.com/OpenHands/software-agent-sdk?lang=ja">日本語</a> |
  <a href="https://www.readme-i18n.com/OpenHands/software-agent-sdk?lang=ko">한국어</a> |
  <a href="https://www.readme-i18n.com/OpenHands/software-agent-sdk?lang=pt">Português</a> |
  <a href="https://www.readme-i18n.com/OpenHands/software-agent-sdk?lang=ru">Русский</a> |
  <a href="https://www.readme-i18n.com/OpenHands/software-agent-sdk?lang=zh">中文</a>

  <hr>
</div>

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
llm = LLM(model="openhands/claude-sonnet-4-5-20250929", api_key='...')
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

## Cite

```
@misc{wang2025openhandssoftwareagentsdk,
      title={The OpenHands Software Agent SDK: A Composable and Extensible Foundation for Production Agents}, 
      author={Xingyao Wang and Simon Rosenberg and Juan Michelini and Calvin Smith and Hoang Tran and Engel Nyst and Rohit Malhotra and Xuhui Zhou and Valerie Chen and Robert Brennan and Graham Neubig},
      year={2025},
      eprint={2511.03690},
      archivePrefix={arXiv},
      primaryClass={cs.SE},
      url={https://arxiv.org/abs/2511.03690}, 
}
```
