from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 non disponibile nell'ambiente di test corrente")

from PySide6.QtWidgets import QApplication

from GUI.core.controller import ApplicationController
from GUI.ui.main_window import MainWindow
from GUI.services.player_registry import PlayerRecord


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def main_window(qapp: QApplication, tmp_path: Path) -> MainWindow:
    settings_path = tmp_path / "settings.json"
    controller = ApplicationController(settings_path)
    win = MainWindow(controller)
    yield win
    win.deleteLater()
    controller._executor.shutdown(wait=False)


def _mk_player(ip: str) -> PlayerRecord:
    return PlayerRecord(name="P1", ip=ip, port=8080)


def test_main_window_receives_display_center_headless(main_window: MainWindow, qapp: QApplication) -> None:
    player = _mk_player("10.0.0.1")
    main_window._selected_players = [player]
    # headless style: top-level display_center dict
    payload = {"display_center": {"enabled": True}}
    main_window._controller.statusReceived.emit(player.ip, payload)
    qapp.processEvents()
    assert main_window._commands_tab._display_center_checkbox.isChecked()


def test_main_window_receives_display_center_off(main_window: MainWindow, qapp: QApplication) -> None:
    player = _mk_player("10.0.0.2")
    main_window._selected_players = [player]
    # off style: nested player.centerVideo
    payload = {"player": {"centerVideo": True}}
    main_window._controller.statusReceived.emit(player.ip, payload)
    qapp.processEvents()
    assert main_window._commands_tab._display_center_checkbox.isChecked()
