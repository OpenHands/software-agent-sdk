"""Shared test utilities for execute_bash tests."""

import tempfile

from openhands.sdk.logger import get_logger
from openhands.sdk.tool.schema import TextContent
from openhands.tools.execute_bash.constants import TIMEOUT_MESSAGE_TEMPLATE
from openhands.tools.execute_bash.definition import ExecuteBashObservation
from openhands.tools.execute_bash.terminal import create_terminal_session


logger = get_logger(__name__)


def get_output_text(obs: ExecuteBashObservation) -> str:
    """Extract text from observation output field.

    This helper handles type-safe extraction of text from the observation's
    output field, which can be a str or list of Content items.
    """
    if isinstance(obs.output, str):
        return obs.output
    if not obs.output:
        return ""
    first_item = obs.output[0]
    return first_item.text if isinstance(first_item, TextContent) else ""


def get_no_change_timeout_suffix(timeout_seconds):
    """Helper function to generate the expected no-change timeout suffix."""
    return (
        f"\n[The command has no new output after {timeout_seconds} seconds. "
        f"{TIMEOUT_MESSAGE_TEMPLATE}]"
    )


def create_test_bash_session(work_dir=None):
    """Create a terminal session for testing purposes."""
    if work_dir is None:
        work_dir = tempfile.mkdtemp()
    return create_terminal_session(work_dir=work_dir)


def cleanup_bash_session(session):
    """Clean up a terminal session after testing."""
    if hasattr(session, "close"):
        try:
            session.close()
        except Exception as e:
            # Ignore cleanup errors - session might already be closed
            logger.warning(f"Error during session cleanup: {e}")
