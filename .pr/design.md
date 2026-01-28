# Design Document: Python-Packaged Plugins for OpenHands

## Overview

This document proposes extending the existing OpenHands plugin system to support plugins distributed as Python packages (pip/uv installable), while maintaining full compatibility with the Claude Code plugin structure.

### Goals

1. **Same Structure**: Plugin consumed from git repo and from Python package have identical directory structure
2. **Python Package Distribution**: Enable `pip install openhands-plugin-xyz` workflow  
3. **Dependency Management**: Leverage pip/uv for transitive dependency resolution (including plugin-to-plugin)
4. **Isolated Installation**: Install plugins to a dedicated directory, NOT the agent's working environment
5. **Optional Python Code**: Packages can optionally include Python code for custom tools, agent setup, etc.
6. **Seamless Integration**: Fit naturally into the existing `Plugin.fetch()` / `Plugin.load()` API

### Non-Goals (v1)

- Multi-plugin packages (one package = one plugin for simplicity)
- Automatic MCP server installation (npm dependencies)
- Editable install support for end-users (developer convenience only)

### Future Direction

This design sets the foundation for **Custom Agent Packages** where Python packages can define custom agent classes with specialized tools and logic. The optional `__init__.py` in plugin packages allows including Python code that defines custom tools, agent factory functions, and specialized behavior.

---

## Background: Two Complementary Systems

Understanding how this design fits with existing work requires understanding two complementary systems:

### 1. Merged Plugin System (Current in main)

The plugin system merged to `software-agent-sdk` handles **content packages**:
- Skills (from `skills/` directory)
- Commands (from `commands/` directory)  
- Hooks (from `hooks/hooks.json`)
- MCP config (from `.mcp.json`)
- Agent definitions (markdown files in `agents/`)

**Key characteristic**: Plugins are loaded INTO an existing agent instance. The agent class stays the same, but its context (skills, MCP config) is modified.

```python
# Current plugin loading - modifies agent's context
updated_agent = agent.model_copy(update={
    "agent_context": merged_context,  # Skills merged in
    "mcp_config": merged_mcp,          # MCP config merged in
})
```

### 2. Custom Agent Design (PR #11876 in OpenHands/OpenHands)

The custom agent design from PR #11876 handles **behavior packages**:
- Custom `AgentBase` subclasses with specialized logic
- Custom tools defined in Python
- Factory functions for agent instantiation

**Key characteristic**: Creates entirely NEW agent instances from custom classes.

```python
# Custom agent loading - creates new agent instance
def create_agent(llm, tools, config) -> AgentBase:
    return MyCustomAgent(llm=llm, tools=tools, config=config)
```

### How They Fit Together

These systems are **complementary**, not competing:

| Aspect | Plugin System | Custom Agent System |
|--------|--------------|---------------------|
| Purpose | Content (skills, hooks, MCP) | Behavior (Python code) |
| Agent class | Same (modified copy) | Different (custom subclass) |
| Entry point | `openhands.plugins` | `openhands.agents` |
| What changes | Agent's context/config | Agent's class/behavior |

**A single Python package can provide BOTH**:
- Plugin content (skills, commands, hooks, MCP)
- Custom agent behavior (Python code with `create_agent()`)

---

## Key Design Decisions

### 1. Package Structure: Unified Plugin + Agent Package

**Decision**: The Python module IS the plugin directory, with optional agent code

The plugin structure is **identical** whether consumed from git or from a Python package. The only additions for Python packages are `pyproject.toml` and optional Python code files.

**From Git (current):**
```
my-security-plugin/
|-- .plugin/
|   +-- plugin.json
|-- skills/
|   |-- security-scan/
|   |   +-- SKILL.md
|   +-- vulnerability-check.md
|-- commands/
|   +-- scan.md
|-- hooks/
|   +-- hooks.json
+-- .mcp.json
```

**As Python Package (content-only plugin):**
```
openhands-plugin-security/           # Repository/project root
|-- pyproject.toml                   # Python packaging metadata
|-- README.md
+-- openhands_plugin_security/       # Python module = plugin directory
    |-- __init__.py                  # Minimal: just marks as package
    |-- .plugin/
    |   +-- plugin.json
    |-- skills/
    |   |-- security-scan/
    |   |   +-- SKILL.md
    |   +-- vulnerability-check.md
    |-- commands/
    |   +-- scan.md
    |-- hooks/
    |   +-- hooks.json
    +-- .mcp.json
```

