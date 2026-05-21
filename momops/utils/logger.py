"""Enhanced structured logging for production."""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

from rich.logging import RichHandler

from momops.config import get_settings


class StructuredFormatter(logging.Formatter):
    """Outputs logs as structured JSON for production systems."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_obj: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add context-specific fields
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        if hasattr(record, "deployment_id"):
            log_obj["deployment_id"] = record.deployment_id
        if hasattr(record, "resource_id"):
            log_obj["resource_id"] = record.resource_id
        if hasattr(record, "cost"):
            log_obj["cost"] = record.cost

        return json.dumps(log_obj, default=str)


class ProductionFilter(logging.Filter):
    """Redact sensitive data from logs."""

    SENSITIVE_KEYS = {
        "api_key",
        "secret",
        "password",
        "token",
        "credentials",
        "aws_secret_access_key",
        "anthropic_api_key",
    }

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter and redact sensitive data."""
        msg = record.getMessage()

        # Mask common patterns
        for key in self.SENSITIVE_KEYS:
            if key.lower() in msg.lower():
                record.msg = self._redact_value(msg)
                record.args = ()

        return True

    @staticmethod
    def _redact_value(value: str) -> str:
        """Redact API keys and credentials."""
        import re

        # Mask API keys (common patterns)
        value = re.sub(r"sk-[A-Za-z0-9]+", "[REDACTED_API_KEY]", value)
        value = re.sub(r"AKIA[0-9A-Z]{16}", "[REDACTED_AWS_KEY]", value)
        value = re.sub(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", "[REDACTED_TOKEN]", value)

        return value


def configure_logging(
    level: str = "INFO",
    json_format: bool = False,
    log_file: Path | None = None,
) -> None:
    """
    Configure process-wide logging with optional JSON output and file rotation.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: If True, output structured JSON for production
        log_file: Optional path to log file for rotation
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level.upper())

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Production filter for all handlers
    prod_filter = ProductionFilter()

    if json_format:
        # Structured JSON logging for production
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter())
        handler.addFilter(prod_filter)
        root_logger.addHandler(handler)
    else:
        # Pretty console logging for development
        handler = RichHandler(rich_tracebacks=True, show_path=False)
        handler.addFilter(prod_filter)
        root_logger.addHandler(handler)

    # File handler with rotation (only if log_file specified)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=100 * 1024 * 1024,  # 100MB
            backupCount=10,  # Keep 10 rotated files
        )
        file_handler.setFormatter(StructuredFormatter())
        file_handler.addFilter(prod_filter)
        root_logger.addHandler(file_handler)


def get_logger(name: str) -> logging.LoggerAdapter:
    """Get a logger with context support."""
    logger = logging.getLogger(name)
    return logging.LoggerAdapter(logger, {})
