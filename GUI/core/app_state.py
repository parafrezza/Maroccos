"""Shared application state accessible across UI and services."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock

from GUI.core.config import AppConfig


@dataclass(slots=True)
class AppState:
    """Mutable state container guarded by a lock."""

    config: AppConfig = field(default_factory=AppConfig)
    lock: RLock = field(default_factory=RLock)
