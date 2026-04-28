<a name="readme-top"></a>

<div align="center">
  <img src="https://raw.githubusercontent.com/OpenHands/docs/main/openhands/static/img/logo.png" alt="Logo" width="200">
  <h1 align="center">OpenHands Software Agent SDK </h1>
</div>


<div align="center">
  <a href="https://github.com/OpenHands/software-agent-sdk/blob/main/LICENSE"><img src="https://img.shields.io/github/license/OpenHands/software-agent-sdk?style=for-the-badge&color=blue" alt="MIT License"></a>
  <a href="https://openhands.dev/joinslack"><img src="https://img.shields.io/badge/Slack-Join%20Us-red?logo=slack&logoColor=white&style=for-the-badge" alt="Join our Slack community"></a>
  <br>
  <a href="https://docs.openhands.dev/sdk"><img src="https://img.shields.io/badge/Documentation-000?logo=googledocs&logoColor=FFE165&style=for-the-badge" alt="Check out the documentation"></a>
  <a href="https://arxiv.org/abs/2511.03690"><img src="https://img.shields.io/badge/Paper-000?logoColor=FFE165&logo=arxiv&style=for-the-badge" alt="Tech Report"></a>
  <a href="https://docs.google.com/spreadsheets/d/1wOUdFCMyY6Nt0AIqF705KN4JKOWgeI4wUGUP60krXXs/edit?gid=811504672#gid=811504672"><img src="https://img.shields.io/badge/SWEBench-77.6-000?logoColor=FFE165&style=for-the-badge" alt="Benchmark Score"></a>
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

The OpenHands Software Agent SDK is a set of Python and REST APIs for **building agents that work with code**.

You can use the OpenHands Software Agent SDK for:
* One-off tasks, like building a README for your repo
* Routine maintenance tasks, like updating dependencies
* Major tasks that involve multiple agents, like refactors and rewrites

Importantly, agents can either use the local machine as their workspace, or run inside ephemeral workspaces
(e.g. in Docker or Kubernetes) using the Agent Server.

You can even use the SDK to build new developer experiences: it’s the engine behind the
[OpenHands CLI](https://github.com/OpenHands/OpenHands-CLI) and [OpenHands Cloud](https://github.com/OpenHands/OpenHands).

Get started with some [examples](https://docs.openhands.dev/sdk/guides/hello-world) or [check out the docs](https://docs.openhands.dev/sdk) to learn more.

## Quick Start

Here's what building with the SDK looks like:

```python
import os

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


llm = LLM(
    model="anthropic/claude-sonnet-4-5-20250929",
    api_key=os.getenv("LLM_API_KEY"),
)

agent = Agent(
    llm=llm,
    tools=[
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
        Tool(name=TaskTrackerTool.name),
    ],
)

cwd = os.getcwd()
conversation = Conversation(agent=agent, workspace=cwd)

conversation.send_message("Write 3 facts about the current project into FACTS.txt.")
conversation.run()
print("All done!")
```

For installation instructions and detailed setup, see the [Getting Started Guide](https://docs.openhands.dev/sdk/getting-started).
For local development from this repository, run `make build` to install the workspace dependencies and pre-commit hooks.

## Documentation

For detailed documentation, tutorials, and API reference, visit:

**[https://docs.openhands.dev/sdk](https://docs.openhands.dev/sdk)**

The documentation includes:
- [Getting Started Guide](https://docs.openhands.dev/sdk/getting-started) - Installation and setup
- [Architecture & Core Concepts](https://docs.openhands.dev/sdk/arch/overview) - Agents, tools, workspaces, and more
- [Guides](https://docs.openhands.dev/sdk/guides/hello-world) - Hello World, custom tools, MCP, skills, and more
- [Agent Server API Reference](https://docs.openhands.dev/sdk/guides/agent-server/api-reference/server-details/alive) - REST API reference for the remote agent server

## Examples

The `examples/` directory contains comprehensive usage examples:

- **Standalone SDK** (`examples/01_standalone_sdk/`) - Basic agent usage, custom tools, and skills
- **Remote Agent Server** (`examples/02_remote_agent_server/`) - Client-server architecture and WebSocket connections
- **GitHub Workflows** (`examples/03_github_workflows/`) - CI/CD integration and automated workflows

## Skills for modern package tooling

If you enable public skills with `AgentContext(load_public_skills=True)`, the default
`OpenHands/extensions` marketplace includes, for example, `uv` and `deno` skills.
Agents can automatically pick up current package-management guidance for repositories
that use markers like `uv.lock`, `deno.json`, `deno.jsonc`, or `deno.lock`.

See `examples/01_standalone_sdk/03_activate_skill.py` for a minimal example that
turns on public skill loading.

## Contributing

For development setup, testing, and contribution guidelines, see [DEVELOPMENT.md](DEVELOPMENT.md).

## Community

- [Join Slack](https://openhands.dev/joinslack) - Connect with the OpenHands community
- [GitHub Repository](https://github.com/OpenHands/software-agent-sdk) - Source code and issues
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
<hr>

### Thank You to Our Contributors

[![Contributors](https://assets.openhands.dev/readme/openhands-software-agent-sdk-contributors.svg)](https://github.com/OpenHands/software-agent-sdk/graphs/contributors)

<hr>

### Trusted by Engineers at

<div align="center">
<br/><br/>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://assets.openhands.dev/logos/external/white/tiktok.svg">
  <img src="https://assets.openhands.dev/logos/external/black/tiktok.svg" alt="TikTok" height="17" hspace="5">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://assets.openhands.dev/logos/external/white/vmware.svg">
  <img src="https://assets.openhands.dev/logos/external/black/vmware.svg" alt="VMware" height="17" hspace="5">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://assets.openhands.dev/logos/external/white/roche.svg">
  <img src="https://assets.openhands.dev/logos/external/black/roche.svg" alt="Roche" height="17" hspace="5">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://assets.openhands.dev/logos/external/white/amazon.svg">
  <img src="https://assets.openhands.dev/logos/external/black/amazon.svg" alt="Amazon" height="17" hspace="5">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://assets.openhands.dev/logos/external/white/c3-ai.svg">
  <img src="https://assets.openhands.dev/logos/external/black/c3-ai.svg" alt="C3 AI" height="17" hspace="5">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://assets.openhands.dev/logos/external/white/netflix.svg">
  <img src="https://assets.openhands.dev/logos/external/black/netflix.svg" alt="Netflix" height="17" hspace="5">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://assets.openhands.dev/logos/external/white/mastercard.svg">
  <img src="https://assets.openhands.dev/logos/external/black/mastercard.svg" alt="Mastercard" height="17" hspace="5">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://assets.openhands.dev/logos/external/white/red-hat.svg">
  <img src="https://assets.openhands.dev/logos/external/black/red-hat.svg" alt="Red Hat" height="17" hspace="5">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://assets.openhands.dev/logos/external/white/mongodb.svg">
  <img src="https://assets.openhands.dev/logos/external/black/mongodb.svg" alt="MongoDB" height="17" hspace="5">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://assets.openhands.dev/logos/external/white/apple.svg">
  <img src="https://assets.openhands.dev/logos/external/black/apple.svg" alt="Apple" height="17" hspace="5">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://assets.openhands.dev/logos/external/white/nvidia.svg">
  <img src="https://assets.openhands.dev/logos/external/black/nvidia.svg" alt="NVIDIA" height="17" hspace="5">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://assets.openhands.dev/logos/external/white/google.svg">
  <img src="https://assets.openhands.dev/logos/external/black/google.svg" alt="Google" height="17" hspace="5">
</picture>
</div>

