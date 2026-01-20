# Loading Plugins via Conversation

This example demonstrates the **recommended** way to load plugins in OpenHands SDK using the `plugins` parameter on the `Conversation` class.

## Why Use This Approach?

The `plugins` parameter provides several advantages over manually loading plugins:

1. **Automatic Loading**: Plugins are fetched and loaded automatically
2. **Automatic Merging**: Skills, MCP configs, and hooks are merged correctly
3. **Multi-Plugin Support**: Load multiple plugins with proper conflict resolution
4. **Git Support**: Use GitHub shorthand, git URLs, or local paths
5. **Version Pinning**: Pin to specific branches, tags, or commits
6. **Monorepo Support**: Load plugins from subdirectories in monorepos

## Basic Usage

```python
from openhands.sdk import Agent, Conversation
from openhands.sdk.plugin import PluginSource

agent = Agent(llm=llm, tools=[...])

conversation = Conversation(
    agent=agent,
    workspace="./workspace",
    plugins=[
        PluginSource(source="github:org/security-plugin", ref="v2.0.0"),
        PluginSource(source="/local/path/to/plugin"),
    ],
)
```

## Plugin Source Formats

### GitHub Shorthand

```python
PluginSource(source="github:owner/repo")
PluginSource(source="github:owner/repo", ref="v1.0.0")  # Pin to tag
PluginSource(source="github:owner/repo", ref="main")    # Pin to branch
```

### Git URL

```python
PluginSource(source="https://github.com/org/repo.git")
PluginSource(source="https://gitlab.com/org/repo.git", ref="develop")
PluginSource(source="git@github.com:org/repo.git")  # SSH
```

### Local Path

```python
PluginSource(source="/absolute/path/to/plugin")
PluginSource(source="./relative/path/to/plugin")
PluginSource(source="~/home/plugins/my-plugin")
```

### Monorepo (repo_path)

For plugins located in a subdirectory of a repository:

```python
PluginSource(
    source="github:org/plugins-monorepo",
    repo_path="plugins/security",
)
```

## Multiple Plugins

When loading multiple plugins:

- **Skills**: Override by name (last plugin wins)
- **MCP Config**: Override by server name (last plugin wins)
- **Hooks**: Concatenate (all hooks run)

```python
plugins = [
    PluginSource(source="github:org/base-plugin"),
    PluginSource(source="github:org/overlay-plugin"),  # Overrides base
]
```

## Remote Conversations

The same `plugins` parameter works with remote agent servers:

```python
from openhands.sdk.workspace import RemoteWorkspace

conversation = Conversation(
    agent=agent,
    workspace=RemoteWorkspace(host="http://agent-server:8000"),
    plugins=[
        PluginSource(source="github:org/plugin", ref="v1.0.0"),
    ],
)
```

Plugins are sent to the server and loaded there (inside the sandbox).

## Running This Example

```bash
# Set your API key
export LLM_API_KEY=your-api-key

# Optionally set a different model
export LLM_MODEL=anthropic/claude-sonnet-4-5-20250929

# Run the example
python main.py
```

## See Also

- `02_loading_plugins/` - Manual plugin loading (for advanced customization)
- Plugin specification: A plugin is a directory with:
  - `.plugin/plugin.json` or `.claude-plugin/plugin.json` - Manifest
  - `skills/` - Agent skills (markdown files)
  - `hooks/hooks.json` - Event handlers
  - `.mcp.json` - MCP server configuration