**As Python Package (with custom agent):**
```
openhands-plugin-security/           # Repository/project root
|-- pyproject.toml                   # Python packaging metadata
|-- README.md
+-- openhands_plugin_security/       # Python module = plugin directory
    |-- __init__.py                  # Exports create_agent()
    |-- agent.py                     # Custom AgentBase subclass
    |-- tools.py                     # Custom tool definitions
    |-- .plugin/
    |   +-- plugin.json
    |-- skills/
    |   +-- ...
    |-- commands/
    |   +-- ...
    |-- hooks/
    |   +-- hooks.json
    +-- .mcp.json
```

**Key Insight**: The Python module directory has the exact same structure as the git-based plugin. When installed, `Plugin.load()` can treat it as a regular directory. Custom agent code is additive, not replacing.

**Optional Python Code** (`agent.py`, `tools.py`, etc.) can contain:

```python
# agent.py - Custom agent with specialized behavior
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.llm import LLM
from openhands.sdk.tool import Tool

class SecurityAgent(AgentBase):
    """Custom agent specialized for security scanning."""
    
    async def step(self, state):
        # Custom reasoning logic
        ...

# Factory function (required for custom agents)
def create_agent(llm: LLM, tools: list[Tool], config: dict = None) -> AgentBase:
    return SecurityAgent(llm=llm, tools=tools, **config or {})
```

```python
# tools.py - Custom tools
from openhands.sdk.tool import Tool, register_tool

@register_tool
class VulnerabilityScanTool(Tool):
    """Scan code for security vulnerabilities."""
    
    async def execute(self, code_path: str) -> str:
        # Custom tool implementation
        ...
```

```python
# __init__.py - Package exports
from .agent import create_agent, SecurityAgent
from .tools import VulnerabilityScanTool

__all__ = ['create_agent', 'SecurityAgent', 'VulnerabilityScanTool']
```

---

### 2. Isolated Installation Directory

**Decision**: Install plugins to `~/.openhands/plugins/`, NOT system site-packages

**Rationale**: Agent code shouldn't pollute the environment in which the agent normally operates and manipulates. The agent's workspace should remain clean and predictable.

**Installation Location**:
```
~/.openhands/
+-- plugins/
    |-- lib/                         # Installed packages
    |   +-- python3.12/
    |       +-- site-packages/
    |           |-- openhands_plugin_security/
    |           |-- openhands_plugin_git_tools/
    |           +-- requests/        # Transitive dependency
    +-- cache/                       # Download cache
```

**Implementation**: Use `uv pip install --target` or `pip install --target`:
```bash
uv pip install --target ~/.openhands/plugins/lib openhands-plugin-security
```

**Runtime**: Add to `sys.path` when loading plugins:
```python
import sys
sys.path.insert(0, str(Path.home() / ".openhands" / "plugins" / "lib"))
```

**Benefits**:
- Clean separation from agent's working environment
- Easy to wipe and reinstall all plugins
- No conflicts with system packages
- Clear audit trail of agent dependencies

---

### 3. Version Specification in PluginSource

**Decision**: Version is an attribute of PluginSource, similar to `ref` for git sources

**Current API (git)**:
```python
PluginSource(source="github:owner/repo", ref="v1.0.0")
```

**Proposed API (PyPI)**:
```python
# Exact version
PluginSource(source="pypi:security-scanner", version="1.0.0")

# Version constraint
PluginSource(source="pypi:security-scanner", version=">=1.0.0,<2.0.0")

# Latest (default if version omitted)
PluginSource(source="pypi:security-scanner")
```

**PluginSource Model Update**:
```python
class PluginSource(BaseModel):
    source: str = Field(
        description="Plugin source: 'github:owner/repo', 'pypi:package-name', or local path"
    )
    ref: str | None = Field(
        default=None,
        description="Git ref (branch, tag, commit) - only for git sources",
    )
    version: str | None = Field(
        default=None,
        description="Version constraint (e.g., '1.0.0', '>=1.0.0') - only for pypi sources",
    )
    repo_path: str | None = Field(
        default=None,
        description="Subdirectory path within git repository (monorepo support)",
    )
```

---

### 4. Plugin-to-Plugin Dependencies

**Decision**: Express as ordinary Python package dependencies. Let pip/uv do the heavy lifting.

**Example**: If `security-scanner` plugin depends on `git-tools` plugin:

```toml
# openhands-plugin-security/pyproject.toml
[project]
name = "openhands-plugin-security"
version = "1.0.0"
dependencies = [
    "openhands-plugin-git-tools>=1.0.0",  # Plugin dependency
    "requests>=2.28.0",                    # Library dependency
]
```

