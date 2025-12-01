"""Main window wiring together application tabs."""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import time

from PySide6.QtCore import QSize, QTimer, Qt, QFileSystemWatcher, QByteArray
from PySide6.QtNetwork import QUdpSocket, QHostAddress
from PySide6.QtGui import QTextCursor, QIcon
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QDialog,
    QSizePolicy,
)

from GUI.core.controller import ApplicationController
from GUI.app_meta import APP_DISPLAY_NAME
from GUI.ui.tabs.commands_tab import CommandsTab
from GUI.ui.tabs.update_tab import UpdateTab
from GUI.ui.panels.player_panel import PlayerPanel
from GUI.ui.dialogs.startup_mac_dialog import StartupMacDialog
from GUI.services.player_registry import PlayerRecord
import re


class MainWindow(QMainWindow):
    """Top-level window embedding all functional tabs."""

    def __init__(self, controller: ApplicationController) -> None:
        super().__init__()
        self._controller = controller
        self._selected_players = []
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(QSize(1200, 800))
        # Applica icona finestra se disponibile
        try:
            icon = self._resolve_app_icon()
            if icon is not None:
                self.setWindowIcon(icon)
        except Exception:
            pass

        self._tabs = QTabWidget()
        self._commands_tab = CommandsTab(parent=self._tabs)
        self._update_tab = UpdateTab(parent=self._tabs)

        self._tabs.addTab(self._commands_tab, "Comandi")
        self._tabs.addTab(self._update_tab, "Aggiornamento & Settings")

        self._player_panel = PlayerPanel()
        self._player_panel_default_min = self._player_panel.minimumWidth()
        self._player_panel_default_max = self._player_panel.maximumWidth()
        self._player_panel_placeholder: QWidget | None = None
        self._player_panel_window: QDialog | None = None

        splitter = QSplitter()
        splitter.addWidget(self._player_panel)
        splitter.addWidget(self._tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([400, 800])
        self._splitter = splitter
        self._player_panel_last_size = 400
        self._panel_collapsed = False
        self._player_detach_restore_collapse = False
        try:
            self._splitter_total_hint = sum(self._splitter.sizes()) or 1200
        except Exception:
            self._splitter_total_hint = 1200

        self._log_label = QLabel("Event Log — tutti i device")
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_history: deque[tuple[str | None, str]] = deque(maxlen=800)
        self._log_toggle_btn = QToolButton()
        self._log_toggle_btn.setArrowType(Qt.DownArrow)
        self._log_toggle_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._log_toggle_btn.setFixedSize(28, 28)
        self._log_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._log_toggle_btn.setToolTip("Comprimi Event Log")
        self._log_toggle_btn.setAutoRaise(True)
        self._log_toggle_btn.clicked.connect(self._toggle_log_panel)
        self._log_collapsed = False
        self._log_panel_last_size = 220
        self._log_detach_restore_collapse = False

        self._log_container = QWidget()
        log_layout = QVBoxLayout(self._log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(4)
        self._log_header = QWidget()
        header_layout = QHBoxLayout(self._log_header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        header_layout.addWidget(self._log_label)
        self._clear_log_button = QPushButton("Clean log")
        self._clear_log_button.setToolTip("Svuota la vista degli eventi")
        self._clear_log_button.clicked.connect(self._clear_log_view)
        header_layout.addWidget(self._clear_log_button)
        self._detach_log_button = QToolButton()
        self._detach_log_button.setCursor(Qt.PointingHandCursor)
        self._detach_log_button.setAutoRaise(True)
        self._detach_log_button.clicked.connect(self._toggle_log_panel_detach)
        self._detach_log_button.setFixedSize(24, 24)
        header_layout.addWidget(self._detach_log_button)
        header_layout.addStretch(1)
        header_layout.addWidget(self._log_toggle_btn)
        log_layout.addWidget(self._log_header)
        log_layout.addWidget(self._log_view, 1)
        self._log_container_default_min = self._log_container.minimumHeight()
        self._log_container_default_max = self._log_container.maximumHeight()
        self._log_placeholder: QWidget | None = None
        self._log_window: QDialog | None = None
        self._set_log_detach_state(False)

        self._main_splitter = QSplitter(Qt.Vertical)
        self._main_splitter.setHandleWidth(10)
        self._main_splitter.addWidget(splitter)
        self._main_splitter.addWidget(self._log_container)
        self._main_splitter.setStretchFactor(0, 3)
        self._main_splitter.setStretchFactor(1, 1)
        self._main_splitter.setSizes([620, 220])
        try:
            self._main_splitter_total_hint = sum(self._main_splitter.sizes()) or 840
        except Exception:
            self._main_splitter_total_hint = 840

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self._main_splitter)
        self.setCentralWidget(container)

        self._ui_state_path = self._resolve_ui_state_path()
        self._load_ui_state()

        self._setup_connections()
        # Watcher per la cartella media locale (auto-refresh libreria)
        self._media_watcher = QFileSystemWatcher(self)
        try:
            self._media_watcher.directoryChanged.connect(self._handle_media_dir_changed)
            self._media_watcher.fileChanged.connect(self._handle_media_dir_changed)
        except Exception:
            pass
        # Inizializza watcher sul media root attuale
        try:
            current_media_root = getattr(controller.state.config.media, "media_root", None)
            if current_media_root:
                self._update_media_watcher(current_media_root)
        except Exception:
            pass
        # Status polling timer (stopped by default)
        self._status_timer = QTimer(self)
        try:
            initial_poll = int(getattr(controller.state.config.network, "status_poll_ms", 2000))
        except Exception:
            initial_poll = 2000
        self._status_timer.setInterval(initial_poll)
        self._status_timer.timeout.connect(self._poll_status_tick)
        self._status_primary = None
        # Device media polling timer
        self._device_media_timer = QTimer(self)
        self._device_media_timer.setInterval(3000)
        self._device_media_timer.timeout.connect(self._device_media_tick)
        # Playlist status polling timer
        self._playlist_timer = QTimer(self)
        self._playlist_timer.setInterval(4000)
        self._playlist_timer.timeout.connect(self._playlist_tick)

        self._next_status_refresh_ts = 0.0
        # Playlist push tracking
        self._playlist_pending: set[str] = set()
        self._playlist_active: bool = False
        self._clone_job_active: bool = False
        # Countdown to start_show
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(200)
        self._countdown_timer.timeout.connect(self._update_countdown)
        self._show_start_epoch: float | None = None
        # Track last seen action badge to avoid repeats
        self._last_action_badge: str | None = None
        # UDP listener for "start N" commands
        self._udp_socket = QUdpSocket(self)
        try:
            # ShareAddress/ReuseAddressHint to avoid bind issues on some OSes
            flags = QUdpSocket.ShareAddress | QUdpSocket.ReuseAddressHint
            self._udp_socket.bind(QHostAddress.AnyIPv4, 9999, flags)
            self._udp_socket.readyRead.connect(self._handle_udp_ready_read)
            self._append_log("UDP listener attivo su 0.0.0.0:9999 (comando 'start N')")
        except Exception as exc:
            self._append_log(f"UDP listener non avviato: {exc}")

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def _setup_connections(self) -> None:
        ctrl = self._controller
        registry = ctrl.player_registry

        registry.playerUpdated.connect(self._player_panel.update_player)
        registry.playerRemoved.connect(self._player_panel.remove_player)
        # Collapse/expand player panel
        self._player_panel.toggleCollapseRequested.connect(self._toggle_player_panel)
        ctrl.logMessage.connect(self._append_log)
        ctrl.discoveryStarted.connect(lambda: self._player_panel.set_busy(True))
        ctrl.discoveryFinished.connect(self._handle_discovery_finished)
        ctrl.mediaLibraryUpdated.connect(self._commands_tab.set_media_items)
        ctrl.settingsChanged.connect(self._handle_settings_changed)
        ctrl.commandCompleted.connect(self._handle_command_completed)
        # Upload progress bar from controller
        try:
            ctrl.uploadActivityChanged.connect(lambda n: self._commands_tab.set_upload_activity(int(n)))
        except Exception:
            pass
        # Upload progress counter (done/total)
        try:
            ctrl.uploadProgressChanged.connect(lambda done, total, inflight: self._commands_tab.set_upload_progress(int(done), int(total), int(inflight)))
        except Exception:
            pass
        ctrl.updateCompleted.connect(self._handle_update_completed)
        ctrl.frameworkStatusReceived.connect(self._handle_framework_status)
        ctrl.statusReceived.connect(self._handle_status)
        ctrl.deviceMediaReceived.connect(self._handle_device_media)
        ctrl.playlistStatusReceived.connect(self._handle_playlist_status)
        # Media clone workflow wiring
        try:
            ctrl.mediaCloneProgress.connect(self._handle_media_clone_progress)
            ctrl.mediaCloneLog.connect(self._commands_tab.append_clone_log)
            ctrl.mediaCloneCompleted.connect(self._handle_media_clone_completed)
        except Exception:
            pass
        # Start Sync button wiring
        try:
            self._commands_tab.startSyncRequested.connect(self._handle_start_sync_requested)
        except Exception:
            pass
        # Live framework logs wiring
        ctrl.logLiveStatusChanged.connect(self._commands_tab.set_log_live_active)
        self._commands_tab.logLiveToggleRequested.connect(self._handle_log_live_toggle)

        self._player_panel.refreshRequested.connect(ctrl.trigger_discovery)
        self._player_panel.selectionChanged.connect(self._handle_selection_changed)
        self._player_panel.deviceNameEdited.connect(self._handle_inline_name_edit)
        self._player_panel.syncMediaRequested.connect(self._handle_sync_media_request)
        self._player_panel.purgeRequested.connect(self._handle_purge_offline)
        self._player_panel.forcePurgeRequested.connect(self._handle_force_purge)
        self._player_panel.vncRequested.connect(self._handle_open_vnc)
        self._player_panel.detachRequested.connect(self._toggle_player_panel_detach)

        self._commands_tab.playbackTriggered.connect(self._handle_playback)
        self._commands_tab.miscCommandTriggered.connect(self._handle_misc_command)
        self._commands_tab.uploadRequested.connect(self._handle_upload)
        # Upload centralizzato da filesystem (picker + drag&drop)
        try:
            self._commands_tab.filesDroppedForUpload.connect(self._handle_files_dropped_for_upload)
        except Exception:
            pass
        self._commands_tab.mediaDirectoryRequested.connect(self._choose_media_directory)
        self._commands_tab.deviceMediaRefreshRequested.connect(self._handle_device_media_refresh)
        # Refresh manuale libreria media locale
        try:
            self._commands_tab.mediaLibraryRefreshRequested.connect(ctrl.refresh_media_library)
        except Exception:
            pass
        # Playlist wiring
        self._commands_tab.playlistChanged.connect(self._handle_playlist_changed)
        self._commands_tab.playlistPushRequested.connect(self._handle_push_playlist)
        self._commands_tab.playlistAutoApplyRequested.connect(self._handle_auto_apply_playlist)
        self._commands_tab.playlistRefreshRequested.connect(self._handle_playlist_refresh)
        self._commands_tab.startShowRequested.connect(self._handle_start_show)
        self._commands_tab.jumpToTrackRequested.connect(self._handle_jump_to_track)
        ctrl.playlistPhaseChanged.connect(self._handle_playlist_phase)

        self._update_tab.buildRequested.connect(ctrl.build_bundle)
        self._update_tab.deployRequested.connect(self._handle_deploy)
        self._update_tab.sshDeployRequested.connect(self._handle_deploy_via_ssh)
        self._update_tab.frameworkChanged.connect(self._handle_framework_changed)
        self._update_tab.autoplayToggled.connect(self._handle_autoplay_toggle)
        self._update_tab.pollIntervalChanged.connect(self._handle_poll_interval_changed)
        try:
            self._update_tab.pingDurationChanged.connect(self._handle_ping_duration_changed)
        except Exception:
            pass
        self._update_tab.bundleSelected.connect(self._handle_bundle_selected)
        ctrl.autoplayStatusReceived.connect(self._handle_autoplay_status)
        ctrl.bundleBuildStateChanged.connect(self._handle_bundle_state)
        ctrl.startupMacsUpdated.connect(lambda entries: self._update_tab.set_startup_mac_summary(len(entries)))
        self._update_tab.startupListRequested.connect(self._handle_startup_mac_request)
        self._update_tab.startupRequested.connect(self._handle_startup_request)

        config = ctrl.state.config
        self._update_tab.set_settings(
            networks=", ".join(config.network.scan_ranges),
            media_root=str(config.media.media_root) if config.media.media_root else "<non impostata>",
            polling_ms=int(getattr(config.network, "status_poll_ms", 2000)),
            ping_ms=int(getattr(config.network, "ping_ms", 200)),
        )
        # Initialize timer interval from settings
        try:
            self._status_timer.setInterval(int(getattr(config.network, "status_poll_ms", 2000)))
        except Exception:
            pass
        self._update_tab.enable_actions(False)
        self._update_tab.set_build_state(False, "Pronto a creare un bundle")
        self._update_tab.set_framework_state(current=None, available=[])
        # All'avvio non c'è un bundle attivo
        self._update_tab.set_bundle_available(False)
        self._update_tab.set_bundle_version(None)
        self._commands_tab.set_media_root(config.media.media_root)
        self._update_tab.set_startup_mac_summary(len(ctrl.known_startup_macs()))

    # ------------------------------------------------------------------
    # UI state persistence
    # ------------------------------------------------------------------
    def _resolve_ui_state_path(self) -> Path:
        try:
            base = self._controller.settings_path
        except Exception:
            try:
                return Path.home() / ".maroccos_ui_state.json"
            except Exception:
                return Path("ui_state.json")
        try:
            return base.with_name("ui_state.json")
        except Exception:
            return base.parent / "ui_state.json"

    def _encode_state_bytes(self, payload) -> str | None:
        if payload is None:
            return None
        try:
            arr = QByteArray(payload)
            encoded = arr.toBase64()
            return bytes(encoded).decode("ascii")
        except Exception:
            return None

    def _decode_state_bytes(self, encoded: str | None) -> QByteArray | None:
        if not encoded:
            return None
        try:
            arr = QByteArray.fromBase64(encoded.encode("ascii"))
            return arr if not arr.isEmpty() else None
        except Exception:
            return None

    def _load_ui_state(self) -> None:
        path = getattr(self, "_ui_state_path", None)
        if not isinstance(path, Path) or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        player_detached = bool(payload.get("player_detached"))
        log_detached = bool(payload.get("log_detached"))
        geometry = self._decode_state_bytes(payload.get("geometry"))
        if geometry is not None:
            try:
                self.restoreGeometry(geometry)
            except Exception:
                pass
        if bool(payload.get("maximized")):
            try:
                self.setWindowState(self.windowState() | Qt.WindowMaximized)
            except Exception:
                pass
        try:
            hint = int(payload.get("player_panel_width", self._player_panel_last_size))
            if hint > 0:
                self._player_panel_last_size = hint
        except Exception:
            pass
        try:
            hint = int(payload.get("log_panel_height", self._log_panel_last_size))
            if hint > 0:
                self._log_panel_last_size = hint
        except Exception:
            pass
        if not player_detached:
            state = self._decode_state_bytes(payload.get("splitter_state"))
            if state is not None:
                try:
                    self._splitter.restoreState(state)
                except Exception:
                    pass
            else:
                sizes = payload.get("splitter_sizes")
                if isinstance(sizes, list) and len(sizes) >= 2:
                    try:
                        left = max(120, int(sizes[0]))
                        right = max(200, int(sizes[1]))
                        self._splitter.setSizes([left, right])
                    except Exception:
                        pass
        else:
            sizes = payload.get("splitter_sizes")
            if isinstance(sizes, list) and len(sizes) >= 2:
                try:
                    total = int(sizes[0]) + int(sizes[1])
                    if total > 0:
                        self._splitter_total_hint = total
                except Exception:
                    pass
        try:
            self._splitter_total_hint = sum(self._splitter.sizes()) or self._splitter_total_hint
        except Exception:
            pass
        if not log_detached:
            state = self._decode_state_bytes(payload.get("main_splitter_state"))
            if state is not None:
                try:
                    self._main_splitter.restoreState(state)
                except Exception:
                    pass
            else:
                sizes = payload.get("main_splitter_sizes")
                if isinstance(sizes, list) and len(sizes) >= 2:
                    try:
                        top = max(200, int(sizes[0]))
                        bottom = max(120, int(sizes[1]))
                        self._main_splitter.setSizes([top, bottom])
                    except Exception:
                        pass
        else:
            sizes = payload.get("main_splitter_sizes")
            if isinstance(sizes, list) and len(sizes) >= 2:
                try:
                    total = int(sizes[0]) + int(sizes[1])
                    if total > 0:
                        self._main_splitter_total_hint = total
                except Exception:
                    pass
        try:
            self._main_splitter_total_hint = sum(self._main_splitter.sizes()) or self._main_splitter_total_hint
        except Exception:
            pass
        desired_panel_collapsed = bool(payload.get("player_collapsed"))
        if not player_detached and desired_panel_collapsed != self._panel_collapsed:
            self._toggle_player_panel()
        desired_log_collapsed = bool(payload.get("log_collapsed"))
        if not log_detached and desired_log_collapsed != self._log_collapsed:
            self._toggle_log_panel()
        if player_detached:
            self._detach_player_panel()
            geom = self._decode_state_bytes(payload.get("player_window_geometry"))
            if geom is not None and self._player_panel_window:
                try:
                    self._player_panel_window.restoreGeometry(geom)
                except Exception:
                    pass
        if log_detached:
            self._detach_log_panel()
            geom = self._decode_state_bytes(payload.get("log_window_geometry"))
            if geom is not None and self._log_window:
                try:
                    self._log_window.restoreGeometry(geom)
                except Exception:
                    pass

    def _save_ui_state(self) -> None:
        path = getattr(self, "_ui_state_path", None)
        if not isinstance(path, Path):
            return
        payload: dict[str, object] = {}
        geometry = self._encode_state_bytes(self.saveGeometry())
        if geometry:
            payload["geometry"] = geometry
        payload["maximized"] = bool(self.isMaximized())
        payload["player_collapsed"] = bool(self._panel_collapsed)
        payload["log_collapsed"] = bool(self._log_collapsed)
        payload["player_panel_width"] = int(self._player_panel_last_size)
        payload["log_panel_height"] = int(self._log_panel_last_size)
        payload["splitter_sizes"] = [int(x) for x in self._splitter.sizes()]
        payload["main_splitter_sizes"] = [int(x) for x in self._main_splitter.sizes()]
        split_state = self._encode_state_bytes(self._splitter.saveState())
        if split_state:
            payload["splitter_state"] = split_state
        main_state = self._encode_state_bytes(self._main_splitter.saveState())
        if main_state:
            payload["main_splitter_state"] = main_state
        payload["player_detached"] = bool(self._player_panel_window)
        payload["log_detached"] = bool(self._log_window)
        if self._player_panel_window:
            geom = self._encode_state_bytes(self._player_panel_window.saveGeometry())
            if geom:
                payload["player_window_geometry"] = geom
        if self._log_window:
            geom = self._encode_state_bytes(self._log_window.saveGeometry())
            if geom:
                payload["log_window_geometry"] = geom
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            self._save_ui_state()
        except Exception:
            pass
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Floating detach helpers
    # ------------------------------------------------------------------
    def _create_floating_window(self, title: str, on_close) -> QDialog:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        dlg._on_close_cb = on_close  # type: ignore[attr-defined]

        def _close(ev):
            try:
                if dlg._on_close_cb:  # type: ignore[attr-defined]
                    dlg._on_close_cb()
            except Exception:
                pass
            ev.accept()
        dlg.closeEvent = _close  # type: ignore[assignment]
        return dlg

    def _toggle_player_panel_detach(self) -> None:
        if self._player_panel_window:
            try:
                self._player_panel_window.close()
            except Exception:
                self._reattach_player_panel()
        else:
            self._detach_player_panel()

    def _toggle_log_panel_detach(self) -> None:
        if self._log_window:
            try:
                self._log_window.close()
            except Exception:
                self._reattach_log_panel()
        else:
            self._detach_log_panel()

    def _set_player_detach_state(self, detached: bool) -> None:
        try:
            self._player_panel.set_detach_state(detached)
        except Exception:
            pass

    def _set_log_detach_state(self, detached: bool) -> None:
        try:
            self._detach_log_button.setText("↙" if detached else "↗")
            if detached:
                tip = "Riaggancia Event Log nella finestra principale"
            else:
                tip = "Stacca Event Log in finestra separata"
            self._detach_log_button.setToolTip(tip)
        except Exception:
            pass
        try:
            self._log_toggle_btn.setVisible(not detached)
        except Exception:
            pass

    def _detach_player_panel(self) -> None:
        if self._player_panel_window:
            try:
                self._player_panel_window.raise_()
                self._player_panel_window.activateWindow()
            except Exception:
                pass
            return
        self._player_detach_restore_collapse = bool(self._panel_collapsed)
        if self._panel_collapsed:
            self._toggle_player_panel()
        try:
            sizes = self._splitter.sizes()
        except Exception:
            sizes = []
        total = 0
        if sizes:
            try:
                if sizes[0] > 0:
                    self._player_panel_last_size = sizes[0]
            except Exception:
                pass
            try:
                total = sum(sizes)
            except Exception:
                total = 0
        if total <= 0:
            try:
                total = int(self._splitter_total_hint)
            except Exception:
                total = 1200
        placeholder = QWidget()
        placeholder.setMinimumWidth(0)
        placeholder.setMaximumWidth(0)
        placeholder.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        idx = self._splitter.indexOf(self._player_panel)
        if idx < 0:
            idx = 0
        self._splitter.replaceWidget(idx, placeholder)
        self._player_panel_placeholder = placeholder
        self._player_panel.setParent(None)
        dlg = self._create_floating_window("Player", self._reattach_player_panel)
        dlg.layout().addWidget(self._player_panel)
        self._player_panel_window = dlg
        dlg.resize(420, 700)
        dlg.show()
        self._set_player_detach_state(True)
        try:
            self._splitter.setSizes([0, max(1, int(total))])
            self._splitter_total_hint = sum(self._splitter.sizes()) or int(total)
        except Exception:
            pass

    def _reattach_player_panel(self) -> None:
        if not self._player_panel_window:
            return
        try:
            self._player_panel_window.layout().removeWidget(self._player_panel)  # type: ignore[arg-type]
        except Exception:
            pass
        self._player_panel.setParent(None)
        idx = self._splitter.indexOf(self._player_panel_placeholder) if self._player_panel_placeholder else 0
        if idx < 0:
            idx = 0
        self._splitter.replaceWidget(idx, self._player_panel)
        if self._player_panel_placeholder:
            self._player_panel_placeholder.deleteLater()
            self._player_panel_placeholder = None
        self._player_panel_window = None
        self._set_player_detach_state(False)
        restore_collapse = getattr(self, "_player_detach_restore_collapse", False)
        self._player_detach_restore_collapse = False
        try:
            total = sum(self._splitter.sizes())
        except Exception:
            total = 0
        if total <= 0:
            try:
                total = int(self._splitter_total_hint)
            except Exception:
                total = 1200
        if self._panel_collapsed:
            collapsed_width = max(60, self._player_panel.collapsed_width_hint())
            total = max(total, collapsed_width + 200)
            try:
                self._splitter.setSizes([collapsed_width, max(200, total - collapsed_width)])
            except Exception:
                pass
        else:
            left = max(220, int(self._player_panel_last_size))
            total = max(total, left + 200)
            try:
                self._splitter.setSizes([left, max(200, total - left)])
            except Exception:
                pass
        try:
            self._splitter_total_hint = sum(self._splitter.sizes()) or self._splitter_total_hint
        except Exception:
            pass
        if restore_collapse and not self._panel_collapsed:
            self._toggle_player_panel()

    def _detach_log_panel(self) -> None:
        if self._log_window:
            try:
                self._log_window.raise_()
                self._log_window.activateWindow()
            except Exception:
                pass
            return
        self._log_detach_restore_collapse = bool(self._log_collapsed)
        if self._log_collapsed:
            self._toggle_log_panel()
        try:
            sizes = self._main_splitter.sizes()
        except Exception:
            sizes = []
        total = 0
        if sizes:
            try:
                if len(sizes) >= 2 and sizes[1] > 0:
                    self._log_panel_last_size = sizes[1]
            except Exception:
                pass
            try:
                total = sum(sizes)
            except Exception:
                total = 0
        if total <= 0:
            try:
                total = int(self._main_splitter_total_hint)
            except Exception:
                total = 840
        placeholder = QWidget()
        placeholder.setMinimumHeight(0)
        placeholder.setMaximumHeight(0)
        placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        idx = self._main_splitter.indexOf(self._log_container)
        if idx < 0:
            idx = 1
        self._main_splitter.replaceWidget(idx, placeholder)
        self._log_placeholder = placeholder
        self._log_container.setParent(None)
        dlg = self._create_floating_window("Event Log", self._reattach_log_panel)
        dlg.layout().addWidget(self._log_container)
        self._log_window = dlg
        dlg.resize(900, 260)
        dlg.show()
        self._set_log_detach_state(True)
        try:
            self._main_splitter.setSizes([max(1, int(total)), 0])
            self._main_splitter_total_hint = sum(self._main_splitter.sizes()) or int(total)
        except Exception:
            pass

    def _reattach_log_panel(self) -> None:
        if not self._log_window:
            return
        try:
            self._log_window.layout().removeWidget(self._log_container)  # type: ignore[arg-type]
        except Exception:
            pass
        self._log_container.setParent(None)
        idx = self._main_splitter.indexOf(self._log_placeholder) if self._log_placeholder else 1
        if idx < 0:
            idx = 1
        self._main_splitter.replaceWidget(idx, self._log_container)
        if self._log_placeholder:
            self._log_placeholder.deleteLater()
            self._log_placeholder = None
        self._log_window = None
        self._set_log_detach_state(False)
        restore_collapse = getattr(self, "_log_detach_restore_collapse", False)
        self._log_detach_restore_collapse = False
        try:
            total = sum(self._main_splitter.sizes())
        except Exception:
            total = 0
        if total <= 0:
            try:
                total = int(self._main_splitter_total_hint)
            except Exception:
                total = 840
        if self._log_collapsed:
            collapsed_height = self._log_collapsed_height_hint()
            total = max(total, collapsed_height + 200)
            try:
                self._main_splitter.setSizes([max(200, total - collapsed_height), collapsed_height])
            except Exception:
                pass
        else:
            bottom = max(180, int(self._log_panel_last_size))
            total = max(total, bottom + 200)
            try:
                self._main_splitter.setSizes([max(200, total - bottom), bottom])
            except Exception:
                pass
        try:
            self._main_splitter_total_hint = sum(self._main_splitter.sizes()) or self._main_splitter_total_hint
        except Exception:
            pass
        if restore_collapse and not self._log_collapsed:
            self._toggle_log_panel()

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_discovery_finished(self, players_raw: list[dict]) -> None:
        self._player_panel.set_busy(False)
        formatted = []
        for entry in players_raw:
            status = entry.get("status") or {}
            formatted.append(
                {
                    "name": status.get("name") or status.get("player_name") or entry.get("ip"),
                    "ip": entry.get("ip"),
                    "version": entry.get("version") or status.get("version_current") or "?",
                }
            )
        self._update_tab.set_players(formatted)

    # --------------------------------------------------
    # Icon helpers
    # --------------------------------------------------
    def _resolve_app_icon(self) -> QIcon | None:
        """Trova un'icona applicabile (PNG o ICO) tra:
        - Percorso esplicito via env GUI_ICON_PATH
        - icon.png accanto al main (packaged via PyInstaller add-data)
        - icon-maker/icon.png nella root repo (sviluppo)
        - Risorse estratte in _MEIPASS (onefile)
        Ritorna None se non trovata.
        """
        import os, sys
        candidates: list[Path] = []
        env_path = os.environ.get("GUI_ICON_PATH")
        if env_path:
            try:
                candidates.append(Path(env_path))
            except Exception:
                pass
        try:
            here = Path(__file__).resolve().parent
            candidates.append(here / "icon.png")
        except Exception:
            pass
        try:
            repo_root = Path(__file__).resolve().parents[2]
            candidates.append(repo_root / "icon-maker" / "icon.png")
        except Exception:
            pass
        # PyInstaller onefile unpack dir
        try:
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.append(Path(meipass) / "icon.png")
        except Exception:
            pass
        for c in candidates:
            try:
                if c.exists() and c.is_file():
                    return QIcon(str(c))
            except Exception:
                continue
        return None

    def _handle_startup_mac_request(self) -> None:
        dialog = StartupMacDialog(self)
        dialog.set_entries(self._controller.known_startup_macs())
        dialog.exec()

    def _handle_startup_request(self) -> None:
        target = self._selected_players[0] if self._selected_players else None
        self._controller.trigger_startup_magic(player=target)

    def _handle_start_sync_requested(self) -> None:
        if not self._selected_players:
            self._append_log("Sync orologi richiesto ma nessun player selezionato.")
            return
        players = list(self._selected_players)
        snippet = ", ".join(str(p.ip) for p in players[:3])
        if len(players) > 3:
            snippet += f", ... ({len(players)} in totale)"
        self._append_log(f"[Sync] Richiesta manuale per i player: {snippet}")
        try:
            self._controller.restart_time_sync(players)
        except Exception as exc:
            self._append_log(f"[Sync] Errore riavviando sync: {exc}")
        finally:
            for player in players:
                self._controller.refresh_status(player)

    def _handle_selection_changed(self, ips: list[str]) -> None:
        self._selected_players = self._controller.get_selected_players(ips)
        has_targets = bool(self._selected_players)
        self._commands_tab.set_targets_selected(has_targets)
        self._update_tab.enable_actions(has_targets)
        self._player_panel.set_vnc_enabled(has_targets)
        self._update_log_label()
        self._rebuild_log_view()
        # Reset last action badge on selection change
        try:
            self._last_action_badge = None
            self._commands_tab.set_action_badge(None)
        except Exception:
            pass
        if has_targets:
            self._controller.refresh_autoplay_status(self._selected_players[0])
            self._controller.refresh_framework_status(self._selected_players[0])
            # Update HUD multi-selection hint immediately
            try:
                count = len(self._selected_players)
                self._commands_tab.set_hud_state(None, count)
                # Also show multi-target cue for loop toggles
                self._commands_tab.set_playback_loop(None, count)
                self._commands_tab.set_stop_at_end(None, count)
                self._commands_tab.set_playlist_loop(None, count)
            except Exception:
                pass
            # Start polling status for the primary player
            self._status_primary = self._selected_players[0].ip
            self._status_timer.start()
            self._controller.refresh_status(self._selected_players[0])
            self._next_status_refresh_ts = time.monotonic() + self._status_poll_interval_seconds()
            # Start device media polling for primary
            self._device_media_timer.start()
            self._handle_device_media_refresh()
            # Start playlist status polling for primary
            self._playlist_timer.start()
            self._handle_playlist_refresh()
            # If live CVLC log is active, restart it for the new primary
            try:
                if self._commands_tab.is_log_live_active():
                    self._controller.start_log_live(self._selected_players[0], lines=100)
            except Exception:
                pass
        else:
            self._update_tab.set_autoplay(False)
            self._update_tab.set_framework_state(current=None, available=[])
            self._status_primary = None
            self._status_timer.stop()
            self._next_status_refresh_ts = 0.0
            self._device_media_timer.stop()
            self._playlist_timer.stop()
            # Stop live logs when no selection
            try:
                self._controller.stop_log_live()
                self._commands_tab.set_log_live_active(False)
            except Exception:
                pass

    def _handle_sync_media_request(self, ip: str, port: int) -> None:
        _ = port  # legacy signature; port non più necessario
        if self._clone_job_active:
            self._append_log("[Clone] Operazione già in corso: attendi il completamento")
            return
        record = self._controller.player_registry.get_player(ip)
        if not record:
            self._append_log(f"[Clone] Player {ip} non più disponibile")
            return
        try:
            self._commands_tab.reset_clone_panel()
        except Exception:
            pass
        label = record.name or record.ip
        self._set_clone_job_state(True, f"Clonazione avviata da {label}")
        try:
            self._controller.clone_media_from_player(record)
        except Exception as exc:
            self._append_log(f"[Clone] Avvio fallito: {exc}")
            self._set_clone_job_state(False, f"Avvio clonazione fallito: {exc}")

    def _handle_media_clone_progress(self, fraction: float, stage: str) -> None:
        try:
            self._commands_tab.set_clone_progress(float(fraction), stage)
        except Exception:
            pass
        if not self._clone_job_active:
            self._set_clone_job_state(True, stage)

    def _handle_media_clone_completed(self, ok: bool, summary: str | None) -> None:
        try:
            self._commands_tab.finish_clone_job(bool(ok), summary)
        except Exception:
            pass
        label = summary or ("Clonazione completata" if ok else "Clonazione fallita")
        tooltip = f"Ultima clonazione: {label}"
        self._set_clone_job_state(False, tooltip)

    def _set_clone_job_state(self, active: bool, message: str | None = None) -> None:
        self._clone_job_active = bool(active)
        note = (message or "").strip() or None
        if active:
            reason = note or "Clonazione media in corso…"
            try:
                self._player_panel.set_sync_lock(reason)
            except Exception:
                pass
            try:
                self._player_panel.set_sync_status_tip(reason)
            except Exception:
                pass
        else:
            try:
                self._player_panel.set_sync_lock(None)
            except Exception:
                pass
            try:
                self._player_panel.set_sync_status_tip(note)
            except Exception:
                pass

    def _toggle_player_panel(self) -> None:
        if self._player_panel_window:
            return
        try:
            if not self._panel_collapsed:
                # Remember current width and collapse
                try:
                    sizes = self._splitter.sizes()
                    if sizes and sizes[0] > 0:
                        self._player_panel_last_size = sizes[0]
                except Exception:
                    pass
                collapsed_width = max(60, self._player_panel.collapsed_width_hint())
                self._player_panel.setMinimumWidth(collapsed_width)
                self._player_panel.setMaximumWidth(collapsed_width)
                total = sum(self._splitter.sizes()) or (collapsed_width * 2)
                right = max(200, total - collapsed_width)
                self._splitter.setSizes([collapsed_width, right])
                self._player_panel.set_collapsed(True)
                self._panel_collapsed = True
            else:
                self._player_panel.setMinimumWidth(self._player_panel_default_min)
                self._player_panel.setMaximumWidth(self._player_panel_default_max)
                total = sum(self._splitter.sizes()) or 1
                left = max(220, int(self._player_panel_last_size))
                right = max(200, total - left)
                self._splitter.setSizes([left, right])
                self._player_panel.set_collapsed(False)
                self._panel_collapsed = False
            try:
                self._splitter_total_hint = sum(self._splitter.sizes()) or self._splitter_total_hint
            except Exception:
                pass
        except Exception:
            pass

    def _toggle_log_panel(self) -> None:
        if self._log_window:
            return
        try:
            if not self._log_collapsed:
                try:
                    sizes = self._main_splitter.sizes()
                    if len(sizes) >= 2 and sizes[1] > 0:
                        self._log_panel_last_size = sizes[1]
                except Exception:
                    pass
                collapsed_height = self._log_collapsed_height_hint()
                self._log_view.setVisible(False)
                self._log_container.setMinimumHeight(collapsed_height)
                self._log_container.setMaximumHeight(collapsed_height)
                total = sum(self._main_splitter.sizes()) or (collapsed_height + 200)
                top = max(200, total - collapsed_height)
                self._main_splitter.setSizes([top, collapsed_height])
                self._log_toggle_btn.setArrowType(Qt.UpArrow)
                self._log_toggle_btn.setToolTip("Espandi Event Log")
                self._log_collapsed = True
            else:
                self._log_view.setVisible(True)
                self._log_container.setMinimumHeight(self._log_container_default_min)
                self._log_container.setMaximumHeight(self._log_container_default_max)
                total = sum(self._main_splitter.sizes()) or 1
                bottom = max(180, int(self._log_panel_last_size))
                top = max(200, total - bottom)
                self._main_splitter.setSizes([top, bottom])
                self._log_toggle_btn.setArrowType(Qt.DownArrow)
                self._log_toggle_btn.setToolTip("Comprimi Event Log")
                self._log_collapsed = False
            try:
                self._main_splitter_total_hint = sum(self._main_splitter.sizes()) or self._main_splitter_total_hint
            except Exception:
                pass
        except Exception:
            pass

    def _log_collapsed_height_hint(self) -> int:
        try:
            header_height = self._log_header.sizeHint().height()
        except Exception:
            header_height = 32
        return max(48, header_height + 8)

    def _handle_playback(self, command: str, payload: dict | None) -> None:
        if not self._selected_players:
            return
        self._controller.send_playback_command(command, payload or {}, self._selected_players)

    def _handle_misc_command(self, command: str, payload: dict | None) -> None:
        if not self._selected_players:
            return
        self._controller.send_misc_command(command, payload or {}, self._selected_players)
        if command == "hud_visible":
            for player in self._selected_players:
                self._controller.refresh_status(player)

    def _handle_upload(self, path: str) -> None:
        if not self._selected_players:
            return
        self._controller.upload_media(Path(path), self._selected_players)

    def _handle_files_dropped_for_upload(self, paths: list[str]) -> None:
        """Gestisce upload multipli da filesystem (picker o drag&drop)."""
        if not self._selected_players:
            return
        for p in paths or []:
            try:
                self._controller.upload_media(Path(p), self._selected_players)
            except Exception:
                # Ignora singoli errori, continuerà con gli altri file
                continue

    def _handle_deploy(self) -> None:
        if not self._selected_players:
            return
        self._controller.deploy_update(self._selected_players)

    def _handle_deploy_via_ssh(self) -> None:
        targets = list(self._selected_players)
        if not targets:
            # Nessuna selezione: chiedi gli IP/host manualmente
            text, ok = QInputDialog.getMultiLineText(
                self,
                "Deploy via SSH",
                "Inserisci IP/host dei player (separati da virgola, spazio o a capo):",
                "",
            )
            if not ok:
                return
            raw = (text or "").strip()
            if not raw:
                return
            parts = [p.strip() for p in re.split(r"[\s,]+", raw) if p.strip()]
            if not parts:
                return
            # Crea PlayerRecord sintetici per gli host inseriti
            targets = [PlayerRecord(name=ip, ip=ip, version="?", state="unknown") for ip in parts]

        # Usa credenziali di default; in futuro potremmo esporre un dialog per username/password
        self._controller.deploy_via_ssh(targets, username="extra", password="extra", port=22)

    def _handle_open_vnc(self, ip: str) -> None:
        if not self._selected_players:
            QMessageBox.warning(
                self,
                "Connessione VNC",
                "Seleziona almeno un player prima di aprire la connessione VNC.",
            )
            return
        target = next((player for player in self._selected_players if player.ip == ip), None)
        if target is None:
            QMessageBox.critical(
                self,
                "Connessione VNC",
                f"Nessun player selezionato corrisponde all'indirizzo {ip}.",
            )
            return
        self._controller.open_vnc_viewer(target)

    def _handle_framework_changed(self, name: str) -> None:
        if not name or not self._selected_players:
            return
        self._controller.send_misc_command("change_framework", {"name": name}, self._selected_players)
        self._controller.refresh_framework_status(self._selected_players[0])

    def _handle_bundle_state(self, running: bool, message: str) -> None:
        self._update_tab.set_build_state(running, message)
        if running:
            # Disabilita i deploy durante la build
            self._update_tab.set_bundle_available(False)
            self._update_tab.set_bundle_version(None)

    def _handle_autoplay_toggle(self, enabled: bool) -> None:
        if not self._selected_players:
            return
        self._controller.set_autoplay(enabled=enabled, targets=self._selected_players)

    def _handle_bundle_selected(self, path: str) -> None:
        if not path:
            return
        try:
            info = self._controller.set_active_bundle_from_file(path)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Bundle non valido",
                f"Impossibile utilizzare il bundle selezionato:\n{exc}",
            )
            return
        bundle_path = info.get("bundle", path)
        version_value = info.get("version") or bundle_path
        label = self._normalize_version_label(str(version_value))
        self._update_tab.set_bundle_available(True)
        self._update_tab.set_bundle_version(label)
        try:
            self._update_tab.clear_update_summary()
        except Exception:
            pass
        self._append_log(f"Bundle selezionato manualmente: {label} — {bundle_path}")

    def _handle_autoplay_status(self, ip: str, payload: dict) -> None:
        if not self._selected_players:
            return
        primary = self._selected_players[0]
        if ip != primary.ip:
            return
        if payload.get("ok"):
            self._update_tab.set_autoplay(bool(payload.get("enabled")))
        else:
            error = payload.get("error") or "Autoplay status sconosciuto"
            self._append_log(f"[{ip}] {error}")

    def _handle_framework_status(self, ip: str, payload: dict) -> None:
        if not self._selected_players:
            return
        primary = self._selected_players[0]
        if ip != primary.ip:
            return
        if payload.get("ok"):
            current_value = payload.get("current")
            current = str(current_value) if isinstance(current_value, str) else None
            raw_available = payload.get("available") or []
            options = [str(item) for item in raw_available if isinstance(item, str)]
            if current and current not in options:
                options.append(current)
            self._update_tab.set_framework_state(current=current, available=options)
        else:
            error = payload.get("error") or "Framework status sconosciuto"
            self._append_log(f"[{ip}] {error}")

    def _poll_status_tick(self) -> None:
        """Tick di polling: mostra l'ultimo snapshot e pianifica refresh HTTP."""
        if not self._selected_players:
            return
        primary = self._selected_players[0]
        self._status_primary = primary.ip
        # Usa il payload /status cache-ato se disponibile, altrimenti fallback
        # ai soli dati del PlayerRecord per le parti di UI che lo supportano.
        payload = self._controller.get_cached_status(primary.ip) or {}
        interval_s = self._status_poll_interval_seconds()
        now = time.monotonic()
        if payload:
            self._handle_status(primary.ip, payload)
        else:
            self._controller.refresh_status(primary)
            self._next_status_refresh_ts = now + interval_s
            return

        if now >= self._next_status_refresh_ts:
            self._controller.refresh_status(primary)
            self._next_status_refresh_ts = now + interval_s

    def _status_poll_interval_seconds(self) -> float:
        try:
            ms_val = int(getattr(self._controller.state.config.network, "status_poll_ms", 2000))
        except Exception:
            ms_val = 2000
        return max(0.3, float(ms_val) / 1000.0)

    def _handle_status(self, ip: str, payload: dict) -> None:
        # Only reflect the primary selected player's status in the UI
        if not self._selected_players or ip != self._selected_players[0].ip:
            return
        # Timing LED logic
        timing = payload.get("timing")
        timing_ok: bool | None = None
        timing_text: str | None = None
        skew_val: float | None = None
        try:
            if isinstance(timing, dict):
                if "ok" in timing:
                    timing_ok = bool(timing.get("ok"))
                skew = timing.get("skew_ms") or timing.get("skew")
                if isinstance(skew, (int, float)):
                    timing_text = f"Δ {skew:.0f} ms"
                    skew_val = float(skew)
                    if timing_ok is None:
                        timing_ok = abs(float(skew)) <= 50.0
            elif isinstance(timing, (bool, int, float)):
                if isinstance(timing, bool):
                    timing_ok = timing
                else:
                    timing_text = f"Δ {float(timing):.0f} ms"
                    skew_val = float(timing)
                    timing_ok = abs(float(timing)) <= 50.0
        except Exception:
            timing_ok = None
            timing_text = None
        # Blink when within margin (e.g., 30..50 ms)
        blink = False
        if timing_ok is True and isinstance(skew_val, (int, float)):
            blink = 30.0 < abs(float(skew_val)) <= 50.0
        # Determine syncing state from payload
        syncing = False
        try:
            if isinstance(timing, dict) and bool(timing.get("syncing")):
                syncing = True
        except Exception:
            syncing = False
        try:
            self._commands_tab.set_timing_status(timing_ok, timing_text, blink=blink, syncing=bool(syncing))
        except Exception:
            self._commands_tab.set_timing_status(timing_ok, timing_text, blink=blink)

        # HUD visible state
        try:
            hud = payload.get("hud") or {}
            mode = None
            supported_flag: bool | None = None
            if isinstance(hud, dict):
                if "supported" in hud:
                    supported_val = hud.get("supported")
                    if isinstance(supported_val, bool):
                        supported_flag = supported_val
                if "mode" in hud:
                    try:
                        mode_val = hud.get("mode")
                        if isinstance(mode_val, (int, float)):
                            mode = int(mode_val)
                    except Exception:
                        mode = None
                if mode is None and "visible" in hud:
                    hv = hud.get("visible")
                    if isinstance(hv, bool):
                        mode = 2 if hv else 0
                    elif isinstance(hv, (int, float)):
                        mode = 2 if int(hv) != 0 else 0
                if isinstance(mode, int):
                    if mode < 0:
                        mode = 0
                    if mode > 2:
                        mode = 2
            count = len(self._selected_players)
            self._commands_tab.set_hud_state(mode, count, supported=supported_flag)
        except Exception:
            pass

        # Loop states (playback vs playlist)
        try:
            loop_enabled = payload.get("loop_enabled")
            pl_loop = payload.get("playlist_loop")
            count = len(self._selected_players)
            if isinstance(loop_enabled, bool):
                self._commands_tab.set_playback_loop(loop_enabled, count)
            else:
                self._commands_tab.set_playback_loop(None, count)
            stop_at_end = payload.get("stop_at_end")
            if isinstance(stop_at_end, bool):
                self._commands_tab.set_stop_at_end(stop_at_end, count)
            else:
                self._commands_tab.set_stop_at_end(None, count)
            if isinstance(pl_loop, bool):
                self._commands_tab.set_playlist_loop(pl_loop, count)
            else:
                self._commands_tab.set_playlist_loop(None, count)
        except Exception:
            pass

        # Fast-start ready indicator
        fs_ready = False
        fs = payload.get("faststart") or {}
        if isinstance(fs, dict):
            fs_ready = bool(fs.get("ready") or fs.get("prepared"))
        fs_ready = fs_ready or bool(payload.get("faststart_ready") or payload.get("faststart_prepared"))
        self._commands_tab.set_faststart_ready(bool(fs_ready))

        # Overlay probe status (rimosso su richiesta)

        # Loop state sync
        try:
            loop_enabled = payload.get("loop_enabled")
            if loop_enabled is not None:
                self._commands_tab.set_playback_loop(bool(loop_enabled))
        except Exception:
            pass
        try:
            stop_at_end = payload.get("stop_at_end")
            if stop_at_end is not None:
                self._commands_tab.set_stop_at_end(bool(stop_at_end))
        except Exception:
            pass
        try:
            playlist_loop = payload.get("playlist_loop")
            # OFF-backend: accetta anche off_loop
            if playlist_loop is None and str(payload.get("framework")).lower() == "off":
                playlist_loop = payload.get("off_loop")
            if playlist_loop is not None:
                self._commands_tab.set_playlist_loop(bool(playlist_loop))
        except Exception:
            pass

        try:
            self._update_playlist_led_state()
        except Exception:
            pass

        # Brightness value (0..1)
        try:
            b = payload.get("brightness")
            if isinstance(b, (int, float)):
                self._commands_tab.set_brightness(float(b), len(self._selected_players))
        except Exception:
            pass

        # Display mode (resolution)
        try:
            disp = payload.get("display_mode") if isinstance(payload, dict) else None
            if isinstance(disp, dict):
                self._commands_tab.set_display_mode(disp, len(self._selected_players))
            else:
                self._commands_tab.set_display_mode(None, len(self._selected_players))
        except Exception:
            pass

        try:
            center_payload = payload.get("display_center") if isinstance(payload, dict) else None
            center_enabled = None
            if isinstance(center_payload, dict):
                enabled_val = center_payload.get("enabled")
                if isinstance(enabled_val, bool):
                    center_enabled = enabled_val
                elif enabled_val is not None:
                    center_enabled = bool(enabled_val)
            elif center_payload is not None:
                center_enabled = bool(center_payload)
            self._commands_tab.set_display_center(center_enabled, len(self._selected_players))
        except Exception:
            pass

        try:
            img_payload = payload.get("image_duration") if isinstance(payload, dict) else None
            if isinstance(img_payload, dict):
                self._commands_tab.set_image_duration(img_payload, len(self._selected_players))
        except Exception:
            pass

        # Playlist ready aggregation: when pushing, mark device ready on show_ready
        try:
            if self._playlist_active and isinstance(payload, dict) and payload.get("show_ready"):
                if ip in self._playlist_pending:
                    self._playlist_pending.discard(ip)
                if not self._playlist_pending:
                    # All ready -> clear state and set LED green and banner
                    self._playlist_active = False
                    try:
                        self._commands_tab.set_playlist_led("green")
                        self._commands_tab.set_banner("Playlist pronta su tutti i device", level="success")
                    except Exception:
                        pass
        except Exception:
            pass

        # Current device name
        # Show running LED from player_state
        try:
            state = str(payload.get("player_state") or payload.get("state") or "")
            # OFF-backend: se presente off_playing, usalo per forzare LED running
            if str(payload.get("framework")).lower() == "off":
                off_playing = payload.get("off_playing")
                if isinstance(off_playing, bool):
                    self._commands_tab.set_show_running(off_playing)
                elif isinstance(off_playing, str):
                    self._commands_tab.set_show_running(off_playing.lower() in {"playing","true","1","yes"})
            self._commands_tab.set_show_running(state == "playing")
            # If playing started, stop countdown
            if state == "playing" and self._countdown_timer.isActive():
                self._countdown_timer.stop()
                self._show_start_epoch = None
                self._commands_tab.set_countdown_text("In corso")
        except Exception:
            pass

    def _handle_command_completed(self, ip: str, response: dict) -> None:
        self._append_log(f"[{ip}] {response}")
        if not isinstance(response, dict):
            return
        command_name = response.get("_command")
        primary = self._selected_players[0] if self._selected_players else None
        if command_name in {"image_duration_get", "image_duration_set"} and primary and ip == primary.ip:
            if response.get("ok", True):
                try:
                    self._commands_tab.set_image_duration(response, len(self._selected_players))
                except Exception:
                    pass
        if command_name == "display_center" and primary and ip == primary.ip:
            enabled_val = response.get("enabled")
            enabled_flag = None
            if isinstance(enabled_val, bool):
                enabled_flag = enabled_val
            elif enabled_val is not None:
                enabled_flag = bool(enabled_val)
            try:
                self._commands_tab.set_display_center(enabled_flag, len(self._selected_players))
            except Exception:
                pass
            try:
                self._controller.refresh_status(primary)
            except Exception:
                pass
        if response.get("skipped") and response.get("action") in {"next", "prev"}:
            msg = response.get("message") or "Comando ignorato: playlist con un solo elemento"
            try:
                self._commands_tab.set_banner(msg, level="info")
            except Exception:
                pass
            return
        if "missing" in response or "invalid" in response:
            missing_raw = response.get("missing") or []
            invalid_raw = response.get("invalid") or []
            missing = [str(item) for item in missing_raw if item is not None]
            invalid = [str(item) for item in invalid_raw if item is not None]
            try:
                self._commands_tab.set_playlist_validation(missing, invalid)
            except Exception:
                pass
            if missing:
                # Evita di attendere readiness se il push ha fallito per asset mancanti
                self._playlist_active = False
                self._playlist_pending.clear()
        if "loop" in response and "prepared" in response:
            # Playlist apply successo: torna verde il LED se non ci sono warning
            if not response.get("missing") and not response.get("invalid"):
                try:
                    self._commands_tab.set_playlist_led("green")
                except Exception:
                    pass
        if "mode" in response and "visible" in response:
            try:
                mode_val = response.get("mode")
                mode = int(mode_val)
            except Exception:
                mode = None
            count = len(self._selected_players)
            if mode is not None:
                self._commands_tab.set_hud_state(mode, count)
            if self._selected_players:
                self._controller.refresh_status(self._selected_players[0])

    def _handle_device_media_refresh(self) -> None:
        if not self._selected_players:
            return
        self._controller.refresh_device_media(self._selected_players[0])

    def _device_media_tick(self) -> None:
        if not self._selected_players or not self._status_primary:
            return
        # Rispetta il toggle Auto-refresh nel tab
        try:
            if not self._commands_tab.wants_device_auto_refresh():
                return
        except Exception:
            pass
        self._controller.refresh_device_media(self._selected_players[0])

    def _playlist_tick(self) -> None:
        if not self._selected_players or not self._status_primary:
            return
        try:
            if self._commands_tab.playlist_updates_paused():
                return
        except Exception:
            pass
        self._controller.refresh_playlist_status(self._selected_players[0])

    def _handle_playlist_status(self, ip: str, payload: dict) -> None:
        # Aggiorna la playlist GUI con lo stato del device primario
        if not self._selected_players or ip != self._selected_players[0].ip:
            return
        if not isinstance(payload, dict):
            return
        if not payload.get("ok"):
            # Errore o endpoint non disponibile: non fare nulla
            return
        try:
            if self._commands_tab.playlist_updates_paused():
                return
        except Exception:
            pass
        try:
            items = payload.get("items") or []
            current_index = int(payload.get("index", 0))
            loop_flag = payload.get("loop")
            if isinstance(loop_flag, bool):
                loop_value: bool | None = loop_flag
            elif loop_flag is None:
                loop_value = None
            else:
                try:
                    loop_value = bool(int(loop_flag))
                except Exception:
                    loop_value = bool(loop_flag)
            self._commands_tab.set_playlist_from_player(items, current_index, loop=loop_value)
            # Visual badge se playlist proviene da OFF backend
            try:
                if payload.get("_source") == "off":
                    self._commands_tab.set_banner("Playlist OFF", level="info")
            except Exception:
                pass
        except Exception as exc:
            print(f"[GUI] Errore aggiornamento playlist: {exc}", flush=True)
        try:
            self._update_playlist_led_state()
        except Exception:
            pass

    def _handle_device_media(self, ip: str, payload: dict) -> None:
        # Mostra la lista media del device primario selezionato
        if not self._selected_players or ip != self._selected_players[0].ip:
            return
        if not isinstance(payload, dict):
            return
        if not payload.get("ok"):
            err = payload.get("error") or "Errore lettura /media"
            self._append_log(f"[{ip}] {err}")
            return
        files = payload.get("files") or []
        try:
            self._commands_tab.set_device_media_items(files)
        except Exception:
            pass

    def _handle_update_completed(self, payload: dict) -> None:
        if error := payload.get("error"):
            self._append_log(f"Update error: {error}")
            # Nessun bundle valido disponibile
            self._update_tab.set_bundle_available(False)
            self._update_tab.set_bundle_version(None)
        else:
            summary = payload.get("results")
            if summary:
                success = sum(1 for item in summary if item.get("ok"))
                total = len(summary)
                self._append_log(f"Update completato: {success}/{total} successi")
                # Evidenzia fallimenti nella tabella player (colonna Version)
                failed = [r for r in summary if not r.get("ok")]
                for r in summary:
                    ip = r.get("ip") or "?"
                    if r.get("ok"):
                        try:
                            self._player_panel.clear_version_highlight(str(ip))
                        except Exception:
                            pass
                        try:
                            self._player_panel.clear_status_highlight(str(ip))
                        except Exception:
                            pass
                    else:
                        # Prova a costruire un messaggio di errore leggibile
                        msg = None
                        try:
                            msg = r.get("error")
                            resp = r.get("response") or {}
                            if not msg and isinstance(resp, dict):
                                msg = resp.get("error") or resp.get("message")
                            if not msg and r.get("status_code") not in (None, 200):
                                msg = f"HTTP {r.get('status_code')}"
                            if not msg and r.get("phase"):
                                msg = f"fase {r.get('phase')}"
                        except Exception:
                            msg = None
                        try:
                            self._player_panel.highlight_version_failure(str(ip), msg or "errore sconosciuto")
                        except Exception:
                            pass
                        try:
                            self._player_panel.highlight_status_failure(str(ip), msg or "errore sconosciuto")
                        except Exception:
                            pass
                # Log sintetico del primo errore
                if failed:
                    first = failed[0]
                    ip = first.get("ip") or "?"
                    err = None
                    try:
                        err = first.get("error")
                        resp = first.get("response") or {}
                        if not err and isinstance(resp, dict):
                            err = resp.get("error") or resp.get("message")
                        if not err and first.get("status_code") not in (None, 200):
                            err = f"HTTP {first.get('status_code')}"
                        if not err and first.get("phase"):
                            err = f"fase {first.get('phase')}"
                    except Exception:
                        err = None
                    self._append_log(f"[ALERT] Update fallito per {ip}: {err or 'errore sconosciuto'}")
            if payload.get("bundle"):
                # Mostra anche la versione normalizzata accanto al path del bundle
                ver_raw = payload.get("version") or payload.get("bundle") or ""
                ver_label = self._normalize_version_label(str(ver_raw)) if isinstance(ver_raw, str) else str(ver_raw)
                self._append_log(f"Bundle usato: {ver_label} — {payload['bundle']}")
            # Se abbiamo appena creato un bundle, abilita i pulsanti di deploy
            if payload.get("zip_path"):
                self._update_tab.set_bundle_available(True)
                version = payload.get("version") or payload.get("zip_path")
                if isinstance(version, str):
                    version_label = self._normalize_version_label(version)
                    self._update_tab.set_bundle_version(version_label)
            # Mostra un riepilogo grafico nell'Update tab
            try:
                total = len(summary or [])
                succ = sum(1 for item in (summary or []) if item.get("ok"))
                first_err = None
                if total > succ:
                    try:
                        fe = next((r for r in (summary or []) if not r.get("ok")), None)
                        if fe:
                            resp = fe.get("response") or {}
                            if isinstance(resp, dict):
                                first_err = resp.get("error") or resp.get("message")
                            if not first_err and fe.get("status_code") not in (None, 200):
                                first_err = f"HTTP {fe.get('status_code')}"
                            if not first_err and fe.get("phase"):
                                first_err = f"fase {fe.get('phase')}"
                    except Exception:
                        first_err = None
                self._update_tab.show_update_summary(succ, total, first_err)
            except Exception:
                pass

    def _append_log(self, message: str) -> None:
        context_ip = self._extract_log_ip(message)
        self._log_history.append((context_ip, message))
        if self._should_display_log(context_ip):
            self._log_view.append(message)
            self._log_view.moveCursor(QTextCursor.End)

    def _extract_log_ip(self, message: str) -> str | None:
        try:
            match = re.search(r"(?:\d{1,3}\.){3}\d{1,3}", message)
            return match.group(0) if match else None
        except Exception:
            return None

    def _selected_ips(self) -> set[str]:
        try:
            return {p.ip for p in self._selected_players}
        except Exception:
            return set()

    def _should_display_log(self, context_ip: str | None) -> bool:
        selected = self._selected_ips()
        if not selected:
            return True
        if context_ip is None:
            return True
        return context_ip in selected

    def _clear_log_view(self) -> None:
        self._log_history.clear()
        self._log_view.clear()

    def _rebuild_log_view(self) -> None:
        self._log_view.blockSignals(True)
        try:
            self._log_view.clear()
            for context_ip, line in self._log_history:
                if self._should_display_log(context_ip):
                    self._log_view.append(line)
            self._log_view.moveCursor(QTextCursor.End)
        finally:
            self._log_view.blockSignals(False)

    def _update_log_label(self) -> None:
        selected = list(self._selected_players)
        if not selected:
            self._log_label.setText("Event Log — tutti i device")
            return
        if len(selected) == 1:
            player = selected[0]
            name = player.name or player.ip
            self._log_label.setText(f"Event Log — {name} ({player.ip})")
            return
        self._log_label.setText(f"Event Log — {len(selected)} device selezionati")

    def _choose_media_directory(self) -> None:
        current_root = self._controller.state.config.media.media_root
        start_dir = str(current_root) if current_root else str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Seleziona cartella media", start_dir)
        if not selected:
            return
        if self._controller.set_media_root(Path(selected)):
            media_root = str(Path(selected))
            networks = ", ".join(self._controller.state.config.network.scan_ranges)
            self._update_tab.set_settings(networks=networks, media_root=media_root)
            self._commands_tab.set_media_root(Path(selected))
            # Aggiorna watcher
            try:
                self._update_media_watcher(Path(selected))
            except Exception:
                pass

    def _handle_settings_changed(self, payload: dict) -> None:
        raw_media_root = payload.get("media_root")
        media_root = raw_media_root or "<non impostata>"
        networks = payload.get("networks") or ", ".join(self._controller.state.config.network.scan_ranges)
        poll_ms = payload.get("status_poll_ms") or int(getattr(self._controller.state.config.network, "status_poll_ms", 2000))
        self._update_tab.set_settings(networks=networks, media_root=media_root, polling_ms=int(poll_ms))
        self._commands_tab.set_media_root(raw_media_root)
        # Aggiorna watcher se cambia cartella media
        try:
            self._update_media_watcher(raw_media_root)
        except Exception:
            pass
        try:
            self._status_timer.setInterval(int(poll_ms))
        except Exception:
            pass

    def _handle_purge_offline(self) -> None:
        self._controller.purge_offline_players()

    def _handle_force_purge(self, ip: str) -> None:
        self._controller.purge_player(ip)

    # -----------------------------
    # Media directory watcher helpers
    # -----------------------------
    def _update_media_watcher(self, root: Path | str | None) -> None:
        try:
            watcher = getattr(self, "_media_watcher", None)
            if watcher is None:
                return
            # Rimuovi precedenti path osservati
            try:
                for d in watcher.directories():
                    watcher.removePath(d)
            except Exception:
                pass
            path_obj: Path | None = None
            if isinstance(root, Path):
                path_obj = root
            elif isinstance(root, str) and root and root != "<non impostata>":
                try:
                    path_obj = Path(root)
                except Exception:
                    path_obj = None
            if path_obj and path_obj.exists() and path_obj.is_dir():
                try:
                    watcher.addPath(str(path_obj))
                except Exception:
                    pass
        except Exception:
            pass

    def _handle_media_dir_changed(self, path: str) -> None:
        # Trigger refresh della libreria locale quando cambia la cartella o i file
        try:
            self._controller.refresh_media_library()
        except Exception:
            pass

    # -----------------------------
    # Helpers: versione bundle UI
    # -----------------------------
    def _normalize_version_label(self, value: str) -> str:
        """Restituisce una label compatta tipo 'vX.Y.Z' a partire da:
        - path completo del bundle
        - nome file bundle (bundle_v<ver>_<sha>.zip)
        - stringa versione senza 'v' (es. '0.2.12')
        - stringa con prefisso 'v' (es. 'v0.2.12')
        """
        try:
            from pathlib import Path as _P
            name = _P(value).name if ("/" in value or "\\" in value) else value
        except Exception:
            name = value or ""
        s = name.strip()
        import re as _re
        # 1) bundle_v<ver>_<sha>.zip -> <ver>
        m = _re.search(r"bundle_v([0-9A-Za-z_.:+-]+)_", s)
        if m:
            ver = m.group(1)
            return ver if ver.lower().startswith("v") else f"v{ver}"
        # 2) già formattata 'v...' => estrai token migliore
        m = _re.search(r"v[0-9][0-9A-Za-z_.:+-]*", s)
        if m:
            return m.group(0)
        # 3) pura versione numerica/simile => aggiungi prefisso 'v'
        m = _re.fullmatch(r"[0-9][0-9A-Za-z_.:+-]*", s)
        if m:
            return f"v{s}"
        # 4) fallback: restituisci s com'è
        return s

    def _handle_poll_interval_changed(self, ms: int) -> None:
        try:
            ms_val = int(ms)
        except Exception:
            return
        # Robustezza: se il timer non fosse ancora inizializzato, crealo ora
        try:
            timer = getattr(self, "_status_timer")
        except Exception:
            timer = None
        if timer is None:
            try:
                self._status_timer = QTimer(self)
                self._status_timer.timeout.connect(self._poll_status_tick)
            except Exception:
                return
        self._status_timer.setInterval(ms_val)
        self._controller.set_status_poll_ms(ms_val)
        try:
            if self._selected_players:
                self._next_status_refresh_ts = time.monotonic() + self._status_poll_interval_seconds()
        except Exception:
            pass

    def _handle_ping_duration_changed(self, ms: int) -> None:
        try:
            self._controller.set_ping_duration_ms(int(ms))
        except Exception:
            pass

    def _handle_log_live_toggle(self, active: bool, lines: int) -> None:
        # Start/stop live CVLC logs for the primary selected player
        if not self._selected_players:
            # Reflect off state if no targets
            try:
                self._commands_tab.set_log_live_active(False)
            except Exception:
                pass
            return
        if active:
            try:
                self._controller.start_log_live(self._selected_players[0], lines=int(lines))
            except Exception:
                self._controller.stop_log_live()
                self._commands_tab.set_log_live_active(False)
        else:
            self._controller.stop_log_live()

    def _handle_inline_name_edit(self, ip: str, new_name: str) -> None:
        # Invia il set name solo al device editato
        targets = self._controller.get_selected_players([ip])
        if not targets:
            return
        self._controller.send_misc_command("device_name_set", {"name": new_name}, targets)

    # -----------------------------
    # Playlist handlers
    # -----------------------------
    def _handle_playlist_changed(self, items: list[str]) -> None:
        # Any change marks playlist as dirty; LED handled in tab
        # Reset pending state
        self._playlist_active = False
        self._playlist_pending.clear()
        try:
            local_hash = self._commands_tab.get_local_playlist_hash()
            self._controller.set_expected_playlist_hash(local_hash)
        except Exception:
            pass
        self._update_playlist_led_state()

    def _handle_playlist_refresh(self) -> None:
        if not self._selected_players:
            return
        try:
            self._commands_tab.resume_playlist_updates()
        except Exception:
            pass
        self._controller.refresh_playlist_status(self._selected_players[0])

    def _handle_push_playlist(self, items: list[str], clear_before: bool, loop_enabled: bool) -> None:
        if not self._selected_players:
            return
        # Track pending devices for LED aggregation
        self._playlist_pending = {p.ip for p in self._selected_players}
        self._playlist_active = True
        try:
            local_hash = self._commands_tab.get_local_playlist_hash()
            self._controller.set_expected_playlist_hash(local_hash)
        except Exception:
            pass
        # Banner during phases
        if clear_before:
            try:
                self._commands_tab.set_banner("Svuotamento media sui device…", level="progress")
            except Exception:
                pass
        else:
            try:
                self._commands_tab.set_banner("Upload playlist in corso…", level="progress")
            except Exception:
                pass
        # Pass clear_before flag to controller
        self._controller.push_playlist(
            items,
            self._selected_players,
            loop=bool(loop_enabled),
            clear_before=bool(clear_before),
        )

    def _handle_auto_apply_playlist(self, items: list[str], loop_enabled: bool) -> None:
        if not self._selected_players:
            return
        self._playlist_pending = {p.ip for p in self._selected_players}
        self._playlist_active = True
        try:
            local_hash = self._commands_tab.get_local_playlist_hash()
            self._controller.set_expected_playlist_hash(local_hash)
        except Exception:
            pass
        try:
            self._commands_tab.set_banner("Riordino inviato automaticamente…", level="progress")
        except Exception:
            pass
        self._controller.apply_playlist_only(items, self._selected_players, loop=bool(loop_enabled))

    def _update_playlist_led_state(self) -> None:
        # LED aggregato: rosso se dirty, arancio se parziale, verde se tutti pronti, grigio se nessuna selezione
        dirty = False
        try:
            dirty = self._commands_tab.is_playlist_dirty()
        except Exception:
            dirty = False
        if dirty or self._playlist_active:
            try:
                self._commands_tab.set_playlist_led("orange" if self._playlist_active else "red")
                self._commands_tab.set_action_badge("Playlist non inviata")
            except Exception:
                pass
            return
        if not self._selected_players:
            try:
                self._commands_tab.set_playlist_led("gray")
                self._commands_tab.set_action_badge(None)
            except Exception:
                pass
            return
        local_hash = None
        try:
            local_hash = self._commands_tab.get_local_playlist_hash()
        except Exception:
            local_hash = None
        ready_all = True
        ready_any = False
        not_ready_ips: list[str] = []
        reason_counts: dict[str, int] = {}
        reason_details: dict[str, list[str]] = {}
        for p in self._selected_players:
            rec = self._controller.get_player_record(p.ip)
            reason = self._classify_playlist_pending(rec, local_hash)
            if reason is None:
                ready_any = True
                continue
            ready_all = False
            not_ready_ips.append(p.ip)
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            label = None
            try:
                label = getattr(rec, "name", None)
            except Exception:
                label = None
            if not label:
                try:
                    label = getattr(p, "name", None)
                except Exception:
                    label = None
            reason_details.setdefault(reason, []).append(label or p.ip)
        badge_text = self._format_playlist_pending_badge(len(not_ready_ips), reason_counts)
        badge_tip = self._format_playlist_pending_tooltip(reason_details)
        try:
            if ready_all:
                self._commands_tab.set_playlist_led("green")
                self._commands_tab.set_action_badge(None)
            elif ready_any:
                self._commands_tab.set_playlist_led("orange")
                self._commands_tab.set_action_badge(badge_text, tooltip=badge_tip)
            else:
                self._commands_tab.set_playlist_led("red")
                self._commands_tab.set_action_badge(badge_text, tooltip=badge_tip)
        except Exception:
            pass

    def _classify_playlist_pending(self, record: PlayerRecord | None, local_hash: str | None) -> str | None:
        """Return a reason key if the player is not ready, otherwise None."""
        if record is None:
            return "unknown"
        try:
            ready = bool(getattr(record, "playlist_ready", False))
        except Exception:
            ready = False
        try:
            missing = int(getattr(record, "playlist_missing", 0) or 0)
        except Exception:
            missing = 0
        try:
            invalid = int(getattr(record, "playlist_invalid", 0) or 0)
        except Exception:
            invalid = 0
        try:
            remote_hash = getattr(record, "playlist_hash", None)
        except Exception:
            remote_hash = None
        if ready:
            if local_hash and remote_hash and remote_hash != local_hash:
                return "hash"
            return None
        if missing:
            return "missing"
        if invalid:
            return "invalid"
        return "not_applied"

    def _format_playlist_pending_badge(self, total: int, reason_counts: dict[str, int]) -> str | None:
        if total <= 0:
            return None
        labels = {
            "missing": "file mancanti",
            "invalid": "file invalidi",
            "hash": "hash diverso",
            "not_applied": "non applicata",
            "unknown": "sconosciuto",
        }
        order = ["missing", "invalid", "hash", "not_applied", "unknown"]
        parts: list[str] = []
        for key in order:
            count = reason_counts.get(key)
            if not count:
                continue
            parts.append(f"{count} {labels.get(key, key)}")
        if not parts:
            return f"Pending: {total} non pronti"
        return "Pending: " + ", ".join(parts)

    def _format_playlist_pending_tooltip(self, details: dict[str, list[str]]) -> str | None:
        if not details:
            return None
        labels = {
            "missing": "File mancanti",
            "invalid": "File invalidi",
            "hash": "Hash diverso",
            "not_applied": "Playlist non applicata",
            "unknown": "Stato sconosciuto",
        }
        order = ["missing", "invalid", "hash", "not_applied", "unknown"]
        lines: list[str] = []
        for key in order:
            entries = details.get(key)
            if not entries:
                continue
            sample = ", ".join(entries[:4])
            extra = len(entries) - 4
            if extra > 0:
                lines.append(f"{labels.get(key, key)}: {sample} +{extra}")
            else:
                lines.append(f"{labels.get(key, key)}: {sample}")
        return "\n".join(lines) if lines else None

    def _handle_start_show(self, in_time: float | None) -> None:
        """Handle START SHOW button or UDP trigger.
        in_time is expected to be an absolute epoch seconds timestamp if provided.
        If None, start as soon as possible.
        """
        # Determine targets: if none selected, use all known players
        targets = list(self._selected_players) if self._selected_players else list(self._controller.player_registry.current_players())
        if not targets:
            self._append_log("Nessun player noto per START SHOW")
            return
        import time as _time
        now = _time.time()
        start_epoch: float
        if isinstance(in_time, (int, float)) and float(in_time) > now - 1:
            start_epoch = float(in_time)
        else:
            # Immediate (small delay to allow propagation)
            start_epoch = now + 0.2
        # Update countdown UI
        self._show_start_epoch = start_epoch
        remaining = start_epoch - now
        if remaining > 0:
            self._countdown_timer.start()
        else:
            self._countdown_timer.stop()
            self._commands_tab.set_countdown_text("In corso")
        # Issue faststart_go with in_time
        # Pre-flight check: warn if alcuni player non pronti
        not_ready = []
        local_hash = None
        try:
            local_hash = self._commands_tab.get_local_playlist_hash()
        except Exception:
            local_hash = None
        for p in targets:
            rec = self._controller.get_player_record(p.ip)
            ready = bool(rec and getattr(rec, "playlist_ready", False))
            if ready and local_hash:
                ready = rec.playlist_hash == local_hash  # type: ignore[union-attr]
            if not ready:
                not_ready.append(p.ip)
        if not_ready:
            msg = "Questi player non risultano pronti:\n- " + "\n- ".join(not_ready) + "\nProcedere comunque?"
            ans = QMessageBox.warning(self, "Player non pronti", msg, QMessageBox.Yes | QMessageBox.No)
            if ans != QMessageBox.Yes:
                return

        payload = {"in_time": start_epoch}
        self._controller.send_misc_command("faststart_go", payload, targets)

    def _handle_jump_to_track(self, track_number: int) -> None:
        """Handle jump to track request (1-based index)."""
        if not self._selected_players:
            return
        # Converti in 0-based per l'endpoint
        index = track_number - 1
        payload = {"index": index}
        self._controller.send_misc_command("playlist_jump", payload, self._selected_players)

    def _update_countdown(self) -> None:
        import time as _time
        if self._show_start_epoch is None:
            self._countdown_timer.stop()
            self._commands_tab.set_countdown_text("T- —")
            return
        now = _time.time()
        dt = self._show_start_epoch - now
        if dt <= 0:
            self._countdown_timer.stop()
            self._show_start_epoch = None
            self._commands_tab.set_countdown_text("In corso")
            # Mark running LED preemptively
            self._commands_tab.set_show_running(True)
            return
        # Format as T- M:SS or T- S.s
        if dt >= 60:
            m = int(dt // 60)
            s = int(dt % 60)
            txt = f"T- {m:d}:{s:02d}"
        elif dt >= 10:
            s = int(dt)
            txt = f"T- {s:02d}s"
        else:
            txt = f"T- {dt:0.1f}s"
        self._commands_tab.set_countdown_text(txt)

    def _handle_udp_ready_read(self) -> None:
        """Parse UDP events:
        - JSON lines: framework logs forwarded by headless (kind="framework")
        - Text commands: 'start N' and 'jump N' helpers
        """
        import time as _time
        import json as _json
        try:
            while self._udp_socket.hasPendingDatagrams():
                size = self._udp_socket.pendingDatagramSize()
                if size <= 0:
                    break
                data, host, port = self._udp_socket.readDatagram(size)
                # First try JSON payloads (framework logs)
                try:
                    raw = data.decode(errors="ignore").strip()
                except Exception:
                    raw = ""
                if not raw:
                    continue
                if raw.lstrip().startswith("{"):
                    try:
                        payload = _json.loads(raw)
                    except Exception:
                        payload = None
                    if isinstance(payload, dict):
                        kind = str(payload.get("kind") or payload.get("type") or "")
                        if kind == "framework":
                            device_ip = payload.get("device") or payload.get("ip") or host.toString()
                            line = payload.get("line") or payload.get("msg") or payload.get("message")
                            if isinstance(line, str) and line:
                                # Evita duplicati quando il live log è attivo via SSE sul player primario
                                try:
                                    primary_ip = self._selected_players[0].ip if (self._commands_tab.is_log_live_active() and self._selected_players) else None
                                except Exception:
                                    primary_ip = None
                                if primary_ip and str(device_ip) == str(primary_ip):
                                    continue
                                self._append_log(f"[{device_ip}] {line}")
                            continue
                # Fallback to text commands parsing (lowercased)
                text = raw.lower()
                if text.startswith("start"):
                    parts = text.split()
                    secs = 0.0
                    if len(parts) >= 2:
                        try:
                            secs = float(parts[1])
                        except Exception:
                            secs = 0.0
                    secs = max(0.0, secs)
                    epoch = _time.time() + secs
                    self._append_log(f"[UDP {host.toString()}:{port}] start {secs:.1f} -> epoch {epoch:.3f}")
                    self._handle_start_show(epoch)
                elif text.startswith("jump"):
                    parts = text.split()
                    if len(parts) >= 2:
                        # jump N
                        try:
                            track_num = int(parts[1])
                            self._append_log(f"[UDP {host.toString()}:{port}] jump {track_num}")
                            self._handle_jump_to_track(track_num)
                        except Exception:
                            self._append_log(f"[UDP {host.toString()}:{port}] jump: formato non valido")
                    else:
                        # jump (without number): use currently selected in playlist)
                        try:
                            row = self._commands_tab._playlist.currentRow()
                            if row >= 0:
                                track_num = row + 1
                                self._append_log(f"[UDP {host.toString()}:{port}] jump to selected -> {track_num}")
                                self._handle_jump_to_track(track_num)
                            else:
                                self._append_log(f"[UDP {host.toString()}:{port}] jump: nessuna traccia selezionata")
                        except Exception:
                            self._append_log(f"[UDP {host.toString()}:{port}] jump: errore lettura selezione")
        except Exception as exc:
            self._append_log(f"UDP read error: {exc}")

    def _handle_playlist_phase(self, phase: str) -> None:
        # Update banner according to phase
        try:
            if phase == "clearing":
                self._commands_tab.set_banner("Svuotamento media sui device…", level="progress")
            elif phase == "uploading":
                self._commands_tab.set_banner("Upload playlist in corso…", level="progress")
            elif phase == "applying":
                self._commands_tab.set_banner("Applicazione playlist ai device…", level="progress")
            elif phase == "ready":
                self._commands_tab.set_banner("Playlist pronta su tutti i device", level="success")
        except Exception:
            pass
