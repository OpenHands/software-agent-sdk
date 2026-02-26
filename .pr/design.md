# OpenHands Settings Architecture Proposal

## Executive Summary

This document proposes a unified settings architecture where the **SDK's Pydantic models become the single source of truth** for all configuration. Client projects (CLI, GUI) will automatically derive their settings UI and CLI arguments from these models, ensuring instant propagation of new settings.

## Current State Analysis

### 1. SDK (software-agent-sdk)

The SDK has well-defined Pydantic models:

**LLM (`openhands/sdk/llm/llm.py`)**
- ~50+ fields with `Field()` definitions
- Examples: `model`, `api_key`, `base_url`, `temperature`, `max_output_tokens`, `reasoning_effort`, etc.

**AgentBase (`openhands/sdk/agent/base.py`)**
- ~15 fields for agent configuration
- Examples: `llm`, `tools`, `condenser`, `critic`, `agent_context`, etc.

**Conversation**
- ~10+ parameters for conversation setup
- Examples: `workspace`, `persistence_dir`, `max_iteration_per_run`, `stuck_detection`, etc.

All settings use Pydantic's `Field()` with descriptions, defaults, and validation constraints.

### 2. CLI (openhands-cli)

The CLI maintains **separate** settings models:

**Separate Models (`stores/cli_settings.py`)**
```python
class CriticSettings(BaseModel):
    enable_critic: bool = True
    enable_iterative_refinement: bool = False
    critic_threshold: float = DEFAULT_CRITIC_THRESHOLD
    # ... more fields
```

**Manual Agent Building (`stores/agent_store.py`)**
```python
llm = LLM(
    model=model,
    api_key=llm_api_key,
    base_url=base_url,
    usage_id="agent",
)
```

**Hardcoded UI (`tui/modals/settings/`)**
- Settings forms are manually defined in Python
- Each new SDK setting requires manual UI updates

**Manual Argparse (`argparsers/`)**
- Command-line arguments are manually defined
- No connection to SDK field definitions

### 3. GUI (openhands/openhands)

**Legacy V0 (`core/config/`)**
- `LLMConfig`, `AgentConfig` - duplicates SDK models
- Marked for deprecation

**V1 App Server (`app_server/`)**
- Moving toward SDK-based configuration
- Uses `OpenHandsModel` from SDK

**Separate Settings Model (`storage/data_models/settings.py`)**
```python
class Settings(BaseModel):
    language: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    # ... duplicated from SDK
```

## Problem Statement

Settings are defined in **three places** with manual synchronization:

```
┌─────────────────┐     manual sync     ┌─────────────────┐
│   SDK Models    │ ◄─────────────────► │   CLI Models    │
│ (LLM, Agent)    │                     │ (CliSettings)   │
└────────┬────────┘                     └────────┬────────┘
         │                                       │
         │ manual sync                           │ manual sync
         │                                       │
         ▼                                       ▼
┌─────────────────┐                     ┌─────────────────┐
│   GUI Models    │                     │   CLI UI/Args   │
│   (Settings)    │                     │   (hardcoded)   │
└─────────────────┘                     └─────────────────┘
```

**Consequences:**
1. New SDK settings require manual updates in CLI and GUI
2. Settings descriptions may drift between projects
3. Validation logic is duplicated
4. High maintenance burden for new features

## Proposed Architecture

### Goal: Zero-Latency Settings Propagation

```
┌─────────────────────────────────────────────────────────────┐
│                    SDK (Source of Truth)                     │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Pydantic Models with UI Metadata                    │    │
│  │  - LLM: model, api_key, temperature, ...            │    │
│  │  - Agent: tools, condenser, critic, ...             │    │
│  │  - Conversation: workspace, stuck_detection, ...    │    │
│  └─────────────────────────────────────────────────────┘    │
│                            │                                 │
│                            ▼                                 │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Settings Schema Export API                          │    │
│  │  - JSON Schema generation                           │    │
│  │  - UI hints and metadata                            │    │
│  │  - Grouping and categorization                      │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
       ┌──────────┐   ┌──────────┐   ┌──────────┐
       │   CLI    │   │   GUI    │   │  Other   │
       │ (auto-   │   │ (auto-   │   │ Clients  │
       │  gen UI) │   │  gen UI) │   │          │
       └──────────┘   └──────────┘   └──────────┘
```

