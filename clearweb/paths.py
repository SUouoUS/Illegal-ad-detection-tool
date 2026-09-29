"""Filesystem locations. When frozen (PyInstaller), results go next to the .exe."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "clearweb" if base else Path(__file__).resolve().parent


def result_dir() -> Path:
    return Path(os.environ.get("CLEARWEB_RESULT_DIR", app_dir()))


def runs_dir() -> Path:
    d = app_dir() / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_browser_env() -> None:
    """Use browsers shipped next to the executable when present."""
    bundled = app_dir() / "browsers"
    if bundled.is_dir() and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)
