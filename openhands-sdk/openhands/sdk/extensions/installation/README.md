# Installation

Generic framework for installing, tracking, and loading extensions from local
or remote sources.

## Overview

The installation module is **extension-type agnostic**.  It is parameterised by
a type `T` (any object with `name`, `version`, and `description` attributes)
and an `InstallationInterface[T]` that knows how to load `T` from a directory.
Everything else — fetching, copying, metadata bookkeeping, enable/disable
state — is handled generically.

### Module layout

| File | Purpose |
|---|---|
| `interface.py` | `ExtensionProtocol` (structural typing contract) and `InstallationInterface[T]` (abstract loader) |
| `info.py` | `InstallationInfo` — Pydantic model stored per-extension in the metadata file |
| `metadata.py` | `InstallationMetadata` — persistence of `.installed.json`; `MetadataSession` — context manager that auto-saves on exit |
| `manager.py` | `InstallationManager[T]` — high-level API for install / uninstall / enable / disable / update |
| `utils.py` | `validate_extension_name` — kebab-case enforcement to prevent path traversal |

## Usage

### 1. Define your extension type and loader

```python
from pathlib import Path
from pydantic import BaseModel
from openhands.sdk.extensions.installation import (
    InstallationInterface,
    InstallationManager,
)

class Widget(BaseModel):
    name: str
    version: str
    description: str

class WidgetLoader(InstallationInterface[Widget]):
    @staticmethod
    def load_from_dir(extension_dir: Path) -> Widget:
        return Widget.model_validate_json(
            (extension_dir / "widget.json").read_text()
        )
```

### 2. Create a manager

```python
manager = InstallationManager(
    installation_dir=Path("~/.myapp/widgets/installed").expanduser(),
    installation_interface=WidgetLoader(),
)
```

### 3. Manage extensions

```python
# Install from a local path or remote source
info = manager.install("github:owner/my-widget", ref="v1.0.0")
info = manager.install("/path/to/local/widget")

# Force-overwrite an existing installation (preserves enabled state)
info = manager.install("github:owner/my-widget", force=True)

# List / load
all_info = manager.list_installed()        # List[InstallationInfo]
widgets  = manager.load_installed()        # List[Widget]  (enabled only)

# Enable / disable
manager.disable("my-widget")               # excluded from load_installed()
manager.enable("my-widget")                # included again

# Look up a single extension
info = manager.get("my-widget")            # InstallationInfo | None

# Update to latest from the original source
info = manager.update("my-widget")

# Remove completely
manager.uninstall("my-widget")
```

## Self-healing metadata

`list_installed()` (and by extension `load_installed()`) automatically
reconciles the `.installed.json` metadata with what is actually on disk:

- **Stale entries** — if a tracked extension's directory has been manually
  deleted, the metadata entry is pruned.
- **Untracked directories** — if a valid extension directory exists but is not
  in metadata, it is discovered and added with `source="local"`.

This means the metadata file is always the single source of truth *after* a
list/load call, even if the filesystem was modified externally.

## Extension naming

Extension names must be **kebab-case** (`^[a-z0-9]+(-[a-z0-9]+)*$`).  This is
enforced on install, uninstall, enable, disable, get, and update to prevent
path-traversal attacks (e.g. `../evil`).