### Key Components

#### 1. UI Metadata in SDK Field Definitions

Enhance Pydantic `Field()` definitions with UI hints:

```python
# openhands/sdk/llm/llm.py

class LLM(BaseModel):
    model: str = Field(
        default="claude-sonnet-4-20250514",
        description="The language model to use for completions.",
        json_schema_extra={
            # UI Configuration
            "ui_type": "select",           # text, password, select, number, toggle
            "ui_group": "Model",           # Grouping for UI tabs/sections
            "ui_order": 1,                 # Display order within group
            
            # CLI Configuration  
            "cli_flags": ["--model", "-m"],
            "cli_group": "LLM Options",
            
            # Environment Variable
            "env_var": "LLM_MODEL",
            
            # Dynamic choices (optional)
            "choices_provider": "get_model_recommendations",
            
            # Visibility
            "advanced": False,             # Show in basic/advanced mode
        }
    )
    
    api_key: str | SecretStr | None = Field(
        default=None,
        description="API key for authentication.",
        json_schema_extra={
            "ui_type": "password",
            "ui_group": "Authentication",
            "ui_order": 1,
            "cli_flags": ["--api-key"],
            "env_var": "LLM_API_KEY",
            "required_if_no_default": True,
        }
    )
    
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Sampling temperature (0-2). Higher values = more random.",
        json_schema_extra={
            "ui_type": "slider",
            "ui_group": "Generation",
            "ui_order": 1,
            "advanced": True,
            "cli_flags": ["--temperature"],
        }
    )
```

#### 2. Settings Schema Export API

Add a settings module to the SDK that exports schemas:

```python
# openhands/sdk/settings/__init__.py

from typing import TypedDict
from openhands.sdk import LLM, AgentBase
from openhands.sdk.conversation import Conversation

class FieldMetadata(TypedDict, total=False):
    name: str
    type: str
    description: str
    default: any
    required: bool
    # UI metadata
    ui_type: str
    ui_group: str
    ui_order: int
    advanced: bool
    # CLI metadata
    cli_flags: list[str]
    cli_group: str
    # Environment variable
    env_var: str
    # Validation
    minimum: float | None
    maximum: float | None
    choices: list[str] | None
    choices_provider: str | None


class SettingsCategory(TypedDict):
    name: str
    description: str
    fields: list[FieldMetadata]


def get_llm_settings_schema() -> SettingsCategory:
    """Extract settings schema from LLM model."""
    ...

def get_agent_settings_schema() -> SettingsCategory:
    """Extract settings schema from Agent model."""
    ...

def get_conversation_settings_schema() -> SettingsCategory:
    """Extract settings schema from Conversation model."""
    ...

def get_all_settings_schemas() -> dict[str, SettingsCategory]:
    """Get all configurable settings from SDK models."""
    return {
        "llm": get_llm_settings_schema(),
        "agent": get_agent_settings_schema(),
        "conversation": get_conversation_settings_schema(),
    }

# Export version for compatibility checking
SETTINGS_SCHEMA_VERSION = "1.0.0"
```

#### 3. Settings Form Generator Library

A library that generates UI components from schemas:

