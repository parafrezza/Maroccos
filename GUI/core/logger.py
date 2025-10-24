"""Central logging utilities for the GUI application."""

from __future__ import annotations

import logging

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger for the GUI application."""
    logging.basicConfig(level=level, format=_LOG_FORMAT)


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger."""
    return logging.getLogger(name)
