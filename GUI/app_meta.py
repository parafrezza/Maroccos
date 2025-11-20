"""Application metadata for the Morocco Player Manager GUI."""

from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "Morocco Player Manager"


def _resolve_version() -> str:
    env_version = os.getenv("MAROCCOS_GUI_VERSION")
    if env_version:
        value = env_version.strip()
        if value:
            return value
    version_file = Path(__file__).with_name("VERSION")
    try:
        if version_file.exists():
            value = version_file.read_text(encoding="utf-8").strip()
            if value:
                return value
    except Exception:
        pass
    return "0.1.0"


APP_VERSION = _resolve_version()
APP_DISPLAY_NAME = f"{APP_NAME} v{APP_VERSION}"


def build_window_title(suffix: str | None = None) -> str:
    if suffix:
        return f"{APP_DISPLAY_NAME} — {suffix}"
    return APP_DISPLAY_NAME
