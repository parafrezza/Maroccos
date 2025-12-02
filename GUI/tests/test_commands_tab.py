from __future__ import annotations

import os
from pathlib import Path
import time
from collections.abc import Iterator

import pytest

pytest.importorskip("PySide6", reason="PySide6 non disponibile nell'ambiente di test corrente")

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QListWidgetItem, QMessageBox

from GUI.ui.tabs.commands_tab import CommandsTab


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def commands_tab(qapp: QApplication) -> Iterator[CommandsTab]:
    tab = CommandsTab()
    yield tab
    tab.deleteLater()


def _populate_playlist(tab: CommandsTab, items: list[str]) -> None:
    for name in items:
        row = QListWidgetItem(name)
        row.setData(Qt.UserRole, name)
        tab._playlist.addItem(row)
    tab._update_playlist_controls_state()


def test_playlist_push_emits_loop_state(commands_tab: CommandsTab, qapp: QApplication, tmp_path: Path) -> None:
    _populate_playlist(commands_tab, ["video_a.mp4"])
    media_root = tmp_path / "media"
    media_root.mkdir()
    commands_tab.set_media_root(media_root)
    commands_tab.set_targets_selected(True)
    commands_tab._playlist_loop_checkbox.setChecked(False)
    spy = QSignalSpy(commands_tab.playlistPushRequested)

    commands_tab._push_playlist.click()
    qapp.processEvents()

    assert spy.count() == 1
    emitted_items, clear_before, loop_flag = spy.at(0)
    assert emitted_items == ["video_a.mp4"]
    assert clear_before is False
    assert loop_flag is False


def test_playlist_validation_disables_start_show(commands_tab: CommandsTab, qapp: QApplication, tmp_path: Path) -> None:
    _populate_playlist(commands_tab, ["clip.mov"])
    media_root = tmp_path / "media"
    media_root.mkdir()
    commands_tab.set_media_root(media_root)
    commands_tab.set_targets_selected(True)
    qapp.processEvents()
    assert commands_tab._start_show.isEnabled()

    commands_tab.set_playlist_validation(["missing/clip.mov"], [])
    qapp.processEvents()

    assert not commands_tab._start_show.isEnabled()
    assert commands_tab._push_playlist.isEnabled()
    assert commands_tab._banner_label.text()


def test_media_root_warning_tooltips(commands_tab: CommandsTab, tmp_path: Path, qapp: QApplication) -> None:
    commands_tab.set_targets_selected(True)
    commands_tab.set_media_root(None)
    qapp.processEvents()

    _populate_playlist(commands_tab, ["clip.mov"])
    warning = "Cartella media" in commands_tab._banner_label.text()
    assert warning
    assert "Cartella media" in commands_tab._push_playlist.toolTip()

    valid_root = tmp_path / "media"
    valid_root.mkdir()
    commands_tab.set_media_root(valid_root)
    qapp.processEvents()

    assert commands_tab._banner_label.text() == ""
    assert commands_tab._push_playlist.toolTip() == "Invia la playlist corrente ai player selezionati"


def test_auto_download_started_for_missing_media(commands_tab: CommandsTab, tmp_path: Path, qapp: QApplication) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    clip = media_root / "clip.mp4"
    clip.write_bytes(b"data")
    commands_tab.set_media_root(media_root)
    commands_tab.set_targets_selected(True)
    # Nessun file presente sul device
    commands_tab.set_device_media_items([])

    list_item = QListWidgetItem("clip.mp4")
    list_item.setData(Qt.UserRole, str(clip))
    list_item.setData(CommandsTab._RELATIVE_ROLE, "clip.mp4")
    commands_tab._media_list.addItem(list_item)
    commands_tab._media_list.setCurrentRow(0)

    spy = QSignalSpy(commands_tab.uploadRequested)
    commands_tab._add_selected_to_playlist()
    qapp.processEvents()

    assert spy.count() == 1
    emitted_path = spy.at(0)[0]
    assert emitted_path == str(clip.resolve())
    item = commands_tab._playlist.item(0)
    assert item is not None
    brush = item.data(Qt.BackgroundRole)
    assert brush is not None
    assert brush.color().name() == "#fff4e5"  # arancio trasferimento


def test_device_media_update_clears_transfer_state(commands_tab: CommandsTab, tmp_path: Path, qapp: QApplication) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    clip = media_root / "clip.mp4"
    clip.write_bytes(b"x")
    commands_tab.set_media_root(media_root)
    commands_tab.set_targets_selected(True)
    commands_tab.set_device_media_items([])

    list_item = QListWidgetItem("clip.mp4")
    list_item.setData(Qt.UserRole, str(clip))
    list_item.setData(CommandsTab._RELATIVE_ROLE, "clip.mp4")
    commands_tab._media_list.addItem(list_item)
    commands_tab._media_list.setCurrentRow(0)
    commands_tab._add_selected_to_playlist()
    qapp.processEvents()
    assert commands_tab._downloads_in_progress  # download avviato

    commands_tab.set_device_media_items([{"name": "clip.mp4", "size": 10}])
    qapp.processEvents()

    item = commands_tab._playlist.item(0)
    assert item is not None
    assert item.data(Qt.BackgroundRole) is None
    assert not commands_tab._downloads_in_progress


