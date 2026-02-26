"""
Proof of Concept: Unified Settings Schema

This module demonstrates how SDK Pydantic models can be the single source of truth
for settings, with automatic schema generation for CLI and GUI.

Run with: python settings_schema.py
"""

from __future__ import annotations

import argparse
import json
from typing import Any, TypedDict, get_type_hints, get_origin, get_args, Literal

from pydantic import BaseModel, Field, SecretStr


# =============================================================================
# PART 1: SDK Model Definitions (Enhanced with UI Metadata)
# =============================================================================

class LLMConfig(BaseModel):
    """LLM configuration - single source of truth for LLM settings."""
    
    model: str = Field(
        default="claude-sonnet-4-20250514",
        description="The language model to use for completions.",
        json_schema_extra={
            "ui_type": "select",
            "ui_group": "Model",
            "ui_order": 1,
            "cli_flags": ["--model", "-m"],
            "env_var": "LLM_MODEL",
            "advanced": False,
            # Dynamic choices could be loaded from a provider
            "choices": [
                "claude-sonnet-4-20250514",
                "claude-opus-4-20250514", 
                "gpt-4o",
                "gpt-4o-mini",
            ],
        }
    )
    
    api_key: str | SecretStr | None = Field(
        default=None,
        description="API key for authentication with the LLM provider.",
        json_schema_extra={
            "ui_type": "password",
            "ui_group": "Authentication",
            "ui_order": 1,
            "cli_flags": ["--api-key"],
            "env_var": "LLM_API_KEY",
            "advanced": False,
        }
    )
    
    base_url: str | None = Field(
        default=None,
        description="Custom base URL for the API (for proxies or self-hosted models).",
        json_schema_extra={
            "ui_type": "text",
            "ui_group": "Authentication",
            "ui_order": 2,
            "cli_flags": ["--base-url"],
            "env_var": "LLM_BASE_URL",
            "advanced": True,
        }
    )
    
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Sampling temperature (0-2). Higher values make output more random.",
        json_schema_extra={
            "ui_type": "slider",
            "ui_group": "Generation",
            "ui_order": 1,
            "cli_flags": ["--temperature"],
            "advanced": True,
        }
    )
    
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of tokens in the response.",
        json_schema_extra={
            "ui_type": "number",
            "ui_group": "Generation",
            "ui_order": 2,
            "cli_flags": ["--max-tokens"],
            "advanced": True,
        }
    )
    
    caching_prompt: bool = Field(
        default=True,
        description="Enable prompt caching for faster responses.",
        json_schema_extra={
            "ui_type": "toggle",
            "ui_group": "Performance",
            "ui_order": 1,
            "cli_flags": ["--cache-prompts/--no-cache-prompts"],
            "advanced": True,
        }
    )
    
    reasoning_effort: Literal["low", "medium", "high", "none"] | None = Field(
        default=None,
        description="Effort level for reasoning models (low/medium/high/none).",
        json_schema_extra={
            "ui_type": "select",
            "ui_group": "Generation",
            "ui_order": 3,
            "cli_flags": ["--reasoning-effort"],
            "advanced": True,
            "choices": ["low", "medium", "high", "none"],
        }
    )


class AgentConfig(BaseModel):
    """Agent configuration - single source of truth for agent settings."""
    
    enable_browsing: bool = Field(
        default=True,
        description="Enable web browsing capabilities.",
        json_schema_extra={
            "ui_type": "toggle",
            "ui_group": "Tools",
            "ui_order": 1,
            "cli_flags": ["--browsing/--no-browsing"],
            "advanced": False,
        }
    )
    
    enable_jupyter: bool = Field(
        default=True,
        description="Enable Jupyter notebook tool.",
        json_schema_extra={
            "ui_type": "toggle",
            "ui_group": "Tools",
            "ui_order": 2,
            "cli_flags": ["--jupyter/--no-jupyter"],
            "advanced": False,
        }
    )
    
    enable_stuck_detection: bool = Field(
        default=True,
        description="Automatically detect when agent is stuck in a loop.",
        json_schema_extra={
            "ui_type": "toggle",
            "ui_group": "Behavior",
            "ui_order": 1,
            "cli_flags": ["--stuck-detection/--no-stuck-detection"],
            "advanced": True,
        }
    )


class ConversationConfig(BaseModel):
    """Conversation configuration."""
    
    max_iteration_per_run: int = Field(
        default=500,
        ge=1,
        description="Maximum iterations per agent run.",
        json_schema_extra={
            "ui_type": "number",
            "ui_group": "Limits",
            "ui_order": 1,
            "cli_flags": ["--max-iterations"],
            "advanced": True,
        }
    )
    
    enable_condenser: bool = Field(
        default=True,
        description="Enable conversation condensation to manage context window.",
        json_schema_extra={
            "ui_type": "toggle",
            "ui_group": "Memory",
            "ui_order": 1,
            "cli_flags": ["--condenser/--no-condenser"],
            "advanced": False,
        }
    )


