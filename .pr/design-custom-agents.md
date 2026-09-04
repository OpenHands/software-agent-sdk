# Design: Custom Agents in Plugins

This document describes how plugins can define custom agents with specialized behavior, custom tools, and tailored prompts using the OpenHands SDK.

---

## Overview

Plugins can provide two types of agent customization:

| Type | Definition | Use Case |
|------|------------|----------|
| **Declarative** | Markdown files with YAML frontmatter | Configuration-based: custom prompts, tool selection, model choice |
| **Programmatic** | Python code with factory functions | Behavior-based: custom tools, custom logic, external integrations |

Both types use entry points for discovery and can coexist in the same plugin.

---

## Entry Points

### Entry Point Groups

| Entry Point Group | Format | Purpose |
|-------------------|--------|---------|
| `openhands.plugins` | Module path | Plugin content (skills, hooks, MCP, declarative agents) |
| `openhands.agents` | `module:factory_function` | Programmatic agent factory |

### pyproject.toml Configuration

```toml
[project.entry-points."openhands.plugins"]
security-scanner = "openhands_plugin_security"

[project.entry-points."openhands.agents"]
security-scanner = "openhands_plugin_security:create_agent"
```

- **`openhands.plugins`** points to the module containing plugin content
- **`openhands.agents`** points to the factory function that creates the agent

---

## Declarative Agents (Markdown)

Declarative agents are defined in Markdown files in the `agents/` directory. They configure existing agent behavior without custom Python code.

### Directory Structure

```
my-plugin/
├── .plugin/plugin.json
├── agents/
│   ├── code-reviewer.md        # Declarative agent
│   └── security-auditor.md     # Another declarative agent
└── skills/
    └── ...
```

### Agent Definition Format

```markdown
---
name: code-reviewer
description: Reviews code for quality, style, and best practices
model: gpt-4o
tools:
  - Read
  - Glob
  - Grep
skills:
  - code-style-guide
  - review-checklist
max_iteration_per_run: 50
permission_mode: confirm_risky
---

You are a code reviewer specializing in Python and TypeScript projects.

When reviewing code:
1. Check for correctness and potential bugs
2. Evaluate code style and readability
3. Suggest improvements and optimizations
4. Flag security concerns

Be constructive and specific in your feedback.
```

### Frontmatter Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | **Required**. Agent identifier (kebab-case) |
| `description` | string | **Required**. Human-readable description |
| `model` | string | LLM model to use (overrides default) |
| `tools` | list[string] | Tool allowlist (if specified, only these tools are available) |
| `skills` | list[string] | Skills to preload by name |
| `max_iteration_per_run` | int | Maximum iterations per run |
| `permission_mode` | string | One of: `always_confirm`, `never_confirm`, `confirm_risky` |
| `hooks` | object | Lifecycle hooks configuration |
| `mcp_servers` | object | MCP server configuration |

### Loading Declarative Agents

```python
from openhands.sdk.subagent import load_agents_from_dir, AgentDefinition

def load_plugin_agents(plugin_path: Path) -> list[AgentDefinition]:
    """Load declarative agents from plugin's agents/ directory."""
    agents_dir = plugin_path / "agents"
    if not agents_dir.exists():
        return []
    
    return load_agents_from_dir(agents_dir)
```

---

## Programmatic Agents (Python)

Programmatic agents are defined in Python code with a factory function. They can include custom tools, custom logic, and external integrations.

### Directory Structure

```
my-plugin/
├── .plugin/plugin.json
├── skills/
├── pyproject.toml
└── src/
    └── openhands_plugin_security/
        ├── __init__.py          # Exports create_agent
        ├── agent.py             # Custom agent class
        └── tools.py             # Custom tool definitions
```

### Factory Function Interface

```python
# src/openhands_plugin_security/__init__.py

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.llm import LLM

def create_agent(
    llm: LLM,
    tools: list | None = None,
    config: dict | None = None,
) -> AgentBase:
    """Factory function to create custom agent.
    
    Args:
        llm: LLM instance configured by the platform
        tools: Base tools provided by the platform (can be extended)
        config: Optional configuration dict from plugin parameters
    
    Returns:
        Configured agent instance
    """
    from .agent import SecurityAgent
    from .tools import VulnerabilityScanTool, DependencyCheckTool
    
    # Add custom tools to the base tools
    custom_tools = [
        VulnerabilityScanTool(),
        DependencyCheckTool(),
    ]
    all_tools = (tools or []) + custom_tools
    
    return SecurityAgent(
        llm=llm,
        tools=all_tools,
        **config or {},
    )
```

### Custom Agent Class

