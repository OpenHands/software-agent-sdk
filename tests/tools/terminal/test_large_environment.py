"""
Tests for handling large environment variables in terminal sessions.

This test suite verifies that terminal implementations can handle large
environment dictionaries without hitting command-line length limitations.
This addresses issue #1330.
"""

import os
import platform
import tempfile

import pytest

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal import create_terminal_session


# Skip all tests in this file on Windows since tmux is not available
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="Tmux is not available on Windows",
)


@pytest.mark.parametrize("terminal_type", ["tmux"])
def test_large_environment_variables(terminal_type):
    """Test that terminal can handle large environment variables (issue #1330)."""
    # Store original environment variables to restore later
    original_vars = {}
    test_var_prefix = "TEST_LARGE_ENV_VAR_"

    try:
        # Add 100 large environment variables (total ~100KB)
        # This would cause "command too long" error with the old implementation
        for i in range(100):
            var_name = f"{test_var_prefix}{i}"
            var_value = "X" * 1000  # 1KB per variable
            original_vars[var_name] = os.environ.get(var_name)
            os.environ[var_name] = var_value

        with tempfile.TemporaryDirectory() as temp_dir:
            # This should not raise "command too long" error
            session = create_terminal_session(
                work_dir=temp_dir, terminal_type=terminal_type
            )
            session.initialize()

            # Verify the session works with a simple command
            obs = session.execute(TerminalAction(command="echo 'test_large_env'"))
            assert "test_large_env" in obs.text
            assert obs.metadata.exit_code == 0

            # Verify one of the large environment variables is accessible
            test_var = f"{test_var_prefix}0"
            obs = session.execute(TerminalAction(command=f"echo ${test_var}"))
            assert "XXX" in obs.text  # Should see part of the long value
            assert obs.metadata.exit_code == 0

            session.close()

    finally:
        # Clean up: restore original environment
        for var_name, original_value in original_vars.items():
            if original_value is None:
                if var_name in os.environ:
                    del os.environ[var_name]
            else:
                os.environ[var_name] = original_value


@pytest.mark.parametrize("terminal_type", ["tmux"])
def test_environment_variable_access(terminal_type):
    """Test that environment variables are accessible in the terminal session."""
    test_var = "TEST_TERMINAL_ENV_VAR_12345"
    test_value = "test_value_xyz_abc"

    try:
        os.environ[test_var] = test_value

        with tempfile.TemporaryDirectory() as temp_dir:
            session = create_terminal_session(
                work_dir=temp_dir, terminal_type=terminal_type
            )
            session.initialize()

            # Check that the environment variable is accessible
            obs = session.execute(TerminalAction(command=f"echo ${test_var}"))
            assert test_value in obs.text
            assert obs.metadata.exit_code == 0

            session.close()

    finally:
        if test_var in os.environ:
            del os.environ[test_var]


@pytest.mark.parametrize("terminal_type", ["tmux"])
def test_very_large_environment(terminal_type):
    """Test with very large environment (500KB+) to ensure robustness."""
    original_vars = {}
    test_var_prefix = "TEST_VERY_LARGE_ENV_"

    try:
        # Add 500 large environment variables (total ~500KB)
        # This definitely would fail with the old implementation
        for i in range(500):
            var_name = f"{test_var_prefix}{i}"
            var_value = "Y" * 1000  # 1KB per variable
            original_vars[var_name] = os.environ.get(var_name)
            os.environ[var_name] = var_value

        with tempfile.TemporaryDirectory() as temp_dir:
            # This should work with the new implementation
            session = create_terminal_session(
                work_dir=temp_dir, terminal_type=terminal_type
            )
            session.initialize()

            # Verify basic functionality
            obs = session.execute(TerminalAction(command="echo 'very_large_env_test'"))
            assert "very_large_env_test" in obs.text
            assert obs.metadata.exit_code == 0

            session.close()

    finally:
        # Clean up
        for var_name, original_value in original_vars.items():
            if original_value is None:
                if var_name in os.environ:
                    del os.environ[var_name]
            else:
                os.environ[var_name] = original_value