# =============================================================================
# PART 2: Schema Extraction
# =============================================================================

class FieldMetadata(TypedDict, total=False):
    """Metadata for a single settings field."""
    name: str
    type: str
    description: str
    default: Any
    required: bool
    # UI metadata
    ui_type: str
    ui_group: str
    ui_order: int
    advanced: bool
    # CLI metadata
    cli_flags: list[str]
    # Environment variable
    env_var: str
    # Validation
    minimum: float | None
    maximum: float | None
    choices: list[str] | None


class SettingsCategory(TypedDict):
    """A category of settings."""
    name: str
    description: str
    fields: list[FieldMetadata]


def extract_field_metadata(model_class: type[BaseModel]) -> list[FieldMetadata]:
    """Extract field metadata from a Pydantic model."""
    fields = []
    
    for field_name, field_info in model_class.model_fields.items():
        metadata: FieldMetadata = {
            "name": field_name,
            "description": field_info.description or "",
            "default": field_info.default if field_info.default is not None else None,
            "required": field_info.is_required(),
        }
        
        # Extract type information
        annotation = field_info.annotation
        origin = get_origin(annotation)
        
        if annotation == bool:
            metadata["type"] = "boolean"
        elif annotation == int:
            metadata["type"] = "integer"
        elif annotation in (float, int | None):
            metadata["type"] = "number"
        elif origin is Literal:
            metadata["type"] = "string"
            metadata["choices"] = list(get_args(annotation))
        else:
            metadata["type"] = "string"
        
        # Extract validation constraints
        for constraint in field_info.metadata:
            if hasattr(constraint, 'ge'):
                metadata["minimum"] = constraint.ge
            if hasattr(constraint, 'le'):
                metadata["maximum"] = constraint.le
        
        # Extract UI metadata from json_schema_extra
        extra = field_info.json_schema_extra or {}
        if isinstance(extra, dict):
            for key in ["ui_type", "ui_group", "ui_order", "advanced", 
                       "cli_flags", "env_var", "choices"]:
                if key in extra:
                    metadata[key] = extra[key]
        
        fields.append(metadata)
    
    return fields


def get_settings_schema(model_class: type[BaseModel], name: str) -> SettingsCategory:
    """Get settings schema for a model."""
    return {
        "name": name,
        "description": model_class.__doc__ or "",
        "fields": extract_field_metadata(model_class),
    }


def get_all_settings_schemas() -> dict[str, SettingsCategory]:
    """Get all settings schemas from SDK models."""
    return {
        "llm": get_settings_schema(LLMConfig, "LLM Settings"),
        "agent": get_settings_schema(AgentConfig, "Agent Settings"),
        "conversation": get_settings_schema(ConversationConfig, "Conversation Settings"),
    }


# =============================================================================
# PART 3: Form Generators
# =============================================================================

class ArgparseGenerator:
    """Generate argparse arguments from settings schema."""
    
    def generate(self, schemas: dict[str, SettingsCategory]) -> argparse.ArgumentParser:
        """Generate a complete argument parser from schemas."""
        parser = argparse.ArgumentParser(
            description="OpenHands CLI (auto-generated from SDK schema)"
        )
        
        for category_name, category in schemas.items():
            group = parser.add_argument_group(category["name"])
            
            for field in category["fields"]:
                flags = field.get("cli_flags", [f"--{field['name'].replace('_', '-')}"])
                
                # Skip if no CLI flags defined
                if not flags:
                    continue
                
                kwargs: dict[str, Any] = {
                    "help": field.get("description"),
                    "default": field.get("default"),
                    "dest": field["name"],
                }
                
                field_type = field.get("type")
                
                # Handle boolean flags
                if field_type == "boolean":
                    # Check if flag uses the /--no- pattern
                    if any("/" in f for f in flags):
                        flag = flags[0].split("/")[0]
                        kwargs["action"] = "store_true"
                        kwargs["dest"] = field["name"]
                        group.add_argument(flag, **kwargs)
                        continue
                    else:
                        kwargs["action"] = "store_true"
                elif field_type == "integer":
                    kwargs["type"] = int
                elif field_type == "number":
                    kwargs["type"] = float
                
                # Handle choices
                if field.get("choices"):
                    kwargs["choices"] = field["choices"]
                
                group.add_argument(*flags, **kwargs)
        
        return parser


