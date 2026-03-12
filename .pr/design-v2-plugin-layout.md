# Proposed Revision to PR #1847 Design Document

This document outlines revisions to the "Python-Packaged Plugins" design based on discussion about maintaining a consistent plugin layout between source-only and Python-packaged plugins.

---

## Key Change: Plugin Layout Stays at Root

### Previous Design (in current PR #1847)

The current design places plugin content **inside** the Python module:

```
openhands-plugin-security/
├── pyproject.toml
├── README.md
└── openhands_plugin_security/       # Plugin content inside module
    ├── __init__.py
    ├── agent.py
    ├── .plugin/plugin.json
    ├── skills/
    └── commands/
```

**Problem**: This makes Python-packaged plugins look structurally different from source-only plugins, which have content at the root.

### Revised Design: Plugin Content at Root

Plugin content stays at the repository root (identical to source-only plugins), with Python code in a `src/` subdirectory:

```
security-scanner/                    # Repository root = Plugin root
├── pyproject.toml                   # Indicates this is also a Python package
├── README.md
│
├── .plugin/                         # Plugin content at root (normal location)
│   └── plugin.json
├── skills/
│   └── security-scan.md
├── commands/
│   └── scan.md
├── hooks/
│   └── hooks.json
├── .mcp.json
│
└── src/                             # Python code in src/ subdirectory
    └── openhands_plugin_security/
        ├── __init__.py              # Exports create_agent (if custom agent)
        ├── agent.py                 # Custom AgentBase subclass
        └── tools.py                 # Custom tool definitions
```

**Benefit**: The plugin layout is **identical** to source-only plugins. The only additions are `pyproject.toml` and `src/`.

---

## Comparison: Source-Only vs Python-Packaged

| Aspect | Source-Only Plugin | Python-Packaged Plugin |
|--------|-------------------|------------------------|
| `.plugin/` | Root | Root |
| `skills/` | Root | Root |
| `commands/` | Root | Root |
| `hooks/` | Root | Root |
| `.mcp.json` | Root | Root |
| Python code | None | `src/<module>/` |
| `pyproject.toml` | None | Root |
| Consumed via | Git clone / local path | Git clone OR `pip install` |

**Source-only plugin:**
```
code-review/
├── .plugin/plugin.json
├── skills/
│   └── review.md
└── commands/
    └── review.md
```

**Python-packaged plugin (same layout + Python code):**
```
security-scanner/
├── .plugin/plugin.json              # Same location
├── skills/                          # Same location
│   └── scan.md
├── commands/                        # Same location
│   └── run-scan.md
├── pyproject.toml                   # Added
└── src/                             # Added
    └── openhands_plugin_security/
        ├── __init__.py
        └── agent.py
```

---

## Build Solution: Hatchling's `force-include`

### The Challenge

Standard Python packaging only includes files **inside** the Python module in the wheel. Files at root (`.plugin/`, `skills/`, etc.) would be excluded.

### The Solution

Use **hatchling** build backend with `force-include` to copy root-level plugin content into the wheel:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "openhands-plugin-security"
version = "1.0.0"
description = "Security scanning plugin with custom agent"
requires-python = ">=3.10"
dependencies = []

[project.entry-points."openhands.plugins"]
security-scanner = "openhands_plugin_security"

[project.entry-points."openhands.agents"]
security-scanner = "openhands_plugin_security:create_agent"

[tool.hatch.build.targets.wheel]
packages = ["src/openhands_plugin_security"]

# Include root-level plugin content in the wheel
[tool.hatch.build.targets.wheel.force-include]
".plugin" = "openhands_plugin_security/.plugin"
"skills" = "openhands_plugin_security/skills"
"commands" = "openhands_plugin_security/commands"
"hooks" = "openhands_plugin_security/hooks"
".mcp.json" = "openhands_plugin_security/.mcp.json"
```

### How It Works

**Repository layout (what developers see and edit):**
```
security-scanner/
├── pyproject.toml
├── .plugin/plugin.json          # Edit here
├── skills/scan.md               # Edit here
├── commands/run.md              # Edit here
└── src/openhands_plugin_security/
    ├── __init__.py
    └── agent.py
```

**Installed wheel (what gets deployed to site-packages):**
```
site-packages/openhands_plugin_security/
├── __init__.py
├── agent.py
├── .plugin/plugin.json          # Copied via force-include
├── skills/scan.md               # Copied via force-include
└── commands/run.md              # Copied via force-include
```

### Why Hatchling?

- **Build-time only**: Listed in `[build-system] requires`, not a runtime dependency
- **Automatic**: pip/uv automatically downloads hatchling when building
- **No developer action needed**: `pip install -e .` just works
- **Modern standard**: Used by major projects (Black, Ruff, etc.)
- **Maintained by PyPA**: Same organization that maintains pip

Consumers installing via `pip install openhands-plugin-security` never see hatchling - they just get a normal wheel.

---

## Marketplace Integration

### Marketplace with Mixed Plugin Types

A marketplace repository can contain both source-only and Python-packaged plugins:

```
openhands-marketplace/
├── marketplace.json                 # Optional: index of all plugins
│
├── plugins/
│   ├── code-review/                 # Source-only plugin
│   │   ├── .plugin/plugin.json
│   │   └── skills/
│   │
│   ├── docs-generator/              # Source-only plugin
│   │   ├── .plugin/plugin.json
│   │   └── commands/
│   │
│   └── security-scanner/            # Python-packaged plugin
│       ├── .plugin/plugin.json      # Same layout!
│       ├── skills/
│       ├── commands/
│       ├── pyproject.toml           # Indicates Python package
│       └── src/
│           └── openhands_plugin_security/
│               └── ...
```

### Detection: How Loader Knows the Difference

**Option A: Presence of `pyproject.toml`**
```python
def detect_plugin_type(plugin_dir: Path) -> str:
    if (plugin_dir / "pyproject.toml").exists():
        return "python-package"
    return "source-only"
