"""Custom logging configuration for uvicorn to reuse the SDK's root logger."""

import logging
from typing import Any

from openhands.sdk.logger import (
    ENV_JSON,
    ENV_LOG_LEVEL,
    IN_CI,
)


class UvicornAccessFormatter(logging.Formatter):
    """Custom formatter for uvicorn access logs."""

    def format(self, record):
        # Set default values for uvicorn-specific fields if they don't exist
        if not hasattr(record, "client_addr"):
            record.client_addr = "-"
        if not hasattr(record, "request_line"):
            record.request_line = record.getMessage()
        if not hasattr(record, "status_code"):
            record.status_code = "-"

        return super().format(record)


def get_uvicorn_logging_config() -> dict[str, Any]:
    """
    Generate uvicorn logging configuration that integrates with SDK's root logger.

    This function creates a logging configuration that:
    1. Preserves the SDK's root logger configuration
    2. Routes uvicorn logs through the same handlers
    3. Keeps access logs separate for better formatting
    4. Supports both Rich and JSON output based on SDK settings
    """
    # Base configuration
    # Note: We cannot use incremental=True with partial handler definitions
    # So we set it to False but configure loggers to propagate to root
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "incremental": False,  # Cannot be True when defining new handlers
        "formatters": {},
        "handlers": {},
        "loggers": {},
    }

    # Add formatters based on SDK settings
    if ENV_JSON or IN_CI:
        # JSON formatter for access logs
        config["formatters"]["access"] = {
            "()": "pythonjsonlogger.json.JsonFormatter",
            "fmt": "%(asctime)s %(levelname)s %(name)s "
            "%(client_addr)s %(request_line)s %(status_code)s",
        }
    else:
        # Custom formatter for access logs that handles missing fields
        config["formatters"]["access"] = {
            "()": UvicornAccessFormatter,
            "format": '%(asctime)s - %(name)s - %(levelname)s - "'
            '"%(client_addr)s - "%(request_line)s" %(status_code)s',
        }

    # Access handler - always separate for proper formatting
    config["handlers"]["access"] = {
        "formatter": "access",
        "class": "logging.StreamHandler",
        "stream": "ext://sys.stdout",
    }

    # Configure uvicorn loggers
    config["loggers"] = {
        # Main uvicorn logger - propagate to root
        "uvicorn": {
            "handlers": [],  # Use root's handlers via propagation
            "level": logging.getLevelName(ENV_LOG_LEVEL),
            "propagate": True,
        },
        # Error logger - propagate to root for stack traces
        "uvicorn.error": {
            "handlers": [],  # Use root's handlers via propagation
            "level": logging.getLevelName(ENV_LOG_LEVEL),
            "propagate": True,
        },
        # Access logger - use dedicated handler
        "uvicorn.access": {
            "handlers": ["access"],
            "level": logging.getLevelName(ENV_LOG_LEVEL),
            "propagate": False,  # Don't duplicate to root
        },
    }

    return config


LOGGING_CONFIG = get_uvicorn_logging_config()
