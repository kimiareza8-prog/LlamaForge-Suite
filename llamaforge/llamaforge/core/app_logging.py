from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import APP_DIR

LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "llamaforge.log"


def get_logger() -> logging.Logger:
    """Return the process-wide LlamaForge logger.

    The logger is intentionally file-first. UI logging is layered on top by the
    local control plane so worker threads stay independent of presentation code.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("llamaforge")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = RotatingFileHandler(
            LOG_FILE, maxBytes=16 * 1024 * 1024, backupCount=10, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s")
        )
        logger.addHandler(handler)
        logger.propagate = False
    return logger
