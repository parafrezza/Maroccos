"""Command tab providing playback, system commands, and media tools."""

from __future__ import annotations

from typing import Any, Sequence
import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer, QDateTime, QMimeData, QSize, QTime
import time as _time
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QDialogButtonBox,
    QDateTimeEdit,
    QFileDialog,
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
    QSlider,
    QStyle,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

# Custom DnD MIME for media items dragged from the media list to the playlist
_MEDIA_MIME = "application/x-maroccos-mediaitems"
_RELATIVE_ROLE = Qt.UserRole + 1
_AVAILABILITY_ROLE = Qt.UserRole + 5


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

    # Segnale centralizzato per upload di file dal filesystem (picker o drag&drop)
    filesDroppedForUpload = Signal(list)

    playbackTriggered = Signal(str, dict)
    miscCommandTriggered = Signal(str, dict)
    uploadRequested = Signal(str)
    mediaDirectoryRequested = Signal()
    deviceMediaRefreshRequested = Signal()
    # Playlist signals
    playlistChanged = Signal(list)
    playlistPushRequested = Signal(list, bool, bool)
    playlistRefreshRequested = Signal()
    startShowRequested = Signal(object)
    jumpToTrackRequested = Signal(int)
    jumpToSelectedRequested = Signal()
    # Live framework log
    logLiveToggleRequested = Signal(bool, int)
    startSyncRequested = Signal()

    _RELATIVE_ROLE = Qt.UserRole + 1

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QGridLayout(self)
        self._control_tooltips: dict[QWidget, tuple[str | None, str | None]] = {}
        self._selection_required_tip = "Seleziona almeno un player per usare questo comando"
        self._media_root_warning: str | None = None

        self._targets_enabled = False
        self._playlist_dirty = False  # True se l'utente ha modificato localmente la playlist
        self._media_root: Path | None = None
        self._media_root_valid = False
        self._playlist_missing: list[str] = []
        self._playlist_invalid: list[str] = []
        self._playlist_banner_validation = False
        self._device_media_names: set[str] = set()
        self._device_media_seen: bool = False
        self._downloads_in_progress: set[str] = set()
        self._banner_fallback: tuple[str, str] | None = None
        self._playback_buttons: list[QPushButton] = []
        self._upload_started_at: float | None = None
        self._brightness_dirty = False
        self._brightness_pending_remote: int | None = None
        self._brightness_last_multi_count: int | None = None
        self._brightness_base_slider_style = ""
        self._brightness_apply_default_text = ""
        self._brightness_apply_base_style = ""
        self._brightness_updating = False
        self._timing_syncing = False

        playback_box = QGroupBox("Playback")
        playback_layout = QGridLayout(playback_box)
        self._loop_checkbox = QCheckBox("Loop singolo")
        self._loop_checkbox.setChecked(True)
        self._loop_checkbox.setStyleSheet("font-weight: 600;")
        # Toggle immediate command dispatch
        try:
            self._loop_checkbox.toggled.connect(lambda checked: self.miscCommandTriggered.emit("loop_on" if checked else "loop_off", {}))
        except Exception:
            pass
        self._fade_seconds = QDoubleSpinBox()
        self._fade_seconds.setRange(0.0, 10.0)
        self._fade_seconds.setSingleStep(0.1)
        self._fade_seconds.setValue(1.0)

        playback_defs = [
            {"label": "Start Playlist", "command": "play", "use_media": True, "icon": QStyle.SP_MediaPlay},
            {"label": "Stop", "command": "stop", "use_fade": True, "icon": QStyle.SP_MediaStop},
            {"label": "Pause", "command": "pause", "icon": QStyle.SP_MediaPause},
            {"label": "Resume", "command": "resume", "icon": QStyle.SP_MediaPlay},
            {"label": "Prev", "command": "prev", "use_fade": True, "icon": QStyle.SP_MediaSkipBackward},
            {"label": "Next", "command": "next", "use_fade": True, "icon": QStyle.SP_MediaSkipForward},
            {"label": "FTB", "command": "ftb", "use_fade": True, "icon": QStyle.SP_DialogDiscardButton},
            {"label": "Ping", "command": "ping", "use_ping": True, "icon": QStyle.SP_BrowserReload},
        ]

        columns = 2
        style = self.style()
        for index, meta in enumerate(playback_defs):
            button = QPushButton(meta["label"])
            button.clicked.connect(lambda _=False, data=meta: self._handle_playback_button(data))
            button.setStyleSheet("padding:6px 10px; font-size:12px; font-weight:600;")
            button.setIconSize(QSize(18, 18))
            icon_key = meta.get("icon")
            if icon_key is not None:
                try:
                    button.setIcon(style.standardIcon(icon_key))
                except Exception:
                    pass
            self._playback_buttons.append(button)
            button.setEnabled(False)
            self._register_control(
                button,
                enabled_tip=f"Esegue '{meta['label']}' sui player selezionati",
                disabled_tip=self._selection_required_tip,
            )
            row = index // columns
            col = index % columns
            playback_layout.addWidget(button, row, col)

        for col in range(columns):
            playback_layout.setColumnStretch(col, 1)

        controls_row = (len(playback_defs) + (columns - 1)) // columns
        controls = QHBoxLayout()
        controls.addWidget(self._loop_checkbox)
        # Playlist loop toggle (promoted here)
        controls.addSpacing(12)
        self._playlist_loop_toggle = QCheckBox("Loop playlist")
        self._playlist_loop_toggle.setChecked(True)
        self._playlist_loop_toggle.setStyleSheet("font-weight: 600;")
        try:
            self._playlist_loop_toggle.toggled.connect(self._on_playlist_loop_toggled)
        except Exception:
            pass
        try:
            self._register_control(
                self._playlist_loop_toggle,
                enabled_tip="Ripeti playlist sul/i player selezionato/i",
                disabled_tip=self._selection_required_tip,
            )
        except Exception:
            pass
        controls.addWidget(self._playlist_loop_toggle)
        controls.addSpacing(12)
        controls.addWidget(QLabel("Fade (s)"))
        controls.addWidget(self._fade_seconds)
        # Ping duration moved to Settings tab; keep only the Ping action button here
        controls.addStretch(1)
        playback_layout.addLayout(controls, controls_row, 0, 1, columns)
        # Action badge row (shows short-lived action like NEXT/PREV/PLAY)
        action_row = QHBoxLayout()
        self._action_badge = QLabel("—")
        self._action_badge.setVisible(False)
        # pill style
        self._action_badge.setStyleSheet(
            "padding: 2px 8px; border-radius: 9px; background-color: #34495e; color: white; font-weight: 600;"
        )
        action_row.addWidget(self._action_badge)
        action_row.addStretch(1)
        playback_layout.addLayout(action_row, controls_row + 1, 0, 1, columns)
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
        self._execute_button.setEnabled(False)
        self._register_control(
            self._execute_button,
            enabled_tip="Esegue il comando selezionato sui player attivi",
            disabled_tip="Scegli un comando e seleziona almeno un player",
        )
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
        # Start sync button
        self._start_sync = QPushButton("Start Sync")
        self._start_sync.setStyleSheet(
            "QPushButton {"
            " padding: 6px 14px;"
            " font-weight: 600;"
            " color: #ecf0f1;"
            " background-color: #2c3e50;"
            " border: none;"
            " border-radius: 4px;"
            "}"
            "QPushButton:hover { background-color: #34495e; }"
            "QPushButton:pressed { background-color: #1f2a36; }"
        )
        self._start_sync.setEnabled(False)
        self._start_sync.clicked.connect(lambda _=False: self._emit_start_sync())
        self._register_control(
            self._start_sync,
            enabled_tip="Riavvia la sincronizzazione degli orologi sui device selezionati",
            disabled_tip=self._selection_required_tip,
        )
        timing_row.addSpacing(12)
        timing_row.addWidget(self._start_sync)
        timing_row.addStretch(1)
        aux_layout.addLayout(timing_row)

        # Overlay controls removed (feature deprecated) + HUD text toggle for OFF-player
        overlay_row = QHBoxLayout()
        overlay_row.addWidget(QLabel("HUD:"))
        overlay_row.addSpacing(12)
        self._hud_mode_combo = QComboBox()
        self._hud_mode_combo.addItems(["Nascosto", "IP & Porte", "Completo"])
        self._hud_mode_combo.setCurrentIndex(0)
        self._hud_mode_combo.setEnabled(False)
        self._hud_mode_combo.setToolTip("Seleziona la modalità HUD del player")
        self._hud_mode_base_style = self._hud_mode_combo.styleSheet() or ""
        self._hud_mode_combo.currentIndexChanged.connect(self._on_hud_mode_changed)
        self._register_control(
            self._hud_mode_combo,
            enabled_tip="Configura la modalità HUD del player",
            disabled_tip=self._selection_required_tip,
        )
        overlay_row.addWidget(self._hud_mode_combo)
        overlay_row.addStretch(1)
        aux_layout.addLayout(overlay_row)

        # Display mode indicator
        display_row = QHBoxLayout()
        display_row.addWidget(QLabel("Display:"))
        self._display_label = QLabel("—")
        self._display_label.setStyleSheet("color: #7f8c8d; font-style: italic; padding: 1px 4px;")
        self._display_label.setToolTip("Risoluzione corrente non disponibile")
        display_row.addWidget(self._display_label)
        display_row.addStretch(1)
        aux_layout.addLayout(display_row)

        # Brightness controls
        brightness_row = QHBoxLayout()
        brightness_row.addWidget(QLabel("Brightness:"))
        self._brightness_slider = QSlider(Qt.Horizontal)
        self._brightness_slider.setRange(0, 100)
        self._brightness_slider.setValue(100)
        self._brightness_slider.setToolTip("Luminosità 0–100%")
        self._brightness_slider.setEnabled(False)
        self._brightness_base_slider_style = self._brightness_slider.styleSheet() or ""
        try:
            self._brightness_slider.sliderPressed.connect(self._on_brightness_slider_pressed)
            self._brightness_slider.valueChanged.connect(self._on_brightness_value_changed)
            self._brightness_slider.sliderReleased.connect(self._on_brightness_slider_released)
        except Exception:
            pass
        self._register_control(
            self._brightness_slider,
            enabled_tip="Regola la luminosità del player",
            disabled_tip=self._selection_required_tip,
        )
        brightness_row.addWidget(self._brightness_slider, 1)
        brightness_row.addSpacing(8)
        brightness_row.addWidget(QLabel("in"))
        self._brightness_seconds = QDoubleSpinBox()
        self._brightness_seconds.setRange(0.0, 10.0)
        self._brightness_seconds.setSingleStep(0.1)
        self._brightness_seconds.setValue(0.5)
        self._brightness_seconds.setEnabled(False)
        self._register_control(
            self._brightness_seconds,
            enabled_tip="Durata transizione luminosità (s)",
            disabled_tip=self._selection_required_tip,
        )
        brightness_row.addWidget(self._brightness_seconds)
        brightness_row.addWidget(QLabel("s"))
        brightness_row.addSpacing(8)
        self._brightness_apply = QPushButton("Imposta")
        self._brightness_apply.setEnabled(False)
        self._brightness_apply.clicked.connect(self._emit_brightness_command)
        self._brightness_apply_default_text = self._brightness_apply.text()
        self._register_control(
            self._brightness_apply,
            enabled_tip="Applica luminosità ai player selezionati",
            disabled_tip=self._selection_required_tip,
        )
        self._brightness_apply_base_style = self._brightness_apply.styleSheet() or ""
        brightness_row.addWidget(self._brightness_apply)
        aux_layout.addLayout(brightness_row)
        self._update_brightness_controls_state()
        self._update_brightness_tooltip()

        # Overlay diagnostics row (rimosso su richiesta)

        # Fast-start controls
        fs_row = QHBoxLayout()
        fs_row.addWidget(QLabel("Fast-Start:"))
        self._fs_prepare = QPushButton("Prepare (usa media selezionato)")
        self._fs_go = QPushButton("Go")
        self._fs_prepare.clicked.connect(lambda _=False: self._emit_faststart_prepare())
        self._fs_go.clicked.connect(lambda _=False: self._emit_faststart_go())
        self._fs_prepare.setEnabled(False)
        self._fs_go.setEnabled(False)
        self._register_control(
            self._fs_prepare,
            enabled_tip="Prepara il Fast-Start con il media scelto",
            disabled_tip="Seleziona un player e un media per usare il Fast-Start",
        )
        self._register_control(
            self._fs_go,
            enabled_tip="Avvia il media preparato in Fast-Start",
            disabled_tip=self._selection_required_tip,
        )
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
        self._play_at.setEnabled(False)
        self._register_control(
            self._play_at,
            enabled_tip="Schedula la riproduzione del media selezionato",
            disabled_tip=self._selection_required_tip,
        )
        playat_row.addWidget(self._play_at)
        playat_row.addStretch(1)
        aux_layout.addLayout(playat_row)

        # Test mode controls
        test_row = QHBoxLayout()
        test_row.addWidget(QLabel("Test Mode:"))
        self._test_width = QSpinBox()
        self._test_width.setRange(0, 8192)
        self._test_width.setSpecialValueText("auto")
        self._test_width.setMaximumWidth(80)
        self._test_width.setToolTip("Larghezza sezione LED (0 = usa dimensioni finestra)")
        self._test_height = QSpinBox()
        self._test_height.setRange(0, 8192)
        self._test_height.setSpecialValueText("auto")
        self._test_height.setMaximumWidth(80)
        self._test_height.setToolTip("Altezza sezione LED (0 = usa dimensioni finestra)")
        self._test_offset_x = QSpinBox()
        self._test_offset_x.setRange(0, 8192)
        self._test_offset_x.setMaximumWidth(80)
        self._test_offset_x.setToolTip("Offset X dall'angolo sinistro della finestra")
        self._test_offset_y = QSpinBox()
        self._test_offset_y.setRange(0, 8192)
        self._test_offset_y.setMaximumWidth(80)
        self._test_offset_y.setToolTip("Offset Y dall'angolo superiore della finestra")
        for _spin in (self._test_width, self._test_height, self._test_offset_x, self._test_offset_y):
            _spin.setEnabled(False)
        self._test_on = QPushButton("ON")
        self._test_off = QPushButton("OFF")
        self._test_on.clicked.connect(lambda _=False: self._emit_test_on())
        self._test_off.clicked.connect(lambda _=False: self._emit_misc_simple("test_off"))
        self._test_on.setEnabled(False)
        self._test_off.setEnabled(False)
        self._register_control(
            self._test_on,
            enabled_tip="Abilita la modalità Test sui player selezionati",
            disabled_tip=self._selection_required_tip,
        )
        self._register_control(
            self._test_off,
            enabled_tip="Disabilita la modalità Test sui player selezionati",
            disabled_tip=self._selection_required_tip,
        )
        test_row.addWidget(QLabel("W"))
        test_row.addWidget(self._test_width)
        test_row.addWidget(QLabel("H"))
        test_row.addWidget(self._test_height)
        test_row.addWidget(QLabel("X"))
        test_row.addWidget(self._test_offset_x)
        test_row.addWidget(QLabel("Y"))
        test_row.addWidget(self._test_offset_y)
        test_row.addWidget(self._test_on)
        test_row.addWidget(self._test_off)
        test_row.addStretch(1)
        aux_layout.addLayout(test_row)

        layout.addWidget(aux_box, 2, 0)

        # Live Framework log controls (output now routed to Event Log)
        logs_box = QGroupBox("Log Framework (Event Log)")
        logs_layout = QHBoxLayout(logs_box)
        self._log_live_button = QPushButton("Start Live")
        self._log_live_button.setCheckable(True)
        self._log_live_button.toggled.connect(self._emit_log_live_toggle)
        self._log_live_button.setEnabled(False)
        self._register_control(
            self._log_live_button,
            enabled_tip="Invia i log framework nell'Event Log del player primario",
            disabled_tip=self._selection_required_tip,
        )
        self._log_live_lines = QSpinBox()
        self._log_live_lines.setRange(0, 2000)
        self._log_live_lines.setValue(100)
        logs_layout.addWidget(self._log_live_button)
        logs_layout.addSpacing(8)
        logs_layout.addWidget(QLabel("Righe iniziali:"))
        logs_layout.addWidget(self._log_live_lines)
        logs_layout.addStretch(1)
        layout.addWidget(logs_box, 3, 0)

        media_box = QGroupBox("Media disponibili")
        media_layout = QVBoxLayout(media_box)
        choose_row = QHBoxLayout()
        self._choose_dir_button = QPushButton("Scegli cartella…")
        self._choose_dir_button.clicked.connect(self.mediaDirectoryRequested.emit)
        choose_row.addWidget(self._choose_dir_button)
        # Nuovo: selettore file con filtro media + upload immediato
        self._choose_files_button = QPushButton("Seleziona/Upload…")
        self._choose_files_button.setToolTip("Seleziona file video/immagine dal disco e caricali sui device selezionati")
        self._choose_files_button.clicked.connect(self._open_media_picker)
        choose_row.addWidget(self._choose_files_button)
        choose_row.addStretch(1)
        media_layout.addLayout(choose_row)
        # Media list with external drag enabled
        self._media_list = MediaListWidget()
        self._media_list.itemSelectionChanged.connect(self._on_media_selection_changed)
        # Double-click to upload selected media
        try:
            self._media_list.itemDoubleClicked.connect(lambda it: self.uploadRequested.emit(str(it.data(Qt.UserRole) or it.text())))  # type: ignore[attr-defined]
        except Exception:
            pass
        media_layout.addWidget(self._media_list)
        # Nota: l'area dedicata "Trascina qui per upload" è stata rimossa.
        # Barra di avanzamento upload (indeterminata, visibile solo durante attività)
        from PySide6.QtWidgets import QProgressBar
        self._upload_progress = QProgressBar()
        self._upload_progress.setRange(0, 0)  # busy
        self._upload_progress.setTextVisible(False)
        self._upload_progress.setVisible(False)
        media_layout.addWidget(self._upload_progress)
        # Label numerica per conteggio upload
        self._upload_progress_label = QLabel("")
        self._upload_progress_label.setVisible(False)
        self._upload_progress_label.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        media_layout.addWidget(self._upload_progress_label)
        layout.addWidget(media_box, 0, 1)

        # Media presenti sul device
        device_box = QGroupBox("Media sul device")
        device_layout = QVBoxLayout(device_box)
        dev_controls = QHBoxLayout()
        self._device_refresh = QPushButton("Aggiorna")
        self._device_refresh.clicked.connect(self.deviceMediaRefreshRequested.emit)
        self._device_clear = QPushButton("Svuota media")
        self._device_clear.clicked.connect(lambda _=False: self.miscCommandTriggered.emit("media_clear", {}))
        self._device_refresh.setEnabled(False)
        self._device_clear.setEnabled(False)
        self._register_control(
            self._device_refresh,
            enabled_tip="Richiede la lista media dal player selezionato",
            disabled_tip=self._selection_required_tip,
        )
        self._register_control(
            self._device_clear,
            enabled_tip="Svuota i media sul player selezionato",
            disabled_tip=self._selection_required_tip,
        )
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
        # Abilita drop: trascinando elementi dalla libreria o file dal filesystem parte l'upload
        self._device_media_list.setAcceptDrops(True)
        self._device_media_list.setToolTip("Trascina qui per caricare media sul device")

        def _dev_drag_enter(ev):
            """Accetta sia drag interno (_MEDIA_MIME) che file dal filesystem (URLs)."""
            try:
                md = ev.mimeData()
                if md.hasFormat(_MEDIA_MIME) or md.hasUrls():
                    try:
                        self._device_media_list.setStyleSheet(
                            "background-color: #f0fff4; border: 2px dashed #27ae60; color: #111111;"
                        )
                        self._device_media_list.setDropIndicatorShown(True)
                    except Exception:
                        pass
                    ev.setDropAction(Qt.CopyAction)
                    ev.acceptProposedAction()
                else:
                    ev.ignore()
            except Exception:
                ev.ignore()

        def _dev_drag_move(ev):
            try:
                md = ev.mimeData()
                if md.hasFormat(_MEDIA_MIME) or md.hasUrls():
                    ev.setDropAction(Qt.CopyAction)
                    ev.acceptProposedAction()
                else:
                    ev.ignore()
            except Exception:
                ev.ignore()

        def _dev_drop(ev):
            """Gestisce drop dalla libreria interna o da file system con filtratura MIME."""
            md = ev.mimeData()

            # 1) Drag & drop interno dalla libreria (formato _MEDIA_MIME)
            try:
                if md.hasFormat(_MEDIA_MIME):
                    try:
                        raw = bytes(md.data(_MEDIA_MIME))
                        items = json.loads(raw.decode("utf-8", errors="ignore"))
                    except Exception:
                        items = []
                    rels: list[str] = []
                    for ent in items or []:
                        try:
                            rel = ent.get("rel") or ent.get("label")
                        except Exception:
                            rel = None
                        if rel:
                            rels.append(str(rel))
                    if rels:
                        self._trigger_auto_downloads(rels)
                        try:
                            self._device_media_list.setStyleSheet("")
                            self._device_media_list.setDropIndicatorShown(False)
                        except Exception:
                            pass
                        ev.acceptProposedAction()
                        return
            except Exception:
                # Se fallisce il formato interno, proviamo comunque gli URL
                pass

            # 2) Drag & drop dal filesystem (file URLs)
            try:
                if md.hasUrls():
                    paths: list[str] = []
                    for url in md.urls():
                        try:
                            if url.isLocalFile():
                                paths.append(url.toLocalFile())
                        except Exception:
                            continue
                    # Filtra le estensioni usando lo stesso criterio del file picker
                    if paths:
                        try:
                            allowed = set(self._allowed_patterns())
                        except Exception:
                            allowed = set()
                        filtered: list[str] = []
                        for p in paths:
                            ext = "*" + Path(p).suffix.lower() if Path(p).suffix else ""
                            if not allowed or ext in allowed:
                                filtered.append(p)
                        if not filtered:
                            ev.ignore()
                            return
                        # Riutilizza la stessa pipeline di upload esterno
                        try:
                            self.filesDroppedForUpload.emit(filtered)  # type: ignore[attr-defined]
                        except Exception:
                            # Fallback: prova a usare uploadRequested con il primo file
                            try:
                                self.uploadRequested.emit(filtered[0])  # type: ignore[attr-defined]
                            except Exception:
                                pass
                        try:
                            self._device_media_list.setStyleSheet("")
                            self._device_media_list.setDropIndicatorShown(False)
                        except Exception:
                            pass
                        ev.acceptProposedAction()
                        return
            except Exception:
                pass

            try:
                self._device_media_list.setStyleSheet("")
                self._device_media_list.setDropIndicatorShown(False)
            except Exception:
                pass
            ev.ignore()
        self._device_media_list.dragEnterEvent = _dev_drag_enter  # type: ignore[assignment]
        self._device_media_list.dragMoveEvent = _dev_drag_move  # type: ignore[assignment]
        self._device_media_list.dropEvent = _dev_drop  # type: ignore[assignment]
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
        self._add_to_playlist.setEnabled(False)
        self._register_control(
            self._add_to_playlist,
            enabled_tip="Aggiunge il media selezionato alla playlist",
            disabled_tip="Seleziona un player e un media per poter aggiungere",
        )
        quick_row.addWidget(self._add_to_playlist)
        self._remove_from_playlist = QPushButton("Rimuovi")
        self._remove_from_playlist.clicked.connect(self._remove_selected_from_playlist)
        self._remove_from_playlist.setEnabled(False)
        self._register_control(
            self._remove_from_playlist,
            enabled_tip="Rimuove l'elemento selezionato dalla playlist",
            disabled_tip="Disponibile solo con player selezionati e una traccia evidenziata",
        )
        quick_row.addWidget(self._remove_from_playlist)
        self._refresh_playlist = QPushButton("Aggiorna da player")
        self._refresh_playlist.clicked.connect(lambda: self.playlistRefreshRequested.emit())
        self._refresh_playlist.setEnabled(False)
        self._register_control(
            self._refresh_playlist,
            enabled_tip="Scarica la playlist dal player primario",
            disabled_tip=self._selection_required_tip,
        )
        quick_row.addWidget(self._refresh_playlist)
        quick_row.addStretch(1)
        playlist_layout.addLayout(quick_row)

        # Playlist accepts drops from media list and allows internal reorder
        self._playlist = PlaylistWidget()
        # Riordino interno tramite drag&drop
        self._playlist.setDragDropMode(QAbstractItemView.InternalMove)
        self._playlist.itemsAdded.connect(self._on_playlist_items_added)
        self._playlist.model().rowsMoved.connect(self._emit_playlist_changed)
        self._playlist.itemSelectionChanged.connect(self._update_jump_buttons)
        playlist_layout.addWidget(self._playlist)

        controls_section = QVBoxLayout()
        controls_section.setSpacing(6)
        playlist_row = QHBoxLayout()
        playlist_row.setSpacing(8)
        self._playlist_loop_checkbox = QCheckBox("Loop playlist")
        self._playlist_loop_checkbox.setChecked(True)
        self._playlist_loop_checkbox.setStyleSheet("font-weight: 600;")
        # Toggle immediate command dispatch for playlist loop
        try:
            self._playlist_loop_checkbox.toggled.connect(lambda checked: self.miscCommandTriggered.emit("playlist_loop_on" if checked else "playlist_loop_off", {}))
        except Exception:
            pass
        playlist_row.addWidget(self._playlist_loop_checkbox)
        # Hide duplicate control; promoted toggle in Playback row is the visible one
        try:
            self._playlist_loop_checkbox.setVisible(False)
        except Exception:
            pass
        self._push_playlist = QPushButton("Push Playlist")
        self._push_playlist.clicked.connect(self._emit_push_playlist)
        self._push_playlist.setEnabled(False)
        self._register_control(
            self._push_playlist,
            enabled_tip="Invia la playlist corrente ai player selezionati",
            disabled_tip="Serve almeno un player selezionato e una playlist non vuota",
        )
        playlist_row.addWidget(self._push_playlist)
        # Clear-before-push option
        self._clear_before_push = QCheckBox("Svuota media sui device prima del push")
        playlist_row.addWidget(self._clear_before_push)
        playlist_row.addSpacing(8)
        playlist_row.addWidget(QLabel("Stato:"))
        self._playlist_led = QLabel()
        self._playlist_led.setFixedSize(14, 14)
        self._set_playlist_led_color("gray")
        playlist_row.addWidget(self._playlist_led)
        playlist_row.addStretch(1)
        controls_section.addLayout(playlist_row)

        start_row = QHBoxLayout()
        start_row.setSpacing(8)
        self._start_delay_label = QLabel("Countdown (HH:MM:SS)")
        self._start_delay_label.setStyleSheet("font-weight: 600;")
        self._start_delay = QTimeEdit()
        self._start_delay.setDisplayFormat("HH:mm:ss")
        self._start_delay.setTime(QTime(0, 0, 5))
        self._start_delay.setMaximumWidth(110)
        self._start_delay.setEnabled(False)
        self._start_delay.setToolTip("Lascia 00:00:00 per partire subito")
        start_row.addWidget(self._start_delay_label)
        start_row.addWidget(self._start_delay)
        start_row.addSpacing(8)
        self._start_show = QPushButton("START SHOW")
        # Red prominent style
        self._start_show.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold;")
        self._start_show.clicked.connect(self._emit_start_show)
        self._start_show.setEnabled(False)
        self._register_control(
            self._start_show,
            enabled_tip="Avvia lo show sui player selezionati",
            disabled_tip="Serve almeno un player selezionato e una playlist valida",
        )
        start_row.addWidget(self._start_show)
        start_row.addSpacing(8)
        start_row.addWidget(QLabel("Show:"))
        self._show_led = QLabel()
        self._show_led.setFixedSize(14, 14)
        self._set_show_led(False)
        start_row.addWidget(self._show_led)
        start_row.addSpacing(12)
        self._countdown_label = QLabel("T- —")
        start_row.addWidget(self._countdown_label)
        start_row.addSpacing(6)
        self._total_tc_label = QLabel("Totale: —")
        start_row.addWidget(self._total_tc_label)
        start_row.addStretch(1)
        controls_section.addLayout(start_row)
        playlist_layout.addLayout(controls_section)
        # Banner row under playlist controls
        banner_row = QHBoxLayout()
        self._banner_label = QLabel("")
        self._banner_label.setStyleSheet("color: #333333;")
        banner_row.addWidget(self._banner_label)
        banner_row.addStretch(1)
        playlist_layout.addLayout(banner_row)

        # Jump controls row
        jump_row = QHBoxLayout()
        jump_row.addWidget(QLabel("Jump to track:"))
        self._jump_track_spin = QSpinBox()
        self._jump_track_spin.setRange(1, 999)
        self._jump_track_spin.setValue(1)
        self._jump_track_spin.setMinimumWidth(60)
        jump_row.addWidget(self._jump_track_spin)
        self._jump_go_button = QPushButton("GO")
        self._jump_go_button.setEnabled(False)
        self._jump_go_button.clicked.connect(self._emit_jump_to_track_number)
        self._register_control(
            self._jump_go_button,
            enabled_tip="Salta alla traccia indicata",
            disabled_tip="Richiede player selezionati e una playlist caricata",
        )
        jump_row.addWidget(self._jump_go_button)
        jump_row.addSpacing(16)
        self._jump_selected_button = QPushButton("Jump to Selected")
        self._jump_selected_button.setEnabled(False)
        self._jump_selected_button.clicked.connect(self._emit_jump_to_selected)
        self._register_control(
            self._jump_selected_button,
            enabled_tip="Salta alla traccia selezionata nella playlist",
            disabled_tip="Richiede player selezionati e una traccia evidenziata",
        )
        jump_row.addWidget(self._jump_selected_button)
        jump_row.addStretch(1)
        playlist_layout.addLayout(jump_row)

        layout.addWidget(playlist_box, 2, 1)
        # durations map for computing total TC
        self._media_durations: dict[str, float] = {}

        # Stato iniziale dei controlli Fast-Start
        self._update_faststart_enabled()
        self._update_playlist_controls_state()

    # -----------------------------
    # Live framework log UI helpers
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
        self._device_media_names = set()
        for it in (items or []):
            try:
                name = str(it.get("name") or it.get("path") or "?")
                # Filtra: mostra solo immagini/video
                if not self._is_allowed_media(name):
                    continue
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
            base = self._normalize_device_basename(name)
            if base:
                self._device_media_names.add(base)
        self._device_media_seen = True
        self._apply_playlist_availability_styles()

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
        if not enabled:
            self._device_media_seen = False
            self._device_media_names.clear()
            self._downloads_in_progress.clear()
            self._clear_availability_highlight()
            self._clear_brightness_dirty(apply_pending_remote=False)
        for button in self._playback_buttons:
            self._set_control_enabled(button, enabled, disabled_reason=self._selection_required_tip)
        self._update_misc_button(enabled)
        self._update_upload_button(enabled)
        # Playlist controls
        add_reason = None
        if not enabled:
            add_reason = self._selection_required_tip
        elif self._media_list.count() == 0:
            add_reason = "Aggiungi media nella libreria per popolare la playlist"
        self._set_control_enabled(
            self._add_to_playlist,
            enabled and self._media_list.count() > 0,
            disabled_reason=add_reason,
        )
        self._set_control_enabled(self._refresh_playlist, enabled, disabled_reason=self._selection_required_tip)
        # Defer specific playlist button states to helper
        self._update_playlist_controls_state()
        # Toggle auxiliary controls
        for btn in (
            self._fs_prepare,
            self._fs_go,
            self._play_at,
            self._test_on,
            self._test_off,
            getattr(self, "_hud_mode_combo", None),
            getattr(self, "_start_sync", None),
        ):
            if btn is None:
                continue
            self._set_control_enabled(btn, enabled, disabled_reason=self._selection_required_tip)
        for spin in (self._test_width, self._test_height, self._test_offset_x, self._test_offset_y):
            spin.setEnabled(enabled)
        # Jump controls depend on selection and target availability
        self._update_jump_buttons()
        self._set_control_enabled(
            self._device_refresh,
            enabled,
            disabled_reason=self._selection_required_tip,
        )
        self._set_control_enabled(
            self._device_clear,
            enabled,
            disabled_reason=self._selection_required_tip,
        )
        self._log_live_lines.setEnabled(enabled)
        self._set_control_enabled(self._log_live_button, enabled, disabled_reason=self._selection_required_tip)
        self._start_delay.setEnabled(enabled)
        self._start_delay_label.setEnabled(enabled)
        self._update_brightness_controls_state()
        self._update_brightness_tooltip()
        self._update_start_sync_enabled()

    def set_media_items(self, items: list[dict]) -> None:
        self._media_list.clear()
        for item in items:
            # Filtra per estensioni consentite (video/immagini)
            label = str(item.get("label") or "")
            p = str(item.get("path") or label)
            if not self._is_allowed_media(p):
                continue
            list_item = QListWidgetItem(label or p)
            list_item.setData(Qt.UserRole, item.get("path") or p)
            list_item.setData(self._RELATIVE_ROLE, item.get("relative"))
            # store duration mapping for total TC (keyed by relative or basename)
            if item.get("relative") and isinstance(item.get("duration"), (int, float)):
                self._media_durations[str(item["relative"])] = float(item["duration"])
            elif isinstance(item.get("duration"), (int, float)):
                from pathlib import Path as _Path
                try:
                    self._media_durations[_Path(item.get("path") or p).name] = float(item["duration"])
                except Exception:
                    pass
            self._media_list.addItem(list_item)
        if self._media_list.count() > 0:
            self._media_list.setCurrentRow(0)
        self._update_upload_button(self._targets_enabled)
        # Playlist: abilita Aggiungi se ci sono elementi e target
        add_reason = None
        if not self._targets_enabled:
            add_reason = self._selection_required_tip
        elif self._media_list.count() == 0:
            add_reason = "Aggiungi media nella libreria per popolare la playlist"
        self._set_control_enabled(
            self._add_to_playlist,
            self._targets_enabled and self._media_list.count() > 0,
            disabled_reason=add_reason,
        )
        # update total TC after media refresh
        self._update_total_tc()

    def set_playlist_from_player(self, items: list[str], current_index: int = 0, *, loop: bool | None = None) -> None:
        """Popola la playlist con gli item dal player selezionato.
        items: lista di path/filename relativi
        current_index: indice corrente nella playlist del player
        """
        # Se l'utente ha modifiche locali pending, non sovrascrivere
        if self._playlist_dirty:
            return
        try:
            self._playlist.clear()
            self._downloads_in_progress.clear()
            for idx, item_path in enumerate(items):
                try:
                    # Usa solo il basename per il label
                    from pathlib import Path as _Path
                    label = _Path(item_path).name
                except Exception:
                    label = str(item_path)
                it = QListWidgetItem(label)
                it.setData(Qt.UserRole, str(item_path))
                # Evidenzia l'item corrente
                if idx == current_index:
                    try:
                        font = it.font()
                        font.setBold(True)
                        it.setFont(font)
                    except Exception:
                        pass
                self._playlist.addItem(it)
            # Aggiorna il totale TC
            self._update_total_tc()
            # Stato LED grigio (sincronizzato)
            self._set_playlist_led_color("gray")
            self._playlist_dirty = False
            if loop is not None:
                self.set_playlist_loop(bool(loop))
            # Re-applica eventuali warning su elementi mancanti
            self._apply_playlist_validation()
            self._update_playlist_controls_state()
        except Exception as exc:
            print(f"[GUI] Errore popolamento playlist: {exc}", flush=True)

    def set_media_root(self, root: Path | str | None) -> None:
        """Aggiorna il media root locale e mostra un avviso se mancante."""
        path: Path | None
        if isinstance(root, Path):
            path = root if str(root) else None
        elif isinstance(root, str):
            stripped = root.strip()
            if not stripped or stripped == "<non impostata>":
                path = None
            else:
                try:
                    path = Path(stripped)
                except Exception:
                    path = None
        else:
            path = None
        self._media_root = path
        warning = "Cartella media (file server) non configurata o non raggiungibile: verifica Settings → Cartella media"
        try:
            self._media_root_valid = bool(path and path.exists())
        except Exception:
            self._media_root_valid = False
        if self._media_root_valid:
            was_warning = self._banner_label.text().strip() == warning
            self._banner_fallback = None
            self._media_root_warning = None
            if was_warning and not self._playlist_banner_validation:
                self.set_banner(None)
        else:
            self._banner_fallback = (warning, "error")
            self._media_root_warning = warning
        if not self._banner_label.text().strip():
            self.set_banner(None)
        self._update_playlist_controls_state()

    def set_playback_loop(self, loop: bool | None, multi_count: int | None = None) -> None:
        if loop is not None:
            self._loop_checkbox.blockSignals(True)
            try:
                self._loop_checkbox.setChecked(bool(loop))
            finally:
                self._loop_checkbox.blockSignals(False)
        # Multi-target cue
        base_tip = "Ripeti traccia corrente (loop riproduzione)"
        if isinstance(multi_count, int) and multi_count > 1:
            self._loop_checkbox.setStyleSheet("font-weight: 600; background-color: #fff4e5; border: 1px solid #f39c12;")
            self._loop_checkbox.setToolTip(f"{base_tip} (si applica a {multi_count} player)")
        else:
            self._loop_checkbox.setStyleSheet("font-weight: 600;")
            self._loop_checkbox.setToolTip(base_tip)

    def set_playlist_loop(self, loop: bool | None, multi_count: int | None = None) -> None:
        if loop is not None:
            self._playlist_loop_checkbox.blockSignals(True)
            try:
                self._playlist_loop_checkbox.setChecked(bool(loop))
            finally:
                self._playlist_loop_checkbox.blockSignals(False)
            # Mirror promoted toggle if present
            try:
                if hasattr(self, "_playlist_loop_toggle") and self._playlist_loop_toggle is not None:
                    self._playlist_loop_toggle.blockSignals(True)
                    try:
                        self._playlist_loop_toggle.setChecked(bool(loop))
                    finally:
                        self._playlist_loop_toggle.blockSignals(False)
            except Exception:
                pass
        # Multi-target cue
        base_tip = "Ripeti playlist (loop playlist)"
        if isinstance(multi_count, int) and multi_count > 1:
            self._playlist_loop_checkbox.setStyleSheet("font-weight: 600; background-color: #fff4e5; border: 1px solid #f39c12;")
            self._playlist_loop_checkbox.setToolTip(f"{base_tip} (si applica a {multi_count} player)")
            try:
                if hasattr(self, "_playlist_loop_toggle") and self._playlist_loop_toggle is not None:
                    self._playlist_loop_toggle.setStyleSheet("font-weight: 600; background-color: #fff4e5; border: 1px solid #f39c12;")
                    self._playlist_loop_toggle.setToolTip(f"{base_tip} (si applica a {multi_count} player)")
            except Exception:
                pass
        else:
            self._playlist_loop_checkbox.setStyleSheet("font-weight: 600;")
            self._playlist_loop_checkbox.setToolTip(base_tip)
            try:
                if hasattr(self, "_playlist_loop_toggle") and self._playlist_loop_toggle is not None:
                    self._playlist_loop_toggle.setStyleSheet("font-weight: 600;")
                    self._playlist_loop_toggle.setToolTip(base_tip)
            except Exception:
                pass

    def set_playlist_validation(self, missing: Sequence[str] | None, invalid: Sequence[str] | None) -> None:
        missing_list = [str(item) for item in (missing or [])]
        invalid_list = [str(item) for item in (invalid or [])]
        self._playlist_missing = missing_list
        self._playlist_invalid = invalid_list
        self._apply_playlist_validation()
        self._update_playlist_controls_state()
        if missing_list or invalid_list:
            sample_src = missing_list if missing_list else invalid_list
            sample_names: list[str] = []
            for value in sample_src[:2]:
                try:
                    sample_names.append(Path(value).name)
                except Exception:
                    sample_names.append(str(value))
            suffix = f" ({', '.join(sample_names)})" if sample_names else ""
            if missing_list:
                msg = f"{len(missing_list)} asset mancanti" + suffix
                self.set_banner(msg, level="error")
                self._set_playlist_led_color("red")
            else:
                msg = f"{len(invalid_list)} elementi non validi" + suffix
                self.set_banner(msg, level="warning")
                self._set_playlist_led_color("orange")
            self._playlist_banner_validation = True
        elif self._playlist_banner_validation:
            self._playlist_banner_validation = False
            self.set_banner(None)

    def _clear_playlist_validation(self) -> None:
        if not self._playlist_missing and not self._playlist_invalid:
            return
        self._playlist_missing = []
        self._playlist_invalid = []
        self._apply_playlist_validation()
        if self._playlist_banner_validation:
            self._playlist_banner_validation = False
            self.set_banner(None)

    def set_brightness(self, value: float | None, multi_count: int | None = None) -> None:
        """Update brightness slider from player status. Value in 0..1."""
        if value is None:
            return
        try:
            pct = int(max(0.0, min(1.0, float(value))) * 100.0 + 0.5)
        except Exception:
            return
        pct = max(0, min(100, pct))
        self._brightness_last_multi_count = (
            int(multi_count) if isinstance(multi_count, int) and multi_count > 0 else None
        )
        if self._brightness_dirty:
            # Keep remote update queued until the local change is applied
            self._brightness_pending_remote = pct
            self._update_brightness_tooltip()
            return
        self._brightness_pending_remote = None
        self._apply_remote_brightness(pct)
        self._set_brightness_dirty(False)
        self._update_brightness_controls_state()
        self._update_brightness_tooltip()

    def _apply_remote_brightness(self, pct: int) -> None:
        try:
            value = int(pct)
        except Exception:
            value = pct
        value = max(0, min(100, int(value)))
        self._brightness_updating = True
        try:
            self._brightness_slider.blockSignals(True)
            self._brightness_slider.setValue(value)
        finally:
            self._brightness_slider.blockSignals(False)
            self._brightness_updating = False

    def _set_brightness_dirty(self, dirty: bool, *, value: int | None = None) -> None:
        self._brightness_dirty = dirty
        if dirty:
            if value is None:
                try:
                    value = int(self._brightness_slider.value())
                except Exception:
                    value = None
            if value is not None:
                self._brightness_apply.setText(f"Imposta ({int(value)}%)")
            highlight_slider = (
                f"{self._brightness_base_slider_style}\n"
                "QSlider::groove:horizontal { background-color: #fcebd6; height: 6px; }\n"
                "QSlider::handle:horizontal { background-color: #e67e22; border: 1px solid #d35400; border-radius: 7px; width: 14px; margin: -4px 0; }"
            ).strip()
            self._brightness_slider.setStyleSheet(highlight_slider)
            highlight_button = (
                f"{self._brightness_apply_base_style}\n"
                "QPushButton { background-color: #f9e79f; border: 1px solid #d4ac0d; font-weight: 600; }"
            ).strip()
            self._brightness_apply.setStyleSheet(highlight_button)
        else:
            self._brightness_apply.setText(self._brightness_apply_default_text)
            self._brightness_slider.setStyleSheet(self._brightness_base_slider_style)
            self._brightness_apply.setStyleSheet(self._brightness_apply_base_style)

    def _clear_brightness_dirty(self, *, apply_pending_remote: bool = True) -> None:
        self._set_brightness_dirty(False)
        if apply_pending_remote and self._brightness_pending_remote is not None:
            self._apply_remote_brightness(self._brightness_pending_remote)
        self._brightness_pending_remote = None
        self._update_brightness_controls_state()
        self._update_brightness_tooltip()

    def _update_brightness_controls_state(self) -> None:
        slider_reason = self._selection_required_tip if not self._targets_enabled else None
        self._set_control_enabled(
            self._brightness_slider,
            self._targets_enabled,
            disabled_reason=slider_reason,
        )
        self._set_control_enabled(
            self._brightness_seconds,
            self._targets_enabled,
            disabled_reason=slider_reason,
        )
        apply_enabled = self._targets_enabled and self._brightness_dirty
        if not self._targets_enabled:
            apply_reason = self._selection_required_tip
        elif not self._brightness_dirty:
            apply_reason = "Modifica la luminosità per abilitarne l'invio"
        else:
            apply_reason = None
        self._set_control_enabled(
            self._brightness_apply,
            apply_enabled,
            disabled_reason=apply_reason,
        )

    def _update_brightness_tooltip(self) -> None:
        slider_tip = "Luminosità 0–100%"
        if self._brightness_dirty:
            slider_tip += "\nValore locale in attesa: premi 'Imposta' per inviarlo"
        elif self._brightness_pending_remote is not None:
            slider_tip += f"\nAggiornamento remoto in attesa: {self._brightness_pending_remote}%"
        elif isinstance(self._brightness_last_multi_count, int) and self._brightness_last_multi_count > 1:
            slider_tip += f"\nValore mostrato per il primario ({self._brightness_last_multi_count} selezionati)"
        self._brightness_slider.setToolTip(slider_tip)

        seconds_tip = "Durata transizione luminosità (s)"
        if self._brightness_dirty:
            seconds_tip += "\nSi applica al valore locale quando premi 'Imposta'"
        self._brightness_seconds.setToolTip(seconds_tip)

        apply_tip = "Applica luminosità ai player selezionati"
        if not self._targets_enabled:
            apply_tip = self._selection_required_tip
        elif not self._brightness_dirty:
            apply_tip += "\nNessuna modifica locale da inviare"
        self._brightness_apply.setToolTip(apply_tip)

    def _on_brightness_slider_pressed(self) -> None:
        if not self._targets_enabled:
            return
        self._brightness_pending_remote = None

    def _on_brightness_value_changed(self, value: int) -> None:
        if self._brightness_updating:
            return
        try:
            pct = int(value)
        except Exception:
            pct = None
        self._brightness_pending_remote = None
        self._set_brightness_dirty(True, value=pct)
        self._update_brightness_controls_state()
        self._update_brightness_tooltip()

    def _on_brightness_slider_released(self) -> None:
        if not self._brightness_dirty:
            return
        self._update_brightness_tooltip()

    def set_display_mode(self, mode: dict | None, multi_count: int | None = None) -> None:
        """Aggiorna l'etichetta con la risoluzione corrente del player."""
        label_widget = getattr(self, "_display_label", None)
        if label_widget is None:
            return

        base_style = "padding: 1px 4px;"

        if not isinstance(mode, dict):
            label_widget.setText("—")
            label_widget.setStyleSheet(base_style + " color: #7f8c8d; font-style: italic;")
            tip = "Risoluzione corrente non disponibile"
            if isinstance(multi_count, int) and multi_count > 1:
                tip += f" (mostrata per il primario; {multi_count} selezionati)"
            label_widget.setToolTip(tip)
            return

        def _as_int(value: Any) -> int | None:
            try:
                if value is None or value == "":
                    return None
                return int(float(value))
            except Exception:
                return None

        def _as_float(value: Any) -> float | None:
            try:
                if value is None or value == "":
                    return None
                return float(value)
            except Exception:
                return None

        width = _as_int(mode.get("width"))
        height = _as_int(mode.get("height"))
        valid_flag = mode.get("valid")
        valid = bool(valid_flag) if valid_flag is not None else bool(width and height)
        if not valid:
            label_widget.setText("—")
            label_widget.setStyleSheet(base_style + " color: #7f8c8d; font-style: italic;")
            tip = "Risoluzione corrente non disponibile"
            if isinstance(multi_count, int) and multi_count > 1:
                tip += f" (mostrata per il primario; {multi_count} selezionati)"
            label_widget.setToolTip(tip)
            return

        refresh = _as_float(mode.get("refresh_hz") if mode.get("refresh_hz") is not None else mode.get("refresh"))
        interlaced = bool(mode.get("interlaced"))
        label = mode.get("label") if isinstance(mode.get("label"), str) else None
        if not label:
            if width and height:
                label = f"{width}x{height}"
                if isinstance(refresh, (int, float)) and refresh > 0:
                    rounded = round(refresh)
                    if abs(refresh - rounded) < 0.05:
                        label += f" @ {int(rounded)} Hz"
                    else:
                        label += f" @ {refresh:.2f} Hz"
                if interlaced:
                    label += " (interlaced)"
            else:
                label = "—"
        label_widget.setText(label)

        matches_target = mode.get("matches_target")
        target_width = _as_int(mode.get("target_width"))
        target_height = _as_int(mode.get("target_height"))

        style = base_style + " font-style: normal;"
        if isinstance(matches_target, bool) and not matches_target and target_width and target_height:
            style += " background-color: #fdecea; border: 1px solid #e74c3c; color: #c0392b;"
        else:
            style += " color: #2c3e50;"
        label_widget.setStyleSheet(style)

        tooltip_lines: list[str] = []
        if width and height:
            tooltip_lines.append(f"Risoluzione: {width}x{height}")
        if isinstance(refresh, (int, float)) and refresh > 0:
            rounded = round(refresh)
            if abs(refresh - rounded) < 0.05:
                tooltip_lines.append(f"Refresh: {int(rounded)} Hz")
            else:
                tooltip_lines.append(f"Refresh: {refresh:.2f} Hz")
        if interlaced:
            tooltip_lines.append("Interlacciato: sì")
        if target_width and target_height:
            tooltip_lines.append(f"Target richiesto: {target_width}x{target_height}")
        target_fps = mode.get("target_fps")
        if isinstance(target_fps, str) and target_fps:
            tooltip_lines.append(f"Target fps: {target_fps}")
        source = mode.get("source")
        if isinstance(source, str) and source:
            tooltip_lines.append(f"Sorgente: {source}")
        age_ms = mode.get("age_ms")
        if isinstance(age_ms, (int, float)) and age_ms >= 0:
            tooltip_lines.append(f"Aggiornato {age_ms / 1000.0:.1f}s fa")
        if isinstance(matches_target, bool) and not matches_target and target_width and target_height:
            tooltip_lines.append("ATTENZIONE: risoluzione diversa dal target")
        if isinstance(multi_count, int) and multi_count > 1:
            tooltip_lines.append(f"Valore mostrato per il player primario ({multi_count} selezionati)")

        tooltip = "\n".join(tooltip_lines) if tooltip_lines else "Risoluzione corrente"
        label_widget.setToolTip(tooltip)

    def _emit_brightness_command(self) -> None:
        if not self._targets_enabled or not self._brightness_dirty:
            return
        try:
            value = int(self._brightness_slider.value())
        except Exception:
            value = 100
        seconds = float(self._brightness_seconds.value()) if hasattr(self, "_brightness_seconds") else 0.5
        payload = {"value": value, "seconds": seconds}
        self.miscCommandTriggered.emit("brightness", payload)
        self._clear_brightness_dirty(apply_pending_remote=False)

    def _update_playlist_controls_state(self) -> None:
        has_items = self._playlist.count() > 0
        has_selection = self._playlist.currentRow() >= 0
        valid_playlist = has_items and not self._playlist_missing
        can_push = self._targets_enabled and has_items and self._media_root_valid
        if not self._targets_enabled:
            push_reason = self._selection_required_tip
        elif not has_items:
            push_reason = "La playlist è vuota"
        elif not self._media_root_valid and self._media_root_warning:
            push_reason = self._media_root_warning
        else:
            push_reason = None
        self._set_control_enabled(
            self._push_playlist,
            can_push,
            disabled_reason=push_reason,
        )
        if not self._targets_enabled:
            start_reason = self._selection_required_tip
        elif not valid_playlist:
            start_reason = "Aggiorna o sincronizza la playlist prima di avviare lo show"
        else:
            start_reason = None
        self._set_control_enabled(
            self._start_show,
            self._targets_enabled and valid_playlist,
            disabled_reason=start_reason,
        )
        self._update_jump_buttons()

    def _apply_playlist_validation(self) -> None:
        missing_lookup = self._build_validation_lookup(self._playlist_missing)
        invalid_lookup = self._build_validation_lookup(self._playlist_invalid)
        for idx in range(self._playlist.count()):
            item = self._playlist.item(idx)
            if item is None:
                continue
            self._reset_playlist_item_style(item)
            raw = item.data(Qt.UserRole) or item.text()
            ref = self._normalize_validation_key(str(raw))
            try:
                base = Path(ref).name
            except Exception:
                base = ref
            issues: list[str] = []
            warnings: list[str] = []
            if ref in missing_lookup or base in missing_lookup:
                issues.append("Asset non trovato sul file server")
            if ref in invalid_lookup or base in invalid_lookup:
                warnings.append("Elemento playlist non valido")
            if issues:
                self._flag_playlist_item(item, QColor("#ffe6e6"), QColor("#c0392b"))
            elif warnings:
                self._flag_playlist_item(item, QColor("#fff3cd"), QColor("#7f6000"))
            if issues or warnings:
                item.setToolTip("; ".join(issues + warnings))
            else:
                item.setToolTip("")
        self._apply_playlist_availability_styles()

    @staticmethod
    def _normalize_validation_key(value: str) -> str:
        key = str(value or "")
        return key.replace("\\", "/")

    def _build_validation_lookup(self, entries: Sequence[str]) -> set[str]:
        lookup: set[str] = set()
        for raw in entries or []:
            key = self._normalize_validation_key(str(raw))
            lookup.add(key)
            try:
                lookup.add(Path(key).name)
            except Exception:
                if "/" in key:
                    lookup.add(key.split("/")[-1])
        return lookup

    def _reset_playlist_item_style(self, item: QListWidgetItem) -> None:
        try:
            item.setData(Qt.BackgroundRole, None)
            item.setData(Qt.ForegroundRole, None)
        except Exception:
            pass

    def _flag_playlist_item(self, item: QListWidgetItem, background: QColor, foreground: QColor) -> None:
        try:
            item.setData(Qt.BackgroundRole, QBrush(background))
            item.setData(Qt.ForegroundRole, QBrush(foreground))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_misc_entries(self) -> list[dict[str, Any]]:
        return [
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
                "label": "Display: Forza 1280x720",
                "command": "force_1280x720",
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
        if command in {"next", "prev"}:
            total = self._playlist.count()
            if total <= 1:
                self.set_banner("Playlist con un solo elemento: comando ignorato", level="warning")
                return
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
        # Ping duration is now controlled from Settings; controller applies default
        # Feedback se next a fine playlist
        if command == "next":
            # Se la playlist è vuota o siamo già all'ultimo elemento
            if self._playlist.count() == 0 or self._playlist.currentRow() >= self._playlist.count() - 1:
                self.set_banner("Fine playlist: nessun elemento successivo", level="info")
                return

        self.playbackTriggered.emit(command, payload)
        if command == "stop":
            try:
                self.miscCommandTriggered.emit("media_clear", {})
            except Exception:
                pass

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

    def _emit_test_on(self) -> None:
        if not self._targets_enabled:
            return
        payload: dict[str, Any] = {}
        width = int(self._test_width.value())
        height = int(self._test_height.value())
        if width > 0:
            payload["width"] = width
        if height > 0:
            payload["height"] = height
        payload["offsetX"] = int(self._test_offset_x.value())
        payload["offsetY"] = int(self._test_offset_y.value())
        self.miscCommandTriggered.emit("test_on", payload)

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

    def _update_start_sync_enabled(self) -> None:
        button = getattr(self, "_start_sync", None)
        if button is None:
            return
        if self._timing_syncing:
            self._set_control_enabled(button, False, disabled_reason="Sincronizzazione in corso…")
            button.setText("Sync…")
        else:
            reason = None if self._targets_enabled else self._selection_required_tip
            self._set_control_enabled(button, self._targets_enabled, disabled_reason=reason)
            button.setText("Start Sync")

    def set_timing_status(self, ok: bool | None, text: str | None = None, *, blink: bool = False, syncing: bool = False) -> None:
        """Update timing LED and optional text.
        ok=True -> green, ok=False -> red, ok=None -> gray.
        """
        previous_sync = self._timing_syncing
        self._timing_syncing = bool(syncing)
        if self._timing_syncing and text is None:
            text = "Sync…"
        self._update_start_sync_enabled()
        if self._timing_syncing:
            color = "#f39c12"  # orange (in progress)
        elif ok is True:
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
        if previous_sync and not self._timing_syncing:
            # Restore neutral label if backend stopped syncing and no custom text provided
            if not text:
                self._timing_text.setText("—")

    def set_faststart_ready(self, ready: bool) -> None:
        """Highlight Fast-Start Prepare button when backend reports ready."""
        if ready:
            self._fs_prepare.setStyleSheet("background-color: #d4edda; border: 1px solid #52a057;")
        else:
            self._fs_prepare.setStyleSheet("")

    def set_upload_activity(self, active_count: int) -> None:
        """Show/hide the upload progress bar based on in-flight uploads."""
        try:
            n = int(active_count)
        except Exception:
            n = 0
        if hasattr(self, "_upload_progress"):
            if n > 0:
                self._upload_progress.setRange(0, 0)
                self._upload_progress.setVisible(True)
                try:
                    if hasattr(self, "_upload_progress_label"):
                        self._upload_progress_label.setText(f"Upload in corso… ({n})")
                        self._upload_progress_label.setVisible(True)
                except Exception:
                    pass
                # Mark start time if first activation
                if self._upload_started_at is None:
                    try:
                        self._upload_started_at = _time.time()
                    except Exception:
                        self._upload_started_at = None
            else:
                self._upload_progress.setVisible(False)
                self._upload_progress.setRange(0, 1)
                try:
                    if hasattr(self, "_upload_progress_label"):
                        self._upload_progress_label.clear()
                        self._upload_progress_label.setVisible(False)
                except Exception:
                    pass
                # Reset ETA start timestamp
                self._upload_started_at = None

    def set_upload_progress(self, done: int, total: int, in_flight: int) -> None:
        """Update numeric label with done/total and keep bar visible while in-flight > 0."""
        try:
            d = max(0, int(done))
            t = max(0, int(total))
            f = max(0, int(in_flight))
        except Exception:
            d = int(done) if isinstance(done, int) else 0
            t = int(total) if isinstance(total, int) else 0
            f = int(in_flight) if isinstance(in_flight, int) else 0
        if hasattr(self, "_upload_progress_label") and self._upload_progress_label is not None:
            if t > 0 or f > 0:
                # Compute ETA if we have a start timestamp and at least 1 completed
                eta_txt = None
                try:
                    if self._upload_started_at is not None and d > 0 and t >= d:
                        elapsed = max(0.0, _time.time() - float(self._upload_started_at))
                        per_item = elapsed / float(d)
                        remaining = max(0.0, (t - d) * per_item)
                        # format mm:ss or h:mm:ss
                        rem = int(remaining)
                        h = rem // 3600; m = (rem % 3600) // 60; s = rem % 60
                        if h > 0:
                            eta_txt = f"{h:d}:{m:02d}:{s:02d}"
                        else:
                            eta_txt = f"{m:02d}:{s:02d}"
                except Exception:
                    eta_txt = None
                txt = f"Upload: {d}/{t}"
                if f > 0:
                    txt += f" (+{f})"
                if eta_txt:
                    txt += f" · ETA {eta_txt}"
                self._upload_progress_label.setText(txt)
                self._upload_progress_label.setVisible(True)
            else:
                self._upload_progress_label.clear()
                self._upload_progress_label.setVisible(False)
        # Keep bar visibility in sync with activity
        self.set_upload_activity(f)

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

    # ------------------------------------------------------------------
    # HUD visible (OFF-player)
    # ------------------------------------------------------------------
    def set_hud_state(self, mode: int | None, multi_count: int | None = None) -> None:
        """Reflect HUD mode selection and multi-target hint.
        - mode: 0 hidden, 1 minimal, 2 full; None keeps current selection.
        - multi_count: if >1, highlight combo to indicate multi-apply.
        """
        try:
            combo = getattr(self, "_hud_mode_combo", None)
            if combo is None:
                return
            if isinstance(mode, (int, float)):
                idx = int(mode)
                if idx < 0:
                    idx = 0
                if idx > 2:
                    idx = 2
                combo.blockSignals(True)
                try:
                    combo.setCurrentIndex(idx)
                finally:
                    combo.blockSignals(False)
            base_tip = "Seleziona la modalità HUD del player"
            if isinstance(multi_count, int) and multi_count > 1:
                combo.setStyleSheet("background-color: #fff4e5; border: 1px solid #f39c12;")
                combo.setToolTip(f"{base_tip} (si applica a {multi_count} player)")
            else:
                combo.setStyleSheet(getattr(self, "_hud_mode_base_style", ""))
                combo.setToolTip(base_tip)
        except Exception:
            pass

    def _on_hud_mode_changed(self, index: int) -> None:
        """Dispatch HUD mode change to controller when user adjusts the combo."""
        try:
            combo = getattr(self, "_hud_mode_combo", None)
            if combo is None or not combo.isEnabled():
                return
        except Exception:
            return
        try:
            mode = int(index)
        except Exception:
            mode = 0
        if mode < 0:
            mode = 0
        if mode > 2:
            mode = 2
        try:
            self.miscCommandTriggered.emit("hud_visible", {"mode": mode})
        except Exception:
            pass

    # Upload via doppio click o drag&drop; pulsante Upload rimosso

    def _open_media_picker(self) -> None:
        """Apre un file dialog filtrato su immagini/video e invia direttamente l'upload."""
        try:
            start_dir = str(self._media_root) if (self._media_root and self._media_root.exists()) else str(Path.home())
        except Exception:
            start_dir = str(Path.home())
        patterns = " ".join(sorted(self._allowed_patterns()))
        filter_str = f"Media ( {patterns} );;Tutti i file (*)"
        paths, _ = QFileDialog.getOpenFileNames(self, "Seleziona media da caricare", start_dir, filter_str)
        if not paths:
            return
        # Centralizza: usa il segnale condiviso per tutti gli upload da filesystem
        try:
            self.filesDroppedForUpload.emit(paths)
        except Exception:
            # Fallback: mantieni il vecchio comportamento se il segnale non è connesso
            for p in paths:
                try:
                    self.uploadRequested.emit(p)
                except Exception:
                    pass

    def _on_media_selection_changed(self) -> None:
        self._update_upload_button(self._targets_enabled)
        self._update_faststart_enabled()

    def _update_misc_button(self, enabled: bool) -> None:
        has_command = self._command_selector.currentIndex() > 0
        if not enabled:
            reason = self._selection_required_tip
        elif not has_command:
            reason = "Seleziona un comando dall'elenco per procedere"
        else:
            reason = None
        can_run = enabled and has_command
        self._set_control_enabled(self._execute_button, can_run, disabled_reason=reason)

    def _update_upload_button(self, targets_enabled: bool) -> None:
        # Upload button rimosso: mantieni logica playlist
        self._update_playlist_controls_state()

    def _update_faststart_enabled(self) -> None:
        """Abilita/Disabilita il pulsante Prepare in base alle selezioni correnti.
        Aggiorna anche l'etichetta sorgente (Device / Locale / -).
        """
        has_dev = self._device_media_list.currentItem() is not None
        has_local = self._media_list.currentItem() is not None
        enabled = self._targets_enabled and (has_dev or has_local)
        try:
            if not self._targets_enabled:
                reason = self._selection_required_tip
            elif not (has_dev or has_local):
                reason = "Seleziona un media locale o dal device per il Fast-Start"
            else:
                reason = None
            self._set_control_enabled(self._fs_prepare, enabled, disabled_reason=reason)
            if has_dev:
                src = "Device"
            elif has_local:
                src = "Locale"
            else:
                src = "-"
            self._fs_source.setText(src)
            if not enabled:
                self._fs_source.setStyleSheet("color: #7f8c8d; font-style: italic;")
            else:
                self._fs_source.setStyleSheet("")
        except Exception:
            pass
            self._fs_source.setStyleSheet("")

    def _on_playlist_loop_toggled(self, checked: bool) -> None:
        """Dispatch playlist loop command and mirror hidden checkbox without feedback loops."""
        try:
            self.miscCommandTriggered.emit("playlist_loop_on" if checked else "playlist_loop_off", {})
        except Exception:
            pass
        try:
            if hasattr(self, "_playlist_loop_checkbox") and self._playlist_loop_checkbox is not None:
                self._playlist_loop_checkbox.blockSignals(True)
                try:
                    self._playlist_loop_checkbox.setChecked(bool(checked))
                finally:
                    self._playlist_loop_checkbox.blockSignals(False)
        except Exception:
            pass
            self._set_control_enabled(
                self._fs_go,
                self._targets_enabled,
                disabled_reason=self._selection_required_tip,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Playlist helpers
    # ------------------------------------------------------------------
    def _add_selected_to_playlist(self) -> None:
        items = self._media_list.selectedItems()
        if not items:
            return
        added_rel: list[str] = []
        for item in items:
            label = item.text()
            rel = item.data(self._RELATIVE_ROLE) or item.data(Qt.UserRole)
            if not rel:
                continue
            entry = QListWidgetItem(label)
            entry.setData(Qt.UserRole, rel)
            self._playlist.addItem(entry)
            added_rel.append(str(rel))
        if added_rel:
            self._handle_new_playlist_entries(added_rel)
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

    def _on_playlist_items_added(self, items: list[str]) -> None:
        if items:
            self._handle_new_playlist_entries(items)
        self._emit_playlist_changed()

    def _emit_playlist_changed(self) -> None:
        self._clear_playlist_validation()
        items: list[str] = []
        for i in range(self._playlist.count()):
            it = self._playlist.item(i)
            rel = it.data(Qt.UserRole) or it.text()
            items.append(str(rel))
        current_keys = {self._normalize_validation_key(rel) for rel in items}
        for key in list(self._downloads_in_progress):
            if key not in current_keys:
                self._downloads_in_progress.discard(key)
        # Marca la playlist come modificata localmente
        self._playlist_dirty = True
        # LED rosso (dirty)
        self._set_playlist_led_color("red")
        self.playlistChanged.emit(items)
        self._update_total_tc()
        self._update_playlist_controls_state()
        self._apply_playlist_availability_styles()

    def _handle_new_playlist_entries(self, rel_items: Sequence[str]) -> None:
        """Gestisce i media aggiunti alla playlist lato GUI.
        Se non presenti sul device, avvia automaticamente il download/upload.
        """
        if not rel_items:
            return
        to_transfer: list[str] = []
        for rel in rel_items:
            if rel is None:
                continue
            key = self._normalize_validation_key(str(rel))
            base = self._playlist_basename(key)
            norm_base = base.lower() if base else ""
            if norm_base and self._device_media_seen and norm_base in self._device_media_names:
                continue
            to_transfer.append(str(rel))
        if not to_transfer:
            return
        if not self._targets_enabled:
            self.set_banner("Seleziona almeno un player per scaricare i nuovi asset", level="warning")
            return
        self._trigger_auto_downloads(to_transfer)

    def _trigger_auto_downloads(self, rel_items: Sequence[str]) -> None:
        started = False
        missing_local: list[str] = []
        for rel in rel_items:
            key = self._normalize_validation_key(str(rel))
            if key in self._downloads_in_progress:
                continue
            media_path = self._resolve_media_path(str(rel))
            if not media_path or not media_path.exists():
                missing_local.append(str(rel))
                continue
            self._downloads_in_progress.add(key)
            self.uploadRequested.emit(str(media_path))
            started = True
        if missing_local:
            sample = ", ".join(self._playlist_basename(p) or p for p in missing_local[:2])
            self.set_banner(f"File locali non trovati: {sample}", level="error")
        if started:
            # Aggiorna immediatamente lo stato visivo
            self._apply_playlist_availability_styles()

    def _resolve_media_path(self, rel: str) -> Path | None:
        try:
            raw = Path(rel)
        except Exception:
            raw = None
        if raw and raw.is_absolute():
            return raw
        if not self._media_root:
            return None
        try:
            candidate = (self._media_root / Path(rel))
        except Exception:
            return None
        try:
            return candidate.resolve()
        except Exception:
            return candidate

    def _apply_playlist_availability_styles(self) -> None:
        """Evidenzia gli elementi non presenti sul device o in download."""
        if not self._downloads_in_progress and not self._device_media_seen:
            self._clear_availability_highlight()
            return
        total = self._playlist.count()
        for idx in range(total):
            item = self._playlist.item(idx)
            if item is None:
                continue
            self._reset_availability_style(item)
            # Se già marcato da validation server-side lascia priorità a quel colore
            if item.data(Qt.BackgroundRole) is not None:
                continue
            raw = item.data(Qt.UserRole) or item.text()
            key = self._normalize_validation_key(str(raw))
            base = self._playlist_basename(key)
            base_norm = base.lower() if base else ""
            if base_norm and self._device_media_seen and base_norm in self._device_media_names:
                self._downloads_in_progress.discard(key)
                continue
            if key in self._downloads_in_progress:
                self._flag_availability(item, status="downloading")
            elif self._device_media_seen and base_norm and base_norm not in self._device_media_names:
                self._flag_availability(item, status="missing")

    def _clear_availability_highlight(self) -> None:
        for idx in range(self._playlist.count()):
            item = self._playlist.item(idx)
            if item:
                self._reset_availability_style(item)

    def _reset_availability_style(self, item: QListWidgetItem) -> None:
        if item.data(_AVAILABILITY_ROLE):
            item.setData(Qt.BackgroundRole, None)
            item.setData(Qt.ForegroundRole, None)
            item.setData(_AVAILABILITY_ROLE, None)

    def _flag_availability(self, item: QListWidgetItem, *, status: str) -> None:
        if status == "downloading":
            self._flag_playlist_item(item, QColor("#fff4e5"), QColor("#a55d00"))
        else:
            self._flag_playlist_item(item, QColor("#ffe6e6"), QColor("#c0392b"))
        item.setData(_AVAILABILITY_ROLE, status)

    def _normalize_device_basename(self, name: str | None) -> str:
        if not name:
            return ""
        try:
            base = Path(str(name)).name
        except Exception:
            base = str(name)
        return base.strip().lower()

    def _playlist_basename(self, value: str | None) -> str:
        if not value:
            return ""
        try:
            return Path(value).name
        except Exception:
            return str(value)

    def _emit_push_playlist(self) -> None:
        items: list[str] = []
        for i in range(self._playlist.count()):
            it = self._playlist.item(i)
            rel = it.data(Qt.UserRole) or it.text()
            items.append(str(rel))
        if not items:
            return
        # Reset del flag dirty (stiamo inviando al player)
        self._playlist_dirty = False
        # LED arancione (in progress)
        self._set_playlist_led_color("orange")
        # Determine playlist loop from promoted toggle if available
        try:
            if hasattr(self, "_playlist_loop_checkbox") and self._playlist_loop_checkbox is not None:
                pl_loop = bool(self._playlist_loop_checkbox.isChecked())
            elif hasattr(self, "_playlist_loop_toggle") and self._playlist_loop_toggle is not None:
                pl_loop = bool(self._playlist_loop_toggle.isChecked())
            else:
                pl_loop = False
        except Exception:
            pl_loop = False
        self.playlistPushRequested.emit(items, self._clear_before_push.isChecked(), pl_loop)

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
        level in {info, progress, success, error, warning} controls the style.
        """
        colors = {
            "info": "#2c3e50",
            "progress": "#8e44ad",
            "success": "#2ecc71",
            "error": "#e74c3c",
            "warning": "#f39c12",
        }
        if not text:
            if self._banner_fallback:
                fb_text, fb_level = self._banner_fallback
                color = colors.get(fb_level, "#2c3e50")
                self._banner_label.setStyleSheet(f"color: {color}; font-weight: 500;")
                self._banner_label.setText(fb_text)
            else:
                self._banner_label.setText("")
                self._banner_label.setStyleSheet("color: #333333;")
            return
        color = colors.get(level, "#2c3e50")
        self._banner_label.setStyleSheet(f"color: {color}; font-weight: 500;")
        self._banner_label.setText(text)

    # Overlay probing rimosso: nessuna API di aggiornamento necessaria

    # ------------------------------------------------------------------
    # UI helpers (enabled state + tooltips)
    # ------------------------------------------------------------------
    def _register_control(
        self,
        widget: QWidget,
        *,
        enabled_tip: str | None = None,
        disabled_tip: str | None = None,
    ) -> None:
        self._control_tooltips[widget] = (enabled_tip, disabled_tip)
        self._apply_disabled_theme(widget)
        tip = enabled_tip if widget.isEnabled() else (disabled_tip or enabled_tip)
        if tip:
            widget.setToolTip(tip)

    def _apply_disabled_theme(self, widget: QWidget) -> None:
        if not hasattr(widget, "styleSheet"):
            return
        try:
            current = widget.styleSheet() or ""
        except Exception:
            current = ""
        if "QPushButton:disabled" in current:
            return
        if isinstance(widget, QPushButton):
            disabled_css = (
                "QPushButton:disabled {"
                " background-color: #bdc3c7;"
                " color: #6c7a89;"
                " border: 1px solid #a0a0a0;"
                "}"
            )
            base = current.strip()
            chunks: list[str] = []
            if base:
                if "{" not in base and "}" not in base:
                    chunks.append(f"QPushButton {{ {base} }}")
                else:
                    chunks.append(base)
            chunks.append(disabled_css)
            merged = "\n".join(chunks).strip()
            widget.setStyleSheet(merged)

    def _set_control_enabled(
        self,
        widget: QWidget,
        enabled: bool,
        *,
        disabled_reason: str | None = None,
    ) -> None:
        widget.setEnabled(enabled)
        enabled_tip, disabled_tip = self._control_tooltips.get(widget, (None, None))
        if enabled:
            tip = enabled_tip or ""
        else:
            tip = disabled_reason or disabled_tip or enabled_tip or ""
        widget.setToolTip(tip)

    # ------------------------------------------------------------------
    # Scheduling helpers
    # ------------------------------------------------------------------
    def _compute_in_time_epoch_seconds(self) -> float | None:
        """Translate the countdown widget into an absolute epoch timestamp."""
        try:
            t = self._start_delay.time()
        except Exception:
            return None
        total_seconds = (
            t.hour() * 3600
            + t.minute() * 60
            + t.second()
            + t.msec() / 1000.0
        )
        if total_seconds <= 0:
            return None
        import time as _time

        return _time.time() + total_seconds

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
        in_time = self._compute_in_time_epoch_seconds()
        self.startShowRequested.emit(in_time)

    def _emit_jump_to_track_number(self) -> None:
        if not self._targets_enabled:
            return
        try:
            track_num = int(self._jump_track_spin.value())
            self.jumpToTrackRequested.emit(track_num)
        except Exception as exc:
            print(f"[GUI] Errore jump to track: {exc}", flush=True)

    def _emit_jump_to_selected(self) -> None:
        if not self._targets_enabled:
            return
        # Ottieni l'indice della traccia selezionata nella playlist
        row = self._playlist.currentRow()
        if row < 0:
            return
        # Emit con l'indice (1-based per utente, ma 0-based internamente)
        self.jumpToTrackRequested.emit(row + 1)

    def _update_jump_buttons(self) -> None:
        """Aggiorna stato pulsanti jump in base a selezione playlist."""
        try:
            has_selection = self._playlist.currentRow() >= 0
            has_items = self._playlist.count() > 0
            jump_reason = None
            if not self._targets_enabled:
                jump_reason = self._selection_required_tip
            elif not has_items:
                jump_reason = "La playlist è vuota"
            self._set_control_enabled(
                self._jump_go_button,
                self._targets_enabled and has_items,
                disabled_reason=jump_reason,
            )
            selected_reason = jump_reason
            if self._targets_enabled and has_items and not has_selection:
                selected_reason = "Seleziona una traccia nella playlist"
            self._set_control_enabled(
                self._jump_selected_button,
                self._targets_enabled and has_items and has_selection,
                disabled_reason=selected_reason,
            )
            remove_reason = selected_reason
            if self._targets_enabled and not has_selection:
                remove_reason = "Seleziona una traccia per rimuoverla"
            self._set_control_enabled(
                self._remove_from_playlist,
                self._targets_enabled and has_items and has_selection,
                disabled_reason=remove_reason,
            )
        except Exception:
            pass

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

    # ------------------------------------------------------------------
    # Media filtering helpers
    # ------------------------------------------------------------------
    def _allowed_extensions(self) -> set[str]:
        """Estensioni consentite (minuscole, con punto)."""
        return {
            # Video
            ".mp4", ".mkv", ".mov", ".avi", ".mpg", ".mpeg", ".m4v", ".webm", ".wmv", ".ts",
            # Immagini
            ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff",
        }

    def _allowed_patterns(self) -> list[str]:
        return [f"*{ext}" for ext in self._allowed_extensions()]

    def _is_allowed_media(self, path_or_name: str) -> bool:
        try:
            ext = Path(str(path_or_name)).suffix.lower()
        except Exception:
            ext = ""
        return ext in self._allowed_extensions()
    def _emit_start_sync(self) -> None:
        if not self._targets_enabled or self._timing_syncing:
            return
        self._timing_syncing = True
        self._update_start_sync_enabled()
        # Show immediate feedback on the timing widgets
        self.set_timing_status(None, "Sync…", syncing=True)
        try:
            self.startSyncRequested.emit()
        except Exception:
            pass


