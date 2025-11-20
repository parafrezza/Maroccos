"""Entry point for the Morocco player management GUI."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ is None:  # pragma: no cover - allow "python GUI/main.py"
    sys.path.append(str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette, QColor
from PySide6.QtCore import Qt

from GUI.core.logger import configure_logging
from GUI.app_meta import APP_DISPLAY_NAME
# Defer importing GUI widgets/controllers until after QApplication is created
ApplicationController = None
MainWindow = None


def run() -> int:
    """Create the Qt application and start the main event loop."""
    configure_logging()
    settings_path = Path(__file__).resolve().parent / "settings.json"
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
