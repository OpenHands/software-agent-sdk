"""Convert legacy OpenHands skills to AgentSkills standard format.

This module provides utilities to convert single .md skill files to the
AgentSkills directory format:
- Creates skill-name/ directory with SKILL.md
- Converts mcp_tools frontmatter to .mcp.json files
- Preserves OpenHands-specific fields (triggers, inputs) for compatibility
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import frontmatter

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# AgentSkills name validation pattern
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def normalize_skill_name(name: str) -> str:
    """Normalize a skill name to conform to AgentSkills spec.

    Converts to lowercase, replaces underscores with hyphens,
    and removes invalid characters.

    Args:
        name: Original skill name

    Returns:
        Normalized skill name
    """
    # Convert to lowercase
    normalized = name.lower()
    # Replace underscores with hyphens
    normalized = normalized.replace("_", "-")
    # Remove any characters that aren't alphanumeric or hyphens
    normalized = re.sub(r"[^a-z0-9-]", "", normalized)
    # Remove consecutive hyphens
    normalized = re.sub(r"-+", "-", normalized)
    # Remove leading/trailing hyphens
    normalized = normalized.strip("-")
    return normalized


def validate_skill_name(name: str) -> list[str]:
    """Validate skill name according to AgentSkills spec.

    Args:
        name: The skill name to validate

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    if not name:
        errors.append("Name cannot be empty")
        return errors
    if len(name) > 64:
        errors.append(f"Name exceeds 64 characters: {len(name)}")
    if not SKILL_NAME_PATTERN.match(name):
        errors.append(
            "Name must be lowercase alphanumeric with single hyphens "
            "(e.g., 'my-skill', 'pdf-tools')"
        )
    return errors


def generate_description(
    content: str,
    triggers: list[str] | None = None,
    name: str | None = None,
) -> str:
    """Generate a description for the skill.

    Tries to extract a meaningful description from:
    1. First non-header, non-empty line of content
    2. Triggers list
    3. Skill name

    Args:
        content: Markdown content of the skill
        triggers: Optional list of trigger keywords
        name: Optional skill name

    Returns:
        Generated description (max 1024 chars)
    """
    # Try to extract first meaningful line from content
    for line in content.split("\n"):
        stripped = line.strip()
        # Skip empty lines, headers, and XML-like tags
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("<") and stripped.endswith(">"):
            continue
        # Found a meaningful line
        description = stripped[:1024]
        return description

    # Fall back to triggers
    if triggers:
        trigger_str = ", ".join(triggers[:5])  # Limit to first 5
        if len(triggers) > 5:
            trigger_str += f" (+{len(triggers) - 5} more)"
        return f"Activated by: {trigger_str}"[:1024]

    # Fall back to name
    if name:
        return f"Skill: {name}"[:1024]

    return "A skill for OpenHands agent."


