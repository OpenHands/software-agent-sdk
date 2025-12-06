"""Custom logging configuration for uvicorn to reuse the SDK's root logger."""

import logging
from typing import Any

from openhands.sdk.logger import ENV_LOG_LEVEL


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
        # Access logger - propagate to root
        "uvicorn.access": {
            "handlers": [],
            "level": logging.getLevelName(ENV_LOG_LEVEL),
            "propagate": True,
        },
    }

    return config


LOGGING_CONFIG = get_uvicorn_logging_config()