```python
# src/openhands_plugin_security/agent.py

from openhands.sdk.agent.base import AgentBase

class SecurityAgent(AgentBase):
    """Custom agent specialized for security scanning."""
    
    # Custom system prompt
    system_prompt_filename = "security_prompt.j2"
    
    def __init__(self, llm, tools, scan_depth: str = "standard", **kwargs):
        super().__init__(llm=llm, tools=tools, **kwargs)
        self.scan_depth = scan_depth
    
    async def step(self, state):
        """Custom step logic (optional override)."""
        # Add custom pre-processing
        if self._should_run_initial_scan(state):
            await self._run_security_scan(state)
        
        # Call parent step
        return await super().step(state)
    
    def _should_run_initial_scan(self, state) -> bool:
        # Custom logic
        return state.iteration == 0
    
    async def _run_security_scan(self, state):
        # Custom security scan logic
        pass
```

### Custom Tools

```python
# src/openhands_plugin_security/tools.py

from openhands.sdk.tool import Tool
from pydantic import Field

class VulnerabilityScanTool(Tool):
    """Scan code for security vulnerabilities."""
    
    name: str = "vulnerability_scan"
    description: str = "Scan a file or directory for security vulnerabilities"
    
    path: str = Field(description="Path to scan")
    severity: str = Field(default="all", description="Minimum severity: low, medium, high, critical")
    
    async def execute(self) -> str:
        """Run vulnerability scan."""
        import subprocess
        
        result = subprocess.run(
            ["semgrep", "--config", "auto", self.path, "--json"],
            capture_output=True,
            text=True,
        )
        
        return self._format_results(result.stdout)
    
    def _format_results(self, json_output: str) -> str:
        # Format scan results for LLM consumption
        ...


class DependencyCheckTool(Tool):
    """Check dependencies for known vulnerabilities."""
    
    name: str = "dependency_check"
    description: str = "Check project dependencies for known vulnerabilities"
    
    manifest_path: str = Field(description="Path to package manifest (requirements.txt, package.json, etc.)")
    
    async def execute(self) -> str:
        """Run dependency vulnerability check."""
        ...
```

---

## Agent Discovery and Loading

### Discovery Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    Plugin Loading                            │
└─────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼                                         ▼
┌─────────────────────┐                 ┌─────────────────────┐
│  openhands.plugins  │                 │  openhands.agents   │
│    entry point      │                 │    entry point      │
└──────────┬──────────┘                 └──────────┬──────────┘
           │                                       │
           ▼                                       ▼
┌─────────────────────┐                 ┌─────────────────────┐
│  Load plugin module │                 │  Load factory func  │
│  - skills/          │                 │  - create_agent()   │
│  - commands/        │                 │                     │
│  - hooks/           │                 │                     │
│  - agents/ (decl.)  │                 │                     │
└──────────┬──────────┘                 └──────────┬──────────┘
           │                                       │
           └───────────────────┬───────────────────┘
                               ▼
                    ┌─────────────────────┐
                    │  Merge into Agent   │
                    │  or Create Custom   │
                    └─────────────────────┘
```

### Loading Implementation

```python
from importlib.metadata import entry_points

def load_plugin_with_agent(
    plugin_name: str,
    base_llm: LLM,
    base_tools: list[Tool],
    config: dict | None = None,
) -> tuple[Plugin, AgentBase]:
    """Load plugin and create agent (custom or default).
    
    Returns:
        (plugin, agent) tuple
    """
    # Load plugin content
    plugin_ep = get_entry_point("openhands.plugins", plugin_name)
    plugin_module = plugin_ep.load()
    plugin_path = get_module_path(plugin_module)
    plugin = Plugin.load(plugin_path)
    
    # Check for custom agent factory
    agent_ep = get_entry_point("openhands.agents", plugin_name)
    
    if agent_ep:
        # Use custom agent factory
        create_agent = agent_ep.load()
        agent = create_agent(
            llm=base_llm,
            tools=base_tools,
            config=config,
        )
    else:
        # Use default agent with plugin content merged in
        agent = Agent(llm=base_llm, tools=base_tools)
    
    # Always merge plugin content (skills, hooks, MCP) into agent
    agent = plugin.add_skills_to(agent.agent_context)
    agent = agent.model_copy(update={
        "mcp_config": plugin.add_mcp_config_to(agent.mcp_config)
    })
    
    return plugin, agent


def get_entry_point(group: str, name: str):
    """Get a specific entry point by group and name."""
    eps = entry_points(group=group)
    for ep in eps:
        if ep.name == name:
            return ep
    return None
