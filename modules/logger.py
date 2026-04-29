"""
Structured logging module with rotating file handler.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from modules.constants import (
    LOG_BACKUP_COUNT,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOG_MAX_BYTES,
)


LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_FILE = LOG_DIR / "farmer.log"


def setup_logger(name: str = "tf2_farmer", level: int = logging.INFO) -> logging.Logger:
    """
    Create and configure a logger with both file and console handlers.

    Args:
        name:  Logger name (use module ``__name__`` for per-module loggers).
        level: Logging level (default ``INFO``).

    Returns:
        Configured :class:`logging.Logger` instance.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers on re-import
    if logger.handlers:
        return logger

    logger.setLevel(level)

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # --- Rotating file handler ---
    file_handler = RotatingFileHandler(
        filename=LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    # --- Console (stdout) handler ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# Module-level default logger — import and use directly across modules
log = setup_logger()