from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openhands.sdk.extensions.installation import InstallationInfo


@dataclass
class MockExtension:
    name: str
    version: str
    description: str


def test_installation_info_from_extension():
    """Test InstallationInfo construction from extensions populates as expected."""
    extension = MockExtension(
        name="name", version="0.1.2", description="Test extension please ignore"
    )
    source = "local"
    install_path = Path.cwd()
    info = InstallationInfo.from_extension(extension, source, install_path)

    assert info.name == extension.name
    assert info.version == extension.version
    assert info.description == extension.description

    assert info.source == source
    assert info.install_path == install_path

    assert info.enabled

    assert info.resolved_ref is None
    assert info.repo_path is None

    assert datetime.fromisoformat(info.installed_at)