When user installs `openhands-plugin-security`, pip/uv automatically installs `openhands-plugin-git-tools` too.

**Benefits**:
- No custom dependency resolution
- Leverages battle-tested pip/uv resolver
- Version conflicts handled by standard tooling
- Transitive dependencies just work

---

### 5. Version Source of Truth

**Decision**: `pyproject.toml` is the source of truth. Provide validation tooling.

**Simplification**: Since all packages are new, we can require:
- Modern `pyproject.toml` only (no setup.py)
- Python 3.10+ only
- Version in plugin.json is optional (falls back to package version)

**Validation Tool**:
```bash
# Validates package structure and version consistency
ohp validate --python-package .

# Output:
# OK pyproject.toml found
# OK Plugin module found: openhands_plugin_security/
# OK plugin.json found at openhands_plugin_security/.plugin/plugin.json
# OK Versions match: 1.0.0
# OK Entry point configured correctly
# OK Package data includes all plugin files
```

**If versions don't match**, the tool warns but doesn't fail. Runtime uses Python package version.

---

### 6. Discovery Mechanism

**Decision**: Entry points for discovery, explicit loading for use

```toml
# pyproject.toml
[project.entry-points."openhands.plugins"]
security-scanner = "openhands_plugin_security"
```

**API**:
```python
from openhands.sdk.plugin import Plugin

# List all installed plugins (uses entry points)
installed = Plugin.list_installed()
# Returns: [("security-scanner", "1.0.0"), ("git-tools", "2.1.0")]

# Load specific installed plugin
plugin = Plugin.load_installed("security-scanner")

# Load with version constraint (installs if needed)
plugin = Plugin.fetch_and_load(
    PluginSource(source="pypi:security-scanner", version=">=1.0.0")
)
```

**Entry Point Name**: Matches `name` field in plugin.json (kebab-case).

---

### 7. Simplifying Assumptions

Since we're targeting new packages only:

| Aspect | Decision |
|--------|----------|
| Python version | 3.10+ only |
| Package format | pyproject.toml only (no setup.py) |
| Build backend | Any PEP 517 compliant (setuptools, hatch, flit) |
| Editable installs | Developer convenience only, not runtime supported |
| Legacy packages | Not supported |

**Editable Installs**: 
- Developers use `pip install -e .` during development
- For production/runtime, always do proper install to isolated directory
- No need to handle editable install edge cases in `Plugin.load_installed()`

---

## Unified Loading Flow

This section describes how the plugin content system and custom agent system work together when loading a Python-packaged plugin.

### Loading Sequence

```
+------------------------------------------------------------------+
|                     PluginSource Received                         |
|         source="pypi:security-scanner", version="1.0.0"          |
+------------------------------------------------------------------+
                                |
                                v
+------------------------------------------------------------------+
|              1. Install Package to Isolated Directory             |
|                    ~/.openhands/plugins/lib/                      |
+------------------------------------------------------------------+
                                |
                                v
+------------------------------------------------------------------+
|              2. Check for Entry Points in Package                 |
|                                                                   |
|   openhands.plugins?  ---------> Load plugin content             |
|   openhands.agents?   ---------> Load custom agent factory       |
+------------------------------------------------------------------+
                                |
                +---------------+---------------+
                v                               v
+---------------------------+   +-------------------------------+
|   3a. Plugin Content      |   |   3b. Custom Agent            |
|   (openhands.plugins)     |   |   (openhands.agents)          |
|                           |   |                               |
|   - Load skills/          |   |   - Import create_agent()     |
|   - Load commands/        |   |   - Instantiate custom agent  |
|   - Load hooks/           |   |   - Register custom tools     |
|   - Load .mcp.json        |   |                               |
+---------------------------+   +-------------------------------+
                |                               |
                +---------------+---------------+
                                v
+------------------------------------------------------------------+
|                    4. Merge Into Conversation                     |
|                                                                   |
|   If custom agent: Use custom agent instance                     |
|   Else: Use default agent with merged plugin content             |
|                                                                   |
|   Either way: Plugin content (skills, hooks, MCP) is applied     |
+------------------------------------------------------------------+
```

### Implementation Pseudocode