def test_device_clear_requires_confirmation(commands_tab: CommandsTab, qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    commands_tab.set_targets_selected(True)
    answers = [QMessageBox.No, QMessageBox.Yes]
    prompts: list[str] = []

    def fake_question(parent, title, text, buttons):  # type: ignore[override]
        prompts.append(str(text))
        return answers.pop(0)

    monkeypatch.setattr(QMessageBox, "question", fake_question)
    spy = QSignalSpy(commands_tab.miscCommandTriggered)

    commands_tab._device_clear.click()
    qapp.processEvents()
    assert spy.count() == 0

    commands_tab._device_clear.click()
    qapp.processEvents()
    assert spy.count() == 1
    emitted_command, payload = spy.at(0)
    assert emitted_command == "media_clear"
    assert payload == {}
    assert prompts and "cartella media" in prompts[0]


def test_next_command_blocked_with_single_playlist_item(commands_tab: CommandsTab, qapp: QApplication) -> None:
    _populate_playlist(commands_tab, ["only.mp4"])
    commands_tab.set_targets_selected(True)
    spy = QSignalSpy(commands_tab.playbackTriggered)

    commands_tab._handle_playback_button({"command": "next", "use_fade": True})
    qapp.processEvents()

    assert spy.count() == 0
    assert "Playlist con un solo elemento" in commands_tab._banner_label.text()


def test_prev_command_blocked_with_single_playlist_item(commands_tab: CommandsTab, qapp: QApplication) -> None:
    _populate_playlist(commands_tab, ["only.mp4"])
    commands_tab.set_targets_selected(True)
    spy = QSignalSpy(commands_tab.playbackTriggered)

    commands_tab._handle_playback_button({"command": "prev", "use_fade": True})
    qapp.processEvents()

    assert spy.count() == 0
    assert "Playlist con un solo elemento" in commands_tab._banner_label.text()


def test_auto_apply_emitted_on_reorder(commands_tab: CommandsTab, qapp: QApplication) -> None:
    commands_tab.set_targets_selected(True)
    commands_tab.set_device_media_items([
        {"name": "track_a.mp4", "size": 10},
        {"name": "track_b.mp4", "size": 10},
    ])
    commands_tab.set_playlist_from_player(["track_a.mp4", "track_b.mp4"], current_index=0)
    commands_tab._auto_apply_timer.setInterval(5)
    spy = QSignalSpy(commands_tab.playlistAutoApplyRequested)

    second = commands_tab._playlist.takeItem(1)
    commands_tab._playlist.insertItem(0, second)
    commands_tab._emit_playlist_changed()
    qapp.processEvents()

    if spy.count() == 0:
        spy.wait(200)
    assert spy.count() == 1
    emitted = spy.at(0)
    assert emitted[0] == ["track_b.mp4", "track_a.mp4"]


def test_auto_apply_not_triggered_on_add(commands_tab: CommandsTab, qapp: QApplication) -> None:
    commands_tab.set_targets_selected(True)
    commands_tab.set_device_media_items([{ "name": "track_a.mp4", "size": 10 }])
    commands_tab.set_playlist_from_player(["track_a.mp4"], current_index=0)
    commands_tab._auto_apply_timer.setInterval(5)
    spy = QSignalSpy(commands_tab.playlistAutoApplyRequested)

    new_item = QListWidgetItem("track_b.mp4")
    new_item.setData(Qt.UserRole, "track_b.mp4")
    commands_tab._playlist.addItem(new_item)
    commands_tab._emit_playlist_changed()
    qapp.processEvents()

    if spy.count() == 0:
        spy.wait(200)
    assert spy.count() == 0


def test_display_center_pending_blocks_outdated_state(commands_tab: CommandsTab, qapp: QApplication) -> None:
    commands_tab.set_targets_selected(True)
    checkbox = commands_tab._display_center_checkbox
    checkbox.blockSignals(True)
    checkbox.setChecked(False)
    checkbox.blockSignals(False)

    spy = QSignalSpy(commands_tab.miscCommandTriggered)
    checkbox.click()
    qapp.processEvents()
    assert spy.count() == 1

    commands_tab.set_display_center(False, 1)
    qapp.processEvents()
    assert checkbox.isChecked() is True

    commands_tab._display_center_pending = (True, time.monotonic() - 1)
    commands_tab.set_display_center(False, 1)
    qapp.processEvents()
    assert checkbox.isChecked() is False


def test_display_center_pending_clears_on_ack(commands_tab: CommandsTab, qapp: QApplication) -> None:
    commands_tab.set_targets_selected(True)
    checkbox = commands_tab._display_center_checkbox
    checkbox.blockSignals(True)
    checkbox.setChecked(False)
    checkbox.blockSignals(False)

    checkbox.click()
    qapp.processEvents()
    assert commands_tab._display_center_pending is not None

    commands_tab.set_display_center(True, 1)
    qapp.processEvents()

    assert commands_tab._display_center_pending is None
    assert checkbox.isChecked() is True
