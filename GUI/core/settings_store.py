"""Persistence helper for GUI configuration settings."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from GUI.core.config import (
    AppConfig,
    MacEntry,
    MediaSettings,
    NetworkSettings,
    RemoteSettings,
    StartupSettings,
    UpdateSettings,
)


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
        remote = self._parse_remote(data.get("remote", {}))
        startup = self._parse_startup(data.get("startup", {}))
        return AppConfig(network=network, media=media, update=update, remote=remote, startup=startup)

    def save(self, config: AppConfig) -> None:
        data = asdict(config)
        # scan_ranges are derived dynamically from active interfaces; avoid persisting
        network_section = data.get("network")
        if isinstance(network_section, dict):
            network_section.pop("scan_ranges", None)
        if config.media.media_root is not None:
            data["media"]["media_root"] = str(config.media.media_root)
        if config.update.bundle_path is not None:
            data["update"]["bundle_path"] = str(config.update.bundle_path)
        # Explicitly ensure status_poll_ms and ping_ms are present
        if "status_poll_ms" not in data["network"]:
            data["network"]["status_poll_ms"] = config.network.status_poll_ms
        data["network"]["ping_ms"] = int(getattr(config.network, "ping_ms", 200))
        base_dir = self._path.parent.resolve()
        if config.remote.vnc_viewer_path is not None:
            data.setdefault("remote", {})
            viewer_path = config.remote.vnc_viewer_path
            if not viewer_path.is_absolute():
                viewer_path = (base_dir / viewer_path).resolve()
            try:
                relative = viewer_path.relative_to(base_dir)
                data["remote"]["vnc_viewer_path"] = str(relative)
            except ValueError:
                data["remote"]["vnc_viewer_path"] = str(viewer_path)
        else:
            data.setdefault("remote", {})
            data["remote"].pop("vnc_viewer_path", None)
        data.setdefault("remote", {})
        data["remote"]["vnc_password"] = config.remote.vnc_password
        data["remote"]["vnc_port"] = int(getattr(config.remote, "vnc_port", 5900))
        try:
            extra = list(getattr(config.remote, "vnc_extra_args", []))
            data["remote"]["vnc_extra_args"] = extra
        except Exception:
            data["remote"]["vnc_extra_args"] = []
        self._path.parent.mkdir(parents=True, exist_ok=True)
        startup_section = {"known_macs": []}
        for entry in config.startup.known_macs:
            startup_section["known_macs"].append(
                {
                    "mac": entry.mac,
                    "ip": entry.ip,
                    "name": entry.name,
                    "last_seen": float(entry.last_seen or 0.0),
                }
            )
        startup_section["broadcast"] = config.startup.broadcast
        startup_section["port"] = int(getattr(config.startup, "port", 9))
        data["startup"] = startup_section
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _parse_network(self, payload: dict[str, Any]) -> NetworkSettings:
        return NetworkSettings(
            # Scan ranges are discovered dynamically; ignore persisted values for backwards compatibility
            scan_ranges=[],
            player_port=int(payload.get("player_port", 8080)),
            ping_interval=float(payload.get("ping_interval", 10.0)),
            ping_ms=int(payload.get("ping_ms", 200)),
            status_poll_ms=int(payload.get("status_poll_ms", 2000)),
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

    def _parse_remote(self, payload: dict[str, Any]) -> RemoteSettings:
        viewer = payload.get("vnc_viewer_path")
        password = payload.get("vnc_password", "extra")
        port = payload.get("vnc_port", 5900)
        try:
            port = int(port)
        except Exception:
            port = 5900
        extra_args_raw = payload.get("vnc_extra_args", [])
        if not isinstance(extra_args_raw, list):
            extra_args_raw = []
        extra_args: list[str] = []
        for a in extra_args_raw:
            try:
                if isinstance(a, str) and a.strip():
                    extra_args.append(a.strip())
            except Exception:
                continue
        viewer_path: Path | None = None
        if viewer:
            candidate = Path(viewer)
            if not candidate.is_absolute():
                candidate = (self._path.parent / candidate).resolve()
            viewer_path = candidate
        return RemoteSettings(
            vnc_viewer_path=viewer_path,
            vnc_password=str(password) if password else "extra",
            vnc_port=port,
            vnc_extra_args=extra_args,
        )

    def _parse_startup(self, payload: dict[str, Any]) -> StartupSettings:
        entries: list[MacEntry] = []
        if not isinstance(payload, dict):
            return StartupSettings()
        raw_entries = payload.get("known_macs")
        if isinstance(raw_entries, list):
            for item in raw_entries:
                if not isinstance(item, dict):
                    continue
                mac = item.get("mac")
                if not mac:
                    continue
                ip = item.get("ip")
                name = item.get("name")
                last_seen_raw = item.get("last_seen")
                try:
                    last_seen = float(last_seen_raw) if last_seen_raw is not None else time.time()
                except Exception:
                    last_seen = time.time()
                entries.append(
                    MacEntry(
                        mac=str(mac),
                        ip=str(ip) if isinstance(ip, str) else "",
                        name=str(name) if isinstance(name, str) else "",
                        last_seen=last_seen,
                    )
                )
        broadcast = payload.get("broadcast", "255.255.255.255")
        port_raw = payload.get("port", 9)
        try:
            port = int(port_raw)
        except Exception:
            port = 9
        return StartupSettings(known_macs=entries, broadcast=str(broadcast), port=port)
