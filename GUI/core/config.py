"""Application configuration models and defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from pathlib import Path


@dataclass(slots=True)
class NetworkSettings:
    """Parameters controlling discovery and connectivity."""

    scan_ranges: list[str] = field(default_factory=list)
    player_port: int = 8080
    ping_interval: float = 10.0
    ping_ms: int = 200
    status_poll_ms: int = 2000
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
class RemoteSettings:
    """Remote access configuration (VNC viewer paths/password)."""

    vnc_viewer_path: Path | None = None
    vnc_password: str = ""
    # Porta VNC (display :0 => 5900). Usata per costruire host::port per TigerVNC.
    vnc_port: int = 5900
    # Argomenti extra da passare al viewer (es. ["-Shared", "-AutoSelect=0"]).
    vnc_extra_args: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MacEntry:
    """Record associating a MAC with the last known player."""

    mac: str
    ip: str | None = None
    name: str | None = None
    last_seen: float = field(default_factory=time.time)


@dataclass(slots=True)
class StartupSettings:
    """Settings used to store startup/WOL targets."""

    known_macs: list[MacEntry] = field(default_factory=list)
    broadcast: str = "255.255.255.255"
    port: int = 9


@dataclass(slots=True)
class AppConfig:
    """Top-level configuration container."""

    network: NetworkSettings = field(default_factory=NetworkSettings)
    media: MediaSettings = field(default_factory=MediaSettings)
    update: UpdateSettings = field(default_factory=UpdateSettings)
    remote: RemoteSettings = field(default_factory=RemoteSettings)
    startup: StartupSettings = field(default_factory=StartupSettings)