class TextualFormGenerator:
    """Generate Textual TUI form descriptions from settings schema."""
    
    def generate(self, schemas: dict[str, SettingsCategory]) -> str:
        """Generate a description of what the Textual form would look like."""
        output = []
        
        for category_name, category in schemas.items():
            output.append(f"\n=== {category['name']} ===")
            output.append(f"Description: {category['description']}")
            
            # Group fields by ui_group
            groups: dict[str, list[FieldMetadata]] = {}
            for field in category["fields"]:
                group = field.get("ui_group", "Other")
                if group not in groups:
                    groups[group] = []
                groups[group].append(field)
            
            for group_name, fields in groups.items():
                output.append(f"\n  [{group_name}]")
                
                for field in sorted(fields, key=lambda f: f.get("ui_order", 999)):
                    ui_type = field.get("ui_type", "text")
                    advanced = "🔧" if field.get("advanced") else ""
                    
                    output.append(f"    {advanced}{field['name']}: {ui_type}")
                    output.append(f"      → {field.get('description', 'No description')}")
                    if field.get("default") is not None:
                        output.append(f"      Default: {field['default']}")
                    if field.get("choices"):
                        output.append(f"      Choices: {field['choices']}")
        
        return "\n".join(output)


class ReactFormGenerator:
    """Generate React-compatible form schema from settings schema."""
    
    def generate(self, schemas: dict[str, SettingsCategory]) -> dict:
        """Generate JSON schema for react-jsonschema-form."""
        form_schemas = {}
        
        for category_name, category in schemas.items():
            schema = {
                "type": "object",
                "title": category["name"],
                "description": category["description"],
                "properties": {},
                "required": [],
            }
            
            ui_schema = {}
            
            for field in category["fields"]:
                name = field["name"]
                
                prop: dict[str, Any] = {
                    "title": name.replace("_", " ").title(),
                    "description": field.get("description"),
                }
                
                field_type = field.get("type")
                if field_type == "boolean":
                    prop["type"] = "boolean"
                elif field_type == "integer":
                    prop["type"] = "integer"
                elif field_type == "number":
                    prop["type"] = "number"
                else:
                    prop["type"] = "string"
                
                if field.get("choices"):
                    prop["enum"] = field["choices"]
                
                if field.get("minimum") is not None:
                    prop["minimum"] = field["minimum"]
                if field.get("maximum") is not None:
                    prop["maximum"] = field["maximum"]
                
                if field.get("default") is not None:
                    prop["default"] = field["default"]
                
                schema["properties"][name] = prop
                
                if field.get("required"):
                    schema["required"].append(name)
                
                # UI Schema
                ui_type = field.get("ui_type")
                if ui_type == "password":
                    ui_schema[name] = {"ui:widget": "password"}
                elif ui_type == "slider":
                    ui_schema[name] = {"ui:widget": "range"}
                elif ui_type == "toggle":
                    ui_schema[name] = {"ui:widget": "checkbox"}
            
            form_schemas[category_name] = {
                "schema": schema,
                "uiSchema": ui_schema,
            }
        
        return form_schemas


# =============================================================================
# PART 4: Demo
# =============================================================================

def main():
    print("=" * 70)
    print("PROOF OF CONCEPT: Unified Settings Schema")
    print("=" * 70)
    
    # Get all schemas
    schemas = get_all_settings_schemas()
    
    # 1. Show raw schema
    print("\n1. RAW SCHEMA (JSON)")
    print("-" * 40)
    print(json.dumps(schemas, indent=2, default=str)[:2000] + "...\n")
    
    # 2. Generate argparse
    print("\n2. AUTO-GENERATED ARGPARSE")
    print("-" * 40)
    argparse_gen = ArgparseGenerator()
    parser = argparse_gen.generate(schemas)
    parser.print_help()
    
    # 3. Show Textual form structure
    print("\n3. TEXTUAL TUI FORM STRUCTURE")
    print("-" * 40)
    textual_gen = TextualFormGenerator()
    print(textual_gen.generate(schemas))
    
    # 4. Show React form schema
    print("\n4. REACT FORM SCHEMA (JSON)")
    print("-" * 40)
    react_gen = ReactFormGenerator()
    react_schemas = react_gen.generate(schemas)
    print(json.dumps(react_schemas["llm"]["schema"], indent=2)[:1500] + "...\n")
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("""
This POC demonstrates:

1. SDK models define settings with UI metadata in json_schema_extra
2. Schema extraction pulls all metadata from Pydantic models
3. Generators create CLI/TUI/GUI forms from the schema

Benefits:
- Adding a new field to the SDK model automatically appears in CLI/GUI
- Descriptions and validation are consistent everywhere
- No manual synchronization needed

To add a new setting:
  Just add a Field to the model with json_schema_extra metadata!
""")


if __name__ == "__main__":
    main()
