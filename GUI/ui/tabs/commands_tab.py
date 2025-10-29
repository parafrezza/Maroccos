"""Command tab providing playback, system commands, and media tools."""

from __future__ import annotations

from typing import Any
import json

from PySide6.QtCore import Qt, Signal, QTimer, QDateTime, QMimeData
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QDialogButtonBox,
    QDateTimeEdit,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# Custom DnD MIME for media items dragged from the media list to the playlist
_MEDIA_MIME = "application/x-maroccos-mediaitems"
_RELATIVE_ROLE = Qt.UserRole + 1


class MediaListWidget(QListWidget):
    """Source list that provides custom MIME payload for external drag."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setToolTip("Trascina uno o più media nella playlist a destra")

    def mimeTypes(self) -> list[str]:  # type: ignore[override]
        return [_MEDIA_MIME]

    def mimeData(self, items: list[QListWidgetItem]) -> QMimeData:  # type: ignore[override]
        md = QMimeData()
        payload: list[dict[str, Any]] = []
        for it in items:
            try:
                rel = it.data(_RELATIVE_ROLE) or it.data(Qt.UserRole)
            except Exception:
                rel = None
            payload.append({"label": it.text(), "rel": rel})
        try:
            raw = json.dumps(payload).encode("utf-8")
        except Exception:
            raw = b"[]"
        md.setData(_MEDIA_MIME, raw)
        return md


class PlaylistWidget(QListWidget):
    """Target list that accepts media items drops and allows internal reordering."""

    itemsAdded = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(_MEDIA_MIME):
            self.setStyleSheet("background-color: #d0f0ff; border: 2px dashed #2980b9;")
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(_MEDIA_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        self.setStyleSheet("")
        if event.mimeData().hasFormat(_MEDIA_MIME):
            try:
                data = bytes(event.mimeData().data(_MEDIA_MIME))
                items = json.loads(data.decode("utf-8", errors="ignore"))
            except Exception:
                items = []
            added: list[str] = []
            for ent in items or []:
                try:
                    label = ent.get("label") or "(sconosciuto)"
                    rel = ent.get("rel") or label
                except Exception:
                    label = "(sconosciuto)"
                    rel = label
                it = QListWidgetItem(label)
                it.setData(Qt.UserRole, rel)
                self.addItem(it)
                added.append(str(rel))
            if added:
                self.itemsAdded.emit(added)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet("")
        super().dragLeaveEvent(event)


class CommandsTab(QWidget):
    """Expose playback commands, media upload helpers, and system actions."""

    playbackTriggered = Signal(str, dict)
    miscCommandTriggered = Signal(str, dict)
    uploadRequested = Signal(str)
    mediaDirectoryRequested = Signal()
    deviceMediaRefreshRequested = Signal()
    # Playlist signals
    playlistChanged = Signal(list)
    playlistPushRequested = Signal(list, bool)
    startShowRequested = Signal(object)
    # Live CVLC log
    logLiveToggleRequested = Signal(bool, int)

    _RELATIVE_ROLE = Qt.UserRole + 1

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QGridLayout(self)

        self._targets_enabled = False
        self._playback_buttons: list[QPushButton] = []

        playback_box = QGroupBox("Playback")
        playback_layout = QGridLayout(playback_box)
        self._loop_checkbox = QCheckBox("Loop riproduzione")
        self._fade_seconds = QDoubleSpinBox()
        self._fade_seconds.setRange(0.0, 10.0)
        self._fade_seconds.setSingleStep(0.1)
        self._fade_seconds.setValue(1.0)
        self._ping_duration = QSpinBox()
        self._ping_duration.setRange(10, 5000)
        self._ping_duration.setSingleStep(50)
        self._ping_duration.setValue(200)

        playback_defs = [
            {"label": "Play", "command": "play", "use_media": True},
            {"label": "Pause", "command": "pause"},
            {"label": "Resume", "command": "resume"},
            {"label": "Stop", "command": "stop", "use_fade": True},
            {"label": "Next", "command": "next"},
            {"label": "Prev", "command": "prev"},
            {"label": "Fade In", "command": "fade_in", "use_fade": True},
            {"label": "Fade Out", "command": "fade_out", "use_fade": True},
            {"label": "FTB", "command": "ftb", "use_fade": True},
            {"label": "Ping", "command": "ping", "use_ping": True},
        ]

        for index, meta in enumerate(playback_defs):
            button = QPushButton(meta["label"])
            button.clicked.connect(lambda _=False, data=meta: self._handle_playback_button(data))
            self._playback_buttons.append(button)
            row = index // 3
            col = index % 3
            playback_layout.addWidget(button, row, col)

        controls_row = (len(playback_defs) + 2) // 3
        controls = QHBoxLayout()
        controls.addWidget(self._loop_checkbox)
        controls.addSpacing(12)
        controls.addWidget(QLabel("Fade (s)"))
        controls.addWidget(self._fade_seconds)
        controls.addSpacing(12)
        controls.addWidget(QLabel("Ping (ms)"))
        controls.addWidget(self._ping_duration)
        controls.addSpacing(12)
        # Schedule controls
        self._schedule_checkbox = QCheckBox("Schedula alle")
        self._schedule_dt = QDateTimeEdit()
        self._schedule_dt.setDisplayFormat("yyyy-MM-dd HH:mm:ss.zzz")
        self._schedule_dt.setCalendarPopup(True)
        controls.addWidget(self._schedule_checkbox)
        controls.addWidget(self._schedule_dt)
        controls.addStretch(1)
        playback_layout.addLayout(controls, controls_row, 0, 1, 3)
        # Action badge row (shows short-lived action like NEXT/PREV/PLAY)
        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("Azione:"))
        self._action_badge = QLabel("—")
        self._action_badge.setVisible(False)
        # pill style
        self._action_badge.setStyleSheet(
            "padding: 2px 8px; border-radius: 9px; background-color: #34495e; color: white; font-weight: 600;"
        )
        action_row.addWidget(self._action_badge)
        action_row.addStretch(1)
        playback_layout.addLayout(action_row, controls_row + 1, 0, 1, 3)
        layout.addWidget(playback_box, 0, 0)

        misc_box = QGroupBox("Altri Comandi")
        misc_layout = QVBoxLayout(misc_box)
        self._command_selector = QComboBox()
        self._misc_entries = self._build_misc_entries()
        self._command_selector.addItem("Seleziona comando…", None)
        for entry in self._misc_entries:
            self._command_selector.addItem(entry["label"], entry)
        self._command_selector.currentIndexChanged.connect(
            lambda _: self._update_misc_button(self._targets_enabled)
        )
        misc_layout.addWidget(self._command_selector)
        self._execute_button = QPushButton("Esegui")
        self._execute_button.clicked.connect(self._emit_misc_command)
        misc_layout.addWidget(self._execute_button)
        layout.addWidget(misc_box, 1, 0)

    # Overlay / Fast-Start / Test controls
        aux_box = QGroupBox("Overlay, Fast-Start e Test")
        aux_layout = QVBoxLayout(aux_box)

        # Timing row (LED + text)
        timing_row = QHBoxLayout()
        timing_row.addWidget(QLabel("Timing:"))
        self._timing_led = QLabel()
        self._timing_led.setFixedSize(12, 12)
        self._timing_led.setStyleSheet("background-color: #AAAAAA; border-radius: 6px;")
        self._timing_text = QLabel("—")
        timing_row.addWidget(self._timing_led)
        timing_row.addSpacing(6)
        timing_row.addWidget(self._timing_text)
        timing_row.addStretch(1)
        aux_layout.addLayout(timing_row)

        # Overlay controls
        overlay_row = QHBoxLayout()
        overlay_row.addWidget(QLabel("Overlay:"))
        self._overlay_show = QPushButton("Mostra")
        self._overlay_hide = QPushButton("Nascondi")
        self._overlay_fade = QPushButton("Fade (usa durata)")
        self._overlay_fade_at = QPushButton("Fade at…")
        self._overlay_show.clicked.connect(lambda _=False: self._emit_overlay_command("overlay_show"))
        self._overlay_hide.clicked.connect(lambda _=False: self._emit_overlay_command("overlay_hide"))
        self._overlay_fade.clicked.connect(lambda _=False: self._emit_overlay_command("overlay_fade", use_fade=True))
        self._overlay_fade_at.clicked.connect(self._emit_overlay_fade_at_dialog)
        overlay_row.addWidget(self._overlay_show)
        overlay_row.addWidget(self._overlay_hide)
        overlay_row.addWidget(self._overlay_fade)
        overlay_row.addWidget(self._overlay_fade_at)
        overlay_row.addStretch(1)
        aux_layout.addLayout(overlay_row)

        # Fast-start controls
        fs_row = QHBoxLayout()
        fs_row.addWidget(QLabel("Fast-Start:"))
        self._fs_prepare = QPushButton("Prepare (usa media selezionato)")
        self._fs_go = QPushButton("Go")
        self._fs_prepare.clicked.connect(lambda _=False: self._emit_faststart_prepare())
        self._fs_go.clicked.connect(lambda _=False: self._emit_faststart_go())
        fs_row.addWidget(self._fs_prepare)
        fs_row.addWidget(self._fs_go)
        # Indicazione sorgente usata per il Prepare
        fs_row.addSpacing(8)
        fs_row.addWidget(QLabel("Sorgente:"))
        self._fs_source = QLabel("—")
        fs_row.addWidget(self._fs_source)
        fs_row.addStretch(1)
        aux_layout.addLayout(fs_row)

        # Scheduled Play control (Play at…)
        playat_row = QHBoxLayout()
        playat_row.addWidget(QLabel("Play at:"))
        self._play_at = QPushButton("Play at… (usa media selezionato)")
        self._play_at.clicked.connect(self._emit_play_at_dialog)
        playat_row.addWidget(self._play_at)
        playat_row.addStretch(1)
        aux_layout.addLayout(playat_row)

        # Test mode controls
        test_row = QHBoxLayout()
        test_row.addWidget(QLabel("Test Mode:"))
        self._test_on = QPushButton("ON")
        self._test_off = QPushButton("OFF")
        self._test_on.clicked.connect(lambda _=False: self._emit_misc_simple("test_on"))
        self._test_off.clicked.connect(lambda _=False: self._emit_misc_simple("test_off"))
        test_row.addWidget(self._test_on)
        test_row.addWidget(self._test_off)
        test_row.addStretch(1)
        aux_layout.addLayout(test_row)

        layout.addWidget(aux_box, 2, 0)

        # Live CVLC log box
        logs_box = QGroupBox("Log CVLC (live)")
        logs_layout = QVBoxLayout(logs_box)
        logs_controls = QHBoxLayout()
        self._log_live_button = QPushButton("Start Live")
        self._log_live_button.setCheckable(True)
        self._log_live_button.toggled.connect(self._emit_log_live_toggle)
        self._log_live_lines = QSpinBox()
        self._log_live_lines.setRange(0, 2000)
        self._log_live_lines.setValue(100)
        logs_controls.addWidget(self._log_live_button)
        logs_controls.addSpacing(8)
        logs_controls.addWidget(QLabel("Righe iniziali:"))
        logs_controls.addWidget(self._log_live_lines)
        logs_controls.addStretch(1)
        self._log_live_clear = QPushButton("Pulisci")
        self._log_live_clear.clicked.connect(lambda _=False: self._cvlc_log_view.clear())
        logs_controls.addWidget(self._log_live_clear)
        logs_layout.addLayout(logs_controls)
        self._cvlc_log_view = QTextEdit()
        self._cvlc_log_view.setReadOnly(True)
        self._cvlc_log_view.setMinimumHeight(120)
        logs_layout.addWidget(self._cvlc_log_view)
        layout.addWidget(logs_box, 3, 0)

        media_box = QGroupBox("Media disponibili")
        media_layout = QVBoxLayout(media_box)
        choose_row = QHBoxLayout()
        self._choose_dir_button = QPushButton("Scegli cartella…")
        self._choose_dir_button.clicked.connect(self.mediaDirectoryRequested.emit)
        choose_row.addWidget(self._choose_dir_button)
        choose_row.addStretch(1)
        media_layout.addLayout(choose_row)
        # Media list with external drag enabled
        self._media_list = MediaListWidget()
        self._media_list.itemSelectionChanged.connect(self._on_media_selection_changed)
        media_layout.addWidget(self._media_list)
        self._upload_button = QPushButton("Upload")
        self._upload_button.setEnabled(False)
        self._upload_button.clicked.connect(self._emit_upload)
        media_layout.addWidget(self._upload_button)
        layout.addWidget(media_box, 0, 1)

        # Media presenti sul device
        device_box = QGroupBox("Media sul device")
        device_layout = QVBoxLayout(device_box)
        dev_controls = QHBoxLayout()
        self._device_refresh = QPushButton("Aggiorna")
        self._device_refresh.clicked.connect(self.deviceMediaRefreshRequested.emit)
        self._device_clear = QPushButton("Svuota media")
        self._device_clear.clicked.connect(lambda _=False: self.miscCommandTriggered.emit("media_clear", {}))
        self._device_auto = QCheckBox("Auto-refresh")
        self._device_auto.setChecked(True)
        dev_controls.addWidget(self._device_refresh)
        dev_controls.addWidget(self._device_clear)
        dev_controls.addSpacing(8)
        dev_controls.addWidget(self._device_auto)
        dev_controls.addStretch(1)
        device_layout.addLayout(dev_controls)

        self._device_media_list = MediaListWidget()
        # Permetti la selezione singola per scegliere il media per fast-start
        self._device_media_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._device_media_list.itemSelectionChanged.connect(self._update_faststart_enabled)
        self._device_media_list.setToolTip("Trascina i media nella playlist per aggiungerli")
        device_layout.addWidget(self._device_media_list)
        layout.addWidget(device_box, 1, 1)

        layout.setColumnStretch(1, 1)
        # Blink timer for timing LED (off by default)
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(400)
        self._blink_timer.timeout.connect(self._toggle_led_blink)
        self._blinking = False
        self._blink_state = False
        # Auto-hide timer for action badge
        self._action_timer = QTimer(self)
        self._action_timer.setSingleShot(True)
        self._action_timer.timeout.connect(lambda: self._action_badge.setVisible(False))

        # Playlist box
        playlist_box = QGroupBox("Playlist")
        playlist_layout = QVBoxLayout(playlist_box)
        help_row = QHBoxLayout()
        help_row.addWidget(QLabel("Trascina dalla lista media qui sotto; riordina con drag&drop"))
        help_row.addStretch(1)
        playlist_layout.addLayout(help_row)

        # Comandi rapidi
        quick_row = QHBoxLayout()
        self._add_to_playlist = QPushButton("Aggiungi selezionato →")
        self._add_to_playlist.clicked.connect(self._add_selected_to_playlist)
        quick_row.addWidget(self._add_to_playlist)
        self._remove_from_playlist = QPushButton("Rimuovi")
        self._remove_from_playlist.clicked.connect(self._remove_selected_from_playlist)
        quick_row.addWidget(self._remove_from_playlist)
        quick_row.addStretch(1)
        playlist_layout.addLayout(quick_row)

        # Playlist accepts drops from media list and allows internal reorder
        self._playlist = PlaylistWidget()
        # Riordino interno tramite drag&drop
        self._playlist.setDragDropMode(QAbstractItemView.InternalMove)
        self._playlist.itemsAdded.connect(lambda _items: self._emit_playlist_changed())
        self._playlist.model().rowsMoved.connect(self._emit_playlist_changed)
        playlist_layout.addWidget(self._playlist)

        bottom_row = QHBoxLayout()
        self._push_playlist = QPushButton("Push Playlist")
        self._push_playlist.clicked.connect(self._emit_push_playlist)
        self._push_playlist.setEnabled(False)
        bottom_row.addWidget(self._push_playlist)
        # Clear-before-push option
        self._clear_before_push = QCheckBox("Svuota media sui device prima del push")
        bottom_row.addWidget(self._clear_before_push)
        bottom_row.addSpacing(8)
        bottom_row.addWidget(QLabel("Stato:"))
        self._playlist_led = QLabel()
        self._playlist_led.setFixedSize(14, 14)
        self._set_playlist_led_color("gray")
        bottom_row.addWidget(self._playlist_led)
        # Start Show controls
        bottom_row.addSpacing(16)
        self._start_show = QPushButton("START SHOW")
        # Red prominent style
        self._start_show.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold;")
        self._start_show.clicked.connect(self._emit_start_show)
        bottom_row.addWidget(self._start_show)
        # Show running LED
        bottom_row.addSpacing(8)
        bottom_row.addWidget(QLabel("Show:"))
        self._show_led = QLabel()
        self._show_led.setFixedSize(14, 14)
        self._set_show_led(False)
        bottom_row.addWidget(self._show_led)
        # Countdown and total TC
        bottom_row.addSpacing(12)
        self._countdown_label = QLabel("T- —")
        self._total_tc_label = QLabel("Totale: —")
        bottom_row.addWidget(self._countdown_label)
        bottom_row.addSpacing(6)
        bottom_row.addWidget(self._total_tc_label)
        bottom_row.addStretch(1)
        playlist_layout.addLayout(bottom_row)
        # Banner row under playlist controls
        banner_row = QHBoxLayout()
        self._banner_label = QLabel("")
        self._banner_label.setStyleSheet("color: #333333;")
        banner_row.addWidget(self._banner_label)
        banner_row.addStretch(1)
        playlist_layout.addLayout(banner_row)

        layout.addWidget(playlist_box, 2, 1)
        # durations map for computing total TC
        self._media_durations: dict[str, float] = {}

        # Stato iniziale dei controlli Fast-Start
        self._update_faststart_enabled()

    # -----------------------------
    # Live CVLC log UI helpers
    # -----------------------------
    def _emit_log_live_toggle(self, checked: bool) -> None:
        # Emit request with current lines value
        try:
            lines = int(self._log_live_lines.value())
        except Exception:
            lines = 50
        self.logLiveToggleRequested.emit(bool(checked), int(lines))

    def set_log_live_active(self, active: bool) -> None:
        # Reflect actual connection state
        self._log_live_button.blockSignals(True)
        try:
            self._log_live_button.setChecked(bool(active))
            self._log_live_button.setText("Stop Live" if active else "Start Live")
        finally:
            self._log_live_button.blockSignals(False)

    def append_log_live_line(self, text: str) -> None:
        try:
            self._cvlc_log_view.append(text)
        except Exception:
            pass

    def is_log_live_active(self) -> bool:
        try:
            return bool(self._log_live_button.isChecked())
        except Exception:
            return False

    # -----------------------------
    # Device media API
    # -----------------------------
    def set_device_media_items(self, items: list[dict]) -> None:
        """Aggiorna la lista dei media presenti sul device selezionato.
        items attesi come lista di dict: {name, size, modified, type}
        """
        self._device_media_list.clear()
        for it in (items or []):
            try:
                name = str(it.get("name") or it.get("path") or "?")
                size = float(it.get("size", 0.0))
                mb = size / (1024 * 1024)
                label = f"{name}  ({mb:.1f} MB)"
            except Exception:
                label = str(it)
            item = QListWidgetItem(label)
            # Conserva il nome file come payload per fast-start
            try:
                item.setData(Qt.UserRole, name)
            except Exception:
                pass
            self._device_media_list.addItem(item)

    def wants_device_auto_refresh(self) -> bool:
        try:
            return bool(self._device_auto.isChecked())
        except Exception:
            return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_targets_selected(self, enabled: bool) -> None:
        self._targets_enabled = enabled
        for button in self._playback_buttons:
            button.setEnabled(enabled)
        self._update_misc_button(enabled)
        self._update_upload_button(enabled)
        # Playlist controls
        self._add_to_playlist.setEnabled(enabled)
        self._remove_from_playlist.setEnabled(enabled)
        self._push_playlist.setEnabled(enabled and self._playlist.count() > 0)
        # Toggle auxiliary controls
        for btn in (
            self._overlay_show,
            self._overlay_hide,
            self._overlay_fade,
            self._overlay_fade_at,
            self._fs_prepare,
            self._fs_go,
            self._play_at,
            self._test_on,
            self._test_off,
        ):
            btn.setEnabled(enabled)

    def set_media_items(self, items: list[dict]) -> None:
        self._media_list.clear()
        for item in items:
            list_item = QListWidgetItem(item["label"])
            list_item.setData(Qt.UserRole, item["path"])
            list_item.setData(self._RELATIVE_ROLE, item.get("relative"))
            # store duration mapping for total TC (keyed by relative or basename)
            if item.get("relative") and isinstance(item.get("duration"), (int, float)):
                self._media_durations[str(item["relative"])] = float(item["duration"])
            elif isinstance(item.get("duration"), (int, float)):
                from pathlib import Path as _Path
                self._media_durations[_Path(item["path"]).name] = float(item["duration"])
            self._media_list.addItem(list_item)
        if self._media_list.count() > 0:
            self._media_list.setCurrentRow(0)
        self._update_upload_button(self._targets_enabled)
        # Playlist: abilita Aggiungi se ci sono elementi e target
        self._add_to_playlist.setEnabled(self._targets_enabled and self._media_list.count() > 0)
        # update total TC after media refresh
        self._update_total_tc()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_misc_entries(self) -> list[dict[str, Any]]:
        return [
            {"label": "Sistema: Mostra splash", "command": "show_splash"},
            {"label": "Sistema: Nascondi splash", "command": "hide_splash"},
            {
                "label": "Sistema: Riavvia servizio player",
                "command": "service_restart",
                "confirm": "Riavviare il servizio headless-player sui device selezionati?",
            },
            {
                "label": "Sistema: Shutdown",
                "command": "shutdown",
                "confirm": "Spegnere i player selezionati?",
            },
            {
                "label": "Sistema: Reboot",
                "command": "reboot",
                "confirm": "Riavviare i player selezionati?",
            },
            {
                "label": "Sistema: Check spazio disco",
                "command": "disk_status",
            },
            {
                "label": "Manutenzione: Esegui setup.sh",
                "command": "run_setup",
            },
            {
                "label": "Manutenzione: Fix permissions",
                "command": "fix_permissions",
            },
            {
                "label": "Media: Cancella cartella media",
                "command": "media_clear",
                "confirm": "Cancellare tutti i file nella cartella media del player?",
            },
            {
                "label": "Logs: Mostra CVLC (ultime 200 righe)",
                "command": "logs_cvlc",
                "defaults": {"lines": 200},
            },
            {
                "label": "Media: Crea playlist automatica",
                "command": "playlist_build",
                "loop_checkbox": True,
            },
            {
                "label": "Playlist: Loop ON",
                "command": "playlist_loop_on",
            },
            {
                "label": "Playlist: Loop OFF",
                "command": "playlist_loop_off",
            },
            {
                "label": "Playback: Loop ON",
                "command": "loop_on",
            },
            {
                "label": "Playback: Loop OFF",
                "command": "loop_off",
            },
            {
                "label": "Download: Scarica asset da URL…",
                "command": "download_asset",
                "input": {"prompt": "Inserisci URL dell'asset", "key": "url"},
            },
        ]

    def _handle_playback_button(self, meta: dict[str, Any]) -> None:
        if not self._targets_enabled:
            return
        command = meta["command"]
        payload: dict[str, Any] = {}
        if meta.get("use_media"):
            item = self._media_list.currentItem()
            if item:
                relative = item.data(self._RELATIVE_ROLE)
                absolute = item.data(Qt.UserRole)
                if relative:
                    payload["filename"] = relative
                elif absolute:
                    payload["path"] = absolute
            payload["loop"] = self._loop_checkbox.isChecked()
        if meta.get("use_fade"):
            payload["seconds"] = float(self._fade_seconds.value())
        if meta.get("use_ping"):
            payload["duration_ms"] = int(self._ping_duration.value())
        # Schedule at time
        if self._schedule_checkbox.isChecked():
            in_time = self._compute_in_time_epoch_seconds()
            if in_time is not None:
                payload["in_time"] = in_time

        # Feedback se next a fine playlist
        if command == "next":
            # Se la playlist è vuota o siamo già all'ultimo elemento
            if self._playlist.count() == 0 or self._playlist.currentRow() >= self._playlist.count() - 1:
                self.set_banner("Fine playlist: nessun elemento successivo", level="info")
                return

        self.playbackTriggered.emit(command, payload)

    def _emit_misc_command(self) -> None:
        if not self._targets_enabled:
            return
        entry = self._command_selector.currentData()
        if not entry:
            return
        payload: dict[str, Any] = {}
        if prompt := entry.get("input"):
            text, ok = QInputDialog.getText(self, entry["label"], prompt["prompt"])
            if not ok:
                return
            value = text.strip()
            if not value:
                QMessageBox.information(self, "Parametro mancante", "Valore obbligatorio")
                return
            payload[prompt["key"]] = value
        if confirm := entry.get("confirm"):
            answer = QMessageBox.question(self, "Conferma", confirm, QMessageBox.Yes | QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        if entry.get("loop_checkbox"):
            payload["loop"] = self._loop_checkbox.isChecked()
        payload.update(entry.get("defaults", {}))
        self.miscCommandTriggered.emit(entry["command"], payload)

    def _emit_misc_simple(self, command: str) -> None:
        if not self._targets_enabled:
            return
        self.miscCommandTriggered.emit(command, {})

    def _emit_overlay_command(self, command: str, *, use_fade: bool = False) -> None:
        if not self._targets_enabled:
            return
        payload: dict[str, Any] = {}
        if use_fade:
            payload["seconds"] = float(self._fade_seconds.value())
        if self._schedule_checkbox.isChecked():
            in_time = self._compute_in_time_epoch_seconds()
            if in_time is not None:
                payload["in_time"] = in_time
        self.miscCommandTriggered.emit(command, payload)

    def _emit_faststart_prepare(self) -> None:
        if not self._targets_enabled:
            return
        payload: dict[str, Any] = {}
        # Preferisci il media selezionato sul device
        dev_item = self._device_media_list.currentItem()
        if dev_item:
            try:
                dev_name = dev_item.data(Qt.UserRole)
                if isinstance(dev_name, str) and dev_name:
                    payload["filename"] = dev_name
            except Exception:
                pass
        # Fallback: media locale
        if not payload:
            item = self._media_list.currentItem()
            if item:
                relative = item.data(self._RELATIVE_ROLE)
                absolute = item.data(Qt.UserRole)
                if relative:
                    payload["filename"] = relative
                elif absolute:
                    payload["path"] = absolute
        self.miscCommandTriggered.emit("faststart_prepare", payload)

    def _emit_faststart_go(self) -> None:
        if not self._targets_enabled:
            return
        payload: dict[str, Any] = {}
        if self._schedule_checkbox.isChecked():
            in_time = self._compute_in_time_epoch_seconds()
            if in_time is not None:
                payload["in_time"] = in_time
        self.miscCommandTriggered.emit("faststart_go", payload)

    def _emit_play_at_dialog(self) -> None:
        if not self._targets_enabled:
            return
        at_value = self._open_schedule_dialog(title="Play at…")
        if at_value is None:
            return
        import time as _time
        # Se il valore è > 1000000000 assumiamo sia epoch, altrimenti secondi relativi
        if at_value > 1000000000:
            in_time = at_value
        else:
            in_time = _time.time() + at_value
        payload: dict[str, Any] = {"in_time": in_time}
        # Include selected media if any
        item = self._media_list.currentItem()
        if item:
            relative = item.data(self._RELATIVE_ROLE)
            absolute = item.data(Qt.UserRole)
            if relative:
                payload["filename"] = relative
            elif absolute:
                payload["path"] = absolute
        self.miscCommandTriggered.emit("play_at", payload)

    def _emit_overlay_fade_at_dialog(self) -> None:
        if not self._targets_enabled:
            return
        at_value = self._open_schedule_dialog(title="Overlay fade at…")
        if at_value is None:
            return
        payload: dict[str, Any] = {"at": at_value, "seconds": float(self._fade_seconds.value()), "target": 1.0}
        self.miscCommandTriggered.emit("overlay_fade_at", payload)

    # ------------------------------------------------------------------
    # Status-driven UI updates
    # ------------------------------------------------------------------

    def set_timing_status(self, ok: bool | None, text: str | None = None, *, blink: bool = False) -> None:
        """Update timing LED and optional text.
        ok=True -> green, ok=False -> red, ok=None -> gray.
        """
        if ok is True:
            color = "#2ecc71"  # green
        elif ok is False:
            color = "#e74c3c"  # red
        else:
            color = "#AAAAAA"  # gray/unknown
        self._timing_led.setStyleSheet(f"background-color: {color}; border-radius: 6px;")
        if text is not None:
            self._timing_text.setText(text)
        else:
            self._timing_text.setText("—")
        # Handle blinking when within margin
        if blink and ok is True:
            if not self._blinking:
                self._blinking = True
                self._blink_state = False
                self._blink_timer.start()
        else:
            if self._blinking:
                self._blinking = False
                self._blink_timer.stop()
                # Ensure LED returns to steady state
                self._timing_led.setStyleSheet(f"background-color: {color}; border-radius: 6px;")

    def set_faststart_ready(self, ready: bool) -> None:
        """Highlight Fast-Start Prepare button when backend reports ready."""
        if ready:
            self._fs_prepare.setStyleSheet("background-color: #d4edda; border: 1px solid #52a057;")
        else:
            self._fs_prepare.setStyleSheet("")

    def set_action_badge(self, action: str | None) -> None:
        """Show a short-lived badge for the given action (next/prev/play/faststart_go)."""
        if not action:
            self._action_badge.setVisible(False)
            self._action_timer.stop()
            return
        label_map = {
            "next": "NEXT",
            "prev": "PREV",
            "play": "PLAY",
            "faststart_go": "FAST",
        }
        text = label_map.get(str(action).lower(), str(action).upper())
        try:
            self._action_badge.setText(text)
            self._action_badge.setVisible(True)
            # Refresh timer (1.6s)
            self._action_timer.start(1600)
        except Exception:
            pass

    def _toggle_led_blink(self) -> None:
        # Alternate between strong and dim color while blinking
        if not self._blinking:
            return
        self._blink_state = not self._blink_state
        # Extract current base color from style (fallback green)
        base_color = "#2ecc71"
        # Choose dim/bright variants
        color = base_color if self._blink_state else "#7ddf9f"
        self._timing_led.setStyleSheet(f"background-color: {color}; border-radius: 6px;")

    def _emit_upload(self) -> None:
        items = self._media_list.selectedItems()
        if not items:
            return
        for item in items:
            path = item.data(Qt.UserRole)
            if path:
                self.uploadRequested.emit(path)

    def _on_media_selection_changed(self) -> None:
        self._update_upload_button(self._targets_enabled)
        self._update_faststart_enabled()

    def _update_misc_button(self, enabled: bool) -> None:
        can_run = enabled and self._command_selector.currentIndex() > 0
        self._execute_button.setEnabled(can_run)

    def _update_upload_button(self, targets_enabled: bool) -> None:
        has_item = self._media_list.currentItem() is not None
        self._upload_button.setEnabled(targets_enabled and has_item)
        # Push abilitato in base alla presenza elementi e target
        self._push_playlist.setEnabled(self._targets_enabled and self._playlist.count() > 0)

    def _update_faststart_enabled(self) -> None:
        """Abilita/Disabilita il pulsante Prepare in base alle selezioni correnti.
        Aggiorna anche l'etichetta sorgente (Device / Locale / —).
        """
        has_dev = self._device_media_list.currentItem() is not None
        has_local = self._media_list.currentItem() is not None
        enabled = self._targets_enabled and (has_dev or has_local)
        try:
            self._fs_prepare.setEnabled(enabled)
            if has_dev:
                src = "Device"
            elif has_local:
                src = "Locale"
            else:
                src = "—"
            self._fs_source.setText(src)
            if not enabled:
                self._fs_prepare.setToolTip("Seleziona un media dal device o dalla lista locale per abilitare il Prepare")
            else:
                self._fs_prepare.setToolTip("")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Playlist helpers
    # ------------------------------------------------------------------
    def _add_selected_to_playlist(self) -> None:
        items = self._media_list.selectedItems()
        if not items:
            return
        for item in items:
            label = item.text()
            rel = item.data(self._RELATIVE_ROLE) or item.data(Qt.UserRole)
            if not rel:
                continue
            entry = QListWidgetItem(label)
            entry.setData(Qt.UserRole, rel)
            self._playlist.addItem(entry)
        self._emit_playlist_changed()

    def _remove_selected_from_playlist(self) -> None:
        row = self._playlist.currentRow()
        if row < 0:
            return
        it = self._playlist.takeItem(row)
        try:
            del it
        except Exception:
            pass
        self._emit_playlist_changed()

    def _emit_playlist_changed(self) -> None:
        items: list[str] = []
        for i in range(self._playlist.count()):
            it = self._playlist.item(i)
            rel = it.data(Qt.UserRole) or it.text()
            items.append(str(rel))
        # LED rosso (dirty)
        self._set_playlist_led_color("red")
        self.playlistChanged.emit(items)
        self._push_playlist.setEnabled(self._targets_enabled and len(items) > 0)
        self._update_total_tc()

    def _emit_push_playlist(self) -> None:
        items: list[str] = []
        for i in range(self._playlist.count()):
            it = self._playlist.item(i)
            rel = it.data(Qt.UserRole) or it.text()
            items.append(str(rel))
        if not items:
            return
        # LED arancione (in progress)
        self._set_playlist_led_color("orange")
        self.playlistPushRequested.emit(items, self._clear_before_push.isChecked())

    def _set_playlist_led_color(self, state: str) -> None:
        colors = {
            "red": "#e74c3c",
            "orange": "#f39c12",
            "green": "#2ecc71",
            "gray": "#AAAAAA",
        }
        color = colors.get(state, "#AAAAAA")
        try:
            self._playlist_led.setStyleSheet(f"background-color: {color}; border-radius: 7px;")
        except Exception:
            pass

    def _set_show_led(self, running: bool) -> None:
        color = "#e74c3c" if running else "#AAAAAA"
        try:
            self._show_led.setStyleSheet(f"background-color: {color}; border-radius: 7px;")
        except Exception:
            pass

    def set_show_running(self, running: bool) -> None:
        self._set_show_led(running)

    def set_countdown_text(self, text: str) -> None:
        self._countdown_label.setText(text)

    def set_total_timecode_text(self, text: str) -> None:
        self._total_tc_label.setText(text)

    def set_playlist_led(self, state: str) -> None:
        """Public setter to control the playlist LED color (red/orange/green/gray)."""
        self._set_playlist_led_color(state)

    def set_banner(self, text: str | None, level: str = "info") -> None:
        """Set a status banner below playlist controls.
        level in {info, progress, success, error} controls the style.
        """
        if not text:
            self._banner_label.setText("")
            self._banner_label.setStyleSheet("color: #333333;")
            return
        colors = {
            "info": "#2c3e50",
            "progress": "#8e44ad",
            "success": "#2ecc71",
            "error": "#e74c3c",
        }
        color = colors.get(level, "#2c3e50")
        self._banner_label.setStyleSheet(f"color: {color}; font-weight: 500;")
        self._banner_label.setText(text)

    # ------------------------------------------------------------------
    # Scheduling helpers
    # ------------------------------------------------------------------
    def _compute_in_time_epoch_seconds(self) -> float | None:
        try:
            dt = self._schedule_dt.dateTime()
            if not dt.isValid():
                return None
            # Epoch in secondi
            utc = QDateTime(dt).toUTC()
            return float(utc.toSecsSinceEpoch())
        except Exception:
            return None

    def _open_schedule_dialog(self, *, title: str) -> float | None:
        """Dialog che permette di scegliere tra secondi relativi o data/ora assoluta.
        Ritorna un float: secondi relativi o epoch secondi.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        v = QVBoxLayout(dlg)
        # Secondi relativi
        row_rel = QHBoxLayout()
        row_rel.addWidget(QLabel("Tra"))
        rel_secs = QDoubleSpinBox()
        rel_secs.setRange(0.0, 3600.0)
        rel_secs.setSingleStep(0.5)
        rel_secs.setValue(5.0)
        row_rel.addWidget(rel_secs)
        row_rel.addWidget(QLabel("secondi"))
        v.addLayout(row_rel)
        # Orario assoluto
        row_abs = QHBoxLayout()
        row_abs.addWidget(QLabel("Oppure alle:"))
        abs_dt = QDateTimeEdit()
        abs_dt.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        abs_dt.setCalendarPopup(True)
        abs_dt.setDateTime(QDateTime.currentDateTime().addSecs(5))
        row_abs.addWidget(abs_dt)
        v.addLayout(row_abs)
        # Pulsanti
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        v.addWidget(btns)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.Accepted:
            return None
        # Preferisci orario assoluto se futuro
        try:
            now = QDateTime.currentDateTimeUtc()
            chosen_abs = QDateTime(abs_dt.dateTime()).toUTC()
            if chosen_abs > now:
                return float(chosen_abs.toSecsSinceEpoch())
        except Exception:
            pass
        try:
            return float(rel_secs.value())
        except Exception:
            return None

    def _emit_start_show(self) -> None:
        # Use scheduling controls if enabled
        in_time = None
        if self._schedule_checkbox.isChecked():
            in_time = self._compute_in_time_epoch_seconds()
        self.startShowRequested.emit(in_time)

    def _update_total_tc(self) -> None:
        # Sum durations for items in playlist
        total = 0.0
        for i in range(self._playlist.count()):
            it = self._playlist.item(i)
            key = str(it.data(Qt.UserRole) or it.text())
            dur = self._media_durations.get(key)
            if isinstance(dur, (int, float)) and dur >= 0:
                total += float(dur)
        # format HH:MM:SS
        t = int(total)
        h = t // 3600
        m = (t % 3600) // 60
        s = t % 60
        if t <= 0:
            txt = "Totale: —"
        elif h > 0:
            txt = f"Totale: {h:d}:{m:02d}:{s:02d}"
        else:
            txt = f"Totale: {m:02d}:{s:02d}"
        self.set_total_timecode_text(txt)