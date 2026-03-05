# Mixed Marketplace Skills Example

This example demonstrates how to create a marketplace that includes both local and remote skills.

## Overview

A marketplace can reference skills from multiple sources:
- **Local skills**: Hosted in your project directory
- **Remote skills**: Hosted on GitHub (or other Git repositories)

This pattern is useful when you want to:
- Maintain custom skills locally in your project
- Reference community skills from GitHub repositories
- Create a curated skill set for your team

## Directory Structure

```
43_mixed_marketplace_skills/
├── .plugin/
│   └── marketplace.json     # Marketplace configuration
├── skills/
│   └── greeting-helper/
│       └── SKILL.md         # Local skill
├── main.py                  # Example script
└── README.md                # This file
```

## Marketplace Schema

The `marketplace.json` file supports both plugins and skills:

```json
{
    "name": "my-marketplace",
    "owner": {
        "name": "Team Name",
        "email": "team@example.com"
    },
    "metadata": {
        "skillRoot": "./skills"
    },
    "skills": [
        {
            "name": "local-skill",
            "source": "./my-skill",
            "description": "A local skill"
        },
        {
            "name": "remote-skill",
            "source": "https://github.com/owner/repo/blob/main/skills/skill-name",
            "description": "A remote skill from GitHub"
        }
    ]
}
```

### Source Path Formats

Skills can be sourced from:

1. **Relative local paths**: `./path` or `../path`
2. **Absolute paths**: `/absolute/path`
3. **Home directory**: `~/path`
4. **File URLs**: `file:///path`
5. **GitHub URLs**: `https://github.com/{owner}/{repo}/blob/{branch}/{path}`

### Metadata Options

- `skillRoot`: Base directory prepended to relative skill paths

## Usage

```bash
# View marketplace information
python main.py

# Install all skills from marketplace
python main.py --install

# Force reinstall existing skills
python main.py --install --force

# List installed skills
python main.py --list
```

## How It Works

1. **Marketplace Loading**: The `Marketplace.load()` function reads the `.plugin/marketplace.json` file

2. **Source Resolution**: Each skill's source is resolved:
   - Local paths are resolved relative to the marketplace directory
   - GitHub URLs trigger a cached clone of the repository

3. **Skill Installation**: The `install_skills_from_marketplace()` function:
   - Resolves each skill source
   - Copies the skill to `~/.openhands/skills/installed/`
   - Tracks installation metadata

4. **Skill Loading**: Installed skills can be loaded with `load_installed_skills()`

## API Reference

### Install Skills from Marketplace

```python
from openhands.sdk.skills import install_skills_from_marketplace

# Install all skills from a marketplace
installed = install_skills_from_marketplace("./my-marketplace", force=False)

for info in installed:
    print(f"Installed: {info.name}")
```

### Load Installed Skills

```python
from openhands.sdk.skills import load_installed_skills

# Load all installed skills
skills = load_installed_skills()

for skill in skills:
    print(f"Skill: {skill.name}")
    print(f"Description: {skill.description}")
```

### List Installed Skills

```python
from openhands.sdk.skills import list_installed_skills

# Get metadata for installed skills
installed = list_installed_skills()

for info in installed:
    print(f"{info.name}: {info.source}")
```
