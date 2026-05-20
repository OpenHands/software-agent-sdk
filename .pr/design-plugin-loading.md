# Design: Plugin Loading & Discovery

This document describes how OpenHands loads plugins from different sources, discovers installed packages via entry points, and manages plugin installation and caching.

---

## Overview

The plugin loader supports three source types:

| Source Type | Example | Use Case |
|-------------|---------|----------|
| **GitHub shorthand** | `github:owner/repo` | Quick reference to GitHub repos |
| **Git URL** | `https://github.com/owner/repo.git` | Any git provider |
| **PyPI package** | `pypi:openhands-plugin-security` | Published Python packages |
| **Local path** | `/path/to/plugin` or `./plugin` | Development and testing |

All sources ultimately resolve to a local directory containing the plugin content, which is then loaded using `Plugin.load()`.

---

## Source Parsing

### PluginSource Model

```python
class PluginSource(BaseModel):
    """Specification for a plugin to load."""
    
    source: str
    # "github:owner/repo" - GitHub repository shorthand
    # "pypi:package-name" - PyPI package
    # "https://..." - Full git URL
    # "/local/path" - Local filesystem path
    
    ref: str | None = None
    # Git ref (branch, tag, commit) for git sources
    # Example: "v1.0.0", "main", "abc123"
    
    version: str | None = None
    # Version constraint for PyPI sources
    # Example: "1.0.0", ">=1.0.0", ">=1.0.0,<2.0.0"
    
    repo_path: str | None = None
    # Subdirectory within git repo (monorepo support)
    # Example: "plugins/security-scanner"
```

### Parsing Logic

```python
def parse_plugin_source(source: str) -> tuple[str, str]:
    """Parse plugin source into (source_type, normalized_url).
    
    Returns:
        ("github", "https://github.com/owner/repo.git")
        ("pypi", "package-name")
        ("git", "https://gitlab.com/org/repo.git")
        ("local", "/absolute/path")
    """
    if source.startswith("github:"):
        repo_path = source[7:]  # Remove "github:" prefix
        return ("github", f"https://github.com/{repo_path}.git")
    
    if source.startswith("pypi:"):
        package_name = source[5:]  # Remove "pypi:" prefix
        return ("pypi", package_name)
    
    if is_git_url(source):
        return ("git", normalize_git_url(source))
    
    if source.startswith(("/", "~", ".", "file://")):
        return ("local", source)
    
    raise PluginFetchError(f"Unable to parse plugin source: {source}")
```

---

## Fetching Plugins

### Plugin.fetch() Flow

```
┌─────────────────┐
│  PluginSource   │
│  source="..."   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  parse_source() │
└────────┬────────┘
         │
    ┌────┴────┬─────────┬─────────┐
    ▼         ▼         ▼         ▼
┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐
│github │ │ git   │ │ pypi  │ │ local │
│  or   │ │       │ │       │ │       │
│  git  │ │       │ │       │ │       │
└───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘
    │         │         │         │
    ▼         ▼         ▼         ▼
┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
│git clone│ │git clone│ │pip/uv  │ │resolve  │
│to cache │ │to cache │ │install │ │  path   │
└────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘
     │           │           │           │
     └───────────┴─────┬─────┴───────────┘
                       ▼
              ┌─────────────────┐
              │  Local Path to  │
              │ Plugin Directory│
              └─────────────────┘
```

### Git Sources (github:, git URLs)

```python
def fetch_git_source(
    url: str,
    ref: str | None,
    repo_path: str | None,
    cache_dir: Path,
) -> Path:
    """Fetch plugin from git repository.
    
    1. Clone or update cached repo
    2. Checkout specified ref (or default branch)
    3. Return path to plugin directory (with repo_path if specified)
    """
    # Determine cache location
    cache_path = cache_dir / hash_url(url)
    
    if cache_path.exists():
        # Update existing clone
        git_fetch(cache_path)
    else:
        # Fresh clone
        git_clone(url, cache_path)
    
    # Checkout ref
    if ref:
        git_checkout(cache_path, ref)
    
    # Handle monorepo subdirectory
    plugin_path = cache_path
    if repo_path:
        plugin_path = cache_path / repo_path
        if not plugin_path.exists():
            raise PluginFetchError(f"Path {repo_path} not found in repo")
    
    return plugin_path
```

### PyPI Sources