```python
# openhands/sdk/settings/generators.py (or separate package)

import argparse
from typing import Protocol

class FormGenerator(Protocol):
    """Protocol for form generators."""
    
    def generate(self, schema: SettingsCategory) -> any:
        """Generate form from schema."""
        ...


class ArgparseGenerator:
    """Generate argparse arguments from settings schema."""
    
    def generate(self, schema: SettingsCategory) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser()
        
        for field in schema["fields"]:
            flags = field.get("cli_flags", [f"--{field['name'].replace('_', '-')}"])
            
            kwargs = {
                "help": field.get("description"),
                "default": field.get("default"),
            }
            
            # Handle type conversion
            field_type = field.get("type")
            if field_type == "boolean":
                kwargs["action"] = "store_true"
            elif field_type == "integer":
                kwargs["type"] = int
            elif field_type == "number":
                kwargs["type"] = float
            elif field.get("choices"):
                kwargs["choices"] = field["choices"]
            
            # Handle required fields
            if field.get("required"):
                kwargs["required"] = True
            
            parser.add_argument(*flags, **kwargs)
        
        return parser


class TextualFormGenerator:
    """Generate Textual TUI forms from settings schema."""
    
    def generate(self, schema: SettingsCategory):
        """Generate Textual widgets for settings form."""
        from textual.containers import Container
        from textual.widgets import Input, Select, Switch, Label
        
        widgets = []
        
        for field in sorted(schema["fields"], key=lambda f: f.get("ui_order", 999)):
            ui_type = field.get("ui_type", "text")
            
            label = Label(field.get("description", field["name"]))
            
            if ui_type == "password":
                widget = Input(password=True, id=field["name"])
            elif ui_type == "select":
                choices = field.get("choices", [])
                widget = Select(
                    [(c, c) for c in choices],
                    id=field["name"],
                )
            elif ui_type == "toggle":
                widget = Switch(id=field["name"])
            elif ui_type == "number" or ui_type == "slider":
                widget = Input(type="number", id=field["name"])
            else:
                widget = Input(id=field["name"])
            
            widgets.extend([label, widget])
        
        return Container(*widgets)


class ReactFormGenerator:
    """Generate React form definition (JSON) from settings schema."""
    
    def generate(self, schema: SettingsCategory) -> dict:
        """Generate JSON schema for react-jsonschema-form or similar."""
        form_schema = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        
        ui_schema = {}
        
        for field in schema["fields"]:
            name = field["name"]
            
            # JSON Schema property
            prop = {"title": field.get("description", name)}
            
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
            
            form_schema["properties"][name] = prop
            
            if field.get("required"):
                form_schema["required"].append(name)
            
            # UI Schema
            ui_type = field.get("ui_type")
            if ui_type == "password":
                ui_schema[name] = {"ui:widget": "password"}
            elif ui_type == "slider":
                ui_schema[name] = {"ui:widget": "range"}
        
        return {"schema": form_schema, "uiSchema": ui_schema}
```

#### 4. Client Integration

**CLI Integration:**

```python
# openhands_cli/setup.py (updated)

from openhands.sdk.settings import get_llm_settings_schema
from openhands.sdk.settings.generators import ArgparseGenerator

def create_llm_parser() -> argparse.ArgumentParser:
    """Create argparse for LLM settings from SDK schema."""
    schema = get_llm_settings_schema()
    generator = ArgparseGenerator()
    return generator.generate(schema)
```

**TUI Integration:**

```python
# openhands_cli/tui/modals/settings/components/settings_tab.py (updated)

from openhands.sdk.settings import get_llm_settings_schema, get_agent_settings_schema
from openhands.sdk.settings.generators import TextualFormGenerator

class SettingsTab(Container):
    def compose(self) -> ComposeResult:
        generator = TextualFormGenerator()
        
        # LLM Settings
        llm_schema = get_llm_settings_schema()
        yield Static("LLM Settings", classes="section-header")
        yield generator.generate(llm_schema)
        
        # Agent Settings
        agent_schema = get_agent_settings_schema()
        yield Static("Agent Settings", classes="section-header")
        yield generator.generate(agent_schema)
```

**GUI Integration:**

