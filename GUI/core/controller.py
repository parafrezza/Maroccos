"""Coordinator that bridges services and the Qt UI."""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Iterable

import requests
import time
from PySide6.QtCore import QObject, Signal

from GUI.core.app_state import AppState
from GUI.core.logger import get_logger
from GUI.core.settings_store import SettingsStore
from GUI.services.api_client import ApiClient
from GUI.services.file_server import FileServerService
from GUI.services.media_library import MediaItem, MediaLibrary
from GUI.services.player_registry import PlayerRecord, PlayerRegistry

_LOG = get_logger(__name__)

try:  # noqa: SIM105
    import massive_update as updater
except ModuleNotFoundError:  # pragma: no cover
    updater = None  # type: ignore


@dataclass(slots=True)
class DiscoveryResult:
    players: list[PlayerRecord]
    raw: list[dict]


class ApplicationController(QObject):
    """High-level orchestration of discovery, commands, and updates."""

    logMessage = Signal(str)
    discoveryStarted = Signal()
    discoveryFinished = Signal(list)
    mediaLibraryUpdated = Signal(list)
    settingsChanged = Signal(dict)
    commandCompleted = Signal(str, dict)
    updateCompleted = Signal(dict)
    bundleBuildStateChanged = Signal(bool, str)
    autoplayStatusReceived = Signal(str, dict)
    frameworkStatusReceived = Signal(str, dict)
    statusReceived = Signal(str, dict)

    def __init__(self, settings_path: Path) -> None:
        super().__init__()
        self.state = AppState()
        self._settings_store = SettingsStore(settings_path)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self.player_registry = PlayerRegistry(self.state.config.network)
        self._file_server: FileServerService | None = None
        self._media_library: MediaLibrary | None = None
        self._active_bundle: Path | None = None
        self._active_bundle_version: str | None = None
        self._bundle_building = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Load settings, start services, and kick off discovery."""
        self.state.config = self._settings_store.load()
        self._start_file_server()
        self.player_registry.start()
        self.trigger_discovery()
        self.refresh_media_library()

    def stop(self) -> None:
        self.player_registry.stop()
        self._stop_file_server()
        self._settings_store.save(self.state.config)
        self._executor.shutdown(wait=False)

    def set_status_poll_ms(self, ms: int) -> None:
        """Update status polling interval setting and persist it."""
        try:
            ms = int(ms)
        except Exception:
            return
        ms = max(100, min(ms, 10000))
        if self.state.config.network.status_poll_ms == ms:
            return
        self.state.config.network.status_poll_ms = ms
        self._settings_store.save(self.state.config)
        self.settingsChanged.emit(
            {
                "media_root": str(self.state.config.media.media_root) if self.state.config.media.media_root else "<non impostata>",
                "networks": ", ".join(self.state.config.network.scan_ranges),
                "status_poll_ms": ms,
            }
        )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def trigger_discovery(self) -> None:
        if updater is None:
            self.logMessage.emit("Modulo massive_update non disponibile per discovery")
            return
        self.discoveryStarted.emit()
        future = self._executor.submit(self._perform_discovery)
        future.add_done_callback(self._handle_discovery_result)

    def _perform_discovery(self) -> DiscoveryResult:
        cfg = self.state.config.network
        nets = cfg.scan_ranges
        gui_log = self.logMessage.emit
        original_log = getattr(updater, "log", None)

        def _log_wrapper(message: str) -> None:
            gui_log(message)
            if original_log:
                original_log(message)

        if original_log:
            updater.log = _log_wrapper  # type: ignore[attr-defined]

        try:
            ips = updater.expand_networks(nets)
            players_raw = updater.discover_players(
                ips,
                cfg.player_port,
                timeout=2.0,
                threads=80,
                api_key=cfg.api_key,
                verbose=False,
            )
        finally:
            if original_log:
                updater.log = original_log

        records: list[PlayerRecord] = []
        for entry in players_raw:
            status = entry.get("status") or {}
            name = status.get("name") or status.get("player_name") or entry.get("ip")
            version = entry.get("version") or status.get("version_current") or "?"
            records.append(PlayerRecord(name=name, ip=entry["ip"], version=version, state="online"))
        return DiscoveryResult(players=records, raw=players_raw)

    def _handle_discovery_result(self, future: concurrent.futures.Future[DiscoveryResult]) -> None:
        try:
            result = future.result()
        except Exception as exc:  # pragma: no cover
            self.logMessage.emit(f"Discovery failed: {exc}")
            self.discoveryFinished.emit([])
            return
        self.player_registry.sync_players(result.players)
        self.discoveryFinished.emit(result.raw)

    # ------------------------------------------------------------------
    # Media Library & File Server
    # ------------------------------------------------------------------

    def refresh_media_library(self) -> None:
        library = self._media_library
        if not library:
            self.mediaLibraryUpdated.emit([])
            return
        items = list(library.iter_items())
        formatted = [self._format_media_item(item) for item in items]
        self.mediaLibraryUpdated.emit(formatted)

    def _format_media_item(self, item: MediaItem) -> dict:
        root = self.state.config.media.media_root
        label = item.path.name
        if root and item.path.is_relative_to(root):
            rel = item.path.relative_to(root)
            size_mb = item.size / (1024 * 1024)
            label = f"{rel.as_posix()} ({size_mb:.1f} MB)"
            return {"path": str(item.path), "label": label, "relative": rel.as_posix()}
        size_mb = item.size / (1024 * 1024)
        return {"path": str(item.path), "label": f"{label} ({size_mb:.1f} MB)", "relative": item.path.name}

    def _start_file_server(self) -> None:
        media_cfg = self.state.config.media
        if media_cfg.media_root and media_cfg.media_root.exists():
            self._media_library = MediaLibrary(media_cfg.media_root)
            self._file_server = FileServerService(media_cfg.media_root, media_cfg.server_port)
            try:
                self._file_server.start()
            except OSError as exc:
                self.logMessage.emit(f"Impossibile avviare file server media: {exc}")
                self._file_server = None
            else:
                self.logMessage.emit(
                    f"File server attivo su {self._file_server.base_url} (root={media_cfg.media_root})"
                )
        else:
            self._media_library = None
            self._file_server = None
            self.mediaLibraryUpdated.emit([])

    def _stop_file_server(self) -> None:
        if self._file_server:
            self._file_server.stop()
        self._file_server = None
        self._media_library = None

    def set_media_root(self, new_root: Path) -> bool:
        """Update media directory, restart file server, and refresh library."""
        if not new_root.exists() or not new_root.is_dir():
            self.logMessage.emit(f"Cartella media non valida: {new_root}")
            return False

        try:
            new_root = new_root.resolve()
        except OSError:
            new_root = new_root.absolute()

        current_root = self.state.config.media.media_root
        try:
            if current_root and current_root.resolve() == new_root:
                self.logMessage.emit("Cartella media già impostata")
                return True
        except OSError:
            # Se la resolve fallisce, prosegui comunque con l'aggiornamento
            pass

        self._stop_file_server()
        self.state.config.media.media_root = new_root
        self._start_file_server()
        self.refresh_media_library()
        self._settings_store.save(self.state.config)
        self.settingsChanged.emit(
            {
                "media_root": str(new_root),
                "networks": ", ".join(self.state.config.network.scan_ranges),
            }
        )
        return True

    # ------------------------------------------------------------------
    # Commands & Uploads
    # ------------------------------------------------------------------

    def send_playback_command(
        self,
        command: str,
        payload: dict[str, Any] | None,
        targets: Iterable[PlayerRecord],
    ) -> None:
        self._submit_command(command, payload, targets)

    def send_misc_command(
        self,
        command: str,
        payload: dict[str, Any] | None,
        targets: Iterable[PlayerRecord],
    ) -> None:
        self._submit_command(command, payload, targets)

    def set_autoplay(
        self,
        *,
        enabled: bool,
        targets: Iterable[PlayerRecord],
        restart: bool = True,
        delay: float | None = None,
    ) -> None:
        selected = list(targets)
        if not selected:
            self.logMessage.emit("Nessun player selezionato per autoplay")
            return
        for player in selected:
            self._executor.submit(self._invoke_autoplay_toggle, player, enabled, restart, delay)

    def refresh_autoplay_status(self, player: PlayerRecord) -> None:
        self._executor.submit(self._invoke_autoplay_status, player)

    def refresh_framework_status(self, player: PlayerRecord) -> None:
        self._executor.submit(self._invoke_framework_status, player)

    def refresh_status(self, player: PlayerRecord) -> None:
        """Fetch general /status from a player."""
        self._executor.submit(self._invoke_status, player)

    def upload_media(self, media_path: Path, targets: Iterable[PlayerRecord]) -> None:
        target_list = list(targets)
        if not target_list:
            self.logMessage.emit("Nessun player selezionato per upload")
            return
        if not self._file_server:
            self.logMessage.emit("File server non attivo: impossibile procedere con upload")
            return
        root = self.state.config.media.media_root
        if not root:
            self.logMessage.emit("Media root non configurata")
            return
        try:
            rel_path = media_path.relative_to(root)
        except ValueError:
            self.logMessage.emit(f"Il file {media_path} non appartiene alla cartella media")
            return
        media_url = f"http://{self._host_ip()}:{self.state.config.media.server_port}/{rel_path.as_posix()}"
        self.logMessage.emit(
            f"Richiesta download asset per {rel_path.name} ({rel_path.as_posix()}) verso {len(target_list)} player"
        )
        for player in target_list:
            self._executor.submit(self._invoke_upload, player, media_url, rel_path.as_posix())

    def _submit_command(
        self,
        command: str,
        payload: dict[str, Any] | None,
        targets: Iterable[PlayerRecord],
    ) -> None:
        for player in targets:
            cloned = payload.copy() if payload else None
            self._executor.submit(self._invoke_command, player, command, cloned)

    def _invoke_command(self, player: PlayerRecord, command: str, payload: dict | None) -> None:
        client = self._client_for(player)
        try:
            response = self._execute_command(client, command, payload or {})
            self.commandCompleted.emit(player.ip, response)
        except requests.HTTPError as exc:  # pragma: no cover
            # Graceful fallbacks for optional endpoints on older players
            status = getattr(exc.response, "status_code", None)
            if status == 404 and command in {"faststart_prepare", "faststart_go", "overlay_fade_at", "play_at", "test_on", "test_off"}:
                self.commandCompleted.emit(player.ip, {"ok": False, "error": f"{command} non supportato sul device (404)"})
            else:
                self.logMessage.emit(f"Command {command} su {player.ip} fallito: {exc}")
        except Exception as exc:  # pragma: no cover
            self.logMessage.emit(f"Command {command} su {player.ip} fallito: {exc}")

    def _invoke_upload(self, player: PlayerRecord, media_url: str, target: str) -> None:
        client = self._client_for(player)
        try:
            response = client.upload_media(media_url, target)
            self.commandCompleted.emit(player.ip, response)
        except Exception as exc:
            self.logMessage.emit(f"Upload verso {player.ip} fallito: {exc}")

    def _invoke_autoplay_toggle(
        self,
        player: PlayerRecord,
        enabled: bool,
        restart: bool,
        delay: float | None,
    ) -> None:
        client = self._client_for(player)
        try:
            response = client.set_autoplay(enabled=enabled, restart=restart, delay=delay)
        except Exception as exc:
            self.logMessage.emit(f"Autoplay su {player.ip} fallito: {exc}")
            return
        self.commandCompleted.emit(player.ip, response)
        self.autoplayStatusReceived.emit(player.ip, response)

    def _invoke_autoplay_status(self, player: PlayerRecord) -> None:
        client = self._client_for(player)
        try:
            response = client.get_autoplay()
        except Exception as exc:
            payload = {"ok": False, "error": str(exc)}
        else:
            payload = response
        self.autoplayStatusReceived.emit(player.ip, payload)

    def _invoke_framework_status(self, player: PlayerRecord) -> None:
        client = self._client_for(player)
        try:
            response = client.request("get", "/framework")
        except Exception as exc:
            payload: dict[str, Any] = {"ok": False, "error": str(exc)}
        else:
            payload = response if isinstance(response, dict) else {"ok": False, "error": "Risposta non valida"}
        self.frameworkStatusReceived.emit(player.ip, payload)

    def _invoke_status(self, player: PlayerRecord) -> None:
        client = self._client_for(player)
        try:
            response = client.get_status()
        except Exception as exc:
            payload: dict[str, Any] = {"ok": False, "error": str(exc)}
        else:
            payload = response if isinstance(response, dict) else {"ok": False, "error": "Risposta non valida"}
        self.statusReceived.emit(player.ip, payload)

    def _execute_command(self, client: ApiClient, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        cmd = command.lower()
        if cmd == "play":
            body: dict[str, Any] = {}
            filename = payload.get("filename")
            path = payload.get("path")
            if filename:
                body["filename"] = filename
            if path:
                body["path"] = path
            if "loop" in payload:
                body["loop"] = bool(payload["loop"])
            json_payload = body or None
            return client.request("post", "/play", json=json_payload, timeout=15)
        if cmd == "play_at":
            at = payload.get("at") or payload.get("in_time")
            if at is None:
                raise ValueError("Command play_at richiede 'at' o 'in_time'")
            body: dict[str, Any] = {}
            filename = payload.get("filename")
            path = payload.get("path")
            if filename:
                body["filename"] = filename
            if path:
                body["path"] = path
            if "loop" in payload:
                body["loop"] = bool(payload["loop"])
            body["fade_in_seconds"] = float(payload.get("fade_in_seconds", 0.5))
            if (fo := payload.get("fade_out_seconds")) is not None:
                body["fade_out_seconds"] = float(fo)
            return client.request("post", "/play_at", params={"at": at}, json=body, timeout=15)
        if cmd == "pause":
            return client.request("post", "/pause")
        if cmd in {"resume", "play_resume"}:
            return client.request("post", "/resume")
        if cmd == "stop":
            # Se seconds è specificato, effettua un Fade-To-Black prima dello stop
            seconds = float(payload.get("seconds", 0.0)) if isinstance(payload, dict) else 0.0
            if seconds and seconds > 0:
                try:
                    client.request("post", "/visual/ftb", params={"seconds": seconds})
                except Exception:
                    # Se FTB non disponibile, prosegui comunque con stop
                    pass
                # Attendi la durata del fade prima di inviare /stop
                try:
                    time.sleep(max(0.0, float(seconds)))
                except Exception:
                    pass
            return client.request("post", "/stop")
        if cmd == "next":
            return client.request("post", "/playlist/next")
        if cmd == "prev":
            return client.request("post", "/playlist/prev")
        if cmd == "ftb":
            seconds = float(payload.get("seconds", 1.0))
            # Usa il percorso visual per un vero Fade-To-Black cross-backend
            params: dict[str, Any] = {"seconds": seconds}
            if "in_time" in payload:
                params["in_time"] = payload["in_time"]
            return client.request("post", "/visual/ftb", params=params)
        if cmd == "fade_out":
            seconds = float(payload.get("seconds", 1.0))
            params: dict[str, Any] = {"seconds": seconds}
            if "in_time" in payload:
                params["in_time"] = payload["in_time"]
            return client.request("post", "/fade_out", params=params)
        if cmd == "fade_in":
            seconds = float(payload.get("seconds", 1.0))
            params: dict[str, Any] = {"seconds": seconds}
            if "in_time" in payload:
                params["in_time"] = payload["in_time"]
            return client.request("post", "/fade_in", params=params)
        if cmd == "change_framework":
            name = payload.get("name")
            if not name:
                raise ValueError("Campo 'name' richiesto per change_framework")
            body = {"name": name}
            if payload.get("in_time"):
                body["in_time"] = payload["in_time"]
            return client.request("post", "/change_framework", json=body, timeout=10)
        if cmd == "ping":
            duration = int(payload.get("duration_ms", 200))
            params: dict[str, Any] = {"duration_ms": max(10, duration)}
            if "in_time" in payload:
                params["in_time"] = payload["in_time"]
            return client.request("post", "/ping", params=params)
        if cmd == "hide_splash":
            return client.request("post", "/hide_splash")
        if cmd == "show_splash":
            return client.request("post", "/show_splash")
        if cmd == "shutdown":
            try:
                return client.request("post", "/shutdown", timeout=2)
            except requests.RequestException:
                return {"ok": True, "message": "Shutdown command dispatched"}
        if cmd == "reboot":
            try:
                return client.request("post", "/system/reboot", timeout=2)
            except requests.RequestException:
                return {"ok": True, "message": "Reboot command dispatched"}
        if cmd == "run_setup":
            force = bool(payload.get("force", False))
            return client.request("post", "/maintenance/run_setup", json={"force": force}, timeout=120)
        if cmd == "media_clear":
            return client.request("post", "/media/clear", params={"confirm": 1}, timeout=60)
        if cmd == "playlist_build":
            loop = bool(payload.get("loop", True))
            return client.request("post", "/media/playlist", json={"loop": loop}, timeout=30)
        if cmd == "playlist_loop_on":
            return client.request("post", "/playlist/loop", params={"on": 1})
        if cmd == "playlist_loop_off":
            return client.request("post", "/playlist/loop", params={"on": 0})
        if cmd == "loop_on":
            return client.request("post", "/loop", params={"on": 1})
        if cmd == "loop_off":
            return client.request("post", "/loop", params={"on": 0})
        if cmd == "download_asset":
            url = payload.get("url")
            if not url:
                raise ValueError("Command download_asset richiede campo 'url'")
            body: dict[str, Any] = {"url": url}
            filename = payload.get("filename")
            if filename:
                body["filename"] = filename
            return client.request("post", "/download_asset", json=body, timeout=120)
        # Overlay controls
        if cmd == "overlay_show":
            return client.request("post", "/overlay/show")
        if cmd == "overlay_hide":
            return client.request("post", "/overlay/hide")
        if cmd == "overlay_fade":
            seconds = float(payload.get("seconds", 1.0))
            params: dict[str, Any] = {"seconds": seconds}
            if "in_time" in payload:
                params["in_time"] = payload["in_time"]
            return client.request("post", "/overlay/fade", params=params)
        if cmd == "overlay_fade_at":
            at = payload.get("at") or payload.get("in_time")
            if at is None:
                raise ValueError("Command overlay_fade_at richiede 'at' o 'in_time'")
            seconds = float(payload.get("seconds", 1.0))
            target = float(payload.get("target", 0.0))
            return client.request("post", "/overlay/fade_at", params={"at": at, "seconds": seconds, "target": target})
        # Fast-start
        if cmd == "faststart_prepare":
            body: dict[str, Any] = {}
            if (fn := payload.get("filename")):
                body["filename"] = fn
            if (p := payload.get("path")):
                body["path"] = p
            return client.request("post", "/faststart/prepare", json=(body or None), timeout=15)
        if cmd == "faststart_go":
            params: dict[str, Any] = {}
            if "in_time" in payload:
                params["in_time"] = payload["in_time"]
            return client.request("post", "/faststart/go", params=(params or None))
        # Test mode
        if cmd == "test_on":
            return client.request("post", "/test/on")
        if cmd == "test_off":
            return client.request("post", "/test/off")
        # Device name
        if cmd == "device_name_set":
            name = payload.get("name")
            if not name:
                raise ValueError("Campo 'name' richiesto per device_name_set")
            return client.request("post", "/device/name", json={"name": name})
        raise ValueError(f"Comando non riconosciuto: {command}")
    def _client_for(self, player: PlayerRecord) -> ApiClient:
        cfg = self.state.config.network
        base = f"http://{player.ip}:{cfg.player_port}"
        return ApiClient(base, cfg.api_key)

    # ------------------------------------------------------------------
    # Update/Bundles
    # ------------------------------------------------------------------

    def build_bundle(self) -> None:
        if updater is None:
            self.logMessage.emit("Modulo massive_update non disponibile per build bundle")
            self.bundleBuildStateChanged.emit(False, "Modulo massive_update non disponibile")
            return
        if self._bundle_building:
            self.logMessage.emit("Build bundle già in corso, attendi il completamento")
            self.bundleBuildStateChanged.emit(True, "Costruzione bundle già in corso")
            return
        self._bundle_building = True
        self.bundleBuildStateChanged.emit(True, "Avvio creazione bundle...")
        future = self._executor.submit(self._perform_build_bundle)
        future.add_done_callback(self._handle_bundle_built)

    def _perform_build_bundle(self) -> dict:
        result = updater.BundleResult()
        original_log = getattr(updater, "log", None)

        def _log_wrapper(message: str) -> None:
            self.logMessage.emit(message)
            if original_log:
                original_log(message)

        if original_log:
            updater.log = _log_wrapper  # type: ignore[attr-defined]

        try:
            updater.build_bundle_async(result)
            result.done.wait()
        finally:
            if original_log:
                updater.log = original_log
        payload = {
            "zip_path": str(result.zip_path) if result.zip_path else None,
            "zip_sha": result.zip_sha,
            "version": result.version,
            "error": result.error,
        }
        if result.zip_path:
            self._active_bundle = Path(result.zip_path)
            self._active_bundle_version = result.version
        return payload

    def _handle_bundle_built(self, future: concurrent.futures.Future[dict]) -> None:
        message = "Build bundle completata"
        try:
            payload = future.result()
            if error := payload.get("error"):
                message = f"Build bundle fallita: {error}"
            elif payload.get("zip_path"):
                version = payload.get("version") or payload.get("zip_path")
                message = f"Bundle creato ({version})"
        except Exception as exc:  # pragma: no cover
            self.logMessage.emit(f"Build bundle fallita: {exc}")
            payload = {"error": str(exc)}
            message = f"Build bundle fallita: {exc}"
        finally:
            self._bundle_building = False
            self.bundleBuildStateChanged.emit(False, message)
        if error := payload.get("error"):
            self.logMessage.emit(f"Build bundle errore: {error}")
        else:
            self.logMessage.emit(
                f"Bundle pronto: {payload.get('zip_path')} versione={payload.get('version')} sha={payload.get('zip_sha')}"
            )
        self.updateCompleted.emit(payload)

    def deploy_update(self, targets: Iterable[PlayerRecord]) -> None:
        if updater is None:
            self.logMessage.emit("Modulo massive_update non disponibile per deploy")
            return
        targets = list(targets)
        if not targets:
            self.logMessage.emit("Nessun player selezionato per l'update")
            return
        if not self._active_bundle:
            self.logMessage.emit("Nessun bundle attivo. Costruiscilo prima.")
            return
        cfg = self.state.config
        future = self._executor.submit(self._perform_deploy, targets, cfg)
        future.add_done_callback(self._handle_deploy_done)

    def _perform_deploy(self, targets: list[PlayerRecord], cfg) -> dict:
        bundle_path = self._active_bundle
        if not bundle_path:
            raise RuntimeError("Bundle non impostato")
        sha = updater.sha256_of(bundle_path)
        releases_dir = bundle_path.parent
        update_server = FileServerService(releases_dir, cfg.update.serve_port)
        try:
            update_server.start()
        except OSError as exc:
            self.logMessage.emit(f"Impossibile avviare file server update: {exc}")
            raise
        host_ip = self._host_ip()
        bundle_url = f"http://{host_ip}:{cfg.update.serve_port}/{bundle_path.name}"
        try:
            players_payload = [{"ip": p.ip, "port": cfg.network.player_port} for p in targets]
            results = updater.apply_update(
                players_payload,
                version=self._active_bundle_version or bundle_path.name,
                url=bundle_url,
                sha=sha,
                api_key=cfg.network.api_key,
                verbose=False,
            )
            return {"results": results, "bundle": str(bundle_path), "url": bundle_url}
        finally:
            update_server.stop()

    def _handle_deploy_done(self, future: concurrent.futures.Future[dict]) -> None:
        try:
            payload = future.result()
        except Exception as exc:  # pragma: no cover
            self.logMessage.emit(f"Deploy fallito: {exc}")
            return
        self.updateCompleted.emit(payload)

    # ------------------------------------------------------------------
    # Fallback deploy via SSH (SCP + remote install)
    # ------------------------------------------------------------------
    def deploy_via_ssh(self, targets: Iterable[PlayerRecord], *, username: str = "extra", password: str = "extra", port: int = 22) -> None:
        targets = list(targets)
        if not targets:
            self.logMessage.emit("Nessun player selezionato per deploy via SSH")
            return
        bundle = self._active_bundle
        if not bundle or not bundle.exists():
            self.logMessage.emit("Nessun bundle attivo: crea o seleziona un bundle prima di procedere")
            return
        try:
            import paramiko  # type: ignore
        except Exception as exc:
            self.logMessage.emit(f"paramiko non installato: {exc} (aggiungi 'paramiko' ai requirements e reinstalla)")
            return

        self.logMessage.emit(f"[SSH] Avvio deploy via SSH su {len(targets)} host (user={username})…")

        def _deploy_one(player: PlayerRecord) -> dict:
            host = player.ip
            result: dict[str, Any] = {"ip": host, "ok": False}
            client = None
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.logMessage.emit(f"[SSH:{host}] Connessione…")
                client.connect(hostname=host, port=port, username=username, password=password, timeout=10)
                self.logMessage.emit(f"[SSH:{host}] Connesso")
                sftp = client.open_sftp()
                remote_home = f"/home/{username}"
                remote_bundle = f"{remote_home}/{os.path.basename(str(bundle))}"
                total_size = os.path.getsize(str(bundle))
                last_pct_logged = -1

                def _cb(transferred: int, total: int) -> None:  # type: ignore[override]
                    nonlocal last_pct_logged
                    try:
                        pct = int((transferred / (total or total_size)) * 100)
                    except Exception:
                        pct = 0
                    pct = max(0, min(100, pct))
                    if pct // 10 != last_pct_logged // 10:  # log a ogni 10%
                        last_pct_logged = pct
                        self.logMessage.emit(f"[SSH:{host}] Upload bundle {pct}%")

                self.logMessage.emit(f"[SSH:{host}] Caricamento bundle…")
                try:
                    sftp.put(str(bundle), remote_bundle, callback=_cb)
                finally:
                    self.logMessage.emit(f"[SSH:{host}] Upload completato")

                # Script remoto inner (eseguito come root in un'unica sessione sudo)
                # Nota: usa sintassi POSIX sh (niente pipefail) per massima compatibilità.
                inner_script = f'''
set -eu
APP="/opt/headless-player"
# Estrai fuori dall'albero di destinazione per evitare che rsync --delete rimuova la sorgente
TMP="/tmp/headless_release_tmp_ssh"
RB="{remote_bundle}"
echo "[remote] Preparazione ambiente…"
which unzip >/dev/null 2>&1 || (apt-get update -y >/dev/null 2>&1 || true)
which unzip >/dev/null 2>&1 || (apt-get install -y unzip rsync >/dev/null 2>&1 || true)
# Hard guard: non sincronizzare mai fuori da /opt/headless-player
if [ "$APP" != "/opt/headless-player" ] || [ -z "$APP" ]; then
    echo "[remote] ERRORE: destinazione non sicura: '$APP'"
    exit 97
fi
mkdir -p "$APP"
echo "[remote] Pulizia temp…"
rm -rf "$TMP"
mkdir -p "$TMP"
echo "[remote] Estrazione bundle…"
unzip -o "$RB" -d "$TMP" >/dev/null
echo "[remote] Contenuto TMP:"
ls -la "$TMP"
ROOT="$TMP"
COUNT=$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | wc -l || echo 0)
FILES=$(find "$TMP" -mindepth 1 -maxdepth 1 | wc -l || echo 0)
if [ "$COUNT" -eq 1 ] && [ "$FILES" -eq 1 ]; then ROOT=$(find "$TMP" -mindepth 1 -maxdepth 1 -type d); fi
echo "[remote] Root: $ROOT"
echo "[remote] Validazione bundle…"
# Verifica minima: deve esistere almeno app.py oppure la cartella backends
if [ ! -e "$ROOT/app.py" ] && [ ! -d "$ROOT/backends" ]; then
    echo "[remote] ERRORE: bundle non valido (manca app.py e cartella backends)"
    exit 98
fi
echo "[remote] Sincronizzazione file (SAFE: nessuna cancellazione)…"
rsync -a \
    --include 'media/' \
    --include 'media/black_1280_720.png' \
    --include 'media/black.png' \
    --exclude 'media/**' \
    --exclude 'venv/' \
    --exclude 'headless_venv/' \
    --exclude '__MACOSX' \
    --exclude '.git/' \
    --exclude '__pycache__/' \
    --exclude 'tests/' \
    --exclude 'releases/' \
    --exclude '.*/' \
    "$ROOT"/ "$APP"/

if [ ! -x "$APP/venv/bin/uvicorn" ]; then
    echo "[remote] AVVISO: uvicorn non trovato in $APP/venv/bin/uvicorn"
    if [ -x "$APP/venv/bin/python" ]; then
        echo "[remote] Python venv presente, ma uvicorn mancante. Provo a installare requirements…"
        if [ -f "$APP/requirements.txt" ]; then
            "$APP/venv/bin/pip" install --upgrade pip || true
            "$APP/venv/bin/pip" install -r "$APP/requirements.txt" || true
        else
            "$APP/venv/bin/pip" install --upgrade pip || true
            "$APP/venv/bin/pip" install fastapi uvicorn pillow netifaces || true
        fi
    else
        echo "[remote] Nessun venv in $APP/venv: provo a crearne uno e installare i requirements…"
        # Dipendenze per venv/pip
        apt-get update -y >/dev/null 2>&1 || true
        apt-get install -y python3-venv python3-pip >/dev/null 2>&1 || true
        # Crea venv con utente 'video' se esiste, altrimenti come root
        if id -u video >/dev/null 2>&1; then
            su - video -c "python3 -m venv --system-site-packages '$APP/venv'" || python3 -m venv --system-site-packages "$APP/venv"
            chown -R video:video "$APP/venv" || true
        else
            python3 -m venv --system-site-packages "$APP/venv"
        fi
        "$APP/venv/bin/pip" install --upgrade pip || true
        if [ -f "$APP/requirements.txt" ]; then
            "$APP/venv/bin/pip" install -r "$APP/requirements.txt" || true
        else
            "$APP/venv/bin/pip" install fastapi uvicorn pillow netifaces || true
        fi
    fi
fi

echo "[remote] Restart servizio…"
systemctl restart headless-player || true
sleep 1
if ! systemctl is-active --quiet headless-player; then
    echo "[remote] ERRORE: servizio non attivo dopo il restart"
    systemctl status headless-player --no-pager -l || true
    echo "[remote] Ultime 200 righe di journalctl:"
    journalctl -u headless-player -n 200 --no-pager || true
    exit 99
fi
echo "[remote] Servizio attivo."

echo "[remote] Verifica HTTP di salute…"
# Determina la porta dal service file (fallback 8080)
PORT=8080
if [ -f "/etc/systemd/system/headless-player.service" ]; then
    P=$(grep -oE "--port[[:space:]]+[0-9]+" /etc/systemd/system/headless-player.service | awk '{{print $2}}' | tail -n1 || true)
    if [ -n "$P" ]; then PORT="$P"; fi
fi
# Assicura curl
if ! command -v curl >/dev/null 2>&1; then
    apt-get update -y >/dev/null 2>&1 || true
    apt-get install -y curl >/dev/null 2>&1 || true
fi

# Attende che /status risponda 200 per max ~20s
OK=0
for i in $(seq 1 40); do
    if curl -fsS --max-time 1 "http://127.0.0.1:${{PORT}}/status" >/dev/null 2>&1; then
        OK=1
        break
    fi
    sleep 0.5
done
if [ "$OK" -eq 1 ]; then
    echo "[remote] Healthcheck OK su http://127.0.0.1:${{PORT}}/status"
else
    echo "[remote] AVVISO: /status non risponde su http://127.0.0.1:${{PORT}} (verifica manuale)"
fi
echo "[remote] Pulizia temp finale…"
rm -rf "$TMP"
echo "[remote] Fatto."
'''

                # Scrive lo script remoto su /tmp via SFTP per evitare problemi di quotatura/EOF
                try:
                    with sftp.file("/tmp/headless_deploy_inner.sh", "w") as f:
                        f.write(inner_script)
                    sftp.chmod("/tmp/headless_deploy_inner.sh", 0o755)
                finally:
                    try:
                        sftp.close()
                    except Exception:
                        pass

                # Esegue lo script con sudo -S (prompt vuoto) senza PTY
                cmd = "sudo -S -p \"\" sh /tmp/headless_deploy_inner.sh"
                self.logMessage.emit(f"[SSH:{host}] Esecuzione installazione…")
                stdin, stdout, stderr = client.exec_command(cmd, get_pty=False)
                # Combina stderr nello stdout per semplificare il log streaming
                try:
                    stdout.channel.set_combine_stderr(True)
                except Exception:
                    pass
                try:
                    # Unica invocazione sudo: invia password una volta
                    stdin.write(password + "\n")
                    stdin.flush()
                except Exception:
                    pass
                # Stream output remoto
                chan = stdout.channel
                buf = b""
                while True:
                    if chan.recv_ready():
                        try:
                            chunk = chan.recv(4096)
                        except Exception:
                            chunk = b""
                        if not chunk:
                            pass
                        else:
                            buf += chunk
                            lines = buf.split(b"\n")
                            buf = lines[-1]
                            for line in lines[:-1]:
                                text = line.decode(errors="ignore").strip()
                                if not text:
                                    continue
                                self.logMessage.emit(f"[SSH:{host}] {text}")
                                # Nessuna gestione del prompt: -p "" e singolo sudo evitano contaminazioni stdout
                    if chan.exit_status_ready() and not chan.recv_ready():
                        break
                    time.sleep(0.1)
                # Flush finale
                if buf:
                    text = buf.decode(errors="ignore").strip()
                    if text:
                        self.logMessage.emit(f"[SSH:{host}] {text}")
                code = chan.recv_exit_status()
                if code == 0:
                    self.logMessage.emit(f"[SSH:{host}] Installazione completata (exit 0)")
                    result.update({"ok": True, "message": "ok"})
                else:
                    self.logMessage.emit(f"[SSH:{host}] ERRORE installazione (exit {code})")
                    result.update({"ok": False, "error": f"exit {code}"})
            except Exception as exc:
                self.logMessage.emit(f"[SSH:{host}] ERRORE: {exc}")
                result.update({"ok": False, "error": str(exc)})
            finally:
                try:
                    if client:
                        client.close()
                except Exception:
                    pass
            return result

        future = self._executor.submit(lambda: [_deploy_one(p) for p in targets])

        def _done(fut):
            try:
                results = fut.result()
            except Exception as exc:  # pragma: no cover
                self.logMessage.emit(f"Deploy via SSH fallito: {exc}")
                return
            ok_count = sum(1 for r in results if r.get("ok"))
            self.logMessage.emit(f"[SSH] Deploy via SSH completato: {ok_count}/{len(results)} ok")
            for r in results:
                ip = r.get("ip") or "?"
                if r.get("ok"):
                    self.logMessage.emit(f"[SSH:{ip}] OK")
                else:
                    self.logMessage.emit(f"[SSH:{ip}] ERR: {r.get('error')}")

        future.add_done_callback(_done)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_selected_players(self, ips: Iterable[str]) -> list[PlayerRecord]:
        known = {p.ip: p for p in self.player_registry.current_players()}
        return [known[ip] for ip in ips if ip in known]

    def _host_ip(self) -> str:
        if updater and hasattr(updater, "local_primary_ip"):
            try:
                return updater.local_primary_ip()
            except Exception:  # pragma: no cover
                pass
        return "127.0.0.1"
