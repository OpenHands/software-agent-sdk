# Using Skill Packages with OpenHands SDK

Skill packages provide a standardized way to distribute and reuse collections of skills across projects. This document explains how to use skill packages with the OpenHands SDK.

## Overview

Skill packages are Python packages that bundle one or more OpenHands skills together with metadata. They can be:

- **Installed** via pip/uv like any Python package
- **Discovered** automatically using Python's entry points mechanism
- **Configured** via `.openhands/packages.yaml`
- **Loaded** programmatically or automatically

## Quick Start

### 1. Install a Skill Package

```bash
pip install openhands-simple-code-review
```

### 2. Configure Packages to Load

Create `.openhands/packages.yaml` in your project root:

```yaml
packages:
  - simple-code-review
```

### 3. Load Skills in Your Code

```python
from openhands.sdk.context.skills import load_configured_package_skills

# Load all skills from configured packages
repo_skills, knowledge_skills = load_configured_package_skills()

# Skills are now available for use with your agent
print(f"Loaded {len(knowledge_skills)} knowledge skills")
```

## For More Information

- **Complete Tutorial**: See [examples/01_standalone_sdk/31_use_skill_packages.py](../examples/01_standalone_sdk/31_use_skill_packages.py)
- **Creating Packages**: [Skill Packages Tutorial](https://github.com/OpenHands/package-poc/blob/main/doc/tutorial-flow.md)
- **Package POC Repository**: [OpenHands/package-poc](https://github.com/OpenHands/package-poc)

## API Functions

- `list_skill_packages()` - List all installed packages
- `get_skill_package(name)` - Get details for a specific package
- `load_skills_from_package(name)` - Load skills from one package
- `load_skills_from_packages(names)` - Load skills from multiple packages
- `load_configured_package_skills()` - Load skills from configured packages

See the example file for detailed usage of all these functions.
