"""Centralized logging configuration."""

from __future__ import annotations

import logging
import os


def setup_logging() -> None:
    """Configure root logging once, honoring the LOG_LEVEL env var."""
    level_name = (os.getenv("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # aiogram is chatty at DEBUG; keep it at INFO unless explicitly debugging.
    if level > logging.DEBUG:
        logging.getLogger("aiogram").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
