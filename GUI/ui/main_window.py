"""Main window wiring together application tabs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, QTimer
from PySide6.QtNetwork import QUdpSocket, QHostAddress
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QInputDialog,
    QMainWindow,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from GUI.core.controller import ApplicationController
from GUI.ui.tabs.commands_tab import CommandsTab
from GUI.ui.tabs.update_tab import UpdateTab
from GUI.ui.panels.player_panel import PlayerPanel
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
        self._player_panel.setMinimumWidth(320)

        splitter = QSplitter()
        splitter.addWidget(self._player_panel)
        splitter.addWidget(self._tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        self._log_label = QLabel("Event Log")
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMinimumHeight(180)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)
        layout.addWidget(splitter, 1)
        layout.addWidget(self._log_label)
        layout.addWidget(self._log_view, 0)
        self.setCentralWidget(container)

        self._setup_connections()
        # Status polling timer (stopped by default)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._poll_status_tick)
        self._status_primary = None
        # Device media polling timer
        self._device_media_timer = QTimer(self)
        self._device_media_timer.setInterval(3000)
        self._device_media_timer.timeout.connect(self._device_media_tick)
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
        ctrl.logMessage.connect(self._append_log)
        ctrl.discoveryStarted.connect(lambda: self._player_panel.set_busy(True))
        ctrl.discoveryFinished.connect(self._handle_discovery_finished)
        ctrl.mediaLibraryUpdated.connect(self._commands_tab.set_media_items)
        ctrl.settingsChanged.connect(self._handle_settings_changed)
        ctrl.commandCompleted.connect(self._handle_command_completed)
        ctrl.updateCompleted.connect(self._handle_update_completed)
        ctrl.frameworkStatusReceived.connect(self._handle_framework_status)
        ctrl.statusReceived.connect(self._handle_status)
        ctrl.deviceMediaReceived.connect(self._handle_device_media)
        # Live CVLC logs wiring
        ctrl.logLiveLine.connect(self._commands_tab.append_log_live_line)
        ctrl.logLiveStatusChanged.connect(self._commands_tab.set_log_live_active)
        self._commands_tab.logLiveToggleRequested.connect(self._handle_log_live_toggle)

        self._player_panel.refreshRequested.connect(ctrl.trigger_discovery)
        self._player_panel.selectionChanged.connect(self._handle_selection_changed)
        self._player_panel.deviceNameEdited.connect(self._handle_inline_name_edit)

        self._commands_tab.playbackTriggered.connect(self._handle_playback)
        self._commands_tab.miscCommandTriggered.connect(self._handle_misc_command)
        self._commands_tab.uploadRequested.connect(self._handle_upload)
        self._commands_tab.mediaDirectoryRequested.connect(self._choose_media_directory)
        self._commands_tab.deviceMediaRefreshRequested.connect(self._handle_device_media_refresh)
        # Playlist wiring
        self._commands_tab.playlistChanged.connect(self._handle_playlist_changed)
        self._commands_tab.playlistPushRequested.connect(self._handle_push_playlist)
        self._commands_tab.startShowRequested.connect(self._handle_start_show)
        ctrl.playlistPhaseChanged.connect(self._handle_playlist_phase)

        self._update_tab.buildRequested.connect(ctrl.build_bundle)
        self._update_tab.deployRequested.connect(self._handle_deploy)
        self._update_tab.sshDeployRequested.connect(self._handle_deploy_via_ssh)
        self._update_tab.frameworkChanged.connect(self._handle_framework_changed)
        self._update_tab.autoplayToggled.connect(self._handle_autoplay_toggle)
        self._update_tab.deviceNameRequested.connect(self._handle_device_name_request)
        self._update_tab.pollIntervalChanged.connect(self._handle_poll_interval_changed)
        self._update_tab.overlayDurationsApplied.connect(self._handle_overlay_durations_applied)
        ctrl.autoplayStatusReceived.connect(self._handle_autoplay_status)
        ctrl.bundleBuildStateChanged.connect(self._handle_bundle_state)

        config = ctrl.state.config
        self._update_tab.set_settings(
            networks=", ".join(config.network.scan_ranges),
            media_root=str(config.media.media_root) if config.media.media_root else "<non impostata>",
            polling_ms=int(getattr(config.network, "status_poll_ms", 1000)),
        )
        # Initialize timer interval from settings
        try:
            self._status_timer.setInterval(int(getattr(config.network, "status_poll_ms", 1000)))
        except Exception:
            pass
        self._update_tab.enable_actions(False)
        self._update_tab.set_build_state(False, "Pronto a creare un bundle")
        self._update_tab.set_framework_state(current=None, available=[])
        # All'avvio non c'è un bundle attivo
        self._update_tab.set_bundle_available(False)
        self._update_tab.set_bundle_version(None)

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

    def _handle_selection_changed(self, ips: list[str]) -> None:
        self._selected_players = self._controller.get_selected_players(ips)
        has_targets = bool(self._selected_players)
        self._commands_tab.set_targets_selected(has_targets)
        self._update_tab.enable_actions(has_targets)
        # Reset last action badge on selection change
        try:
            self._last_action_badge = None
            self._commands_tab.set_action_badge(None)
        except Exception:
            pass
        if has_targets:
            self._controller.refresh_autoplay_status(self._selected_players[0])
            self._controller.refresh_framework_status(self._selected_players[0])
            # Start polling status for the primary player
            self._status_primary = self._selected_players[0].ip
            self._status_timer.start()
            self._controller.refresh_status(self._selected_players[0])
            # Start device media polling for primary
            self._device_media_timer.start()
            self._controller.refresh_device_media(self._selected_players[0])
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
            # Stop live logs when no selection
            try:
                self._controller.stop_log_live()
                self._commands_tab.set_log_live_active(False)
            except Exception:
                pass

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

    def _handle_device_name_request(self, _placeholder: str) -> None:
        if not self._selected_players:
            return
        name, ok = QInputDialog.getText(self, "Nome Dispositivo", "Inserisci il nuovo nome del dispositivo")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        self._controller.send_misc_command("device_name_set", {"name": name}, self._selected_players)

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
        if not self._selected_players or not self._status_primary:
            return
        primary = self._selected_players[0]
        if primary.ip != self._status_primary:
            self._status_primary = primary.ip
        self._controller.refresh_status(primary)

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
        self._commands_tab.set_timing_status(timing_ok, timing_text, blink=blink)

        # Fast-start ready indicator
        fs_ready = False
        fs = payload.get("faststart") or {}
        if isinstance(fs, dict):
            fs_ready = bool(fs.get("ready") or fs.get("prepared"))
        fs_ready = fs_ready or bool(payload.get("faststart_ready") or payload.get("faststart_prepared"))
        self._commands_tab.set_faststart_ready(bool(fs_ready))

        # Action badge from backend status (fsm_action at top level or nested)
        try:
            action = payload.get("fsm_action")
            if action is None and isinstance(payload.get("fsm"), dict):
                action = payload.get("fsm", {}).get("action")
            if isinstance(action, str) and action:
                if action != self._last_action_badge:
                    self._commands_tab.set_action_badge(action)
                    self._last_action_badge = action
            else:
                # No action -> do nothing (badge auto-hides)
                pass
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
        device_name = payload.get("device_name") or payload.get("name") or ""
        self._update_tab.set_device_name_current(str(device_name))
        # Show running LED from player_state
        try:
            state = str(payload.get("player_state") or payload.get("state") or "")
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
            if payload.get("bundle"):
                self._append_log(f"Bundle usato: {payload['bundle']}")
            # Se abbiamo appena creato un bundle, abilita i pulsanti di deploy
            if payload.get("zip_path"):
                self._update_tab.set_bundle_available(True)
                version = payload.get("version") or payload.get("zip_path")
                if isinstance(version, str):
                    # Normalizza a sola versione: prova a estrarre 'vX.Y.Z' dal nome
                    from pathlib import Path as _Path
                    try:
                        name = _Path(version).name if ("/" in version or "\\" in version) else version
                    except Exception:
                        name = version
                    import re as _re
                    m = _re.search(r"v[0-9][0-9.]*", name)
                    version_label = m.group(0) if m else name
                    self._update_tab.set_bundle_version(version_label)

    def _append_log(self, message: str) -> None:
        self._log_view.append(message)

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

    def _handle_settings_changed(self, payload: dict) -> None:
        media_root = payload.get("media_root") or "<non impostata>"
        networks = payload.get("networks") or ", ".join(self._controller.state.config.network.scan_ranges)
        poll_ms = payload.get("status_poll_ms") or int(getattr(self._controller.state.config.network, "status_poll_ms", 1000))
        self._update_tab.set_settings(networks=networks, media_root=media_root, polling_ms=int(poll_ms))
        try:
            self._status_timer.setInterval(int(poll_ms))
        except Exception:
            pass

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

    def _handle_overlay_durations_applied(self, fade_out_s: float, fade_in_s: float) -> None:
        if not self._selected_players:
            return
        try:
            out_s = float(fade_out_s)
            in_s = float(fade_in_s)
        except Exception:
            return
        self._controller.apply_overlay_settings(out_s, in_s, self._selected_players)

    # -----------------------------
    # Playlist handlers
    # -----------------------------
    def _handle_playlist_changed(self, items: list[str]) -> None:
        # Any change marks playlist as dirty; LED handled in tab
        # Reset pending state
        self._playlist_active = False
        self._playlist_pending.clear()

    def _handle_push_playlist(self, items: list[str], clear_before: bool) -> None:
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
        self._controller.push_playlist(items, self._selected_players, clear_before=bool(clear_before))

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
        """Parse UDP commands like 'start N' on port 9999 and trigger start_show."""
        import time as _time
        try:
            while self._udp_socket.hasPendingDatagrams():
                size = self._udp_socket.pendingDatagramSize()
                if size <= 0:
                    break
                data, host, port = self._udp_socket.readDatagram(size)
                try:
                    text = (data.decode(errors="ignore").strip().lower())
                except Exception:
                    continue
                if not text:
                    continue
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
