"""Edge-case and failure-path tests for the file editor."""

import os

import pytest

from openhands.tools.file_editor import file_editor


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores file permissions, so chmod 0 stays readable",
)
def test_view_unreadable_file_returns_error_observation(tmp_path):
    """An unreadable file must come back as a tool error, not raise out of the tool."""
    path = tmp_path / "secret.txt"
    path.write_text("secret\n")
    os.chmod(path, 0)
    try:
        result = file_editor(command="view", path=str(path))
    finally:
        os.chmod(path, 0o644)

    assert result.is_error
    assert "secret.txt" in result.text