```

---

## Conversation Integration

### Starting a Conversation with Plugin Agent

```python
from openhands.sdk import Conversation, Agent, LLM
from openhands.sdk.plugin import PluginSource, load_plugin_with_agent

async def start_plugin_conversation(
    plugin_source: PluginSource,
    initial_message: str,
):
    """Start a conversation using a plugin's agent."""
    
    # Fetch plugin
    plugin_path = Plugin.fetch(
        plugin_source.source,
        ref=plugin_source.ref,
        version=plugin_source.version,
    )
    
    # Create LLM and base tools
    llm = LLM(model="gpt-4o")
    base_tools = get_default_tools()
    
    # Load plugin and get agent
    plugin, agent = load_plugin_with_agent(
        plugin_path=plugin_path,
        base_llm=llm,
        base_tools=base_tools,
        config=plugin_source.parameters,
    )
    
    # Create conversation with the agent
    conversation = Conversation(
        agent=agent,
        workspace="/workspace/project",
    )
    
    # Send initial message
    conversation.send_message(initial_message)
    
    # Run the conversation
    await conversation.run()
```

### Plugin Parameters

Plugins can accept parameters that are passed to the agent factory:

**plugin.json:**
```json
{
  "name": "security-scanner",
  "version": "1.0.0",
  "parameters": {
    "scan_depth": {
      "type": "string",
      "description": "Depth of security scan",
      "enum": ["quick", "standard", "thorough"],
      "default": "standard"
    },
    "ignore_patterns": {
      "type": "array",
      "description": "File patterns to ignore",
      "items": {"type": "string"},
      "default": []
    }
  }
}
```

**PluginSource with parameters:**
```python
source = PluginSource(
    source="pypi:openhands-plugin-security",
    version="1.0.0",
    parameters={
        "scan_depth": "thorough",
        "ignore_patterns": ["**/test/**", "**/vendor/**"],
    },
)
```

**Factory receives parameters:**
```python
def create_agent(llm, tools, config):
    scan_depth = config.get("scan_depth", "standard")
    ignore_patterns = config.get("ignore_patterns", [])
    
    return SecurityAgent(
        llm=llm,
        tools=tools,
        scan_depth=scan_depth,
        ignore_patterns=ignore_patterns,
    )
```

---

## Relationship: Declarative vs Programmatic

| Aspect | Declarative (Markdown) | Programmatic (Python) |
|--------|------------------------|----------------------|
| **Location** | `agents/*.md` | `src/<module>/` |
| **Entry point** | Discovered via `openhands.plugins` | Explicit `openhands.agents` |
| **Custom tools** | No (select from available) | Yes (`@tool` or Tool subclass) |
| **Custom logic** | No | Yes (override `step()`, etc.) |
| **External deps** | No | Yes (in `pyproject.toml`) |
| **Use case** | Prompt engineering, tool selection | Complex behavior, integrations |

### When to Use Each

**Use Declarative when:**
- You only need to customize the system prompt
- You want to restrict which tools are available
- You want to preload specific skills
- No custom Python code is needed

**Use Programmatic when:**
- You need custom tools that call external APIs
- You need custom step logic or pre/post processing
- You have external Python dependencies
- You need complex initialization

### Using Both Together

A plugin can have both:

```
my-plugin/
├── agents/
│   └── simple-reviewer.md      # Declarative: prompt-only customization
├── src/
│   └── openhands_plugin/
│       ├── __init__.py         # Exports create_agent for advanced-scanner
│       └── agent.py
└── pyproject.toml
```

```toml
# Programmatic agent for complex use case
[project.entry-points."openhands.agents"]
advanced-scanner = "openhands_plugin:create_agent"

# Plugin content (includes declarative agents in agents/)
[project.entry-points."openhands.plugins"]
my-plugin = "openhands_plugin"
```

---

## Security Considerations

### Code Execution

Programmatic agents execute arbitrary Python code. Considerations:

1. **Trust model**: Only install plugins from trusted sources
2. **Dependency review**: Plugin dependencies are installed to isolated directory
3. **Sandbox boundary**: Custom tools run in the same process as the agent

### Recommendations

1. **For OpenHands Cloud**: Implement plugin verification/signing
2. **For self-hosted**: Document security implications clearly
3. **For all**: Consider sandboxing custom tool execution in future

---

## Open Questions

1. **Tool registration timing**: Should custom tools be registered at plugin load time or agent creation time?

2. **Agent lifecycle**: How should custom agents handle conversation resume?

3. **Multi-agent plugins**: Can a plugin provide multiple programmatic agents?

4. **Agent inheritance**: Should custom agents extend `Agent` or `AgentBase`?

5. **State persistence**: How do custom agents persist state across conversation turns?
