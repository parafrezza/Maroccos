from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class StartupMacDialog(QDialog):
    """Show known MAC addresses that can be used for wake-on-LAN."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MAC di avvio")
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["MAC address", "Nome player", "IP", "Ultimo visto"])
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setFocusPolicy(Qt.NoFocus)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_entries(self, entries: list[dict[str, Any]]) -> None:
        self._table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            name = entry.get("name") or ""
            ip = entry.get("ip") or ""
            mac = entry.get("mac") or ""
            last_seen = entry.get("last_seen")
            last_seen_text = ""
            try:
                ts = float(last_seen) if last_seen is not None else 0.0
                last_seen_text = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                last_seen_text = ""
            for col, text in enumerate((mac, name, ip, last_seen_text)):
                item = QTableWidgetItem(str(text))
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEditable)
                self._table.setItem(row, col, item)
