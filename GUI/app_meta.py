"""Application metadata for the Morocco Player Manager GUI."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "Morocco Player Manager"


def _normalize_version(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value[0] in {"v", "V"} and len(value) > 1:
        remainder = value[1:]
        if remainder and remainder[0].isdigit():
            return remainder
    return value


def _read_version_file(path: Path) -> str:
    try:
        if path.exists():
            value = _normalize_version(path.read_text(encoding="utf-8"))
            if value:
                return value
    except Exception:
        pass
    return ""


def _resolve_version() -> str:
    env_version = os.getenv("MAROCCOS_GUI_VERSION")
    if env_version:
        value = _normalize_version(env_version)
        if value:
            return value
    module_path = Path(__file__).resolve()
    module_dir = module_path.parent
    version_file = module_dir / "VERSION"
    value = _read_version_file(version_file)
    if value:
        return value
    frozen_root = None
    if getattr(sys, "frozen", False):
        frozen_root = Path(getattr(sys, "_MEIPASS", module_dir))
        frozen_version = _read_version_file(frozen_root / "VERSION")
        if frozen_version:
            return frozen_version
        frozen_gui_version = _read_version_file(frozen_root / "GUI" / "VERSION")
        if frozen_gui_version:
            return frozen_gui_version
    repo_root = module_dir.parent
    repo_version = _read_version_file(repo_root / "headless-player" / "VERSION")
    if repo_version:
        return repo_version
    if frozen_root:
        frozen_headless = _read_version_file(frozen_root / "headless-player" / "VERSION")
        if frozen_headless:
            return frozen_headless
    return "0.1.0"


APP_VERSION = _resolve_version()
APP_DISPLAY_NAME = f"{APP_NAME} v{APP_VERSION}"


def build_window_title(suffix: str | None = None) -> str:
    if suffix:
        return f"{APP_DISPLAY_NAME} — {suffix}"
    return APP_DISPLAY_NAME