```python
# Frontend can fetch schema via API endpoint

# openhands/app_server/settings_router.py
from fastapi import APIRouter
from openhands.sdk.settings import get_all_settings_schemas, SETTINGS_SCHEMA_VERSION

router = APIRouter()

@router.get("/api/settings/schema")
async def get_settings_schema():
    """Return settings schema for frontend form generation."""
    return {
        "version": SETTINGS_SCHEMA_VERSION,
        "schemas": get_all_settings_schemas(),
    }
```

### Implementation Plan

#### Phase 1: SDK Schema Infrastructure (Week 1-2)
1. Add `json_schema_extra` metadata to LLM fields
2. Add `json_schema_extra` metadata to Agent fields  
3. Create `openhands.sdk.settings` module with schema extraction
4. Add unit tests for schema generation

#### Phase 2: Form Generators (Week 3)
1. Implement `ArgparseGenerator`
2. Implement `TextualFormGenerator`
3. Implement `ReactFormGenerator` (JSON output)
4. Add tests for generators

#### Phase 3: CLI Migration (Week 4)
1. Update CLI to use generated argparse
2. Update TUI settings modal to use generated forms
3. Remove hardcoded settings definitions
4. Ensure backward compatibility

#### Phase 4: GUI Migration (Week 5)
1. Add `/api/settings/schema` endpoint
2. Update frontend to use dynamic form generation
3. Remove hardcoded settings forms
4. Test cross-version compatibility

#### Phase 5: Documentation & Polish (Week 6)
1. Document how to add new settings
2. Create migration guide for existing settings
3. Add schema versioning and deprecation support
4. Performance optimization

### Migration Example: Adding a New Setting

**Before (current approach):**
1. Add field to SDK model
2. Manually add to CLI's `CliSettings` model
3. Manually update CLI's settings UI
4. Manually add CLI argument
5. Manually update GUI's Settings model
6. Manually update frontend form
7. Write documentation

**After (proposed approach):**
1. Add field to SDK model with `json_schema_extra`
2. Done! (CLI and GUI automatically pick up the new field)

```python
# Just add this to SDK:
new_setting: int = Field(
    default=10,
    description="A new configurable setting",
    json_schema_extra={
        "ui_type": "number",
        "ui_group": "Advanced",
        "cli_flags": ["--new-setting"],
        "env_var": "NEW_SETTING",
    }
)
# CLI and GUI automatically show the new setting!
```

### Benefits

1. **Single Source of Truth**: SDK Pydantic models are authoritative
2. **Zero-Latency Propagation**: New settings appear everywhere instantly
3. **Consistent Validation**: Same Pydantic validation in all clients
4. **Reduced Duplication**: No need to maintain parallel model definitions
5. **Better Documentation**: Field descriptions are always in sync
6. **Type Safety**: Schema generation preserves type information
7. **Testability**: Schema can be validated programmatically

### Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Breaking existing CLI/GUI | Use schema versioning, gradual rollout |
| Performance overhead | Cache generated schemas |
| Complex UI requirements | Allow UI overrides where needed |
| Dynamic choices | Support `choices_provider` functions |
| Validation edge cases | Keep Pydantic validators, enhance at generator level |

### Open Questions

1. **Where should generators live?** SDK, separate package, or in clients?
   - Recommendation: SDK exports schema, clients can use shared generator library
   
2. **How to handle client-specific settings?** (e.g., CLI's `auto_open_plan_panel`)
   - Recommendation: Client-specific settings remain in client, only shared settings come from SDK
   
3. **Version compatibility between SDK and clients?**
   - Recommendation: Schema version + graceful degradation for unknown fields

## Conclusion

This architecture eliminates the manual synchronization burden and ensures that any new setting added to the SDK automatically appears in all client applications. The implementation is incremental and backward-compatible, allowing gradual migration of existing settings.

The key insight is that **Pydantic models already contain all the information needed** to generate UIs - we just need to add UI hints and create the generation machinery.
