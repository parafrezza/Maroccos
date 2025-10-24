"""Sidebar widget listing players and exposing controls."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from GUI.services.player_registry import PlayerRecord
from GUI.ui.widgets.player_table import PlayerStatusTable


class PlayerPanel(QWidget):
    """Display known players with refresh/selection controls."""

    refreshRequested = Signal()
    selectionChanged = Signal(list)
    deviceNameEdited = Signal(str, str)  # ip, new_name

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.addWidget(QLabel("Player rilevati"))
        header.addStretch(1)
        self._refresh_button = QPushButton("Scansiona")
        self._refresh_button.clicked.connect(self.refreshRequested.emit)
        header.addWidget(self._refresh_button)
        layout.addLayout(header)

        self._table = PlayerStatusTable()
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table, 1)

    def update_player(self, record: PlayerRecord) -> None:
        """Insert or refresh a player's row."""
        last_seen = datetime.fromtimestamp(record.last_seen).strftime("%H:%M:%S")
        self._table.upsert_record(
            name=record.name,
            ip=record.ip,
            state=record.state,
            last_seen=last_seen,
            version=record.version or "-",
            status_text=record.status_text or "",
        )

    def set_busy(self, busy: bool) -> None:
        self._refresh_button.setEnabled(not busy)

    def selected_ips(self) -> list[str]:
        return self._table.selected_ips()

    def clear_selection(self) -> None:
        self._table.clearSelection()

    def _on_selection_changed(self, *_args) -> None:
        self.selectionChanged.emit(self.selected_ips())

    def _on_item_changed(self, item) -> None:
        # Intercetta modifiche al nome (colonna 0) e propaga richiesta set name
        if not item or item.column() != 0:
            return
        row = item.row()
        ip_item = self._table.item(row, 1)
        if not ip_item:
            return
        new_name = (item.text() or "").strip()
        ip = ip_item.text() if ip_item else ""
        if not ip or not new_name:
            return
        self.deviceNameEdited.emit(ip, new_name)
