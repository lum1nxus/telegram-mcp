"""Logging to stderr and a per-process file; stdout belongs to the MCP protocol."""

import logging
import os
import sys
import time
from pathlib import Path

from tgmcp.config import LIBRARY_LOGGER

FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
MCP_LOGGER = "mcp"
KEEP_DAYS = 7
_installed: list[logging.Handler] = []


def _prune(folder: Path, pattern: str) -> None:
    """Remove log files of earlier processes that were last written over KEEP_DAYS ago."""
    cutoff = time.time() - KEEP_DAYS * 86400
    for old in folder.glob(pattern):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:  # still open in another process, or already gone
            continue


def configure(log_path: Path, level: str = "INFO") -> Path:
    """Log to stderr and to `<stem>-<pid><suffix>` next to `log_path`; returns that file.

    Every `serve` process (one per Claude Code session) writes its own file, so no two
    processes share one; files older than KEEP_DAYS days are removed here.
    """
    folder = log_path.parent
    folder.mkdir(parents=True, exist_ok=True)
    _prune(folder, f"{log_path.stem}-*{log_path.suffix}")
    path = folder / f"{log_path.stem}-{os.getpid()}{log_path.suffix}"
    formatter = logging.Formatter(FORMAT)
    stderr = logging.StreamHandler(sys.stderr)
    to_file = logging.FileHandler(path, encoding="utf-8")
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
    for library in (LIBRARY_LOGGER, MCP_LOGGER):
        # mcp logs every failed tool call at INFO with its error sentence, which may quote the
        # model's arguments; Telethon logs network chatter. Warnings and errors still come.
        logging.getLogger(library).setLevel(logging.WARNING)
    return path
