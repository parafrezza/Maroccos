"""Entry point for the Morocco player management GUI."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# re-enable running `python GUI/main.py`
if __package__ is None:  # pragma: no cover - allow "python GUI/main.py"
    sys.path.append(str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette, QColor
from PySide6.QtCore import Qt

from GUI.core.logger import configure_logging
from GUI.app_meta import APP_DISPLAY_NAME, APP_NAME

# Defer importing GUI widgets/controllers until after QApplication is created
ApplicationController = None
MainWindow = None


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _resolve_user_settings_dir() -> Path:
    base_dir = None
    for env_var in ("LOCALAPPDATA", "APPDATA"):
        value = os.getenv(env_var)
        if isinstance(value, str) and value.strip():
            base_dir = Path(value.strip())
            break
    if base_dir is None:
        base_dir = Path.home()
    return base_dir / APP_NAME


def _resolve_settings_path() -> Path:
    if _is_frozen():
        return _resolve_user_settings_dir() / "settings.json"
    return Path(__file__).resolve().parent / "settings.json"


def _bundled_settings_template() -> Path | None:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for candidate in ("settings.json", "settings.sample.json"):
        path = base / candidate
        if path.exists():
            return path
    return None


def _ensure_settings_file(path: Path) -> None:
    if path.exists():
        return
    template = _bundled_settings_template()
    path.parent.mkdir(parents=True, exist_ok=True)
    if template is not None:
        try:
            shutil.copy2(template, path)
            return
        except Exception:
            pass
    # Fallback: seed a minimal configuration
    path.write_text("{}", encoding="utf-8")


def run() -> int:
    """Create the Qt application and start the main event loop."""
    configure_logging()
    settings_path = _resolve_settings_path()
    _ensure_settings_file(settings_path)
    # Create the Qt application first to ensure any widget creation happens
    # after QApplication exists. Import GUI modules after app is created.
    app = QApplication(sys.argv)
    try:
        app.setApplicationName(APP_DISPLAY_NAME)
        app.setApplicationDisplayName(APP_DISPLAY_NAME)
    except Exception:
        pass
    from GUI.core.controller import ApplicationController as _ApplicationController
    from GUI.ui.main_window import MainWindow as _MainWindow
    global ApplicationController, MainWindow
    ApplicationController = _ApplicationController
    MainWindow = _MainWindow

    controller = ApplicationController(settings_path)
    # Applica un tema scuro di default (Fusion + palette custom)
    try:
        app.setStyle("Fusion")
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(53, 53, 53))
        palette.setColor(QPalette.WindowText, Qt.white)
        palette.setColor(QPalette.Base, QColor(35, 35, 35))
        palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
        palette.setColor(QPalette.ToolTipBase, Qt.white)
        palette.setColor(QPalette.ToolTipText, Qt.white)
        palette.setColor(QPalette.Text, Qt.white)
        palette.setColor(QPalette.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ButtonText, Qt.white)
        palette.setColor(QPalette.BrightText, Qt.red)
        palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.HighlightedText, Qt.black)
        app.setPalette(palette)
    except Exception:
        pass
    window = MainWindow(controller)
    window.show()
    controller.start()
    try:
        return app.exec()
    finally:
        controller.stop()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
