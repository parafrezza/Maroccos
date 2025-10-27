"""Table widget that displays player status information."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from GUI.ui.widgets.status_indicator import StatusIndicator


_COLUMNS = ("Name", "IP", "State", "Last Seen", "Version", "Status")


class PlayerStatusTable(QTableWidget):
    """Table with custom LED indicators for player presence."""

    def __init__(self) -> None:
        super().__init__(0, len(_COLUMNS))
        self.setHorizontalHeaderLabels(_COLUMNS)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        # Consenti editing solo sulla colonna Nome (0)
        self.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.setAlternatingRowColors(True)
        # Auto-layout: mantieni tutte le colonne visibili senza scrollbar orizzontale
        # - Colonna 2 (State/LED) dimensione fissa
        # - Le altre colonne si distribuiscono in proporzione al viewport
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Fixed)
        header.setStretchLastSection(False)

    def upsert_record(self, *, name: str, ip: str, state: str, last_seen: str, version: str, status_text: str | None = None) -> None:
        """Insert or update the row for a player."""
        row = self._find_row(ip)
        if row is None:
            row = self.rowCount()
            self.insertRow(row)
            indicator = StatusIndicator()
            self.setCellWidget(row, 2, indicator)
        else:
            indicator = self.cellWidget(row, 2)
            if not isinstance(indicator, StatusIndicator):
                indicator = StatusIndicator()
                self.setCellWidget(row, 2, indicator)

        self._set_item(row, 0, name or "-")
        self._set_item(row, 1, ip)
        indicator.set_state(state)
        self._set_item(row, 3, last_seen)
        self._set_item(row, 4, version)
        self._set_item(row, 5, status_text or "")
        # Re-alloca larghezze dopo aggiornamento contenuto
        self._auto_resize_columns()

    def _find_row(self, ip: str) -> int | None:
        for row in range(self.rowCount()):
            item = self.item(row, 1)
            if item and item.text() == ip:
                return row
        return None

    def _set_item(self, row: int, column: int, value: str) -> None:
        item = self.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            self.setItem(row, column, item)
        item.setText(value)
        # Rendi solo la prima colonna (Nome) modificabile
        flags = item.flags()
        if column == 0:
            item.setFlags(flags | Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        else:
            # rimuovi editabilità per altre colonne
            item.setFlags((flags | Qt.ItemIsEnabled | Qt.ItemIsSelectable) & ~Qt.ItemIsEditable)

    def selected_ips(self) -> list[str]:
        selected = []
        for index in self.selectionModel().selectedRows(1):
            selected.append(index.data())
        return selected

    # --------------------------
    # Layout helpers
    # --------------------------
    def resizeEvent(self, event):  # noqa: D401
        super().resizeEvent(event)
        self._auto_resize_columns()

    def _auto_resize_columns(self) -> None:
        if self.columnCount() != len(_COLUMNS):
            return
        viewport_w = max(0, self.viewport().width())
        # Colonna LED/State (index 2): fissa
        led_w = 28
        self.setColumnWidth(2, led_w)
        # Spazio residuo
        avail = max(0, viewport_w - led_w)
        if avail <= 0:
            return
        # Pesi: Name(0)=2, IP(1)=1, Last Seen(3)=1, Version(4)=1, Status(5)=2
        weights = {0: 2, 1: 1, 3: 1, 4: 1, 5: 2}
        total = sum(weights.values())
        # Calcola larghezze proporzionali
        widths = {i: int(avail * weights[i] / total) for i in weights}
        # Imposta larghezze
        for i, w in widths.items():
            self.setColumnWidth(i, max(60 if i in (0, 5) else 40, w))
        # Garantisce che non compaia scrollbar orizzontale per piccoli arrotondamenti
        remainder = avail - sum(widths.values())
        if remainder > 0:
            # Aggiungi il resto alla colonna Name
            self.setColumnWidth(0, self.columnWidth(0) + remainder)
