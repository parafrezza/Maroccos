from __future__ import annotations

from collections.abc import Generator
import os

import pytest

pytest.importorskip("PySide6", reason="PySide6 non disponibile nell'ambiente di test corrente")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from GUI.services.player_registry import PlayerRecord
from GUI.ui.panels.player_panel import PlayerPanel


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def panel(qapp: QApplication) -> Generator[PlayerPanel, None, None]:
    w = PlayerPanel()
    yield w
    w.deleteLater()


def _get_status_item(panel: PlayerPanel, ip: str):
    table = panel._table  # type: ignore[attr-defined]
    # Status column (index 6)
    for row in range(table.rowCount()):
        item_ip = table.item(row, 1)
        if item_ip and item_ip.text() == ip:
            return table.item(row, 6)
    return None


def test_status_badge_downloading_elevated(panel: PlayerPanel, qapp: QApplication) -> None:
    rec = PlayerRecord(name="P1", ip="10.0.0.1")
    rec.last_seen = rec.last_seen  # populated by default
    rec.status_text = "idle"
    rec.update_stage = "downloading"
    rec.update_progress = 42.3
    rec.update_elevated = True
    rec.update_backup_dir = "/opt/offplayer/.backup/20250101-120000"

    panel.update_player(rec)
    qapp.processEvents()

    item = _get_status_item(panel, rec.ip)
    assert item is not None
    # Test tooltip content includes stage, percent and elevated hint
    tip = item.toolTip() or ""
    assert "Aggiornamento: downloading 42%" in tip
    assert "elevated" in tip
    assert "backup:" in tip


def test_status_badge_error(panel: PlayerPanel, qapp: QApplication) -> None:
    rec = PlayerRecord(name="P2", ip="10.0.0.2")
    rec.status_text = "idle"
    rec.update_stage = "error"
    rec.update_error = "copy failed"

    panel.update_player(rec)
    qapp.processEvents()

    item = _get_status_item(panel, rec.ip)
    assert item is not None
    tip = item.toolTip() or ""
    assert "Update fallito" in tip
    # Background role should be set (we don't check exact color name as theme may vary)
    brush = item.data(Qt.BackgroundRole)
    assert brush is not None


def test_status_badge_restarting_and_planned(panel: PlayerPanel, qapp: QApplication) -> None:
    # Restarting: deve mostrare badge informativo (blu) e tooltip coerente
    rec1 = PlayerRecord(name="P3", ip="10.0.0.3")
    rec1.status_text = "idle"
    rec1.update_stage = "restarting"
    panel.update_player(rec1)
    qapp.processEvents()
    item1 = _get_status_item(panel, rec1.ip)
    assert item1 is not None
    tip1 = item1.toolTip() or ""
    assert "Aggiornamento: restarting" in tip1
    # Verifica che sia impostato un background (informativo)
    brush1 = item1.data(Qt.BackgroundRole)
    assert brush1 is not None

    # Planned: deve comparire come stato informativo senza percentuale
    rec2 = PlayerRecord(name="P4", ip="10.0.0.4")
    rec2.status_text = "idle"
    rec2.update_stage = "planned"
    panel.update_player(rec2)
    qapp.processEvents()
    item2 = _get_status_item(panel, rec2.ip)
    assert item2 is not None
    tip2 = item2.toolTip() or ""
    assert "Aggiornamento: planned" in tip2
    brush2 = item2.data(Qt.BackgroundRole)
    assert brush2 is not None


def test_update_player_does_not_emit_inline_name(panel: PlayerPanel, qapp: QApplication) -> None:
    emitted: list[tuple[str, str]] = []
    panel.deviceNameEdited.connect(lambda ip, name: emitted.append((ip, name)))

    rec = PlayerRecord(name="Pn", ip="10.0.1.10")
    panel.update_player(rec)
    qapp.processEvents()

    assert emitted == []


def test_inline_name_edit_debounced(panel: PlayerPanel, qapp: QApplication) -> None:
    emitted: list[tuple[str, str]] = []
    panel.deviceNameEdited.connect(lambda ip, name: emitted.append((ip, name)))

    rec = PlayerRecord(name="Px", ip="10.0.1.20")
    panel.update_player(rec)
    qapp.processEvents()

    table = panel._table  # type: ignore[attr-defined]
    item = table.item(0, 0)
    assert item is not None
    item.setText("Sala Principale")
    qapp.processEvents()

    # Debounce: nessun segnale finché il timer non scade
    assert emitted == []
    panel._emit_pending_name(rec.ip)
    qapp.processEvents()
    assert emitted == [(rec.ip, "Sala Principale")]
