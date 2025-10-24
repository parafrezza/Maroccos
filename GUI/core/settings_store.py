"""Persistence helper for GUI configuration settings."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from GUI.core.config import AppConfig, MediaSettings, NetworkSettings, UpdateSettings


class SettingsStore:
    """Load and save application settings from a JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> AppConfig:
        if not self._path.exists():
            return AppConfig()
        data = json.loads(self._path.read_text(encoding="utf-8"))
        network = self._parse_network(data.get("network", {}))
        media = self._parse_media(data.get("media", {}))
        update = self._parse_update(data.get("update", {}))
        return AppConfig(network=network, media=media, update=update)

    def save(self, config: AppConfig) -> None:
        data = asdict(config)
        if config.media.media_root is not None:
            data["media"]["media_root"] = str(config.media.media_root)
        if config.update.bundle_path is not None:
            data["update"]["bundle_path"] = str(config.update.bundle_path)
        # Explicitly ensure status_poll_ms is present
        if "status_poll_ms" not in data["network"]:
            data["network"]["status_poll_ms"] = config.network.status_poll_ms
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _parse_network(self, payload: dict[str, Any]) -> NetworkSettings:
        return NetworkSettings(
            scan_ranges=list(payload.get("scan_ranges", ["192.168.1.0/24"])),
            player_port=int(payload.get("player_port", 8080)),
            ping_interval=float(payload.get("ping_interval", 10.0)),
            status_poll_ms=int(payload.get("status_poll_ms", 1000)),
            api_key=payload.get("api_key"),
        )

    def _parse_media(self, payload: dict[str, Any]) -> MediaSettings:
        root = payload.get("media_root")
        return MediaSettings(
            media_root=Path(root) if root else None,
            server_port=int(payload.get("server_port", 9000)),
        )

    def _parse_update(self, payload: dict[str, Any]) -> UpdateSettings:
        bundle_path = payload.get("bundle_path")
        return UpdateSettings(
            auto_build=bool(payload.get("auto_build", True)),
            bundle_path=Path(bundle_path) if bundle_path else None,
            serve_port=int(payload.get("serve_port", 8000)),
        )
