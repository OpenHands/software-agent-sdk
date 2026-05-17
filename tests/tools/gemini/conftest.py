"""Shared fixtures for Gemini tool tests."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_conv_state(tmp_path):
    """Minimal mock ConversationState with a workspace directory."""
    cs = MagicMock()
    cs.workspace.working_dir = str(tmp_path)
    return cs
