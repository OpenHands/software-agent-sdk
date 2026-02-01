"""Test that LiteLLM INFO logs are suppressed by default."""

import logging


def test_litellm_loggers_suppressed():
    """Test that LiteLLM, litellm, and openai loggers are set to ERROR level."""
    # Import the logger module to trigger initialization

    # Check that the LiteLLM loggers are set to ERROR level
    for logger_name in ["litellm", "LiteLLM", "openai"]:
        llm_logger = logging.getLogger(logger_name)
        assert llm_logger.level == logging.ERROR, (
            f"Logger {logger_name} should be set to ERROR level, got {llm_logger.level}"
        )
        assert llm_logger.propagate is False, (
            f"Logger {logger_name} should not propagate"
        )


def test_litellm_info_logs_not_shown(caplog):
    """Test that INFO level logs from LiteLLM are not shown."""
    # Import the logger module to trigger initialization

    # Set the capture level to INFO to ensure we would capture INFO logs
    # if they were emitted
    caplog.set_level(logging.INFO)

    # Create loggers and emit INFO logs
    for logger_name in ["litellm", "LiteLLM", "openai"]:
        test_logger = logging.getLogger(logger_name)
        test_logger.info("This INFO log should not appear")
        test_logger.warning("This WARNING log should not appear")

    # Check that no INFO or WARNING logs were captured
    for record in caplog.records:
        assert record.name not in [
            "litellm",
            "LiteLLM",
            "openai",
        ], f"Log from {record.name} should not be captured: {record.message}"


def test_litellm_logger_level_blocks_info():
    """Test that INFO/WARNING logs are blocked by the ERROR level."""
    # Import the logger module to trigger initialization

    # Verify that INFO and WARNING logs would be blocked
    for logger_name in ["litellm", "LiteLLM", "openai"]:
        test_logger = logging.getLogger(logger_name)
        # If the logger level is ERROR, INFO and WARNING should not pass
        assert not test_logger.isEnabledFor(logging.INFO), (
            f"Logger {logger_name} should not be enabled for INFO"
        )
        assert not test_logger.isEnabledFor(logging.WARNING), (
            f"Logger {logger_name} should not be enabled for WARNING"
        )
        # But ERROR should pass
        assert test_logger.isEnabledFor(logging.ERROR), (
            f"Logger {logger_name} should be enabled for ERROR"
        )
