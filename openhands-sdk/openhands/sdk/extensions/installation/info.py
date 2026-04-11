from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from openhands.sdk.extensions.installation.interface import ExtensionProtocol


class InstallationInfo(BaseModel):
    """Information about an installed extension.

    Linked to extensions by the installed extension metadata.
    """

    name: str = Field(description="Extension name")
    version: str = Field(default="1.0.0", description="Extension version")
    description: str = Field(default="", description="Extension description")

    enabled: bool = Field(default=True, description="Whether the extension is enabled")

    source: str = Field(description="Original source (e.g., 'github:owner/repo')")
    resolved_ref: str | None = Field(
        default=None, description="Resolved git commit SHA (for version pinning)"
    )
    repo_path: str | None = Field(
        default=None,
        description="Subdirectory path within the repository (for monorepos)",
    )

    installed_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 timestamp of installation",
    )
    install_path: Path = Field(description="Path where the extension is installed")

    @staticmethod
    def from_extension(
        extension: ExtensionProtocol,
        source: str,
        install_path: Path,
        resolved_ref: str | None = None,
        repo_path: str | None = None,
    ) -> InstallationInfo:
        """Construct an InstallationInfo object from an extension, plus relevant
        installation information.

        Args:
            extension: Any installable extension object.
        """
        return InstallationInfo(
            name=extension.name,
            version=extension.version,
            description=extension.description,
            source=source,
            resolved_ref=resolved_ref,
            repo_path=repo_path,
            install_path=install_path,
        )
