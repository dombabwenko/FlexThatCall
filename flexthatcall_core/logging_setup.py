from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import log_dir


def setup_logging(directory: Path | None = None) -> tuple[logging.Logger, Path]:
    folder = directory or log_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "flexthatcall.log"
    logger = logging.getLogger("flexthatcall")
    logger.setLevel(logging.INFO)
    if not any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger, path
