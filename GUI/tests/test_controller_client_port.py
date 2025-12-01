from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 non disponibile nell'ambiente di test corrente")

from PySide6.QtWidgets import QApplication

from GUI.core.controller import ApplicationController
from GUI.services.player_registry import PlayerRecord


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_client_for_prefers_player_port(qapp: QApplication, tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    controller = ApplicationController(settings_path)
    controller.state.config.network.player_port = 8080

    player_with_port = PlayerRecord(name="P1", ip="10.0.0.1", port=9001)
    client = controller._client_for(player_with_port)
    assert client.base_url == "http://10.0.0.1:9001"

    player_no_port = PlayerRecord(name="P2", ip="10.0.0.2")
    client_default = controller._client_for(player_no_port)
    assert client_default.base_url == "http://10.0.0.2:8080"

    controller._executor.shutdown(wait=False)
