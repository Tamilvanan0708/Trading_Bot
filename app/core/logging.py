"""
Structured Logging Configuration.
"""

import logging
import sys
from datetime import datetime


class StructuredFormatter(logging.Formatter):
    """Clean structured log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname.ljust(8)
        name = record.name.ljust(25)
        message = record.getMessage()
        return f"[{timestamp}] [{level}] [{name}] {message}"


def setup_logger(name: str = "xauusd_agent", level: str = "INFO") -> logging.Logger:
    """Configures and returns a structured logger."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
        logger.propagate = False

    return logger


logger = setup_logger()