def convert_legacy_skill(
    source_path: Path,
    output_dir: Path,
    dry_run: bool = False,
) -> Path | None:
    """Convert a legacy OpenHands skill to AgentSkills format.

    Args:
        source_path: Path to the legacy .md skill file
        output_dir: Directory where the converted skill directory will be created
        dry_run: If True, don't write files, just return what would be created

    Returns:
        Path to the created skill directory, or None if conversion failed
    """
    if not source_path.exists():
        logger.error(f"Source file not found: {source_path}")
        return None

    if source_path.name == "README.md":
        logger.debug(f"Skipping README.md: {source_path}")
        return None

    # Read and parse the source file
    with open(source_path) as f:
        file_content = f.read()

    file_io = io.StringIO(file_content)
    loaded = frontmatter.load(file_io)
    content = loaded.content
    metadata = dict(loaded.metadata) if loaded.metadata else {}

    # Get or derive skill name
    original_name = metadata.get("name", source_path.stem)
    skill_name = normalize_skill_name(str(original_name))

    # Validate the normalized name
    name_errors = validate_skill_name(skill_name)
    if name_errors:
        logger.warning(
            f"Skill name '{original_name}' -> '{skill_name}' "
            f"has issues: {'; '.join(name_errors)}"
        )
        # Try to fix by using the stem
        skill_name = normalize_skill_name(source_path.stem)
        if validate_skill_name(skill_name):
            logger.error(f"Cannot normalize skill name for {source_path}")
            return None

    # Create output directory
    skill_dir = output_dir / skill_name
    skill_md_path = skill_dir / "SKILL.md"
    mcp_json_path = skill_dir / ".mcp.json"

    logger.info(f"Converting: {source_path} -> {skill_dir}/")

    if dry_run:
        logger.debug(f"Would create: {skill_md_path}")
        if "mcp_tools" in metadata:
            logger.debug(f"Would create: {mcp_json_path}")
        return skill_dir

    # Create the skill directory
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Build new frontmatter
    new_metadata: dict[str, Any] = {}

    # Required AgentSkills fields
    new_metadata["name"] = skill_name

    # Generate description if not present
    triggers_raw = metadata.get("triggers", [])
    triggers: list[str] = triggers_raw if isinstance(triggers_raw, list) else []
    description = metadata.get("description") or generate_description(
        content, triggers, skill_name
    )
    new_metadata["description"] = description

    # Optional AgentSkills fields
    if "license" in metadata:
        new_metadata["license"] = metadata["license"]
    if "compatibility" in metadata:
        new_metadata["compatibility"] = metadata["compatibility"]

    # Build metadata dict for non-standard fields
    extra_metadata: dict[str, str] = {}
    if "version" in metadata:
        extra_metadata["version"] = str(metadata["version"])
    if "author" in metadata:
        extra_metadata["author"] = str(metadata["author"])
    if "agent" in metadata:
        extra_metadata["agent"] = str(metadata["agent"])
    if "type" in metadata:
        extra_metadata["type"] = str(metadata["type"])

    # Include any existing metadata
    if "metadata" in metadata and isinstance(metadata["metadata"], dict):
        for k, v in metadata["metadata"].items():
            extra_metadata[str(k)] = str(v)

    if extra_metadata:
        new_metadata["metadata"] = extra_metadata

    # Preserve OpenHands-specific fields for compatibility
    if triggers:
        new_metadata["triggers"] = triggers
    if "inputs" in metadata:
        new_metadata["inputs"] = metadata["inputs"]
    if "allowed-tools" in metadata:
        new_metadata["allowed-tools"] = metadata["allowed-tools"]
    if "allowed_tools" in metadata:
        new_metadata["allowed-tools"] = metadata["allowed_tools"]

    # Extract mcp_tools for .mcp.json
    mcp_tools = metadata.get("mcp_tools")

    # Write SKILL.md
    new_post = frontmatter.Post(content, **new_metadata)
    with open(skill_md_path, "w") as f:
        f.write(frontmatter.dumps(new_post))
    logger.debug(f"Created: {skill_md_path}")

    # Write .mcp.json if mcp_tools was present
    if mcp_tools and isinstance(mcp_tools, dict):
        with open(mcp_json_path, "w") as f:
            json.dump(mcp_tools, f, indent=2)
            f.write("\n")
        logger.debug(f"Created: {mcp_json_path}")

    return skill_dir


def convert_skills_directory(
    source_dir: Path,
    output_dir: Path,
    dry_run: bool = False,
) -> list[Path]:
    """Convert all legacy skills in a directory to AgentSkills format.

    Args:
        source_dir: Directory containing legacy .md skill files
        output_dir: Directory where converted skill directories will be created
        dry_run: If True, don't write files, just show what would be converted

    Returns:
        List of paths to created skill directories
    """
    if not source_dir.exists():
        logger.error(f"Source directory not found: {source_dir}")
        return []

    converted: list[Path] = []

    # Find all .md files (excluding README.md)
    md_files = [
        f
        for f in source_dir.glob("*.md")
        if f.name != "README.md" and f.name.lower() != "skill.md"
    ]

    logger.info(f"Found {len(md_files)} skill files to convert")

    for md_file in sorted(md_files):
        result = convert_legacy_skill(md_file, output_dir, dry_run=dry_run)
        if result:
            converted.append(result)

    logger.info(f"Converted {len(converted)} skills")
    return converted