```python
async def load_plugin_package(source: PluginSource, base_agent: AgentBase) -> tuple[AgentBase, HookConfig]:
    """Load a Python-packaged plugin, handling both content and custom agents."""
    
    # 1. Install package to isolated directory
    package_path = await install_to_isolated_dir(
        package=source.source.replace("pypi:", ""),
        version=source.version,
        target_dir=Path.home() / ".openhands" / "plugins" / "lib"
    )
    
    # 2. Discover entry points
    plugin_ep = get_entry_point("openhands.plugins", source.plugin_name)
    agent_ep = get_entry_point("openhands.agents", source.plugin_name)
    
    # 3a. Load plugin content (if openhands.plugins entry point exists)
    plugin_content = None
    if plugin_ep:
        module_path = get_module_path(plugin_ep)
        plugin_content = Plugin.load(module_path)  # Reuse existing Plugin.load()
    
    # 3b. Load custom agent (if openhands.agents entry point exists)
    agent = base_agent
    if agent_ep:
        create_agent_func = load_entry_point(agent_ep)
        agent = create_agent_func(
            llm=base_agent.llm,
            tools=base_agent.tools,
            config={}  # Could come from plugin.json or environment
        )
    
    # 4. Merge plugin content into agent
    hooks = None
    if plugin_content:
        agent = plugin_content.add_skills_to(agent)
        agent = agent.model_copy(update={
            "mcp_config": plugin_content.add_mcp_config_to(agent.mcp_config)
        })
        hooks = plugin_content.hooks
    
    return agent, hooks
```

### Loading Modes

| Mode | Entry Points | Result |
|------|--------------|--------|
| Content-only | `openhands.plugins` only | Default agent + plugin skills/hooks/MCP |
| Agent-only | `openhands.agents` only | Custom agent, no extra content |
| Full package | Both entry points | Custom agent + plugin skills/hooks/MCP |

### Backward Compatibility

- Git-based plugins continue to work unchanged
- Packages without entry points fall back to directory-based loading
- Default agent is used when no `openhands.agents` entry point exists

---

## Implementation Plan

### Phase 1: Core Infrastructure

1. **Update PluginSource model** to support `pypi:` prefix and `version` attribute
2. **Implement isolated installation** to `~/.openhands/plugins/`
3. **Implement Plugin.fetch() for pypi:** sources
   - Parse `pypi:package-name` 
   - Install to isolated directory with version constraint
   - Return path to installed module
4. **Implement Plugin.list_installed()** using entry points
5. **Implement Plugin.load_installed()** for explicit loading

### Phase 2: Custom Agent Support

6. **Implement agent entry point discovery** (`openhands.agents`)
7. **Implement create_agent() factory loading**
8. **Integrate with conversation start flow**

### Phase 3: Tooling

9. **Create validation tool** (`ohp validate --python-package`)
10. **Create package template** (cookiecutter or copier)
11. **Document plugin author guide**

### Phase 4: Testing & Polish

12. **Create reference plugin package** and publish to PyPI
13. **Integration tests** with real PyPI installs
14. **Update existing examples** to show both git and PyPI workflows

---

## API Reference

### PluginSource (Updated)

```python
class PluginSource(BaseModel):
    """Specification for a plugin to load."""
    
    source: str
    # "github:owner/repo" - GitHub repository
    # "pypi:package-name" - PyPI package  
    # "/local/path" - Local directory
    
    ref: str | None = None
    # Git ref (branch, tag, commit) for github: sources
    
    version: str | None = None
    # Version constraint for pypi: sources
    # Examples: "1.0.0", ">=1.0.0", ">=1.0.0,<2.0.0"
    
    repo_path: str | None = None
    # Subdirectory for monorepo git sources
```

### Plugin Class (New Methods)

```python
class Plugin:
    @classmethod
    def list_installed(cls) -> list[tuple[str, str]]:
        """List all installed plugin packages.
        
        Returns:
            List of (name, version) tuples for installed plugins.
        """
        
    @classmethod
    def load_installed(cls, name: str) -> Plugin:
        """Load a plugin from an installed Python package.
        
        Args:
            name: Plugin name (entry point name, matches plugin.json name)
            
        Returns:
            Loaded Plugin instance.
            
        Raises:
            PluginNotFoundError: If plugin is not installed.
        """
        
    @classmethod  
    def install(
        cls, 
        package: str, 
        version: str | None = None,
        upgrade: bool = False,
    ) -> Path:
        """Install a plugin package to the isolated plugins directory.
        
        Args:
            package: PyPI package name
            version: Version constraint (optional)
            upgrade: If True, upgrade existing installation
            
        Returns:
            Path to installed plugin module directory.
        """
```

### Fetch Integration

```python
# Existing fetch() now handles pypi: sources
path = Plugin.fetch("pypi:security-scanner")  # Latest
path = Plugin.fetch("pypi:security-scanner", version="1.0.0")  # Specific version

# Or via PluginSource
source = PluginSource(source="pypi:security-scanner", version=">=1.0.0")
path = Plugin.fetch(source.source, version=source.version)
plugin = Plugin.load(path)
```

