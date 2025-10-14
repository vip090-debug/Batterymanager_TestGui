"""Logging helpers integrating with the Qt GUI."""
from __future__ import annotations

import logging
from logging.handlers import QueueHandler
from queue import Queue

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(queue: Queue[logging.LogRecord]) -> None:
    """Configure application logging to forward records to the GUI."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    queue_handler = QueueHandler(queue)
    queue_handler.setLevel(logging.DEBUG)
    root_logger.handlers.clear()
    root_logger.addHandler(queue_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)


def update_log_level(level_name: str) -> None:
    """Update the root logger level based on a combobox selection."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.getLogger().setLevel(level)


__all__ = ["setup_logging", "update_log_level", "LOG_FORMAT"]