```python
def fetch_pypi_source(
    package_name: str,
    version: str | None,
    install_dir: Path,
) -> Path:
    """Fetch plugin from PyPI.
    
    1. Install package to isolated directory
    2. Discover module path via entry point
    3. Return path to installed plugin module
    """
    # Build install command
    package_spec = package_name
    if version:
        package_spec = f"{package_name}=={version}" if "," not in version else f"{package_name}{version}"
    
    # Install to isolated directory (not system site-packages)
    install_to_isolated_dir(package_spec, install_dir)
    
    # Find installed module via entry point
    module_path = find_plugin_module(package_name, install_dir)
    
    return module_path
```

### Local Sources

```python
def fetch_local_source(path: str) -> Path:
    """Resolve local plugin path.
    
    Handles:
    - Absolute paths: /path/to/plugin
    - Home-relative: ~/plugins/my-plugin
    - Relative paths: ./my-plugin, ../other-plugin
    - file:// URLs: file:///path/to/plugin
    """
    if path.startswith("file://"):
        path = path[7:]
    
    resolved = Path(path).expanduser().resolve()
    
    if not resolved.exists():
        raise PluginFetchError(f"Local path does not exist: {resolved}")
    
    return resolved
```

---

## Plugin Installation

### Isolated Installation Directory

Plugins are installed to an isolated directory to avoid polluting the agent's working environment:

```
~/.openhands/
└── plugins/
    ├── lib/
    │   └── python3.12/
    │       └── site-packages/
    │           ├── openhands_plugin_security/
    │           ├── openhands_plugin_code_review/
    │           └── semgrep/  # transitive dependency
    └── cache/
        └── git/
            ├── github.com/
            │   └── owner/
            │       └── repo/
            └── gitlab.com/
                └── ...
```

### Installation Methods

**For PyPI packages:**
```bash
# Using uv (preferred - faster)
uv pip install --target ~/.openhands/plugins/lib openhands-plugin-security==1.0.0

# Using pip (fallback)
pip install --target ~/.openhands/plugins/lib openhands-plugin-security==1.0.0
```

**For local Python packages (from source):**
```bash
# Install from local directory
uv pip install --target ~/.openhands/plugins/lib /path/to/plugin

# Or in editable mode for development
uv pip install --target ~/.openhands/plugins/lib -e /path/to/plugin
```

### Runtime Path Configuration

When loading plugins, add the isolated directory to Python's path:

```python
import sys

PLUGINS_LIB = Path.home() / ".openhands" / "plugins" / "lib"

def ensure_plugins_path():
    """Add plugins lib to sys.path if not already present."""
    lib_path = str(PLUGINS_LIB)
    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)
```

---

## Entry Point Discovery

### Entry Point Groups

| Entry Point Group | Purpose |
|-------------------|---------|
| `openhands.plugins` | Plugin content (module containing `.plugin/`, `skills/`, etc.) |
| `openhands.agents` | Custom agent factory functions |

### Discovering Installed Plugins

```python
from importlib.metadata import entry_points

def list_installed_plugins() -> list[dict]:
    """List all installed plugin packages."""
    eps = entry_points(group="openhands.plugins")
    
    plugins = []
    for ep in eps:
        try:
            module = ep.load()
            module_path = get_module_path(module)
            manifest = load_plugin_json(module_path / ".plugin" / "plugin.json")
            
            plugins.append({
                "name": ep.name,
                "module": module,
                "path": module_path,
                "manifest": manifest,
            })
        except Exception as e:
            logger.warning(f"Failed to load plugin {ep.name}: {e}")
    
    return plugins

def get_installed_plugin(name: str) -> dict | None:
    """Get a specific installed plugin by name."""
    eps = entry_points(group="openhands.plugins")
    
    for ep in eps:
        if ep.name == name:
            module = ep.load()
            module_path = get_module_path(module)
            return {
                "name": ep.name,
                "module": module,
                "path": module_path,
            }
    
    return None
```

### Finding Module Path

```python
from importlib.resources import files

def get_module_path(module) -> Path:
    """Get filesystem path to a module's directory."""
    return Path(files(module)._path)
```

---

## Detection: Source-Only vs Python-Packaged

When loading from a marketplace or local directory, the loader must determine whether to treat the plugin as source-only or as a Python package.

### Detection Logic

```python
def detect_plugin_type(plugin_dir: Path) -> str:
    """Detect whether plugin is source-only or Python-packaged.
    
    Returns: "source" or "python-package"
    """
    # Primary indicator: presence of pyproject.toml
    if (plugin_dir / "pyproject.toml").exists():
        return "python-package"
    
    return "source"

def should_install_as_package(plugin_dir: Path) -> bool:
    """Determine if plugin should be installed as Python package.
    
    Checks for pyproject.toml AND valid build configuration.
    """
    pyproject = plugin_dir / "pyproject.toml"
    if not pyproject.exists():
        return False
    
    # Verify it has build-system configuration
    try:
        config = load_toml(pyproject)
        return "build-system" in config
    except Exception:
        return False
```

