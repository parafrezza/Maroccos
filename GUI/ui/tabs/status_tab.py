"""Deprecated module retained for backward compatibility."""

from __future__ import annotations


class StatusTab:  # pragma: no cover - legacy stub
    """Placeholder to avoid import errors for legacy plugins."""

    def __init__(self, *_, **__) -> None:
        raise RuntimeError("StatusTab has been removed; use PlayerPanel instead")
