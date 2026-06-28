"""Tests for the forward_subagent_events config flag."""
from openhands.agent_server.config import Config


def test_forward_subagent_events_defaults_to_false():
    """Config.forward_subagent_events must default to False."""
    config = Config()
    assert config.forward_subagent_events is False
