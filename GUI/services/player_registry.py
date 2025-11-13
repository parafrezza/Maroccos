"""Maintain the list of known players and their heartbeat state."""

from __future__ import annotations

import threading
import socket
import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable

import requests
from PySide6.QtCore import QObject, Signal

from GUI.core.config import NetworkSettings
from GUI.core.logger import get_logger


_LOG = get_logger(__name__)

_PLAYER_TIMEOUT = 30.0
_PLAYER_PURGE_SECONDS = 120.0


@dataclass(slots=True)
class PlayerRecord:
    """Represent a player tracked by the GUI."""

    name: str
    ip: str
    port: int | None = None
    last_seen: float = field(default_factory=time.time)
    version: str | None = None
    state: str = "unknown"
    status_text: str | None = None
    device_id: str | None = None
    instance_id: str | None = None
    update_stage: str | None = None
    update_progress: float | None = None
    update_error: str | None = None
    update_elevated: bool = False
    update_backup_dir: str | None = None
    discovered_via: str | None = None  # es. "beacon" | "scan"
    framework: str | None = None
    media_available: bool = True
    # GUI-only: debouncing emission
    _ui_last_status_key: str | None = None
    _ui_last_emit_ts: float = 0.0


class PlayerRegistry(QObject):
    """Track player reachability via background ping."""

    playerUpdated = Signal(object)
    playerRemoved = Signal(str)
    syncCompleted = Signal()

    def __init__(self, settings: NetworkSettings) -> None:
        super().__init__()
        self._settings = settings
        self._players: Dict[str, PlayerRecord] = {}
        self._lock = threading.RLock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._beacon_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="player-ping", daemon=True)
        self._thread.start()
        # Avvia listener UDP per beacon di auto-discovery (best-effort)
        try:
            if os.environ.get("HEADLESS_BEACON_LISTENER", "1") not in {"0", "false", "False"}:
                self._beacon_thread = threading.Thread(target=self._beacon_loop, name="player-beacon", daemon=True)
                self._beacon_thread.start()
        except Exception:
            pass

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self._beacon_thread:
            # Thread è daemon: nessun join bloccherà l'uscita, ma proviamo breve join
            try:
                self._beacon_thread.join(timeout=1)
            except Exception:
                pass
            self._beacon_thread = None

    def sync_players(self, players: Iterable[PlayerRecord]) -> None:
        """Replace tracked players with fresh discovery results."""
        with self._lock:
            self._players = {p.ip: p for p in players}
        for player in players:
            self.playerUpdated.emit(player)
        self.syncCompleted.emit()

    def current_players(self) -> list[PlayerRecord]:
        with self._lock:
            return list(self._players.values())

    def _run(self) -> None:
        while self._running:
            time.sleep(self._settings.ping_interval)
            with self._lock:
                records = list(self._players.values())
            for record in records:
                self._check_player(record)
            self._purge_stale_players()

    def _check_player(self, record: PlayerRecord) -> None:
        port = int(record.port) if record.port else int(self._settings.player_port)
        url = f"http://{record.ip}:{port}/status"
        try:
            response = requests.get(url, timeout=2)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            _LOG.debug("Ping failed for %s: %s", record.ip, exc)
            self._update_state(record, reachable=False)
            return

        record.last_seen = time.time()
        record.version = data.get("version") or data.get("version_current")
        try:
            fw = data.get("framework") if isinstance(data, dict) else None
            record.framework = str(fw) if fw is not None else None
        except Exception:
            record.framework = None
        # Aggiorna identificativi se presenti
        try:
            did = data.get("device_id")
            iid = data.get("instance_id")
            if isinstance(did, str) and did:
                record.device_id = did
            if isinstance(iid, str) and iid:
                record.instance_id = iid
        except Exception:
            pass
        # Aggiorna il nome se il backend lo espone
        try:
            new_name = data.get("device_name") or data.get("name") or None
            if isinstance(new_name, str) and new_name and new_name != record.name:
                record.name = new_name
        except Exception:
            pass
        # Compose human-friendly status (base player activity)
        try:
            state = str(data.get("player_state") or "unknown")
            status_txt = state
            if state == "playing":
                name = data.get("current_media") or "-"
                status_txt = f"play: {name}"
                timing = data.get("timing") or {}
                skew = None
                if isinstance(timing, dict):
                    sv = timing.get("skew_ms") or timing.get("skew")
                    if isinstance(sv, (int, float)):
                        skew = float(sv)
                if isinstance(skew, float):
                    status_txt += f" • Δ {skew:.0f} ms"
            else:
                if state in {"stopped", "idle"}:
                    status_txt = "idle"
            # OFF-backend: preferisci lo stato esposto da OFF se presente
            try:
                if str(data.get("framework")) == "off":
                    off_playing = data.get("off_playing")
                    # Normalizza (può essere string 'playing' o boolean)
                    is_playing = False
                    if isinstance(off_playing, bool):
                        is_playing = off_playing
                    elif isinstance(off_playing, str):
                        is_playing = off_playing.lower() in {"playing", "true", "1", "yes"}
                    if is_playing:
                        name = data.get("off_current_media") or data.get("current_media") or "-"
                        status_txt = f"play: {name}"
                    else:
                        # Quando OFF è idle fermo
                        status_txt = "idle"
            except Exception:
                pass
            sched = data.get("scheduled") or {}
            play_at = sched.get("play_at")
            if isinstance(play_at, (int, float)):
                now = time.time()
                if play_at > now:
                    eta = play_at - now
                    if eta < 60:
                        status_txt += f" • starting in {eta:.1f}s"
                    else:
                        from datetime import datetime
                        hhmmss = datetime.fromtimestamp(play_at).strftime("%H:%M:%S")
                        status_txt += f" • starting at {hhmmss}"
            record.status_text = status_txt
        except Exception:
            record.status_text = None
        # Media availability info
        try:
            has_media = data.get("media_available")
            if isinstance(has_media, bool):
                record.media_available = has_media
        except Exception:
            pass
        # Update metadata for update status badges
        try:
            record.update_stage = data.get("update_stage") or None
            record.update_progress = data.get("update_progress") or None
            record.update_error = data.get("update_error") or None
            elev = data.get("update_elevated") or {}
            record.update_elevated = bool(elev)
            if isinstance(elev, dict):
                record.update_backup_dir = elev.get("backup_dir") or data.get("update_backup_dir") or None
            else:
                record.update_backup_dir = data.get("update_backup_dir") or None
            # Augment status text with update info
            base_txt = record.status_text or ""
            if record.update_stage in {"downloading", "applying", "restarting", "staging"}:
                prog = record.update_progress
                if isinstance(prog, (int, float)):
                    status_txt = f"update: {record.update_stage} {prog:.0f}%"
                else:
                    status_txt = f"update: {record.update_stage}" if record.update_stage else base_txt
            elif record.update_stage == "error":
                status_txt = f"update error" + (f": {record.update_error}" if record.update_error else "")
            elif record.update_stage == "ok":
                # mantiene lo status_txt originale (play/idle) ma potrebbe aggiungere marker
                status_txt = base_txt
            record.status_text = status_txt
        except Exception:
            pass

        self._update_state(record, reachable=True)

    # --- UDP beacon listener ---
    def _beacon_loop(self) -> None:
        """Ascolta pacchetti UDP broadcast/unicast con payload JSON di tipo 'headless_beacon'.
        Aggiorna o inserisce PlayerRecord con ip/porta/versione/nome.
        """
        try:
            port_env = os.environ.get("HEADLESS_BEACON_PORT")
            port = int(port_env) if port_env else 47999
        except Exception:
            port = 47999
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            except Exception:
                pass
            s.bind(("0.0.0.0", port))
            s.settimeout(0.25)
        except Exception as e:
            _LOG.debug("Beacon bind fallito su %s: %s", port, e)
            try:
                s.close()
            except Exception:
                pass
            return
        _LOG.info("Beacon listener avviato su UDP %s", port)
        while self._running:
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                time.sleep(0.05)
                continue
            except Exception as e:
                _LOG.debug("Beacon recv errore: %s", e)
                continue
            try:
                raw = data.decode("utf-8", errors="ignore").strip()
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    continue
                if (payload.get("type") or "").lower() != "headless_beacon":
                    continue
                name = payload.get("name") or ""
                version = payload.get("version") or None
                http_port = payload.get("http_port")
                try:
                    http_port = int(http_port) if http_port is not None else None
                except Exception:
                    http_port = None
                ip = payload.get("ip") or None
                if not ip:
                    ip = addr[0]
                if not isinstance(ip, str) or not ip:
                    continue
                # Inserisci/aggiorna record
                with self._lock:
                    rec = self._players.get(ip)
                    if not rec:
                        rec = PlayerRecord(name=name or ip, ip=ip, port=http_port or self._settings.player_port, discovered_via="beacon")
                        self._players[ip] = rec
                    # Aggiorna campi principali
                    rec.last_seen = time.time()
                    if name:
                        rec.name = name
                    if version:
                        rec.version = version
                    if http_port:
                        rec.port = http_port
                    rec.state = "online"
                    rec.status_text = rec.status_text or "online"
                # Notifica UI
                self.playerUpdated.emit(rec)
            except Exception:
                continue
        try:
            s.close()
        except Exception:
            pass

    def _update_state(self, record: PlayerRecord, reachable: bool) -> None:
        age = time.time() - record.last_seen
        if reachable:
            record.state = "online"
        elif age < _PLAYER_TIMEOUT:
            record.state = "warning"
        else:
            record.state = "offline"
        # Debounce UI updates to reduce flicker during progress streaming
        try:
            prog_key = str(int(record.update_progress)) if isinstance(record.update_progress, (int, float)) else "-"
            stage = (record.update_stage or "").lower() if isinstance(record.update_stage, str) else ""
            key = "|".join([
                record.state or "",
                record.version or "",
                record.status_text or "",
                prog_key,
                stage,
            ])
            now = time.time()
            # Emit if key changed or at least 0.75s since last emit
            if key != record._ui_last_status_key or (now - (record._ui_last_emit_ts or 0)) > 0.75:
                record._ui_last_status_key = key
                record._ui_last_emit_ts = now
                self.playerUpdated.emit(record)
            else:
                # Skip noisy update
                return
        except Exception:
            # Fallback: always emit
            self.playerUpdated.emit(record)

    def _purge_stale_players(self) -> None:
        now = time.time()
        to_remove: list[str] = []
        with self._lock:
            for ip, record in list(self._players.items()):
                age = now - record.last_seen
                if age > _PLAYER_PURGE_SECONDS and record.state == "offline":
                    to_remove.append(ip)
                    del self._players[ip]
        for ip in to_remove:
            self.playerRemoved.emit(ip)

    def purge_offline(self) -> list[str]:
        """Remove all players currently marked as offline (red state)."""
        removed: list[str] = []
        with self._lock:
            for ip, record in list(self._players.items()):
                state = (record.state or "").lower()
                if state == "offline":
                    removed.append(ip)
                    del self._players[ip]
        for ip in removed:
            self.playerRemoved.emit(ip)
        return removed
