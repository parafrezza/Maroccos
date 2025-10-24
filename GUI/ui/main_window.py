"""Main window wiring together application tabs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, QTimer
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

        self._player_panel.refreshRequested.connect(ctrl.trigger_discovery)
        self._player_panel.selectionChanged.connect(self._handle_selection_changed)
        self._player_panel.deviceNameEdited.connect(self._handle_inline_name_edit)

        self._commands_tab.playbackTriggered.connect(self._handle_playback)
        self._commands_tab.miscCommandTriggered.connect(self._handle_misc_command)
        self._commands_tab.uploadRequested.connect(self._handle_upload)
        self._commands_tab.mediaDirectoryRequested.connect(self._choose_media_directory)

        self._update_tab.buildRequested.connect(ctrl.build_bundle)
        self._update_tab.deployRequested.connect(self._handle_deploy)
        self._update_tab.sshDeployRequested.connect(self._handle_deploy_via_ssh)
        self._update_tab.frameworkChanged.connect(self._handle_framework_changed)
        self._update_tab.autoplayToggled.connect(self._handle_autoplay_toggle)
        self._update_tab.deviceNameRequested.connect(self._handle_device_name_request)
        self._update_tab.pollIntervalChanged.connect(self._handle_poll_interval_changed)
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
        if has_targets:
            self._controller.refresh_autoplay_status(self._selected_players[0])
            self._controller.refresh_framework_status(self._selected_players[0])
            # Start polling status for the primary player
            self._status_primary = self._selected_players[0].ip
            self._status_timer.start()
            self._controller.refresh_status(self._selected_players[0])
        else:
            self._update_tab.set_autoplay(False)
            self._update_tab.set_framework_state(current=None, available=[])
            self._status_primary = None
            self._status_timer.stop()

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

        # Current device name
        device_name = payload.get("device_name") or payload.get("name") or ""
        self._update_tab.set_device_name_current(str(device_name))

    def _handle_command_completed(self, ip: str, response: dict) -> None:
        self._append_log(f"[{ip}] {response}")

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
        self._status_timer.setInterval(ms_val)
        self._controller.set_status_poll_ms(ms_val)

    def _handle_inline_name_edit(self, ip: str, new_name: str) -> None:
        # Invia il set name solo al device editato
        targets = self._controller.get_selected_players([ip])
        if not targets:
            return
        self._controller.send_misc_command("device_name_set", {"name": new_name}, targets)
