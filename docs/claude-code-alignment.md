# Claude Code Alignment for Skill Package Loading

## Overview

The OpenHands SDK now requires skill packages to use the **manifest.json** (Claude Desktop Extensions-aligned format) for Scenario 1.

This enables cross-platform package distribution and better integration with the Model Context Protocol (MCP) ecosystem.

## Changes to package_loader.py

### New Helper Function

```python
def _load_descriptor(package_module) -> dict[str, Any]:
    """Load package descriptor from manifest.json."""
```

This function loads `manifest.json` from the package and parses it as JSON.

### Updated Functions

All package loading functions now use `_load_descriptor()`:
- `list_skill_packages()` - Lists all installed skill packages
- `get_skill_package()` - Gets a specific package by name
- `load_skills_from_package()` - Loads skills from flat JSON structure

## Descriptor Format Comparison

### manifest.json (New Format)

```json
{
  "name": "simple-code-review",
  "version": "1.0.0",
  "displayName": "Simple Code Review",
  "description": "Basic code review skills",
  "skills": [
    {
      "name": "code-review",
      "path": "skills/code_review.md",
      "type": "keyword-triggered",
      "triggers": ["codereview", "review-code"]
    }
  ]
}
```

**Structure**: Flat, with `skills` at the top level

### skill-package.yaml (Previous Format - Reference)

For reference, the previous YAML format looked like this:

```yaml
apiVersion: openhands.ai/v1
kind: SkillPackage

metadata:
  name: simple-code-review
  version: "1.0.0"
  displayName: "Simple Code Review"
  description: "Basic code review skills"

spec:
  skills:
    - name: code-review
      path: skills/code_review.md
      type: keyword-triggered
      triggers:
        - codereview
        - review-code
```

**Note**: This format is no longer supported in Scenario 1. Packages must use `manifest.json`.

## Migration from YAML

To migrate from `skill-package.yaml` to `manifest.json`:

1. Flatten the nested YAML structure
2. Move metadata fields to the top level
3. Move skills from `spec.skills` to top-level `skills` array
4. Remove `apiVersion` and `kind` (now in `[tool.openhands]` in `pyproject.toml` if needed)

## Usage Examples

### Loading Packages

```python
from openhands.sdk.context.skills.package_loader import (
    list_skill_packages,
    get_skill_package,
    load_skills_from_package
)

# List all packages
packages = list_skill_packages()
for pkg in packages:
    print(f"Package: {pkg['name']}")

# Get specific package
pkg = get_skill_package('simple-code-review')

# Load skills
repo_skills, knowledge_skills = load_skills_from_package('simple-code-review')
```

### Accessing Descriptor Data

Descriptors now use flat JSON structure:

```python
pkg = get_skill_package('my-package')
descriptor = pkg['descriptor']

# Access flat JSON structure
display_name = descriptor.get('displayName', 'Unknown')
skills = descriptor.get('skills', [])

print(f"Display Name: {display_name}")
print(f"Skills: {len(skills)}")
```

## Creating New Packages

### Required Format: manifest.json

1. Create `manifest.json` in your package root:

```json
{
  "name": "my-awesome-skills",
  "version": "1.0.0",
  "displayName": "My Awesome Skills",
  "description": "A collection of useful skills",
  "author": {
    "name": "Your Name",
    "email": "you@example.com"
  },
  "keywords": ["skills", "awesome"],
  "license": "MIT",
  "skills": [
    {
      "name": "my-skill",
      "description": "Description of my skill",
      "path": "skills/my_skill.md",
      "type": "keyword-triggered",
      "triggers": ["myskill", "awesome"]
    }
  ],
  "package": {
    "type": "python",
    "name": "my-awesome-skills",
    "entry_point": "my_awesome_skills"
  }
}
```

2. Include manifest.json in your package data (pyproject.toml):

```toml
[tool.setuptools.package-data]
my_awesome_skills = ["manifest.json", "skills/*.md"]
```

3. Register the entry point:

```toml
[project.entry-points."openhands.skill_packages"]
my-awesome-skills = "my_awesome_skills"
```

## Package Structure

Packages must include `manifest.json`:

```
my-package/
├── manifest.json          # Required
├── pyproject.toml
└── my_package/
    ├── __init__.py
    ├── manifest.json      # Include in package
    └── skills/
        └── skill.md
```

Update pyproject.toml to include manifest.json:
```toml
[tool.setuptools.package-data]
my_package = ["manifest.json", "skills/*.md"]
```

## Integration with OpenHands

The OpenHands SDK will automatically:
1. Discover packages via entry points
2. Load the appropriate descriptor format
3. Parse skills from the descriptor
4. Load skill content from markdown files
5. Create Skill objects with triggers

No changes needed to your OpenHands agent code!

## Testing

Test your package works:

```bash
# Install your package
pip install -e .

# Test discovery
python -c "from openhands.sdk.context.skills.package_loader import list_skill_packages; print(list_skill_packages())"

# Test loading
python -c "from openhands.sdk.context.skills.package_loader import load_skills_from_package; print(load_skills_from_package('your-package-name'))"
```

## Benefits

1. **Cross-Platform**: Packages work with OpenHands SDK and Claude Desktop
2. **Standard Format**: JSON is more widely adopted than custom YAML
3. **MCP Compatible**: Better integration with Model Context Protocol ecosystem
4. **Developer Friendly**: Familiar format similar to npm package.json
5. **Clean Design**: Single, well-defined format for Scenario 1

## Future Enhancements

Potential future improvements:
1. JSON Schema for manifest.json validation
2. Enhanced MCP tool integration
3. Support for Claude Desktop-specific features
4. Package validation utilities

## References

- [OpenHands Package POC](https://github.com/OpenHands/package-poc)
- [Claude Desktop Extensions](https://www.anthropic.com/engineering/desktop-extensions)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Skill Packages Documentation](./skill-packages.md)
