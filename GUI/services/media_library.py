"""Scan and expose media files for upload operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from GUI.core.logger import get_logger


_LOG = get_logger(__name__)


@dataclass(slots=True)
class MediaItem:
    """Describe a media file available for upload."""

    path: Path
    size: int


class MediaLibrary:
    """Index media files inside a root directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def iter_items(self) -> Iterable[MediaItem]:
        """Yield all media files relative to the configured root."""
        if not self._root.exists():
            _LOG.warning("Media root %s does not exist", self._root)
            return
        for file_path in self._root.rglob("*"):
            if file_path.is_file():
                yield MediaItem(path=file_path, size=file_path.stat().st_size)
