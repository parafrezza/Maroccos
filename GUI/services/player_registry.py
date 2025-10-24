"""Maintain the list of known players and their heartbeat state."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable

import requests
from PySide6.QtCore import QObject, Signal

from GUI.core.config import NetworkSettings
from GUI.core.logger import get_logger


_LOG = get_logger(__name__)

_PLAYER_TIMEOUT = 30.0


@dataclass(slots=True)
class PlayerRecord:
    """Represent a player tracked by the GUI."""

    name: str
    ip: str
    last_seen: float = field(default_factory=time.time)
    version: str | None = None
    state: str = "unknown"
    status_text: str | None = None


class PlayerRegistry(QObject):
    """Track player reachability via background ping."""

    playerUpdated = Signal(object)
    syncCompleted = Signal()

    def __init__(self, settings: NetworkSettings) -> None:
        super().__init__()
        self._settings = settings
        self._players: Dict[str, PlayerRecord] = {}
        self._lock = threading.RLock()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="player-ping", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

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

    def _check_player(self, record: PlayerRecord) -> None:
        url = f"http://{record.ip}:{self._settings.player_port}/status"
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
        # Aggiorna il nome se il backend lo espone
        try:
            new_name = data.get("device_name") or data.get("name") or None
            if isinstance(new_name, str) and new_name and new_name != record.name:
                record.name = new_name
        except Exception:
            pass
        # Compose human-friendly status
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
        self._update_state(record, reachable=True)

    def _update_state(self, record: PlayerRecord, reachable: bool) -> None:
        age = time.time() - record.last_seen
        if reachable:
            record.state = "online"
        elif age < _PLAYER_TIMEOUT:
            record.state = "warning"
        else:
            record.state = "offline"
        self.playerUpdated.emit(record)
