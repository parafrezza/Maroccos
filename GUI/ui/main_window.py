"""Main window wiring together application tabs."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtNetwork import QUdpSocket, QHostAddress
from PySide6.QtGui import QTextCursor
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
)

from GUI.core.controller import ApplicationController
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
        self.setWindowTitle("Morocco Player Manager")
        self.resize(QSize(1200, 800))

        self._tabs = QTabWidget()
        self._commands_tab = CommandsTab(parent=self._tabs)
        self._update_tab = UpdateTab(parent=self._tabs)

        self._tabs.addTab(self._commands_tab, "Comandi")
        self._tabs.addTab(self._update_tab, "Aggiornamento & Settings")

        self._player_panel = PlayerPanel()
        self._player_panel_default_min = self._player_panel.minimumWidth()
        self._player_panel_default_max = self._player_panel.maximumWidth()

        splitter = QSplitter()
        splitter.addWidget(self._player_panel)
        splitter.addWidget(self._tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([400, 800])
        self._splitter = splitter
        self._player_panel_last_size = 400
        self._panel_collapsed = False

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
        header_layout.addStretch(1)
        header_layout.addWidget(self._log_toggle_btn)
        log_layout.addWidget(self._log_header)
        log_layout.addWidget(self._log_view, 1)
        self._log_container_default_min = self._log_container.minimumHeight()
        self._log_container_default_max = self._log_container.maximumHeight()

        self._main_splitter = QSplitter(Qt.Vertical)
        self._main_splitter.setHandleWidth(10)
        self._main_splitter.addWidget(splitter)
        self._main_splitter.addWidget(self._log_container)
        self._main_splitter.setStretchFactor(0, 3)
        self._main_splitter.setStretchFactor(1, 1)
        self._main_splitter.setSizes([620, 220])

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self._main_splitter)
        self.setCentralWidget(container)

        self._setup_connections()
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
        self._playlist_timer.setInterval(2000)
        self._playlist_timer.timeout.connect(self._playlist_tick)
        # Playlist push tracking
        self._playlist_pending: set[str] = set()
        self._playlist_active: bool = False
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
        self._player_panel.vncRequested.connect(self._handle_open_vnc)

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
        # Playlist wiring
        self._commands_tab.playlistChanged.connect(self._handle_playlist_changed)
        self._commands_tab.playlistPushRequested.connect(self._handle_push_playlist)
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
                self._commands_tab.set_playlist_loop(None, count)
            except Exception:
                pass
            # Start polling status for the primary player
            self._status_primary = self._selected_players[0].ip
            self._status_timer.start()
            self._controller.refresh_status(self._selected_players[0])
            # Start device media polling for primary
            self._device_media_timer.start()
            self._controller.refresh_device_media(self._selected_players[0])
            # Start playlist status polling for primary
            self._playlist_timer.start()
            self._controller.refresh_playlist_status(self._selected_players[0])
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
            self._device_media_timer.stop()
            self._playlist_timer.stop()
            # Stop live logs when no selection
            try:
                self._controller.stop_log_live()
                self._commands_tab.set_log_live_active(False)
            except Exception:
                pass

    def _handle_sync_media_request(self, ip: str, port: int) -> None:
        default_url = ""
        try:
            media_root = self._controller.state.config.media.media_root
            if media_root:
                default_url = str(media_root)
        except Exception:
            pass
        url, ok = QInputDialog.getText(
            self, "Sync media", "URL pacchetto media (.zip)", text=default_url
        )
        if not ok or not url:
            return
        self._controller.sync_media_to_player(ip, port, url.strip())

    def _toggle_player_panel(self) -> None:
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
        except Exception:
            pass

    def _toggle_log_panel(self) -> None:
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
        """Tick di polling: usa lo snapshot del registry invece di fare nuove HTTP.

        Il player_registry sta già interrogando /status periodicamente; qui
        ci limitiamo a riflettere in UI lo stato più recente, evitando
        richieste duplicate verso i player.
        """
        if not self._selected_players:
            return
        primary = self._selected_players[0]
        self._status_primary = primary.ip
        # Usa il payload /status cache-ato se disponibile, altrimenti fallback
        # ai soli dati del PlayerRecord per le parti di UI che lo supportano.
        payload = self._controller.get_cached_status(primary.ip) or {}
        if payload:
            self._handle_status(primary.ip, payload)

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
            if isinstance(hud, dict):
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
            self._commands_tab.set_hud_state(mode, count)
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
            playlist_loop = payload.get("playlist_loop")
            # OFF-backend: accetta anche off_loop
            if playlist_loop is None and str(payload.get("framework")).lower() == "off":
                playlist_loop = payload.get("off_loop")
            if playlist_loop is not None:
                self._commands_tab.set_playlist_loop(bool(playlist_loop))
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
                            resp = r.get("response") or {}
                            if isinstance(resp, dict):
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
                        resp = first.get("response") or {}
                        if isinstance(resp, dict):
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

    def _handle_settings_changed(self, payload: dict) -> None:
        raw_media_root = payload.get("media_root")
        media_root = raw_media_root or "<non impostata>"
        networks = payload.get("networks") or ", ".join(self._controller.state.config.network.scan_ranges)
        poll_ms = payload.get("status_poll_ms") or int(getattr(self._controller.state.config.network, "status_poll_ms", 2000))
        self._update_tab.set_settings(networks=networks, media_root=media_root, polling_ms=int(poll_ms))
        self._commands_tab.set_media_root(raw_media_root)
        try:
            self._status_timer.setInterval(int(poll_ms))
        except Exception:
            pass

    def _handle_purge_offline(self) -> None:
        self._controller.purge_offline_players()

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

    def _handle_playlist_refresh(self) -> None:
        if not self._selected_players:
            return
        self._controller.refresh_playlist_status(self._selected_players[0])

    def _handle_push_playlist(self, items: list[str], clear_before: bool, loop_enabled: bool) -> None:
        if not self._selected_players:
            return
        # Track pending devices for LED aggregation
        self._playlist_pending = {p.ip for p in self._selected_players}
        self._playlist_active = True
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