```

**Option B: Field in `plugin.json`**
```json
{
  "name": "security-scanner",
  "version": "1.0.0",
  "distribution": {
    "type": "python-package",
    "package": "openhands-plugin-security"
  }
}
```

**Recommendation**: Use **both**. Presence of `pyproject.toml` is the primary indicator; `distribution` field in `plugin.json` provides explicit metadata (package name, PyPI availability).

### Loading Logic

```python
def load_plugin_from_marketplace(plugin_dir: Path) -> Plugin:
    has_pyproject = (plugin_dir / "pyproject.toml").exists()
    manifest = load_plugin_json(plugin_dir / ".plugin" / "plugin.json")
    
    if has_pyproject:
        # Python-packaged plugin
        distribution = manifest.get("distribution", {})
        pkg_name = distribution.get("package")
        
        if pkg_name and is_available_on_pypi(pkg_name):
            # Prefer PyPI if published
            return Plugin.fetch(f"pypi:{pkg_name}")
        else:
            # Install from source
            return Plugin.install_from_source(plugin_dir)
    else:
        # Source-only plugin
        return Plugin.load(plugin_dir)
```

---

## Updated pyproject.toml Templates

### Content-Only Plugin (skills/commands, no custom agent)

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "openhands-plugin-code-review"
version = "1.0.0"
description = "Code review skills for OpenHands"
requires-python = ">=3.10"
license = {text = "MIT"}
keywords = ["openhands", "plugin", "code-review"]
classifiers = [
    "Framework :: OpenHands",
    "Framework :: OpenHands :: Plugin",
]

[project.entry-points."openhands.plugins"]
code-review = "openhands_plugin_code_review"

# No openhands.agents entry point = no custom agent

[tool.hatch.build.targets.wheel]
packages = ["src/openhands_plugin_code_review"]

[tool.hatch.build.targets.wheel.force-include]
".plugin" = "openhands_plugin_code_review/.plugin"
"skills" = "openhands_plugin_code_review/skills"
"commands" = "openhands_plugin_code_review/commands"
```

**Minimal `src/openhands_plugin_code_review/__init__.py`:**
```python
"""OpenHands Code Review Plugin - skills only, no custom agent."""
```

### Full Plugin with Custom Agent

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "openhands-plugin-security"
version = "1.0.0"
description = "Security scanning with custom agent"
requires-python = ">=3.10"
license = {text = "MIT"}
keywords = ["openhands", "plugin", "agent", "security"]
classifiers = [
    "Framework :: OpenHands",
    "Framework :: OpenHands :: Plugin",
    "Framework :: OpenHands :: Agent",
]
dependencies = [
    "semgrep>=1.0.0",  # External dependency for custom tools
]

[project.entry-points."openhands.plugins"]
security-scanner = "openhands_plugin_security"

[project.entry-points."openhands.agents"]
security-scanner = "openhands_plugin_security:create_agent"

[tool.hatch.build.targets.wheel]
packages = ["src/openhands_plugin_security"]

[tool.hatch.build.targets.wheel.force-include]
".plugin" = "openhands_plugin_security/.plugin"
"skills" = "openhands_plugin_security/skills"
"commands" = "openhands_plugin_security/commands"
"hooks" = "openhands_plugin_security/hooks"
".mcp.json" = "openhands_plugin_security/.mcp.json"
```

---

## Entry Points Summary

| Entry Point | Purpose | Required? |
|-------------|---------|-----------|
| `openhands.plugins` | Plugin content (skills, hooks, MCP) | Yes |
| `openhands.agents` | Custom agent factory function | No (only if custom agent) |

**Both point to the same module**, but:
- `openhands.plugins` → module path (loader finds `.plugin/`, `skills/`, etc.)
- `openhands.agents` → factory function (`module:create_agent`)

---

## Open Questions (carried forward)

1. **Isolated installation directory**: Still `~/.openhands/plugins/`? Or use virtual environments?

2. **Version resolution**: When both source and PyPI available, which wins? Latest version? User choice?

3. **Plugin-to-plugin dependencies**: Express in `[project] dependencies`? How does loader handle?

4. **Agent factory interface**: Exact signature for `create_agent()`? What config is passed?

---

## Next Steps

1. [ ] Revise PR #1847 with this layout approach
2. [ ] Create reference plugin using hatchling + force-include
3. [ ] Implement `pypi:` source handling in `Plugin.fetch()`
4. [ ] Implement agent entry point loading
5. [ ] Test with marketplace monorepo structure