### Loading Based on Type

```python
def load_plugin_smart(plugin_dir: Path) -> Plugin:
    """Load plugin, choosing appropriate method based on type."""
    
    if should_install_as_package(plugin_dir):
        # Install as Python package, then load via entry point
        install_result = install_package_from_source(plugin_dir)
        return Plugin.load(install_result.module_path)
    else:
        # Load directly from directory (source-only)
        return Plugin.load(plugin_dir)
```

---

## Caching Strategy

### Git Cache

- **Location**: `~/.openhands/plugins/cache/git/`
- **Structure**: `{provider}/{owner}/{repo}/`
- **Update policy**: Fetch on each load (unless offline mode)
- **Cleanup**: Manual or via `openhands plugins clean`

### PyPI Cache

- **Location**: `~/.openhands/plugins/lib/`
- **Structure**: Standard site-packages layout
- **Update policy**: Install if not present or version mismatch
- **Cleanup**: Reinstall to upgrade

### Cache Invalidation

```python
def should_update_cache(source: PluginSource, cached_path: Path) -> bool:
    """Determine if cached plugin should be updated."""
    
    if source.source.startswith("pypi:"):
        # Check if installed version matches requested
        installed_version = get_installed_version(source.package_name)
        if source.version and not version_matches(installed_version, source.version):
            return True
        return False
    
    if is_git_source(source.source):
        # Always fetch for git sources (cheap operation)
        return True
    
    # Local sources: never cache
    return False
```

---

## Loading Multiple Plugins

### Load Order and Merging

When loading multiple plugins, they are processed in order with these merge semantics:

```python
def load_plugins(
    plugin_specs: list[PluginSource],
    agent: AgentBase,
) -> tuple[AgentBase, HookConfig | None]:
    """Load multiple plugins and merge into agent.
    
    Merge semantics:
    - Skills: Override by name (last plugin wins)
    - MCP config: Override by server name (last plugin wins)
    - Hooks: Concatenate (all hooks run)
    """
    merged_context = agent.agent_context
    merged_mcp = dict(agent.mcp_config or {})
    all_hooks = []
    
    for spec in plugin_specs:
        path = Plugin.fetch(spec.source, ref=spec.ref, version=spec.version)
        plugin = Plugin.load(path)
        
        merged_context = plugin.add_skills_to(merged_context)
        merged_mcp = plugin.add_mcp_config_to(merged_mcp)
        
        if plugin.hooks:
            all_hooks.append(plugin.hooks)
    
    updated_agent = agent.model_copy(update={
        "agent_context": merged_context,
        "mcp_config": merged_mcp,
    })
    
    return updated_agent, HookConfig.merge(all_hooks)
```

---

## Error Handling

### Error Types

```python
class PluginError(Exception):
    """Base class for plugin errors."""
    pass

class PluginFetchError(PluginError):
    """Failed to fetch plugin from source."""
    pass

class PluginInstallError(PluginError):
    """Failed to install Python package."""
    pass

class PluginLoadError(PluginError):
    """Failed to load plugin content."""
    pass

class PluginNotFoundError(PluginError):
    """Plugin not found at specified source."""
    pass
```

### Graceful Degradation

```python
def load_plugins_graceful(
    plugin_specs: list[PluginSource],
    agent: AgentBase,
    fail_fast: bool = False,
) -> tuple[AgentBase, list[PluginError]]:
    """Load plugins with error collection.
    
    Args:
        fail_fast: If True, raise on first error. If False, collect errors.
    
    Returns:
        (updated_agent, list_of_errors)
    """
    errors = []
    
    for spec in plugin_specs:
        try:
            # ... load plugin ...
        except PluginError as e:
            if fail_fast:
                raise
            errors.append(e)
            logger.warning(f"Failed to load plugin {spec.source}: {e}")
    
    return updated_agent, errors
```

---

## Open Questions

1. **Offline mode**: How to handle loading when network is unavailable?

2. **Concurrent installation**: How to handle multiple conversations installing the same plugin simultaneously?

3. **Version conflicts**: What happens when two plugins depend on different versions of the same package?

4. **Cleanup policy**: When should old cached plugins be removed?
