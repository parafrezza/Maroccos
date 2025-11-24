"""Sidebar widget listing players and exposing controls."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from GUI.services.player_registry import PlayerRecord
from GUI.ui.widgets.player_table import PlayerStatusTable


class PlayerPanel(QWidget):
    syncMediaRequested = Signal(str, int)
    """Display known players with refresh/selection controls."""

    refreshRequested = Signal()
    selectionChanged = Signal(list)
    deviceNameEdited = Signal(str, str)  # ip, new_name
    purgeRequested = Signal()
    vncRequested = Signal(str)
    toggleCollapseRequested = Signal()
    detachRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._collapsed = False

        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("Player rilevati"))
        title_row.addStretch(1)
        self._detach_button = QToolButton()
        self._detach_button.setText("↗")
        self._detach_button.setToolTip("Stacca elenco in finestra separata")
        self._detach_button.setCursor(Qt.PointingHandCursor)
        self._detach_button.setAutoRaise(True)
        self._detach_button.clicked.connect(lambda: self.detachRequested.emit())
        self._detach_button.setFixedSize(24, 24)
        title_row.addWidget(self._detach_button)
        self._collapse_button = QToolButton()
        self._collapse_button.setArrowType(Qt.DownArrow)
        self._collapse_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._collapse_button.setFixedSize(28, 28)
        self._collapse_button.setCursor(Qt.PointingHandCursor)
        self._collapse_button.setToolTip("Comprimi elenco player")
        self._collapse_button.setAutoRaise(True)
        self._collapse_button.clicked.connect(lambda: self.toggleCollapseRequested.emit())
        title_row.addWidget(self._collapse_button)
        layout.addLayout(title_row)
        self._controls_container = QWidget()
        controls_row = QHBoxLayout(self._controls_container)
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(6)
        self._refresh_button = QPushButton("Scansiona")
        self._refresh_button.clicked.connect(self.refreshRequested.emit)
        controls_row.addWidget(self._refresh_button)
        self._purge_button = QPushButton("Pulisci offline")
        self._purge_button.setToolTip("Rimuove dalla lista i player contrassegnati come offline")
        self._purge_button.clicked.connect(self.purgeRequested.emit)
        controls_row.addWidget(self._purge_button)
        self._sync_button = QPushButton("Sync media")
        self._sync_button.setToolTip("Scarica media per il player selezionato")
        self._sync_button.setEnabled(False)
        self._sync_button.clicked.connect(self._request_sync_media)
        controls_row.addWidget(self._sync_button)
        self._vnc_button = QPushButton("Apri VNC")
        self._vnc_button.setToolTip("Apre TigerVNC verso il player selezionato")
        self._vnc_button.setEnabled(False)
        self._vnc_button.clicked.connect(self._request_vnc)
        controls_row.addWidget(self._vnc_button)
        controls_row.addStretch(1)
        layout.addWidget(self._controls_container)

        self._table = PlayerStatusTable()
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.viewport().installEventFilter(self)
        layout.addWidget(self._table, 1)

        self.set_collapsed(False)
        self.set_detach_state(False)
        self._records: dict[str, PlayerRecord] = {}

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
            playlist_state=self._playlist_state_for(record),
            playlist_tip=self._playlist_tip_for(record),
        )
        self._records[record.ip] = record
        # Tooltip con identificativi sul nome
        try:
            parts: list[str] = []
            if getattr(record, "device_id", None):
                parts.append(f"device_id: {record.device_id}")
            if getattr(record, "instance_id", None):
                parts.append(f"instance_id: {record.instance_id}")
            tip = "\n".join(parts) if parts else ""
            if tip:
                self._table.set_name_tooltip(record.ip, tip)
        except Exception:
            pass
        self._update_sync_button_state()
        # Update status badge/highlight based on update stage
        try:
            stage = (record.update_stage or "").lower() if hasattr(record, "update_stage") else ""
            prog = record.update_progress if hasattr(record, "update_progress") else None
            elevated = bool(getattr(record, "update_elevated", False))
            backup_dir = getattr(record, "update_backup_dir", None)
            ip = record.ip
            highlight_applied = False
            if stage in {"planned", "downloading", "staging", "applying", "restarting"}:
                # Info color
                bg = "#e8f4ff"  # light blue
                fg = "#1b4f72"  # dark blue
                pct = f" {prog:.0f}%" if isinstance(prog, (int, float)) else ""
                badge = f"Aggiornamento: {stage}{pct}"
                extra = []
                if elevated:
                    extra.append("elevated")
                if backup_dir:
                    extra.append(f"backup: {backup_dir}")
                tip = badge + (" • " + " • ".join(extra) if extra else "")
                self._table.set_status_highlight(ip, background=bg, foreground=fg, tooltip=tip)
                highlight_applied = True
            elif stage == "error":
                msg = getattr(record, "update_error", None) or "errore sconosciuto"
                tip = f"Update fallito: {msg}"
                if backup_dir:
                    tip += f"\nBackup: {backup_dir}"
                self._table.set_status_highlight(ip, background="#ffe6e6", foreground="#c0392b", tooltip=tip)
                highlight_applied = True
            else:
                self._table.clear_status_highlight(record.ip)
            if not highlight_applied:
                media_missing = hasattr(record, "media_available") and not record.media_available
                playlist_warn = not getattr(record, "playlist_ready", True)
                tip_parts: list[str] = []
                if media_missing:
                    tip_parts.append("Cartella media vuota")
                pmiss = getattr(record, "playlist_missing", 0)
                pinv = getattr(record, "playlist_invalid", 0)
                if playlist_warn:
                    if pmiss or pinv:
                        tip_parts.append(f"Playlist incompleta (missing: {pmiss}, invalid: {pinv})")
                    else:
                        tip_parts.append("Playlist non pronta")
                tip = " • ".join(tip_parts)
                if media_missing or playlist_warn:
                    self._table.set_status_highlight(
                        ip, background="#fff3cd", foreground="#856404", tooltip=tip or "Media/playlist non pronti"
                    )
                else:
                    self._table.clear_status_highlight(ip)
        except Exception:
            # Best-effort UI; don't break updates due to UI decorators
            pass

    def _playlist_state_for(self, record: PlayerRecord) -> str:
        try:
            if record.state not in {"online"}:
                return "offline"
            if record.playlist_ready:
                expected = getattr(record, "expected_playlist_hash", None)
                if expected and record.playlist_hash and expected != record.playlist_hash:
                    return "partial"
                return "ready"
            return "dirty"
        except Exception:
            return "unknown"

    def _playlist_tip_for(self, record: PlayerRecord) -> str | None:
        try:
            if record.expected_playlist_hash and record.playlist_hash and record.expected_playlist_hash != record.playlist_hash:
                return f"Hash diverso: local {record.expected_playlist_hash[:8]} vs remote {record.playlist_hash[:8]}"
            if not record.playlist_ready:
                if record.playlist_missing or record.playlist_invalid:
                    return f"Playlist incompleta (missing: {record.playlist_missing}, invalid: {record.playlist_invalid})"
                return "Playlist non pronta"
            return "Playlist pronta"
        except Exception:
            return None

    def set_busy(self, busy: bool) -> None:
        self._refresh_button.setEnabled(not busy)
        if busy:
            self._vnc_button.setEnabled(False)

    def selected_ips(self) -> list[str]:
        return self._table.selected_ips()

    def clear_selection(self) -> None:
        self._table.clearSelection()

    def remove_player(self, ip: str) -> None:
        had_focus = ip in set(self.selected_ips())
        self._table.remove_record(ip)
        if had_focus:
            # Propaga la nuova selezione dopo la rimozione
            self.selectionChanged.emit(self.selected_ips())

    # --------------------------
    # Highlight helpers (version cell)
    # --------------------------
    def highlight_version_failure(self, ip: str, message: str) -> None:
        tip = f"Update fallito: {message}" if message else "Update fallito"
        self._table.set_version_highlight(ip, background="#ffe6e6", foreground="#c0392b", tooltip=tip)

    def clear_version_highlight(self, ip: str) -> None:
        self._table.clear_version_highlight(ip)

    def highlight_status_failure(self, ip: str, message: str) -> None:
        tip = f"Update fallito: {message}" if message else "Update fallito"
        self._table.set_status_highlight(ip, background="#ffe6e6", foreground="#c0392b", tooltip=tip)

    def clear_status_highlight(self, ip: str) -> None:
        self._table.clear_status_highlight(ip)

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

    def _on_selection_changed(self, *_args) -> None:
        self.selectionChanged.emit(self.selected_ips())
        self._update_sync_button_state()

    def _update_sync_button_state(self) -> None:
        ips = self.selected_ips()
        enabled = False
        if len(ips) == 1:
            record = self._records.get(ips[0])
            if record and not getattr(record, "media_available", True):
                enabled = True
        self._sync_button.setEnabled(enabled)

    def _request_sync_media(self) -> None:
        ips = self.selected_ips()
        if not ips:
            return
        record = self._records.get(ips[0])
        if not record:
            return
        port = int(record.port) if record.port else 8080
        self.syncMediaRequested.emit(record.ip, port)

    def set_vnc_enabled(self, enabled: bool) -> None:
        self._vnc_button.setEnabled(enabled)

    def _request_vnc(self) -> None:
        ips = self.selected_ips()
        if not ips:
            return
        self.vncRequested.emit(ips[0])

    def set_detach_state(self, detached: bool) -> None:
        icon = "↙" if detached else "↗"
        tooltip = (
            "Riaggancia elenco nella finestra principale"
            if detached
            else "Stacca elenco in finestra separata"
        )
        try:
            self._detach_button.setText(icon)
            self._detach_button.setToolTip(tooltip)
        except Exception:
            pass
        try:
            self._collapse_button.setVisible(not detached)
        except Exception:
            pass

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        arrow = Qt.RightArrow if self._collapsed else Qt.DownArrow
        self._collapse_button.setArrowType(arrow)
        self._collapse_button.setToolTip(
            "Espandi elenco player" if self._collapsed else "Comprimi elenco player"
        )
        should_show = not self._collapsed
        try:
            self._controls_container.setVisible(should_show)
        except Exception:
            pass
        try:
            self._table.setVisible(should_show)
        except Exception:
            pass

    def collapsed_width_hint(self) -> int:
        try:
            margins = self.layout().contentsMargins()
            horiz = margins.left() + margins.right()
        except Exception:
            horiz = 0
        base = self._collapse_button.sizeHint().width() + horiz + 16
        return max(72, base)

    # ------------------------------------------------------------------
    # Event filter to detect clicks on empty area and trigger collapse
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):  # noqa: D401
        if obj is self._table.viewport() and event.type() == QEvent.MouseButtonRelease:
            if getattr(event, "button", lambda: None)() == Qt.LeftButton:
                index = self._table.indexAt(event.pos())
                if not index.isValid():
                    self.toggleCollapseRequested.emit()
                    return True
        return super().eventFilter(obj, event)
