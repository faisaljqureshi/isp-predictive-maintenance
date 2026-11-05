"""Logging setup: console plus a size-rotated file, so a long-running service never fills the disk."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(name: str, log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    rotating = RotatingFileHandler(log_dir / f"{name}.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    rotating.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(rotating)