---

## pyproject.toml Templates

### Content-Only Plugin (No Custom Agent)

```toml
[project]
name = "openhands-plugin-security-scanner"
version = "1.0.0"
description = "Security scanning skills for OpenHands"
readme = "README.md"
requires-python = ">=3.10"
license = {text = "MIT"}
authors = [
    {name = "Security Team", email = "security@example.com"}
]
keywords = ["openhands", "plugin", "security"]
classifiers = [
    "Framework :: OpenHands",
    "Framework :: OpenHands :: Plugin",
]
dependencies = [
    # Plugin dependencies (other plugins that this one requires)
    "openhands-plugin-git-tools>=1.0.0",
]

[project.optional-dependencies]
dev = ["pytest", "ruff"]

[project.entry-points."openhands.plugins"]
# Entry point name MUST match plugin.json name
security-scanner = "openhands_plugin_security_scanner"

[project.urls]
Homepage = "https://github.com/example/security-scanner-plugin"

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]

[tool.setuptools.package-data]
openhands_plugin_security_scanner = [
    ".plugin/**/*",
    "skills/**/*", 
    "commands/**/*",
    "hooks/**/*",
    ".mcp.json",
]
```

### Plugin with Custom Agent

```toml
[project]
name = "openhands-plugin-security-scanner"
version = "1.0.0"
description = "Security scanning agent and skills for OpenHands"
readme = "README.md"
requires-python = ">=3.10"
license = {text = "MIT"}
authors = [
    {name = "Security Team", email = "security@example.com"}
]
keywords = ["openhands", "plugin", "agent", "security"]
classifiers = [
    "Framework :: OpenHands",
    "Framework :: OpenHands :: Plugin",
    "Framework :: OpenHands :: Agent",  # Indicates custom agent
]
dependencies = [
    # Python library dependencies for custom agent/tools
    "semgrep>=1.0.0",
    # Plugin dependencies (other plugins)
    "openhands-plugin-git-tools>=1.0.0",
]

[project.optional-dependencies]
dev = ["pytest", "ruff", "mypy"]

# TWO entry points: one for plugin content, one for custom agent
[project.entry-points."openhands.plugins"]
security-scanner = "openhands_plugin_security_scanner"

[project.entry-points."openhands.agents"]
security-scanner = "openhands_plugin_security_scanner:create_agent"

[project.urls]
Homepage = "https://github.com/example/security-scanner-plugin"

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]

[tool.setuptools.package-data]
openhands_plugin_security_scanner = [
    ".plugin/**/*",
    "skills/**/*", 
    "commands/**/*",
    "hooks/**/*",
    ".mcp.json",
]
```

**Key differences for custom agent packages:**
- `openhands.agents` entry point - Points to `create_agent()` factory function
- Additional Python library dependencies for custom tools
- `Framework :: OpenHands :: Agent` classifier for discoverability

---

## Open Questions for Investigation

### Python-Specific Technical Questions

1. **sys.path management**: What's the cleanest way to add isolated plugins dir to path?
   - At import time in Plugin module?
   - Via sitecustomize.py?
   - Environment variable?

2. **Package data in wheels**: Does `setuptools.package-data` correctly include all nested files?
   - Need to test with `.plugin/plugin.json` path
   - May need `include_package_data = true`

3. **uv vs pip**: Should we prefer one over the other?
   - uv is faster but newer
   - pip is more widely available
   - Could support both with abstraction

4. **Cache invalidation**: When does the isolated plugins dir get cleaned?
   - Manual `ohp clean` command?
   - Version-based subdirectories?
   - Never (user manages)?

### Deferred to v2

- Multi-plugin packages
- MCP server dependencies (npm)
- Marketplace integration
- Plugin signing/verification
- Sandboxed plugin execution

---

## References

- **Merged Plugin System**: PRs #1611, #1647, #1651, #1676 in software-agent-sdk
- **Custom Agent Design**: PR #11876 in OpenHands/OpenHands
- **Dynamic Tool Registration**: PR #1383 in software-agent-sdk
- **Python Package-Based Loading POC**: PR #1399 in software-agent-sdk (open)

---

## Next Steps

1. [ ] Review and approve design decisions
2. [ ] Investigate Python-specific technical questions
3. [ ] Create proof-of-concept implementation
4. [ ] Create reference plugin package
5. [ ] Document plugin author guide
6. [ ] PR to software-agent-sdk
