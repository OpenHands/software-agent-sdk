from __future__ import annotations

from pydantic import BaseModel, Field


class InstallationInfo(BaseModel):
    """Information about an installed extension.

    Linked to extensions by the installed extension metadata.
    """

    name: str = Field(description="Extension name")
    version: str = Field(default="1.0.0", description="Extension version")

    enabled: bool = Field(default=True, description="Whether the extension is enabled")

    source: str = Field(description="Original source (e.g., 'github:owner/repo')")
    resolved_ref: str | None = Field(
        default=None, description="Resolved git commit SHA (for version pinning)"
    )
    repo_path: str | None = Field(
        default=None,
        description="Subdirectory path within the repository (for monorepos)",
    )

    installed_at: str = Field(description="ISO 8601 timestamp of installation")
    install_path: str = Field(description="Path where the extension is installed")
