"""Logging to stderr and a rotating file; stdout belongs to the MCP protocol."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from tgmcp.gateway import LIBRARY_LOGGER

FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_installed: list[logging.Handler] = []


def configure(log_path: Path, level: str = "INFO") -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(FORMAT)
    stderr = logging.StreamHandler(sys.stderr)
    to_file = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    root = logging.getLogger()
    root.setLevel(level)
    # Idempotent: a second call replaces the handlers of the first.
    for old in _installed:
        root.removeHandler(old)
        old.close()
    _installed.clear()
    for handler in (stderr, to_file):
        handler.setFormatter(formatter)
        root.addHandler(handler)
        _installed.append(handler)
    logging.getLogger(LIBRARY_LOGGER).setLevel(logging.WARNING)
