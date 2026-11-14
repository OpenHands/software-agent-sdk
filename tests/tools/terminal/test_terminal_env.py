"""Unit tests for ``build_terminal_env()`` pager defaults.

Every terminal backend builds its session environment through this function,
so these tests pin its pager defaults down at the shared chokepoint.
"""

from openhands.tools.terminal.env import build_terminal_env


def test_build_terminal_env_disables_pagers_by_default():
    env = build_terminal_env()
    assert env["GIT_PAGER"] == "cat"
    assert env["PAGER"] == "cat"


def test_client_env_overrides_pager_defaults():
    env = build_terminal_env({"PAGER": "less"})
    assert env["PAGER"] == "less"
    assert env["GIT_PAGER"] == "cat"  # unrelated default untouched
