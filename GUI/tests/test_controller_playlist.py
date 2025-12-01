from __future__ import annotations

import concurrent.futures
import types
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 non disponibile nell'ambiente di test corrente")

from GUI.core.controller import ApplicationController
from GUI.services.player_registry import PlayerRecord


class InlineExecutor:
    """Minimal executor that runs tasks synchronously for deterministic tests."""

    def submit(self, fn, *args, **kwargs):
        future: concurrent.futures.Future = concurrent.futures.Future()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - surfaced in test assertions
            future.set_exception(exc)
        else:
            future.set_result(result)
        return future


def test_perform_push_playlist_runs_full_workflow(tmp_path: Path) -> None:
    controller = ApplicationController(tmp_path / "settings.json")
    controller._executor = InlineExecutor()
    media_root = tmp_path / "media"
    media_root.mkdir()
    controller.state.config.media.media_root = media_root

    player = PlayerRecord(name="Demo", ip="10.0.0.5")

    uploads: list[tuple[Path, list[str]]] = []
    applies: list[tuple[str, list[str], bool]] = []
    prunes: list[tuple[str, list[str]]] = []

    def upload_media_stub(self, media_path: Path, targets):
        uploads.append((Path(media_path), [t.ip for t in targets]))

    def apply_playlist_stub(self, player_record: PlayerRecord, items: list[str], loop: bool):
        applies.append((player_record.ip, list(items), loop))

    def prune_stub(self, player_record: PlayerRecord, items: list[str]) -> bool:
        prunes.append((player_record.ip, list(items)))
        return True

    controller.upload_media = types.MethodType(upload_media_stub, controller)
    controller._invoke_apply_playlist = types.MethodType(apply_playlist_stub, controller)
    controller._invoke_media_prune = types.MethodType(prune_stub, controller)

    playlist_items = ["clip.mp4", "intro.mov"]
    controller._perform_push_playlist(playlist_items, [player], loop=False, clear_before=True)

    assert prunes == [("10.0.0.5", playlist_items)]
    assert uploads == [
        (media_root / "clip.mp4", ["10.0.0.5"]),
        (media_root / "intro.mov", ["10.0.0.5"]),
    ]
    assert applies == [("10.0.0.5", playlist_items, False)]
