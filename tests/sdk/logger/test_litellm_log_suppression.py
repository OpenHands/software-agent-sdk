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


def test_logger_module_does_not_import_litellm_at_module_scope():
    """The logger must not import litellm just to configure logging.

    Importing litellm here would put the provider stack in every process that
    only wants a logger. See issue #6366.
    """
    import ast
    from pathlib import Path

    import openhands.sdk.logger.logger as logger_module

    tree = ast.parse(Path(logger_module.__file__).read_text())
    scope_imports = []
    for node in tree.body:
        if isinstance(node, ast.Import) and any(
            alias.name == "litellm" for alias in node.names
        ):
            scope_imports.append(node.lineno)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("litellm")
        ):
            scope_imports.append(node.lineno)
    assert scope_imports == [], (
        f"logger.py imports litellm at module scope on lines {scope_imports}"
    )


def test_litellm_debug_flags_applied_by_default():
    """The default flags must still be applied once litellm is imported."""
    import litellm

    from openhands.sdk.logger.logger import apply_litellm_debug_flags

    apply_litellm_debug_flags()
    assert litellm.suppress_debug_info is True
    assert litellm.set_verbose is False


def test_litellm_debug_flags_enabled_when_confirmed(monkeypatch):
    """With DEBUG_LLM confirmed, the flags must be applied in the other direction."""
    import litellm

    import openhands.sdk.logger.logger as logger_module

    monkeypatch.setattr(logger_module, "_ENABLE_LITELLM_DEBUG", True)
    logger_module.apply_litellm_debug_flags()
    assert litellm.suppress_debug_info is False
    assert litellm.set_verbose is True

    monkeypatch.setattr(logger_module, "_ENABLE_LITELLM_DEBUG", False)
    logger_module.apply_litellm_debug_flags()
    assert litellm.suppress_debug_info is True
    assert litellm.set_verbose is False
