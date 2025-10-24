"""Application configuration models and defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class NetworkSettings:
    """Parameters controlling discovery and connectivity."""

    scan_ranges: list[str] = field(default_factory=lambda: ["192.168.1.0/24"])
    player_port: int = 8080
    ping_interval: float = 10.0
    status_poll_ms: int = 1000
    api_key: str | None = None


@dataclass(slots=True)
class MediaSettings:
    """Locations for media scanning and upload server."""

    media_root: Path | None = None
    server_port: int = 9000


@dataclass(slots=True)
class UpdateSettings:
    """Parameters for bundle creation and rollout."""

    auto_build: bool = True
    bundle_path: Path | None = None
    serve_port: int = 8000


@dataclass(slots=True)
class AppConfig:
    """Top-level configuration container."""

    network: NetworkSettings = field(default_factory=NetworkSettings)
    media: MediaSettings = field(default_factory=MediaSettings)
    update: UpdateSettings = field(default_factory=UpdateSettings)
