"""Coordinator that bridges services and the Qt UI."""

from __future__ import annotations

import concurrent.futures
import platform
import re
import socket
import threading
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable, Optional

import requests
import time
import zipfile
from urllib.parse import quote


def _build_magic_packet(mac: str) -> bytes:
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", mac)
    if len(cleaned) != 12:
        raise ValueError("MAC non valida")
    raw = bytes.fromhex(cleaned)
    return b"\xFF" * 6 + raw * 16

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - psutil is optional but recommended
    psutil = None  # type: ignore
from PySide6.QtCore import QObject, Signal

from GUI.core.app_state import AppState
from GUI.core.logger import get_logger
from GUI.core.config import MacEntry
from GUI.core.settings_store import SettingsStore
from GUI.services.api_client import ApiClient
from GUI.services.file_server import FileServerService
from GUI.services.media_library import MediaItem, MediaLibrary
from GUI.services.player_registry import PlayerRecord, PlayerRegistry
from GUI.services.time_sync import TimeSyncSupervisor

_LOG = get_logger(__name__)
_MAC_PATTERN = re.compile(r"([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}")


def _normalize_mac(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", raw)
    if len(cleaned) != 12:
        return None
    return ":".join(cleaned[i : i + 2].upper() for i in range(0, 12, 2))

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
    startupMacsUpdated = Signal(list)
    settingsChanged = Signal(dict)
    commandCompleted = Signal(str, dict)
    updateCompleted = Signal(dict)
    # Upload activity (in-flight count)
    uploadActivityChanged = Signal(int)
    # Upload progress (done, total, in_flight)
    uploadProgressChanged = Signal(int, int, int)
    # Upload activity (in-flight count)
    uploadActivityChanged = Signal(int)
    bundleBuildStateChanged = Signal(bool, str)
    autoplayStatusReceived = Signal(str, dict)
    frameworkStatusReceived = Signal(str, dict)
    statusReceived = Signal(str, dict)
    deviceMediaReceived = Signal(str, dict)
    playlistPhaseChanged = Signal(str)
    playlistStatusReceived = Signal(str, dict)
    # Live log streaming (framework logs routed to event log)
    logLiveStatusChanged = Signal(bool)
    # Media clone workflow
    mediaCloneProgress = Signal(float, str)
    mediaCloneLog = Signal(str)
    mediaCloneCompleted = Signal(bool, str)

    def __init__(self, settings_path: Path) -> None:
        super().__init__()
        self.state = AppState()
        self._settings_store = SettingsStore(settings_path)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self.player_registry = PlayerRegistry(self.state.config.network)
        # Time sync supervisor (no config; default UDP port)
        self._time_sync = TimeSyncSupervisor()
        # Hook player events to start/stop sync workers automatically
        try:
            self.player_registry.playerUpdated.connect(self._on_player_updated)
            self.player_registry.playerRemoved.connect(self._on_player_removed)
        except Exception:
            pass
        self._file_server: FileServerService | None = None
        self._media_library: MediaLibrary | None = None
        self._media_scan_future: concurrent.futures.Future | None = None
        self._media_scan_pending: bool = False
        self._media_scan_cancel: threading.Event | None = None
        self._active_bundle: Path | None = None
        self._active_bundle_version: str | None = None
        self._bundle_building = False
        # Live log worker
        self._log_worker_stop = False
        self._log_worker_future: concurrent.futures.Future | None = None
        # Upload tracking
        self._uploads_in_flight = 0
        self._uploads_total = 0
        self._uploads_done = 0
        self._uploads_lock = threading.Lock()
        self._permission_fix_attempted: set[str] = set()
        # Cached /status payloads keyed by player IP to avoid redundant fetches
        self._status_cache: dict[str, dict[str, Any]] = {}
        self._status_cache_lock = threading.Lock()
        self._media_clone_future: concurrent.futures.Future | None = None

    @property
    def settings_path(self) -> Path:
        return self._settings_store.path

    # ------------------------------------------------------------------
    # Snapshots (GUI helpers)
    # ------------------------------------------------------------------

    def get_snapshot_players(self) -> list[PlayerRecord]:
        """Ritorna uno snapshot thread-safe dei player correnti.

        Usato dal main window per aggiornare la UI senza fare altre HTTP.
        """
        try:
            return self.player_registry.current_players()
        except Exception:
            return []

    def get_cached_status(self, ip: str) -> dict[str, Any] | None:
        """Ritorna l'ultimo /status noto per un IP, se presente in cache."""
        try:
            with self._status_cache_lock:
                payload = self._status_cache.get(ip)
                # Copia shallow per evitare modifiche in-place dal chiamante
                return dict(payload) if isinstance(payload, dict) else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Load settings, start services, and kick off discovery."""
        self.state.config = self._settings_store.load()
        self._refresh_scan_ranges_from_local_interfaces(emit=True)
        self._ensure_remote_defaults()
        self._start_file_server()
        self.player_registry.start()
        skip_discovery = os.environ.get("GUI_SKIP_STARTUP_DISCOVERY", "0")
        if skip_discovery not in {"1", "true", "True"}:
            self.trigger_discovery()
        self.refresh_media_library()
        self.startupMacsUpdated.emit(self.known_startup_macs())

    def stop(self) -> None:
        self.player_registry.stop()
        self._stop_file_server()
        if self._media_scan_cancel:
            self._media_scan_cancel.set()
            self._media_scan_cancel = None
        if self._media_scan_future and not self._media_scan_future.done():
            self._media_scan_future.cancel()
            self._media_scan_future = None
        self._media_scan_pending = False
        # Stop time sync workers
        try:
            self._time_sync.stop_all()
        except Exception:
            pass
        self._settings_store.save(self.state.config)
        self._executor.shutdown(wait=False)

    def _ensure_remote_defaults(self) -> None:
        cfg = self.state.config.remote
        current = self._coerce_path(cfg.vnc_viewer_path)
        if current and current.exists():
            return
        candidates: list[Path] = []
        candidates.append(Path(__file__).resolve().parents[2] / "vncviewer64-1.15.0.exe")
        if os.name == "nt":
            program_files = os.environ.get("ProgramFiles")
            program_files_x86 = os.environ.get("ProgramFiles(x86)")
            for root in filter(None, {program_files, program_files_x86}):
                pf_path = Path(root)
                candidates.extend([
                    pf_path / "TigerVNC" / "vncviewer.exe",
                    pf_path / "TigerVNC" / "vncviewer64-1.15.0.exe",
                ])
        for candidate in candidates:
            try:
                if candidate.exists():
                    cfg.vnc_viewer_path = candidate
                    try:
                        self._settings_store.save(self.state.config)
                    except Exception:
                        _LOG.debug("Unable to persist default VNC viewer path", exc_info=True)
                    return
            except OSError:
                continue
        cfg.vnc_viewer_path = None

    def sync_media_to_player(self, ip: str, port: int, url: str) -> None:
        """Ask the player to download media content from the provided URL."""
        def worker():
            base = f"http://{ip}:{port}"
            client = ApiClient(base_url=base, api_key=self.state.config.network.api_key)
            try:
                result = client.sync_media(url, timeout=300)
                self.logMessage.emit(f"[MEDIA] {ip}: sync completed ({result.get('count')} files)")
            except Exception as exc:
                self.logMessage.emit(f"[MEDIA] {ip}: sync failed ({exc})")

        try:
            self._executor.submit(worker)
        except Exception as exc:
            self.logMessage.emit(f"[MEDIA] Unable to run sync worker for {ip}: {exc}")

    def clone_media_from_player(self, source: PlayerRecord) -> None:
        """Clone the media library and playlist from the selected source player to all others."""
        if self._media_clone_future and not self._media_clone_future.done():
            self.logMessage.emit("Clonazione media già in corso: attendi il completamento")
            return
        if not getattr(source, "media_available", True):
            self.logMessage.emit(f"[Clone] Il player {source.ip} non ha media disponibili da clonare")
            return
        label = source.name or source.ip
        self.mediaCloneLog.emit(f"[Clone] Avvio clonazione da {label} ({source.ip})")
        self.mediaCloneProgress.emit(0.0, f"Preparazione clone da {source.ip}")
        try:
            future = self._executor.submit(self._run_media_clone_job, source)
        except Exception as exc:
            msg = f"Impossibile avviare il job di clonazione: {exc}"
            self.logMessage.emit(f"[Clone] {msg}")
            self.mediaCloneLog.emit(f"[Clone] {msg}")
            self.mediaCloneProgress.emit(0.0, msg)
            return
        self._media_clone_future = future
        future.add_done_callback(self._handle_media_clone_done)

    def _handle_media_clone_done(self, future: concurrent.futures.Future) -> None:
        try:
            result = future.result()
            ok = bool(result.get("ok", False)) if isinstance(result, dict) else bool(result)
            summary = result.get("summary") if isinstance(result, dict) else None
        except Exception as exc:
            ok = False
            summary = f"Clonazione media fallita: {exc}"
            self.logMessage.emit(f"[Clone] {summary}")
            self.mediaCloneLog.emit(f"[Clone] {summary}")
        else:
            if not summary:
                summary = "Clonazione completata" if ok else "Clonazione terminata"
            self.logMessage.emit(f"[Clone] {summary}")
        finally:
            self._media_clone_future = None
            self.mediaCloneCompleted.emit(ok, summary or "")

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
        self._emit_settings_snapshot()

    def set_ping_duration_ms(self, ms: int) -> None:
        try:
            ms = int(ms)
        except Exception:
            return
        ms = max(10, min(ms, 5000))
        try:
            current = int(getattr(self.state.config.network, "ping_ms", 200))
        except Exception:
            current = 200
        if current == ms:
            return
        setattr(self.state.config.network, "ping_ms", ms)
        try:
            self._settings_store.save(self.state.config)
        except Exception:
            pass
        self._emit_settings_snapshot()

    def _emit_settings_snapshot(self) -> None:
        media_root = str(self.state.config.media.media_root) if self.state.config.media.media_root else "<non impostata>"
        networks_txt = ", ".join(self.state.config.network.scan_ranges)
        if not networks_txt:
            networks_txt = "<auto>"
        payload = {
            "media_root": media_root,
            "networks": networks_txt,
            "status_poll_ms": self.state.config.network.status_poll_ms,
            "ping_ms": int(getattr(self.state.config.network, "ping_ms", 200)),
        }
        self.settingsChanged.emit(payload)

    def _detect_local_ipv4_networks(self) -> list[str]:
        if psutil is None:  # type: ignore
            _LOG.debug("psutil non disponibile: uso solo reti configurate manualmente")
            return []
        networks: list[str] = []
        try:
            iface_addrs = psutil.net_if_addrs()  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - psutil failure
            _LOG.warning("Impossibile enumerare le interfacce di rete: %s", exc)
            return []

        for iface, addresses in iface_addrs.items():
            for addr in addresses:
                if addr.family != socket.AF_INET:
                    continue
                ip = getattr(addr, "address", None) or getattr(addr, "addr", None)
                netmask = getattr(addr, "netmask", None)
                if not ip or not netmask:
                    continue
                if ip == "0.0.0.0":
                    continue
                if isinstance(ip, str) and (ip.startswith("127.") or ip.startswith("169.254.")):
                    continue
                try:
                    network = ipaddress.IPv4Network((ip, netmask), strict=False)
                except Exception:
                    continue
                networks.append(str(network))

        unique_networks = list(dict.fromkeys(networks))
        # Escludi reti 2.x.x.x/8 dal discovery
        filtered = [n for n in unique_networks if not str(n).startswith("2.")]
        if filtered and len(filtered) != len(unique_networks):
            _LOG.debug("Filtro reti 2.x.x.x: %s -> %s", ", ".join(unique_networks), ", ".join(filtered))
        elif filtered:
            _LOG.debug("Reti IPv4 locali rilevate: %s", ", ".join(filtered))
        return filtered

    def _refresh_scan_ranges_from_local_interfaces(self, *, emit: bool) -> list[str]:
        cfg = self.state.config.network
        detected = self._detect_local_ipv4_networks()
        combined = detected or ["192.168.1.0/24"]

        changed = cfg.scan_ranges != combined
        if changed:
            cfg.scan_ranges = combined
        if emit or changed:
            self._emit_settings_snapshot()
        return cfg.scan_ranges

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def trigger_discovery(self) -> None:
        if updater is None:
            self.logMessage.emit("Modulo massive_update non disponibile per discovery")
            return
        nets = list(self._refresh_scan_ranges_from_local_interfaces(emit=False))
        self.discoveryStarted.emit()
        future = self._executor.submit(self._perform_discovery, nets)
        future.add_done_callback(self._handle_discovery_result)

    def _perform_discovery(self, nets: list[str]) -> DiscoveryResult:
        cfg = self.state.config.network
        if not nets:
            nets = ["192.168.1.0/24"]
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
            # Escludi IP 2.x.x.x dal discovery
            ips = [ip for ip in ips if not str(ip).startswith("2.")]
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

        # Deduplica per device_id quando disponibile (stesso host su più subnet)
        def _ip_score(ip: str) -> int:
            try:
                if ip.startswith("192.168."):
                    return 0
                if ip.startswith("10."):
                    return 1
                if ip.startswith("172."):
                    parts = ip.split(".")
                    if len(parts) >= 2:
                        sec = int(parts[1])
                        if 16 <= sec <= 31:
                            return 2
                return 5
            except Exception:
                return 9

        best_by_id: dict[str, dict] = {}
        for entry in players_raw:
            status = entry.get("status") or {}
            dev_id = status.get("device_id")
            key = dev_id if isinstance(dev_id, str) and dev_id else entry.get("ip")
            cur = best_by_id.get(key)
            if cur is None:
                best_by_id[key] = entry
            else:
                ip_new = str(entry.get("ip"))
                ip_old = str(cur.get("ip"))
                if _ip_score(ip_new) < _ip_score(ip_old):
                    best_by_id[key] = entry

        records: list[PlayerRecord] = []
        for entry in best_by_id.values():
            status = entry.get("status") or {}
            name = status.get("name") or status.get("player_name") or entry.get("ip")
            version = entry.get("version") or status.get("version_current") or "?"
            dev_id = status.get("device_id") if isinstance(status, dict) else None
            inst_id = status.get("instance_id") if isinstance(status, dict) else None
            records.append(
                PlayerRecord(
                    name=name,
                    ip=entry["ip"],
                    version=version,
                    state="online",
                    device_id=(str(dev_id) if isinstance(dev_id, str) else None),
                    instance_id=(str(inst_id) if isinstance(inst_id, str) else None),
                )
            )
        return DiscoveryResult(players=records, raw=list(best_by_id.values()))

    def _handle_discovery_result(self, future: concurrent.futures.Future[DiscoveryResult]) -> None:
        try:
            result = future.result()
        except Exception as exc:  # pragma: no cover
            self.logMessage.emit(f"Discovery failed: {exc}")
            self.discoveryFinished.emit([])
            return
        self.player_registry.sync_players(result.players)
        # Se non esiste un override host esplicito, prova a impostarne uno dinamico
        try:
            self._maybe_set_dynamic_media_host_override(result.players)
        except Exception:
            pass
        self._update_known_startup_macs(result.raw)
        self.discoveryFinished.emit(result.raw)

    def _maybe_set_dynamic_media_host_override(self, players: list[PlayerRecord]) -> None:
        """Se l'override host del file server non è impostato, sceglie dinamicamente
        l'IP locale che copre il maggior numero di player (match /24) e lo persiste.
        Non fa nulla se un override è già configurato o se non ci sono player.
        """
        try:
            media_cfg = self.state.config.media
        except Exception:
            return
        # Rispetta override già configurato
        try:
            if getattr(media_cfg, "server_host_override", None):
                return
        except Exception:
            pass
        if not players:
            return
        candidates = []
        try:
            candidates = self._local_ipv4_candidates()
        except Exception:
            candidates = []
        if not candidates:
            return
        # Conta quanti player per candidato (stessa /24)
        best_ip = None
        best_score = -1
        for cand in candidates:
            score = 0
            for p in players:
                try:
                    net = ipaddress.ip_network(f"{p.ip}/24", strict=False)
                    if ipaddress.ip_address(cand) in net:
                        score += 1
                except Exception:
                    continue
            if score > best_score:
                best_score = score
                best_ip = cand
        # Imposta solo se copre almeno un player
        if best_ip and best_score > 0:
            try:
                media_cfg.server_host_override = best_ip
                self._settings_store.save(self.state.config)
                self._emit_settings_snapshot()
                self.logMessage.emit(
                    f"Host file server dinamico impostato: {best_ip} (copertura {best_score} player)"
                )
            except Exception:
                # Non interrompere il flusso in caso di errore di persistenza
                pass

    def known_startup_macs(self) -> list[dict[str, Any]]:
        with self.state.lock:
            entries = list(self.state.config.startup.known_macs)
        formatted: list[dict[str, Any]] = []
        for entry in entries:
            formatted.append(
                {
                    "mac": entry.mac,
                    "ip": entry.ip,
                    "name": entry.name,
                    "last_seen": entry.last_seen,
                }
            )
        formatted.sort(key=lambda item: float(item.get("last_seen") or 0.0), reverse=True)
        return formatted

    def _update_known_startup_macs(self, raw: list[dict[str, Any]]) -> None:
        if not isinstance(raw, list):
            return
        seen_ips: set[str] = set()
        updates: dict[str, tuple[str, str]] = {}
        for entry in raw:
            ip_raw = entry.get("ip")
            if not ip_raw:
                continue
            ip = str(ip_raw).strip()
            if not ip or ip in seen_ips:
                continue
            seen_ips.add(ip)
            mac = self._resolve_mac_for_ip(ip)
            if not mac:
                continue
            status = entry.get("status") or {}
            name = (
                status.get("device_name")
                or status.get("player_name")
                or status.get("name")
                or ""
            )
            updates[mac] = (ip, name)
        if not updates:
            return
        now = time.time()
        changed = False
        with self.state.lock:
            table = self.state.config.startup.known_macs
            indexed = {entry.mac: entry for entry in table}
            for mac, (ip, name) in updates.items():
                existing = indexed.get(mac)
                if existing:
                    if existing.ip != ip or (name and existing.name != name):
                        existing.ip = ip
                        existing.name = name or existing.name
                    existing.last_seen = now
                    changed = True
                else:
                    table.append(MacEntry(mac=mac, ip=ip, name=name, last_seen=now))
                    changed = True
            if changed:
                self.state.config.startup.known_macs = table
        if not changed:
            return
        try:
            self._settings_store.save(self.state.config)
        except Exception:
            pass
        self.startupMacsUpdated.emit(self.known_startup_macs())

    def trigger_startup_magic(
        self,
        player: PlayerRecord | None = None,
        broadcast: str | None = None,
        port: int | None = None,
    ) -> None:
        mac_entries = [entry["mac"] for entry in self.known_startup_macs() if entry.get("mac")]
        if not mac_entries:
            self.logMessage.emit("Nessun MAC di startup registrato")
            return
        cfg = self.state.config.startup
        effective_broadcast = broadcast or cfg.broadcast or "255.255.255.255"
        try:
            effective_port = int(port if port is not None else cfg.port)
        except Exception:
            effective_port = 9
        report = {"sent": [], "errors": []}
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.settimeout(0.5)
                for mac in mac_entries:
                    try:
                        packet = _build_magic_packet(mac)
                    except Exception as exc:
                        report["errors"].append({"mac": mac, "error": str(exc)})
                        continue
                    try:
                        sock.sendto(packet, (effective_broadcast, effective_port))
                        report["sent"].append(mac)
                    except Exception as exc:
                        report["errors"].append({"mac": mac, "error": str(exc)})
        except Exception as exc:
            report["errors"].append({"socket": str(exc)})
        if report["sent"]:
            self.logMessage.emit(f"Wake-on-LAN inviato ({len(report['sent'])} MAC) a {effective_broadcast}:{effective_port}")
        if report["errors"]:
            self.logMessage.emit(f"Wake-on-LAN errori: {report['errors']}")

    def _resolve_mac_for_ip(self, ip: str) -> Optional[str]:
        if not ip:
            return None
        cleaned_ip = ip.strip()
        if not cleaned_ip:
            return None
        self._ping_ip(cleaned_ip)
        for cmd in self._arp_commands(cleaned_ip):
            output = self._run_command_output(cmd)
            mac = self._extract_mac(output)
            if mac:
                return mac
        return None

    def _ping_ip(self, ip: str) -> None:
        if not ip:
            return
        commands = [["ping", "-c", "1", ip]]
        if os.name == "nt":
            commands = [["ping", "-n", "1", ip]]
        for cmd in commands:
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1.0)
                return
            except Exception:
                continue

    def _arp_commands(self, ip: str) -> list[list[str]]:
        if os.name == "nt":
            return [["arp", "-a", ip]]
        return [
            ["ip", "neigh", "show", ip],
            ["arp", "-n", ip],
            ["arp", "-a", ip],
        ]

    def _run_command_output(self, cmd: list[str]) -> str:
        try:
            return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=1.0)
        except Exception:
            return ""

    def _extract_mac(self, payload: str) -> Optional[str]:
        if not payload:
            return None
        match = _MAC_PATTERN.search(payload)
        if match:
            normalized = _normalize_mac(match.group(0))
            return normalized
        return None

    # ------------------------------------------------------------------
    # Media Library & File Server
    # ------------------------------------------------------------------

    def refresh_media_library(self) -> None:
        library = self._media_library
        if not library:
            self.mediaLibraryUpdated.emit([])
            return
        if self._media_scan_future and not self._media_scan_future.done():
            self._media_scan_pending = True
            if self._media_scan_cancel:
                self._media_scan_cancel.set()
            _LOG.debug("Scan media già in corso: accodo nuova richiesta (cancel)")
            return
        self._media_scan_pending = False

        cancel_event = threading.Event()
        self._media_scan_cancel = cancel_event

        def _scan() -> dict[str, Any]:
            formatted: list[dict] = []
            cancelled = False
            try:
                for item in library.iter_items(cancel_event=cancel_event):
                    if cancel_event.is_set():
                        cancelled = True
                        break
                    formatted.append(self._format_media_item(item))
            except Exception as exc:
                _LOG.warning("Scan media fallita: %s", exc, exc_info=True)
            if cancel_event.is_set():
                cancelled = True
            return {"items": formatted, "cancelled": cancelled}

        future = self._executor.submit(_scan)
        self._media_scan_future = future

        try:
            root_desc = str(getattr(library, "_root", ""))
        except Exception:
            root_desc = ""
        if root_desc:
            self.logMessage.emit(f"Scansione media in corso: {root_desc}")

        def _on_done(fut: concurrent.futures.Future[dict[str, Any]]) -> None:
            cancelled = False
            formatted: list[dict]
            try:
                result = fut.result()
                if isinstance(result, dict):
                    formatted = list(result.get("items", []))
                    cancelled = bool(result.get("cancelled", False))
                else:
                    formatted = list(result or [])
            except Exception as exc:  # pragma: no cover
                self.logMessage.emit(f"Scan media fallita: {exc}")
                formatted = []
            finally:
                if self._media_scan_cancel is cancel_event:
                    self._media_scan_cancel = None
                self._media_scan_future = None

            if cancelled:
                msg = "Scansione media annullata"
                if root_desc:
                    msg += f" ({root_desc})"
                self.logMessage.emit(msg)
            else:
                self.mediaLibraryUpdated.emit(formatted)
                if root_desc:
                    self.logMessage.emit(f"Scansione media completata: {len(formatted)} elementi")
            if self._media_scan_pending:
                self._media_scan_pending = False
                self.refresh_media_library()

        future.add_done_callback(_on_done)

    def _format_media_item(self, item: MediaItem) -> dict:
        root = self.state.config.media.media_root
        label = item.path.name
        # duration formatting HH:MM:SS (optional)
        dur = item.duration_s
        dur_txt = None
        if isinstance(dur, (int, float)) and dur >= 0:
            total = int(dur)
            h = total // 3600
            m = (total % 3600) // 60
            s = total % 60
            if h > 0:
                dur_txt = f"{h:d}:{m:02d}:{s:02d}"
            else:
                dur_txt = f"{m:02d}:{s:02d}"
        if root and item.path.is_relative_to(root):
            rel = item.path.relative_to(root)
            size_mb = item.size / (1024 * 1024)
            if dur_txt:
                label = f"{rel.as_posix()} ({size_mb:.1f} MB, {dur_txt})"
            else:
                label = f"{rel.as_posix()} ({size_mb:.1f} MB)"
            return {"path": str(item.path), "label": label, "relative": rel.as_posix(), "duration": dur}
        size_mb = item.size / (1024 * 1024)
        base_label = f"{label} ({size_mb:.1f} MB)" if not dur_txt else f"{label} ({size_mb:.1f} MB, {dur_txt})"
        return {"path": str(item.path), "label": base_label, "relative": item.path.name, "duration": dur}

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
        self._emit_settings_snapshot()
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
        filtered = self._filter_reachable_targets(targets, f"command {command}")
        if not filtered:
            return
        self._submit_command(command, payload, filtered)

    def send_misc_command(
        self,
        command: str,
        payload: dict[str, Any] | None,
        targets: Iterable[PlayerRecord],
    ) -> None:
        filtered = self._filter_reachable_targets(targets, f"command {command}")
        if not filtered:
            return
        self._submit_command(command, payload, filtered)

    def open_vnc_viewer(self, player: PlayerRecord) -> None:
        viewer = self._resolve_vnc_viewer()
        if viewer is None:
            self.logMessage.emit(
                "VNC viewer non configurato: aggiorna settings.json (chiave remote.vnc_viewer_path) oppure colloca TigerVNC/VNC Viewer nella cartella GUI"
            )
            return
        try:
            viewer_path = Path(viewer)
            is_app_bundle = viewer_path.suffix.lower() == ".app" or (
                viewer_path.is_dir() and viewer_path.name.lower().endswith(".app")
            )
            # Costruisci host:display / host::port per TigerVNC/RealVNC.
            cfg = self.state.config.remote
            port = getattr(cfg, "vnc_port", 5900) or 5900
            try:
                port = int(port)
            except Exception:
                port = 5900
            # TigerVNC accetta sia host:display (host:0) sia host::port (host::5900). Usiamo host::port per evitare ambiguità.
            target = f"{player.ip}::{port}" if port else player.ip
            args: list[str]
            extra: list[str] = []
            try:
                extra = list(getattr(cfg, "vnc_extra_args", []))
            except Exception:
                extra = []
            password = getattr(cfg, "vnc_password", "")
            if isinstance(password, str):
                password = password.strip()
            if not password or (isinstance(password, str) and password.lower() in {"extra"}):
                password = ""
            # TigerVNC supporta -Password=FILE oppure -passwd FILE (file bin). Se abbiamo solo password in chiaro, possiamo tentare un file temporaneo.
            passwd_arg: list[str] = []
            if password:
                try:
                    # Crea file temporaneo obfuscato (TigerVNC accetta formato generato da 'vncpasswd -f').
                    # Se vncpasswd non è disponibile, usiamo -Password= che accetta password in chiaro (build recenti di TigerVNC >=1.13 su Windows/mac). Fallback silenzioso.
                    # Preferisci -Password= per semplicità su Windows.
                    if os.name == "nt":
                        passwd_arg = [f"-Password={password.strip()}"]
                    else:
                        passwd_arg = [f"-Password={password.strip()}"]
                except Exception:
                    passwd_arg = []
            common_flags = ["-Shared", "-AutoSelect=0"]
            # Deduplica preservando ordine
            def _dedupe(seq):
                seen = set()
                out = []
                for x in seq:
                    if x in seen:
                        continue
                    seen.add(x)
                    out.append(x)
                return out
            flags = _dedupe(common_flags + extra + passwd_arg)
            if sys.platform == "darwin" and is_app_bundle:
                # open --args <viewer> <flags> target
                args = ["open", "-a", str(viewer_path), "--args", *flags, target]
            else:
                args = [str(viewer_path), *flags, target]
            # Log command sanitizzando eventuale password
            def _mask(a: str) -> str:
                if isinstance(a, str) and a.startswith("-Password="):
                    return "-Password=****"
                return a
            safe_cmd = " ".join(_mask(x) for x in args)
            self.logMessage.emit(f"Avvio VNC: {safe_cmd}")
            launch_kwargs = {}
            try:
                launch_kwargs["cwd"] = str(viewer_path.parent)
            except Exception:
                pass
            if sys.platform != "win32":
                launch_kwargs["close_fds"] = True
            subprocess.Popen(args, **launch_kwargs)
        except Exception as exc:  # pragma: no cover
            self.logMessage.emit(f"Avvio VNC viewer fallito: {exc}")

    def _resolve_vnc_viewer(self) -> Path | None:
        cfg = self.state.config.remote
        candidate = self._coerce_path(cfg.vnc_viewer_path)
        if candidate and candidate.exists():
            return candidate
        return self._default_vnc_viewer_path()

    def _default_vnc_viewer_path(self) -> Path | None:
        gui_root = Path(__file__).resolve().parents[1]
        repo_root = Path(__file__).resolve().parents[2]

        candidates: list[Path] = []
        if sys.platform == "darwin":
            candidates.extend(
                [
                    gui_root / "TigerVNC viewer 1.15.0.app",
                    gui_root / "TigerVNC Viewer 1.15.0.app",
                    gui_root / "TigerVNC Viewer.app",
                    repo_root / "TigerVNC viewer 1.15.0.app",
                    repo_root / "TigerVNC Viewer 1.15.0.app",
                    repo_root / "TigerVNC Viewer.app",
                    Path("/Applications/TigerVNC Viewer 1.15.0.app"),
                    Path("/Applications/TigerVNC viewer 1.15.0.app"),
                    Path("/Applications/TigerVNC Viewer.app"),
                ]
            )
        elif os.name == "nt":
            candidates.extend(
                [
                    gui_root / "vncviewer64-1.15.0.exe",
                    repo_root / "vncviewer64-1.15.0.exe",
                ]
            )
        else:
            candidates.extend(
                [
                    gui_root / "vncviewer",
                    repo_root / "vncviewer",
                ]
            )

        seen: set[Path] = set()
        for cand in candidates:
            try:
                if cand in seen:
                    continue
                seen.add(cand)
                if cand.exists():
                    return cand
            except OSError:
                continue
        return None

    @staticmethod
    def _coerce_path(candidate: Any) -> Path | None:
        if isinstance(candidate, Path):
            return candidate
        if not candidate:
            return None
        try:
            return Path(str(candidate))
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Helpers: target filtering (avoid hammering offline/red players)
    # ------------------------------------------------------------------
    def _player_label(self, player: PlayerRecord) -> str:
        try:
            name = (getattr(player, "name", "") or "").strip()
        except Exception:
            name = ""
        return name or getattr(player, "ip", "") or "?"

    def _is_offline(self, player: PlayerRecord) -> bool:
        try:
            return str(getattr(player, "state", "")).lower() == "offline"
        except Exception:
            return False

    def _filter_reachable_targets(self, targets: Iterable[PlayerRecord], action: str) -> list[PlayerRecord]:
        selected = []
        skipped = []
        for p in list(targets):
            if self._is_offline(p):
                skipped.append(p)
            else:
                selected.append(p)
        if skipped:
            labels = ", ".join(self._player_label(p) for p in skipped)
            self.logMessage.emit(f"[SKIP] {action}: {len(skipped)} player offline (rosso) -> {labels}")
        return selected

    def _skip_if_offline(self, player: PlayerRecord, action: str, *, log: bool = True) -> bool:
        if not self._is_offline(player):
            return False
        if log:
            self.logMessage.emit(f"[SKIP] {action}: {self._player_label(player)} offline (rosso)")
        return True

    def set_autoplay(
        self,
        *,
        enabled: bool,
        targets: Iterable[PlayerRecord],
        restart: bool = True,
        delay: float | None = None,
    ) -> None:
        selected = self._filter_reachable_targets(targets, "autoplay")
        if not selected:
            self.logMessage.emit("Nessun player selezionato per autoplay")
            return
        for player in selected:
            self._executor.submit(self._invoke_autoplay_toggle, player, enabled, restart, delay)

    def refresh_autoplay_status(self, player: PlayerRecord) -> None:
        if self._skip_if_offline(player, "autoplay status", log=False):
            return
        self._executor.submit(self._invoke_autoplay_status, player)

    def refresh_framework_status(self, player: PlayerRecord) -> None:
        if self._skip_if_offline(player, "framework status", log=False):
            return
        self._executor.submit(self._invoke_framework_status, player)

    def refresh_status(self, player: PlayerRecord) -> None:
        """Fetch general /status from a player."""
        if self._skip_if_offline(player, "status", log=False):
            return
        self._executor.submit(self._invoke_status, player)

    def refresh_device_media(self, player: PlayerRecord) -> None:
        """Fetch /media listing from a player."""
        if self._skip_if_offline(player, "device media", log=False):
            return
        self._executor.submit(self._invoke_device_media, player)

    def refresh_playlist_status(self, player: PlayerRecord) -> None:
        """Fetch /playlist/status from a player."""
        if self._skip_if_offline(player, "playlist status", log=False):
            return
        self._executor.submit(self._invoke_playlist_status, player)
        # Se backend OFF attivo, prova anche /off/playlist per dettagli nativi OFF
        self._executor.submit(self._invoke_off_playlist_status, player)

    # ------------------------------------------------------------------
    # Live log streaming (CVLC)
    # ------------------------------------------------------------------
    def start_log_live(self, player: PlayerRecord, *, lines: int = 50) -> None:
        if self._skip_if_offline(player, "log live"):
            return
        # Stop any previous
        self.stop_log_live()
        # Enable/point UDP framework logs to this GUI before starting SSE log stream (for CVLC)
        try:
            host_ip = self._host_ip()
        except Exception:
            host_ip = "127.0.0.1"
        self._executor.submit(self._invoke_log_udp_settings, player, True, host_ip, 9999)
        self._log_worker_stop = False
        self._log_worker_future = self._executor.submit(self._log_live_worker, player, int(lines))

    def stop_log_live(self) -> None:
        self._log_worker_stop = True
        fut = self._log_worker_future
        self._log_worker_future = None
        # best-effort cancel
        try:
            if fut and not fut.done():
                fut.cancel()
        except Exception:
            pass
        # Best-effort disable UDP logs on the last used player (no-op if unreachable)
        try:
            # We don't track the last player explicitly; this disables on primary if set
            primary = next(iter(self.player_registry.current_players()), None)
            if primary:
                self._executor.submit(self._invoke_log_udp_settings, primary, False, None, None)
        except Exception:
            pass
        self.logLiveStatusChanged.emit(False)

    def _log_live_worker(self, player: PlayerRecord, lines: int) -> None:
        if self._skip_if_offline(player, "log live", log=False):
            self.logLiveStatusChanged.emit(False)
            return
        client = self._client_for(player)
        base = client.base_url.rstrip("/")
        # Determina endpoint log in base al framework corrente
        url = None
        framework: str | None = None
        player_name = (player.name or "").strip()
        try:
            fw = client.request("get", "/framework")
            current = (fw or {}).get("current") if isinstance(fw, dict) else None
            if current:
                framework = str(current).lower()
            if framework == "off":
                url = f"{base}/logs/off/stream"
            elif framework in {"cvlc", "vlc"}:
                url = f"{base}/logs/cvlc/stream"
            else:
                # Tentativi: prima CVLC poi OFF
                url = None
        except Exception:
            url = None
            framework = None
        # Fallback se framework non determinato
        candidates = []
        if url:
            candidates.append(url)
        candidates.extend([f"{base}/logs/cvlc/stream", f"{base}/logs/off/stream"])  # dedup non essenziale
        resp = None
        last_exc: Exception | None = None
        params = {"lines": max(0, min(lines, 2000)), "format": "sse"}
        for u in candidates:
            try:
                resp = requests.get(u, params=params, stream=True, timeout=10)
                resp.raise_for_status()
                url = u
                break
            except Exception as exc:
                last_exc = exc
                resp = None
                continue
        if resp is None:
            self.logMessage.emit(f"Log live non disponibile su {player.ip}: {last_exc}")
            self.logLiveStatusChanged.emit(False)
            return
        self.logLiveStatusChanged.emit(True)
        tag = framework.upper() if framework else "LOG"
        start_suffix = f" ({player_name})" if player_name and player_name != player.ip else ""
        self.logMessage.emit(f"[{player.ip}]{start_suffix} Log live avviato ({tag}, tail {params['lines']} righe)")
        try:
            for raw in resp.iter_lines(decode_unicode=True):
                if self._log_worker_stop:
                    break
                if raw is None:
                    continue
                line = raw.strip()
                if not line:
                    continue
                # SSE format: 'data: ...'
                if line.startswith("data:"):
                    line = line[5:].lstrip()
                if player_name and player_name != player.ip:
                    formatted = f"[{player.ip}] {player_name}: {line}"
                else:
                    formatted = f"[{player.ip}] {line}"
                self.logMessage.emit(formatted)
        except Exception:
            pass
        finally:
            try:
                resp.close()
            except Exception:
                pass
            end_suffix = f" ({player_name})" if player_name and player_name != player.ip else ""
            self.logMessage.emit(f"[{player.ip}]{end_suffix} Log live terminato")
            self.logLiveStatusChanged.emit(False)

    def _invoke_log_udp_settings(self, player: PlayerRecord, enabled: bool, host: str | None, port: int | None) -> None:
        """Configure headless to send framework logs over UDP to the GUI.
        Falls back silently if /settings/reload doesn't support these keys.
        """
        if self._skip_if_offline(player, "log udp settings", log=False):
            return
        client = self._client_for(player)
        payload: dict[str, Any] = {"log_udp_enabled": bool(enabled)}
        if enabled and host and port:
            payload.update({"log_udp_host": str(host), "log_udp_port": int(port)})
        try:
            client.request("post", "/settings/reload", json=payload, timeout=5)
        except Exception:
            # Non-fatal; ignore failures here
            pass

    def upload_media(self, media_path: Path, targets: Iterable[PlayerRecord]) -> None:
        target_list = self._filter_reachable_targets(targets, f"upload {media_path.name}")
        if not target_list:
            self.logMessage.emit("Nessun player selezionato per upload")
            return
        root = self.state.config.media.media_root
        rel_path_str: str | None = None
        media_url: str | None = None
        # Prova percorso relativo solo se root configurata
        if root:
            try:
                rel_path = media_path.relative_to(root)
                rel_path_str = rel_path.as_posix()
            except ValueError:
                rel_path_str = None
        # Se file server attivo e abbiamo relativo, usa pull URL; altrimenti si andrà in push
        if self._file_server and rel_path_str:
            # Sanitize: rimuovi caratteri di controllo e applica URL encoding per ciascun segmento
            try:
                cleaned = re.sub(r"[\x00-\x1f\x7f]", "", rel_path_str)
            except Exception:
                cleaned = rel_path_str
            try:
                # Evita backslash e normalizza
                cleaned = cleaned.replace("\\", "/")
                original_cleaned = cleaned
                safe_segments = [quote(seg, safe="@:!$&'()*+,;=-._~") for seg in cleaned.split("/") if seg]
                sanitized_rel = "/".join(safe_segments)
            except Exception:
                sanitized_rel = cleaned
            # Determina host e schema: override via env o settings, altrimenti best-IP
            scheme = os.environ.get("MEDIA_SERVER_SCHEME") or getattr(self.state.config.media, "server_scheme", "http") or "http"
            host_override_env = os.environ.get("MEDIA_SERVER_HOST")
            host_override_cfg = getattr(self.state.config.media, "server_host_override", None)
            host = (host_override_env or host_override_cfg or self._host_ip(target_list)).strip()
            media_url = f"{scheme}://{host}:{self.state.config.media.server_port}/{sanitized_rel}"
            if sanitized_rel != rel_path_str:
                self.logMessage.emit(f"Path media normalizzato per URL: {rel_path_str} -> {sanitized_rel}")
            self.logMessage.emit(
                f"Richiesta download asset (pull) per {Path(rel_path_str).name} ({rel_path_str}) verso {len(target_list)} player"
            )
        else:
            # Push diretto: usa solo il nome file
            self.logMessage.emit(
                f"Upload diretto (push) per {media_path.name} verso {len(target_list)} player"
            )
        target_name = (rel_path_str or media_path.name)
        # Increment counters: total and in-flight
        self._uploads_add(len(target_list))
        for player in target_list:
            self._executor.submit(self._invoke_upload, player, media_url, target_name, media_path)

    # ------------------------------------------------------------------
    # Media clone orchestration
    # ------------------------------------------------------------------

    def _run_media_clone_job(self, source: PlayerRecord) -> dict[str, Any]:
        source_ip = source.ip
        source_label = (source.name or source_ip).strip() if getattr(source, "name", None) else source_ip
        label = f"{source_label} ({source_ip})" if source_label and source_label != source_ip else source_ip

        def _emit_progress(pct: float, text: str) -> None:
            try:
                self.mediaCloneProgress.emit(max(0.0, min(1.0, float(pct))), text)
            except Exception:
                pass

        def _log(message: str) -> None:
            formatted = f"[Clone] {message}"
            self.logMessage.emit(formatted)
            self.mediaCloneLog.emit(formatted)

        root = self.state.config.media.media_root
        if not root or not root.exists():
            raise RuntimeError("Cartella media locale non configurata o non accessibile")
        if not self._file_server:
            raise RuntimeError("File server media non attivo: configura una cartella media valida")

        snapshot = self.player_registry.current_players()
        targets = [p for p in snapshot if p.ip != source_ip]
        online_targets = [p for p in targets if str(getattr(p, "state", "online")).lower() != "offline"]
        if not online_targets:
            raise RuntimeError("Nessun altro player online disponibile per la clonazione")
        skipped = len(targets) - len(online_targets)
        _log(f"Sorgente: {label} → {len(online_targets)} target")
        if skipped > 0:
            _log(f"{skipped} player ignorati perché offline")

        client = self._client_for(source)
        _emit_progress(0.05, "Analisi inventario media sorgente")
        media_listing = client.request("get", "/media", timeout=30)
        files = media_listing.get("files", []) if isinstance(media_listing, dict) else []
        source_manifest, prefix = self._build_media_manifest(files)
        _log(f"Inventario sorgente: {len(source_manifest)} file")

        playlist_status = client.request("get", "/playlist/status", timeout=10)
        raw_items = playlist_status.get("items") if isinstance(playlist_status, dict) else []
        playlist_loop = bool(playlist_status.get("loop", True)) if isinstance(playlist_status, dict) else True
        playlist_items = self._normalize_playlist_items(raw_items, prefix)
        _log(
            f"Playlist sorgente: {len(playlist_items)} elementi" + (" (loop)" if playlist_loop else "")
        )

        safe_ip = source_ip.replace(":", "-").replace("/", "-").replace(".", "-")
        archive_rel = Path("_sync_jobs") / f"clone_{safe_ip}_{int(time.time())}.zip"
        archive_path = (root / archive_rel)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        cleanup_path: Path | None = archive_path
        try:
            _emit_progress(0.15, "Scarico archivio media dalla sorgente")
            client.download_media_archive(archive_path, timeout=600)
            try:
                size_mb = archive_path.stat().st_size / (1024 * 1024)
                _log(f"Archivio scaricato ({size_mb:.1f} MB) → {archive_rel.as_posix()}")
            except Exception:
                _log(f"Archivio scaricato → {archive_rel.as_posix()}")

            media_url, _ = self._build_media_pull_url(archive_rel, online_targets)
            _log(f"URL distribuzione: {media_url}")

            successes: list[PlayerRecord] = []
            failures: dict[str, str] = {}
            total = len(online_targets)
            for idx, target in enumerate(online_targets, start=1):
                phase = 0.2 + 0.5 * ((idx - 1) / max(1, total))
                _emit_progress(phase, f"Sync {idx}/{total} su {target.ip}")
                target_client = self._client_for(target)
                try:
                    target_client.sync_media(media_url, cleanup=True, timeout=600)
                    successes.append(target)
                    _log(f"{target.ip}: sync completato")
                    if source_manifest:
                        target_manifest = self._fetch_manifest(target_client)
                        if self._manifests_match(target_manifest, source_manifest):
                            _log(f"{target.ip}: inventario allineato ({len(source_manifest)} file)")
                        else:
                            _log(f"{target.ip}: attenzione, inventario diverso dopo il sync")
                except Exception as exc:
                    reason = str(exc)
                    failures[target.ip] = reason
                    _log(f"{target.ip}: sync fallito ({reason})")

            if not successes:
                raise RuntimeError("Sincronizzazione fallita su tutti i target")

            if playlist_items:
                _emit_progress(0.8, "Invio playlist ai target clonati")
                for target in successes:
                    try:
                        payload = {"items": playlist_items, "loop": playlist_loop}
                        self._client_for(target).request("post", "/playlist/apply", json=payload, timeout=40)
                        _log(f"{target.ip}: playlist applicata ({len(playlist_items)} tracce)")
                    except Exception as exc:
                        failures.setdefault(target.ip, f"Playlist: {exc}")
                        _log(f"{target.ip}: playlist non applicata ({exc})")
            else:
                _log("Playlist sorgente vuota: nessun push richiesto")

            _emit_progress(0.98, "Clonazione completata")
            summary = f"Clone completato su {len(successes)}/{total} player"
            if failures:
                summary += f" • errori: {len(failures)}"
            _log(summary)
            return {"ok": True, "summary": summary, "success": [p.ip for p in successes], "failed": failures}
        finally:
            try:
                if cleanup_path and cleanup_path.exists():
                    cleanup_path.unlink()
                    parent = cleanup_path.parent
                    if parent.exists() and not any(parent.iterdir()):
                        parent.rmdir()
            except Exception:
                pass

    def _build_media_pull_url(self, relative_path: Path, targets: Iterable[PlayerRecord]) -> tuple[str, str]:
        rel = relative_path.as_posix().lstrip("./")
        try:
            cleaned = re.sub(r"[\x00-\x1f\x7f]", "", rel)
        except Exception:
            cleaned = rel
        cleaned = cleaned.replace("\\", "/")
        try:
            segments = [quote(seg, safe="@:!$&'()*+,;=-._~") for seg in cleaned.split("/") if seg]
            sanitized = "/".join(segments)
        except Exception:
            sanitized = cleaned
        scheme = os.environ.get("MEDIA_SERVER_SCHEME") or getattr(self.state.config.media, "server_scheme", "http") or "http"
        host_override_env = os.environ.get("MEDIA_SERVER_HOST")
        host_override_cfg = getattr(self.state.config.media, "server_host_override", None)
        host = (host_override_env or host_override_cfg or self._host_ip(targets)).strip()
        port = int(self.state.config.media.server_port)
        return f"{scheme}://{host}:{port}/{sanitized}", sanitized

    def _build_media_manifest(self, files: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], Path | None]:
        manifest: dict[str, dict[str, Any]] = {}
        paths: list[str] = []
        for entry in files or []:
            path_str = entry.get("path")
            if isinstance(path_str, str) and path_str:
                paths.append(path_str)
        prefix: Path | None = None
        if paths:
            try:
                prefix = Path(os.path.commonpath(paths))
            except Exception:
                try:
                    prefix = Path(paths[0]).parent
                except Exception:
                    prefix = None
        for entry in files or []:
            path_str = entry.get("path")
            rel: str | None = None
            if prefix and isinstance(path_str, str) and path_str:
                try:
                    rel = Path(path_str).relative_to(prefix).as_posix()
                except Exception:
                    rel = None
            if not rel:
                name = entry.get("name")
                if isinstance(name, str) and name:
                    rel = name
                elif isinstance(path_str, str) and path_str:
                    rel = Path(path_str).name
            if not rel:
                continue
            manifest[str(rel)] = {
                "size": entry.get("size"),
                "modified": entry.get("modified"),
            }
        return manifest, prefix

    def _normalize_playlist_items(self, items: Any, prefix: Path | None) -> list[str]:
        if not isinstance(items, list):
            return []
        normalized: list[str] = []
        for raw in items:
            try:
                value = str(raw).strip()
            except Exception:
                continue
            if not value:
                continue
            rel = None
            if prefix:
                try:
                    rel = Path(value).relative_to(prefix).as_posix()
                except Exception:
                    rel = None
            if rel:
                normalized.append(rel)
            else:
                try:
                    p = Path(value)
                    if p.is_absolute():
                        normalized.append(p.name or value)
                    else:
                        normalized.append(value)
                except Exception:
                    normalized.append(value)
        # Deduplica mantenendo l'ordine originale
        seen: set[str] = set()
        ordered: list[str] = []
        for entry in normalized:
            if entry in seen:
                continue
            seen.add(entry)
            ordered.append(entry)
        return ordered

    def _fetch_manifest(self, client: ApiClient) -> dict[str, dict[str, Any]]:
        response = client.request("get", "/media", timeout=30)
        files = response.get("files", []) if isinstance(response, dict) else []
        manifest, _ = self._build_media_manifest(files)
        return manifest

    def _manifests_match(self, target_manifest: dict[str, dict[str, Any]], expected: dict[str, dict[str, Any]]) -> bool:
        if not expected:
            return not target_manifest
        if target_manifest.keys() != expected.keys():
            return False
        for key, meta in expected.items():
            target_meta = target_manifest.get(key) or {}
            try:
                src_size = int(meta.get("size") or 0)
                dst_size = int(target_meta.get("size") or 0)
            except Exception:
                return False
            if src_size != dst_size:
                return False
        return True

    # ------------------------------------------------------------------
    # Playlist orchestration
    # ------------------------------------------------------------------
    def push_playlist(self, items: list[str], targets: Iterable[PlayerRecord], *, loop: bool = True, clear_before: bool = False) -> None:
        """Chain clear (optional), uploads, and apply in background; UI stays responsive."""
        targets_list = self._filter_reachable_targets(targets, "push playlist")
        if not targets_list:
            self.logMessage.emit("Nessun player selezionato per push playlist")
            return
        if not items:
            self.logMessage.emit("Playlist vuota: nulla da inviare")
            return
        self._executor.submit(self._perform_push_playlist, items, targets_list, loop, clear_before)

    def apply_playlist_only(self, items: list[str], targets: Iterable[PlayerRecord], *, loop: bool = True) -> None:
        targets_list = self._filter_reachable_targets(targets, "apply playlist")
        if not targets_list:
            self.logMessage.emit("Nessun player selezionato per applicare la playlist")
            return
        if not items:
            self.logMessage.emit("Playlist vuota: nulla da applicare")
            return
        self.playlistPhaseChanged.emit("applying")
        for player in targets_list:
            self._executor.submit(self._invoke_apply_playlist, player, items, loop)

    def _perform_push_playlist(self, items: list[str], targets_list: list[PlayerRecord], loop: bool, clear_before: bool) -> None:
        if not targets_list:
            self.logMessage.emit("Nessun player online per push playlist")
            return
        root = self.state.config.media.media_root
        # Phase 1: optional clear with wait
        if clear_before:
            self.playlistPhaseChanged.emit("clearing")
            futs: list[concurrent.futures.Future[bool]] = []
            for player in targets_list:
                futs.append(self._executor.submit(self._invoke_media_prune, player, items))
            ok_all = True
            for f in concurrent.futures.as_completed(futs):
                try:
                    ok = f.result()
                except Exception:
                    ok = False
                ok_all = ok_all and ok
            if not ok_all:
                self.logMessage.emit("Alcuni device non hanno completato lo svuotamento media")
        # Phase 2: uploads
        self.playlistPhaseChanged.emit("uploading")
        for rel in items:
            try:
                p = Path(rel)
                local_path: Path
                if root and not p.is_absolute():
                    local_path = (root / rel)
                else:
                    local_path = p
                self.upload_media(local_path, targets_list)
            except Exception as exc:
                self.logMessage.emit(f"Errore preparando upload per {rel}: {exc}")
        # Phase 3: apply
        self.playlistPhaseChanged.emit("applying")
        for player in targets_list:
            self._executor.submit(self._invoke_apply_playlist, player, items, loop)

    def _invoke_media_prune(self, player: PlayerRecord, items: list[str]) -> bool:
        client = self._client_for(player)
        try:
            payload = {"items": items}
            resp = client.request("post", "/media/prune_to_playlist", json=payload, timeout=120)
            self.commandCompleted.emit(player.ip, resp if isinstance(resp, dict) else {"ok": True})
            return bool((isinstance(resp, dict) and resp.get("ok", True)) or resp)
        except Exception as exc:
            self.logMessage.emit(f"Prune media su {player.ip} fallito: {exc}")
            return False

    def _invoke_apply_playlist(self, player: PlayerRecord, items: list[str], loop: bool) -> None:
        client = self._client_for(player)
        try:
            payload: dict[str, Any] = {"items": items, "loop": bool(loop)}
            response = client.request("post", "/playlist/apply", json=payload, timeout=30)
            self.commandCompleted.emit(player.ip, response)
        except requests.HTTPError as exc:  # pragma: no cover
            status = getattr(exc.response, "status_code", None)
            if status == 404:
                self.commandCompleted.emit(player.ip, {"ok": False, "error": "Endpoint /playlist/apply non supportato (404)"})
            else:
                self.logMessage.emit(f"Apply playlist su {player.ip} fallito: {exc}")
        except Exception as exc:  # pragma: no cover
            self.logMessage.emit(f"Apply playlist su {player.ip} fallito: {exc}")

    def _submit_command(
        self,
        command: str,
        payload: dict[str, Any] | None,
        targets: Iterable[PlayerRecord],
    ) -> None:
        for player in targets:
            if self._skip_if_offline(player, f"command {command}"):
                continue
            cloned = payload.copy() if payload else None
            self._executor.submit(self._invoke_command, player, command, cloned)

    def _invoke_command(self, player: PlayerRecord, command: str, payload: dict | None) -> None:
        client = self._client_for(player)
        try:
            response = self._execute_command(client, command, payload or {})
            if isinstance(response, dict):
                response.setdefault("_command", command)
            self.commandCompleted.emit(player.ip, response)
        except requests.HTTPError as exc:  # pragma: no cover
            # Graceful fallbacks for optional endpoints on older players
            status = getattr(exc.response, "status_code", None)
            if status == 404 and command in {"faststart_prepare", "faststart_go", "overlay_fade_at", "play_at", "test_on", "test_off"}:
                self.commandCompleted.emit(player.ip, {"ok": False, "error": f"{command} non supportato sul device (404)", "_command": command})
            else:
                self.logMessage.emit(f"Command {command} su {player.ip} fallito: {exc}")
        except Exception as exc:  # pragma: no cover
            self.logMessage.emit(f"Command {command} su {player.ip} fallito: {exc}")

    def _invoke_upload(self, player: PlayerRecord, media_url: str | None, target: str, local_path: Path | None = None) -> None:
        if self._skip_if_offline(player, "upload", log=False):
            # Keep counters balanced if task was queued before the player turned red
            self._uploads_mark_done(1)
            self._uploads_dec(1)
            return
        if self._skip_if_offline(player, "off playlist status", log=False):
            return
        client = self._client_for(player)
        target_name = Path(target).name if target else None

        def _describe_http_error(error: requests.HTTPError) -> str:
            status = getattr(error.response, "status_code", None)
            detail = None
            if error.response is not None:
                try:
                    payload = error.response.json()
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    detail = payload.get("error") or payload.get("message")
                elif isinstance(payload, str):
                    detail = payload
                elif not payload and error.response.text:
                    detail = error.response.text.strip()
            parts: list[str] = []
            if status is not None:
                parts.append(f"HTTP {status}")
            if detail:
                parts.append(str(detail))
            return " - ".join(parts) if parts else str(error)

        # Se abbiamo una URL da cui il device può scaricare, prima verifica reachability lato device,
        # poi prova pull con retry prima del fallback
        if media_url:
            try:
                probe = client.probe_url(media_url, timeout=5)
                if isinstance(probe, dict) and not probe.get("ok", False):
                    self.logMessage.emit(f"Probe URL fallito su {player.ip}: skip pull → push diretto")
                    media_url = None
            except Exception as exc:
                # Se la probe fallisce per errori di rete, non bloccare: continueremo con pull+retry
                self.logMessage.emit(f"Probe URL su {player.ip} non riuscita (continuo con pull): {exc}")
            max_attempts_env = os.environ.get("UPLOAD_PULL_RETRIES")
            try:
                max_attempts = int(max(1, int(max_attempts_env))) if max_attempts_env else 2
            except Exception:
                max_attempts = 2
            backoff = 1.5  # seconds base
            attempt = 0
            while media_url and attempt < max_attempts:
                attempt += 1
                try:
                    response = client.upload_media(media_url, target)
                    if attempt > 1:
                        self.logMessage.emit(f"Upload pull riuscito su {player.ip} (tentativo {attempt}/{max_attempts})")
                    self.commandCompleted.emit(player.ip, response)
                    self._uploads_mark_done(1)
                    self._uploads_dec(1)
                    return
                except requests.HTTPError as exc:
                    reason = _describe_http_error(exc)
                    if self._should_attempt_permission_fix(exc, reason) and self._maybe_fix_permissions(player, reason):
                        continue
                    fatal = getattr(exc.response, "status_code", None) in {400, 403, 404}
                    self.logMessage.emit(
                        f"Upload pull fallito su {player.ip} (tentativo {attempt}/{max_attempts}): {reason}" + (" (non ritento)" if fatal else "")
                    )
                    if fatal:
                        break  # errori logici: non serve ritentare
                except Exception as exc:
                    self.logMessage.emit(f"Upload pull eccezione su {player.ip} (tentativo {attempt}/{max_attempts}): {exc}")
                # Attendi backoff se ci sono tentativi residui
                if attempt < max_attempts:
                    try:
                        time.sleep(backoff * attempt)
                    except Exception:
                        pass
            self.logMessage.emit(f"Procedo con fallback push su {player.ip} dopo tentativi pull esauriti")
        # Fallback o percorso primario: push upload
        if local_path and local_path.exists():
            push_attempts = 2  # single retry on transient failure
            for p_try in range(1, push_attempts + 1):
                try:
                    resp2 = client.upload_media_push(local_path, target_name or local_path.name)
                    if p_try > 1:
                        self.logMessage.emit(f"Upload push riuscito su {player.ip} (retry {p_try}/{push_attempts})")
                    self.commandCompleted.emit(player.ip, resp2)
                    self._uploads_mark_done(1)
                    self._uploads_dec(1)
                    return
                except requests.HTTPError as exc2:
                    reason = _describe_http_error(exc2)
                    if self._should_attempt_permission_fix(exc2, reason) and self._maybe_fix_permissions(player, reason):
                        continue
                    fatal = getattr(exc2.response, "status_code", None) in {400, 403, 404}
                    self.logMessage.emit(
                        f"Upload push fallito su {player.ip} (tentativo {p_try}/{push_attempts}): {reason}" + (" (non ritento)" if fatal else "")
                    )
                    if fatal:
                        break
                except Exception as exc2:
                    self.logMessage.emit(f"Upload push eccezione su {player.ip} (tentativo {p_try}/{push_attempts}): {exc2}")
                if p_try < push_attempts:
                    try:
                        time.sleep(1.0 * p_try)
                    except Exception:
                        pass
        else:
            self.logMessage.emit(f"Upload verso {player.ip} fallito: file locale non trovato")
        # Failure path: mark done and decrement
        self._uploads_mark_done(1)
        self._uploads_dec(1)

    def _should_attempt_permission_fix(self, error: requests.HTTPError, message: str | None) -> bool:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        if status != 403:
            return False
        blob = (message or "").lower()
        if response is not None:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                blob += f" {(payload.get('error') or payload.get('message') or '')}".lower()
            else:
                try:
                    blob += f" {response.text}".lower()
                except Exception:
                    pass
        return "fix_permissions" in blob or "permesso negato" in blob

    def _maybe_fix_permissions(self, player: PlayerRecord, reason: str | None = None) -> bool:
        ip = player.ip
        if ip in self._permission_fix_attempted:
            return False
        self._permission_fix_attempted.add(ip)
        hint = reason or "HTTP 403"
        self.logMessage.emit(f"HTTP 403 su {ip}: provo automaticamente /maintenance/fix_permissions ({hint})")
        try:
            client = self._client_for(player)
            client.request("post", "/maintenance/fix_permissions", timeout=60)
            self.logMessage.emit(f"Fix permissions completato su {ip}, ritento l'upload")
            return True
        except Exception as exc:
            self.logMessage.emit(f"Fix permissions automatico fallito su {ip}: {exc}")
            return False

    def _invoke_autoplay_toggle(
        self,
        player: PlayerRecord,
        enabled: bool,
        restart: bool,
        delay: float | None,
    ) -> None:
        if self._skip_if_offline(player, "autoplay", log=False):
            return
        client = self._client_for(player)
        try:
            response = client.set_autoplay(enabled=enabled, restart=restart, delay=delay)
        except Exception as exc:
            self.logMessage.emit(f"Autoplay su {player.ip} fallito: {exc}")
            return
        self.commandCompleted.emit(player.ip, response)
        self.autoplayStatusReceived.emit(player.ip, response)

    # --------------------
    # Internal helpers
    # --------------------
    def _uploads_dec(self, n: int = 1) -> None:
        try:
            with self._uploads_lock:
                self._uploads_in_flight = max(0, self._uploads_in_flight - int(max(1, n)))
                # Emit both activity and progress
                self.uploadActivityChanged.emit(int(self._uploads_in_flight))
                self.uploadProgressChanged.emit(int(self._uploads_done), int(self._uploads_total), int(self._uploads_in_flight))
        except Exception:
            pass

    def _uploads_add(self, n: int) -> None:
        try:
            n = int(max(0, n))
        except Exception:
            n = 0
        if n <= 0:
            return
        try:
            with self._uploads_lock:
                self._uploads_total += n
                self._uploads_in_flight += n
                self.uploadActivityChanged.emit(int(self._uploads_in_flight))
                self.uploadProgressChanged.emit(int(self._uploads_done), int(self._uploads_total), int(self._uploads_in_flight))
        except Exception:
            pass

    def _uploads_mark_done(self, n: int = 1) -> None:
        try:
            with self._uploads_lock:
                self._uploads_done += int(max(1, n))
                # If we reached total and no in-flight, reset counters for next batch
                if self._uploads_in_flight == 0 and self._uploads_done >= self._uploads_total:
                    self._uploads_total = self._uploads_done
                # Always emit progress
                self.uploadProgressChanged.emit(int(self._uploads_done), int(self._uploads_total), int(self._uploads_in_flight))
        except Exception:
            pass

    def _invoke_autoplay_status(self, player: PlayerRecord) -> None:
        if self._skip_if_offline(player, "autoplay status", log=False):
            return
        client = self._client_for(player)
        try:
            response = client.get_autoplay()
        except Exception as exc:
            payload = {"ok": False, "error": str(exc)}
        else:
            payload = response
        self.autoplayStatusReceived.emit(player.ip, payload)

    def _invoke_framework_status(self, player: PlayerRecord) -> None:
        if self._skip_if_offline(player, "framework status", log=False):
            return
        client = self._client_for(player)
        try:
            response = client.request("get", "/framework")
        except Exception as exc:
            payload: dict[str, Any] = {"ok": False, "error": str(exc)}
        else:
            payload = response if isinstance(response, dict) else {"ok": False, "error": "Risposta non valida"}
        self.frameworkStatusReceived.emit(player.ip, payload)

    def _invoke_status(self, player: PlayerRecord) -> None:
        if self._skip_if_offline(player, "status", log=False):
            return
        client = self._client_for(player)
        try:
            response = client.get_status()
        except Exception as exc:
            payload: dict[str, Any] = {"ok": False, "error": str(exc)}
        else:
            payload = response if isinstance(response, dict) else {"ok": False, "error": "Risposta non valida"}
        try:
            fw_name = None
            if isinstance(payload, dict):
                fw_name = payload.get("framework")
            if fw_name is not None:
                player.framework = str(fw_name)
        except Exception:
            pass
        # Inject timing skew from local UDP sync (if available)
        try:
            sync = self._time_sync.get_latest(player.ip)
        except Exception:
            sync = None
        if isinstance(sync, dict) and "offset_ns" in sync:
            try:
                ms = int(int(sync["offset_ns"]) / 1_000_000)
                timing = payload.get("timing") if isinstance(payload, dict) else None
                if not isinstance(timing, dict):
                    timing = {}
                timing.update({"ok": abs(ms) <= 50, "skew_ms": ms, "method": str(sync.get("method", "udp"))})
                payload["timing"] = timing
            except Exception:
                pass
        else:
            # No measurement yet: indicate syncing in progress when using UDP method
            try:
                timing = payload.get("timing") if isinstance(payload, dict) else None
                if not isinstance(timing, dict):
                    timing = {}
                timing.update({"method": "udp", "syncing": True})
                payload["timing"] = timing
            except Exception:
                pass
        self.statusReceived.emit(player.ip, payload)
        # Cache last known /status payload for auxiliary requests
        try:
            with self._status_cache_lock:
                if isinstance(payload, dict):
                    self._status_cache[player.ip] = payload
                else:
                    self._status_cache.pop(player.ip, None)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Time sync controls
    # ------------------------------------------------------------------
    def restart_time_sync(self, players: Iterable[PlayerRecord]) -> None:
        for p in list(players):
            try:
                self._time_sync.restart_sync(p.ip)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Player events -> time sync workers
    # ------------------------------------------------------------------
    def _on_player_updated(self, record: PlayerRecord) -> None:
        try:
            state = str(getattr(record, "state", "unknown")).lower()
            if state == "online":
                # Default headless UDP port for time sync
                self._time_sync.ensure_sync(record.ip, 7777)
        except Exception:
            pass

    def _on_player_removed(self, ip: str) -> None:
        try:
            self._time_sync.stop_sync(ip)
        except Exception:
            pass

    def _invoke_device_media(self, player: PlayerRecord) -> None:
        if self._skip_if_offline(player, "device media", log=False):
            return
        client = self._client_for(player)
        try:
            response = client.request("get", "/media", timeout=10)
        except Exception as exc:
            payload: dict[str, Any] = {"ok": False, "error": str(exc)}
        else:
            payload = response if isinstance(response, dict) else {"ok": False, "error": "Risposta non valida"}
        self.deviceMediaReceived.emit(player.ip, payload)

    def _invoke_playlist_status(self, player: PlayerRecord) -> None:
        if self._skip_if_offline(player, "playlist status", log=False):
            return
        client = self._client_for(player)
        try:
            response = client.request("get", "/playlist/status", timeout=5)
        except Exception as exc:
            payload: dict[str, Any] = {"ok": False, "error": str(exc)}
        else:
            payload = response if isinstance(response, dict) else {"ok": False, "error": "Risposta non valida"}
        self.playlistStatusReceived.emit(player.ip, payload)

    def _invoke_off_playlist_status(self, player: PlayerRecord) -> None:
        """Se il framework è OFF, tenta di recuperare la playlist nativa via /off/playlist."""
        client = self._client_for(player)

        # Prefer the framework cached on the player record
        fw_attr = None
        try:
            fw_attr = getattr(player, "framework", None)
        except Exception:
            fw_attr = None
        if isinstance(fw_attr, str) and fw_attr.lower() != "off":
            return

        cached_status: dict[str, Any] | None = None
        try:
            with self._status_cache_lock:
                cached_status = self._status_cache.get(player.ip)
        except Exception:
            cached_status = None

        fw: str | None = None
        if isinstance(cached_status, dict):
            try:
                fw_val = cached_status.get("framework")
                fw = str(fw_val).lower() if fw_val is not None else None
            except Exception:
                fw = None

        if fw is None:
            # No cached status yet: fallback to a direct request once
            try:
                status = client.get_status()
            except Exception:
                return
            if isinstance(status, dict):
                fw_val = status.get("framework")
                fw = str(fw_val).lower() if fw_val is not None else None
                try:
                    with self._status_cache_lock:
                        self._status_cache[player.ip] = status
                except Exception:
                    pass
            else:
                return

        if fw != "off":
            return
        try:
            response = client.request("get", "/off/playlist", timeout=5)
        except Exception:
            return  # silenzioso: NON tutti i player supportano endpoint
        if isinstance(response, dict) and response.get("ok"):
            # Rimappa al formato standard playlistStatusReceived
            mapped: dict[str, Any] = {
                "ok": True,
                "items": response.get("items") or [],
                "index": response.get("index") if isinstance(response.get("index"), int) else 0,
                "current": response.get("current"),
                "loop": response.get("loop") if isinstance(response.get("loop"), bool) else None,
                "_source": "off"
            }
            self.playlistStatusReceived.emit(player.ip, mapped)

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
        if cmd in {"next", "prev"}:
            seconds = float(payload.get("seconds", 0.0)) if isinstance(payload, dict) else 0.0
            if seconds > 0:
                try:
                    client.request("post", "/visual/ftb", params={"seconds": seconds})
                except Exception:
                    pass
                try:
                    time.sleep(max(0.0, seconds))
                except Exception:
                    pass
            endpoint = "/playlist/next" if cmd == "next" else "/playlist/prev"
            return client.request("post", endpoint)
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
        if cmd == "brightness":
            # Visual brightness absolute (server accepts 0..1 or 0..100)
            value = payload.get("value")
            seconds = payload.get("seconds", 0.5)
            params: dict[str, Any] = {}
            if value is not None:
                try:
                    params["value"] = float(value)
                except Exception:
                    pass
            try:
                params["seconds"] = float(seconds)
            except Exception:
                pass
            if "in_time" in payload:
                params["in_time"] = payload["in_time"]
            return client.request("post", "/visual/brightness", params=params)
        if cmd == "display_center":
            enabled_flag = payload.get("on")
            if enabled_flag is None:
                enabled_flag = payload.get("enabled")
            enabled_bool = bool(enabled_flag)
            return client.request("post", "/display/center", params={"on": 1 if enabled_bool else 0})
        if cmd in {"image_duration_set", "image_duration"}:
            seconds_value = payload.get("seconds")
            if seconds_value is None:
                raise ValueError("Command image_duration_set richiede 'seconds'")
            try:
                seconds_float = float(seconds_value)
            except Exception as exc:
                raise ValueError(f"Valore seconds non valido: {seconds_value}") from exc
            return client.request("post", "/visual/image_duration", params={"seconds": seconds_float})
        if cmd in {"image_duration_get", "image_duration_refresh"}:
            return client.request("get", "/visual/image_duration")
        if cmd == "change_framework":
            name = payload.get("name")
            if not name:
                raise ValueError("Campo 'name' richiesto per change_framework")
            body = {"name": name}
            if payload.get("in_time"):
                body["in_time"] = payload["in_time"]
            return client.request("post", "/change_framework", json=body, timeout=10)
        if cmd == "ping":
            try:
                default_ms = int(getattr(self.state.config.network, "ping_ms", 200))
            except Exception:
                default_ms = 200
            try:
                duration = int(payload.get("duration_ms", default_ms))
            except Exception:
                duration = default_ms
            params: dict[str, Any] = {"duration_ms": max(10, int(duration))}
            if "in_time" in payload:
                params["in_time"] = payload["in_time"]
            return client.request("post", "/ping", params=params)
        if cmd == "hud_visible":
            # Toggle HUD text visibility on OFF-player via headless proxy
            if "mode" in payload:
                try:
                    mode = int(payload.get("mode", 0))
                except Exception:
                    mode = 0
                params = {"mode": mode, "on": 1 if mode > 0 else 0}
                return client.request("post", "/hud/visible", params=params)
            on = 1 if bool(payload.get("on", False)) else 0
            return client.request("post", "/hud/visible", params={"on": on})
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
        if cmd == "service_restart":
            try:
                # 202 Accepted expected
                return client.request("post", "/system/service/restart", timeout=2)
            except requests.RequestException:
                return {"ok": True, "message": "Service restart dispatched"}
        if cmd == "run_setup":
            force = bool(payload.get("force", False))
            return client.request("post", "/maintenance/run_setup", json={"force": force}, timeout=120)
        if cmd == "media_clear":
            return client.request("post", "/media/prune_to_playlist", json={"items": []}, timeout=120)
        if cmd == "media_prune_to_playlist":
            items_payload = []
            if isinstance(payload, dict):
                raw_items = payload.get("items")
                if isinstance(raw_items, list):
                    items_payload = [str(item) for item in raw_items if item is not None]
            return client.request("post", "/media/prune_to_playlist", json={"items": items_payload}, timeout=120)
        if cmd == "playlist_build":
            loop = bool(payload.get("loop", True))
            return client.request("post", "/media/playlist", json={"loop": loop}, timeout=30)
        if cmd == "playlist_loop_on":
            return client.request("post", "/playlist/loop", params={"on": 1})
        if cmd == "playlist_loop_off":
            return client.request("post", "/playlist/loop", params={"on": 0})
        if cmd == "playlist_jump":
            index = int(payload.get("index", 0))
            return client.request("post", "/playlist/jump", params={"index": index})
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
        if cmd == "fix_permissions":
            return client.request("post", "/maintenance/fix_permissions", timeout=30)
        if cmd == "disk_status":
            return client.request("get", "/system/disk", timeout=5)
        if cmd == "logs_cvlc":
            lines = int(payload.get("lines", 200)) if isinstance(payload, dict) else 200
            params: dict[str, Any] = {"lines": max(1, min(lines, 2000))}
            return client.request("get", "/logs/cvlc", params=params, timeout=5)
        if cmd in {"force_1280x720", "display_force_720p"}:
            body: dict[str, Any] = {"refresh_hz": 50}
            if (hz := payload.get("refresh_hz")) is not None:
                body["refresh_hz"] = hz
            if payload.get("force_discovery"):
                body["force_discovery"] = True
            if payload.get("dry_run"):
                body["dry_run"] = True
            json_payload = body if body else {}
            return client.request("post", "/display/mode/force_720p", json=json_payload, timeout=30)
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
            params: dict[str, Any] = {}
            if payload:
                value = payload.get("width")
                if value is not None:
                    try:
                        params["width"] = int(value)
                    except Exception:
                        pass
                value = payload.get("height")
                if value is not None:
                    try:
                        params["height"] = int(value)
                    except Exception:
                        pass
                value = payload.get("offsetX")
                if value is not None:
                    try:
                        params["offsetX"] = int(value)
                    except Exception:
                        pass
                value = payload.get("offsetY")
                if value is not None:
                    try:
                        params["offsetY"] = int(value)
                    except Exception:
                        pass
            return client.request("post", "/test/on", params=(params or None))
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
                label = self._format_version_label(version) if isinstance(version, str) else str(version)
                message = f"Bundle creato ({label})"
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
            # Log versione normalizzata per coerenza visiva nei log GUI
            raw_ver = payload.get('version')
            try:
                label = self._format_version_label(str(raw_ver)) if isinstance(raw_ver, str) else str(raw_ver)
            except Exception:
                label = str(raw_ver)
            self.logMessage.emit(
                f"Bundle pronto: {payload.get('zip_path')} versione={label} sha={payload.get('zip_sha')}"
            )
        self.updateCompleted.emit(payload)

    def set_active_bundle_from_file(self, bundle_path: str | Path) -> dict:
        path = Path(bundle_path).expanduser()
        try:
            resolved = path.resolve(strict=False)
        except Exception:
            resolved = path
        if not resolved.exists():
            raise FileNotFoundError(f"Bundle non trovato: {resolved}")
        if not resolved.is_file():
            raise ValueError("Seleziona un file bundle valido (atteso file .zip)")
        if resolved.suffix.lower() != ".zip":
            raise ValueError("Il bundle deve essere un file .zip")
        try:
            with zipfile.ZipFile(resolved, "r"):
                pass
        except Exception as exc:
            raise ValueError("Il file selezionato non è uno zip valido") from exc
        version = self._format_version_label(resolved.name)
        if not version:
            version = resolved.name
        self._active_bundle = resolved
        self._active_bundle_version = version
        sha_value = None
        if updater and hasattr(updater, "sha256_of"):
            try:
                sha_value = updater.sha256_of(resolved)
            except Exception:
                sha_value = None
        self.logMessage.emit(f"Bundle manuale impostato: {version} — {resolved}")
        return {"bundle": str(resolved), "version": version, "sha": sha_value}

    # Compatibilità con eventuali build precedenti in cui il nome è stato digitato male
    def set_active_bundle_from_filr(self, bundle_path: str | Path) -> dict:  # pragma: no cover - alias
        return self.set_active_bundle_from_file(bundle_path)

    # --------------------------------------------------------------
    # Helpers: normalizzazione versione per messaggi di stato
    # --------------------------------------------------------------
    def _format_version_label(self, value: str) -> str:
        try:
            from pathlib import Path as _P
            name = _P(value).name if ("/" in value or "\\" in value) else value
        except Exception:
            name = value or ""
        s = name.strip()
        import re as _re
        # bundle_v<ver>_<sha>.zip -> <ver>
        m = _re.search(r"bundle_v([0-9A-Za-z_.:+-]+)_", s)
        if m:
            ver = m.group(1)
            return ver if ver.lower().startswith("v") else f"v{ver}"
        # già 'v...'
        m = _re.search(r"v[0-9][0-9A-Za-z_.:+-]*", s)
        if m:
            return m.group(0)
        # versione numerica semplice
        m = _re.fullmatch(r"[0-9][0-9A-Za-z_.:+-]*", s)
        if m:
            return f"v{s}"
        return s

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
        host_ip = self._host_ip(targets)
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
                remote_bundle = f"/tmp/{os.path.basename(str(bundle))}"
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

    def get_player_record(self, ip: str) -> PlayerRecord | None:
        try:
            return self.player_registry.get_player(ip)
        except Exception:
            return None

    def set_expected_playlist_hash(self, value: str | None) -> None:
        try:
            self.player_registry.set_expected_playlist_hash(value)
        except Exception:
            pass

    def _local_ipv4_candidates(self) -> list[str]:
        candidates: list[str] = []
        if psutil is not None:
            try:
                for iface_addrs in psutil.net_if_addrs().values():  # type: ignore[attr-defined]
                    for addr in iface_addrs:
                        if addr.family != socket.AF_INET:
                            continue
                        ip = getattr(addr, "address", None) or getattr(addr, "addr", None)
                        if not ip or not isinstance(ip, str):
                            continue
                        if ip.startswith("127.") or ip.startswith("169.254."):
                            continue
                        candidates.append(ip)
            except Exception:
                pass
        if not candidates:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                candidates.append(s.getsockname()[0])
                s.close()
            except Exception:
                pass
        # Deduplicate while preserving order
        seen: set[str] = set()
        result: list[str] = []
        for ip in candidates:
            if ip not in seen:
                seen.add(ip)
                result.append(ip)
        return result

    def _host_ip(self, targets: Iterable[PlayerRecord] | None = None) -> str:
        candidates = self._local_ipv4_candidates()
        if targets and candidates:
            for player in targets:
                try:
                    player_net = ipaddress.ip_network(f"{player.ip}/24", strict=False)
                except Exception:
                    continue
                for candidate in candidates:
                    try:
                        if ipaddress.ip_address(candidate) in player_net:
                            return candidate
                    except Exception:
                        continue
        if candidates:
            return candidates[0]
        if updater and hasattr(updater, "local_primary_ip"):
            try:
                return updater.local_primary_ip()
            except Exception:  # pragma: no cover
                pass
        return "127.0.0.1"

    def purge_offline_players(self) -> list[str]:
        removed = self.player_registry.purge_offline()
        if removed:
            for ip in removed:
                self.logMessage.emit(f"Purgato player offline {ip}")
        else:
            self.logMessage.emit("Nessun player offline da purgare")
        return removed
