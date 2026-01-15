"""Custom logging configuration for uvicorn to reuse the SDK's root logger."""

import logging
from typing import Any

from pythonjsonlogger.json import JsonFormatter

from openhands.sdk.logger import ENV_JSON, ENV_LOG_LEVEL, IN_CI


class UvicornAccessJsonFormatter(JsonFormatter):
    """JSON formatter for uvicorn access logs that extracts HTTP fields.

    Uvicorn access logs pass structured data in record.args as a tuple:
    (client_addr, method, full_path, http_version, status_code)

    This formatter extracts these into separate JSON fields for better
    querying and analysis in log aggregation systems like Datadog.
    """

    def add_fields(
        self,
        log_data: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_data, record, message_dict)

        # Extract HTTP fields from uvicorn access log args
        # record.args is a tuple for uvicorn access logs:
        # (client_addr, method, full_path, http_version, status_code)
        args = record.args
        if isinstance(args, tuple) and len(args) >= 5:
            client_addr, method, full_path, http_version, status_code = args[:5]
            log_data["http.client_ip"] = client_addr
            log_data["http.method"] = method
            log_data["http.url"] = full_path
            log_data["http.version"] = http_version
            # status_code from uvicorn is typically an int, but handle edge cases
            if isinstance(status_code, int):
                log_data["http.status_code"] = status_code
            elif isinstance(status_code, str) and status_code.isdigit():
                log_data["http.status_code"] = int(status_code)
            else:
                log_data["http.status_code"] = status_code


def get_uvicorn_logging_config() -> dict[str, Any]:
    """
    Generate uvicorn logging configuration that integrates with SDK's root logger.

    This function creates a logging configuration that:
    1. Preserves the SDK's root logger configuration
    2. Routes uvicorn logs through the same handlers
    3. Uses JSON formatter for access logs when LOG_JSON=true or in CI
    4. Extracts HTTP fields into structured JSON attributes
    """
    use_json = ENV_JSON or IN_CI
    log_level = logging.getLevelName(ENV_LOG_LEVEL)

    # Base configuration
    config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "incremental": False,
        "formatters": {},
        "handlers": {},
        "loggers": {
            # Common logger configurations - propagate to root
            "uvicorn": {
                "handlers": [],
                "level": log_level,
                "propagate": True,
            },
            "uvicorn.error": {
                "handlers": [],
                "level": log_level,
                "propagate": True,
            },
        },
    }

    if use_json:
        # Define JSON formatter for access logs with HTTP field extraction
        config["formatters"]["access_json"] = {
            "()": UvicornAccessJsonFormatter,
            "fmt": "%(asctime)s %(levelname)s %(name)s %(message)s",
        }

        # Define handler for access logs
        config["handlers"]["access_json"] = {
            "class": "logging.StreamHandler",
            "formatter": "access_json",
            "stream": "ext://sys.stderr",
        }

        # Access logger uses dedicated JSON handler with HTTP field extraction
        config["loggers"]["uvicorn.access"] = {
            "handlers": ["access_json"],
            "level": log_level,
            "propagate": False,  # Don't double-log
        }
    else:
        # Non-JSON mode: propagate access logs to root (uses Rich handler)
        config["loggers"]["uvicorn.access"] = {
            "handlers": [],
            "level": log_level,
            "propagate": True,
        }

    return config


LOGGING_CONFIG = get_uvicorn_logging_config()
