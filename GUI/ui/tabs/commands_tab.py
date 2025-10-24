"""Command tab providing playback, system commands, and media tools."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal, QTimer, QDateTime
from PySide6.QtWidgets import (
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
    QVBoxLayout,
    QWidget,
)


class CommandsTab(QWidget):
    """Expose playback commands, media upload helpers, and system actions."""

    playbackTriggered = Signal(str, dict)
    miscCommandTriggered = Signal(str, dict)
    uploadRequested = Signal(str)
    mediaDirectoryRequested = Signal()

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

        media_box = QGroupBox("Media disponibili")
        media_layout = QVBoxLayout(media_box)
        choose_row = QHBoxLayout()
        self._choose_dir_button = QPushButton("Scegli cartella…")
        self._choose_dir_button.clicked.connect(self.mediaDirectoryRequested.emit)
        choose_row.addWidget(self._choose_dir_button)
        choose_row.addStretch(1)
        media_layout.addLayout(choose_row)
        self._media_list = QListWidget()
        self._media_list.itemSelectionChanged.connect(self._on_media_selection_changed)
        media_layout.addWidget(self._media_list)
        self._upload_button = QPushButton("Upload")
        self._upload_button.setEnabled(False)
        self._upload_button.clicked.connect(self._emit_upload)
        media_layout.addWidget(self._upload_button)
        layout.addWidget(media_box, 0, 1, 2, 1)

        layout.setColumnStretch(1, 1)
        # Blink timer for timing LED (off by default)
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(400)
        self._blink_timer.timeout.connect(self._toggle_led_blink)
        self._blinking = False
        self._blink_state = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_targets_selected(self, enabled: bool) -> None:
        self._targets_enabled = enabled
        for button in self._playback_buttons:
            button.setEnabled(enabled)
        self._update_misc_button(enabled)
        self._update_upload_button(enabled)
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
            self._media_list.addItem(list_item)
        if self._media_list.count() > 0:
            self._media_list.setCurrentRow(0)
        self._update_upload_button(self._targets_enabled)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_misc_entries(self) -> list[dict[str, Any]]:
        return [
            {"label": "Sistema: Mostra splash", "command": "show_splash"},
            {"label": "Sistema: Nascondi splash", "command": "hide_splash"},
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
                "label": "Manutenzione: Esegui setup.sh",
                "command": "run_setup",
            },
            {
                "label": "Media: Cancella cartella media",
                "command": "media_clear",
                "confirm": "Cancellare tutti i file nella cartella media del player?",
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
        # Se possibile, includi media selezionato
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
        payload: dict[str, Any] = {"at": at_value}
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
        item = self._media_list.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole)
        if path:
            self.uploadRequested.emit(path)

    def _on_media_selection_changed(self) -> None:
        self._update_upload_button(self._targets_enabled)

    def _update_misc_button(self, enabled: bool) -> None:
        can_run = enabled and self._command_selector.currentIndex() > 0
        self._execute_button.setEnabled(can_run)

    def _update_upload_button(self, targets_enabled: bool) -> None:
        has_item = self._media_list.currentItem() is not None
        self._upload_button.setEnabled(targets_enabled and has_item)

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