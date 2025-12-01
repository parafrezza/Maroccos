import importlib.util
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _make_fake_mp4(path: Path) -> None:
    # Minimal header con ftyp per superare validate_media_file
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42isom\x00\x00\x00\x00")


def _load_app(tmp_path: Path, monkeypatch) -> object:
    cfg_root = tmp_path / "config_root"
    cfg_root.mkdir(exist_ok=True)
    media_dir = tmp_path / "media"
    media_dir.mkdir(exist_ok=True)
    root_dir = Path(__file__).resolve().parent.parent
    if str(root_dir) not in sys.path:
        sys.path.insert(0, str(root_dir))
    # Evita bootstrap e autoplay
    monkeypatch.setenv("LOCALAPPDATA", str(cfg_root))
    monkeypatch.setenv("APPDATA", str(cfg_root))
    monkeypatch.setenv("MEDIA_DIR", str(media_dir))
    monkeypatch.setenv("AUTOPLAY_ENABLED", "0")
    monkeypatch.setenv("BOOTSTRAP_PLAYLIST", "0")
    app_path = Path(__file__).resolve().parent.parent / "app.py"
    spec = importlib.util.spec_from_file_location("headless_player_app", app_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["headless_player_app"] = module
    spec.loader.exec_module(module)  # type: ignore[call-arg]
    # Override percorsi config/media
    module.CONFIG_DIR = cfg_root
    module.CONFIG_FILE = cfg_root / "config.json"
    module.DEVICE_ID_FILE = cfg_root / "device_id"
    module.MEDIA_DIR = media_dir
    module.playlist["items"] = []
    module.playlist["index"] = -1
    module.playlist.pop("fingerprint", None)
    module.VIDEO_PATH = None
    return module


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    app_mod = _load_app(tmp_path, monkeypatch)
    return TestClient(app_mod.app)


def test_manual_bootstrap_disabled_by_default(client: TestClient, tmp_path, monkeypatch):
    app_mod = _load_app(tmp_path, monkeypatch)
    app_mod._autoplay_prepare_manual_bootstrap()
    assert app_mod.playlist["items"] == []
    assert app_mod.playlist["index"] == -1


def test_clear_playlist_endpoint(client: TestClient, tmp_path):
    media = Path(os.environ.get("MEDIA_DIR", tmp_path / "media"))
    _make_fake_mp4(media / "a.mp4")
    _make_fake_mp4(media / "b.mp4")
    resp = client.post("/playlist/apply", json={"items": ["a.mp4", "b.mp4"], "loop": True})
    assert resp.status_code == 200
    status = client.get("/playlist/status").json()
    assert status["items"]
    resp = client.post("/playlist/clear")
    assert resp.status_code == 200
    status = client.get("/playlist/status").json()
    assert status["items"] == []
    assert status["index"] == -1
    assert status["current"] is None


def test_stop_resets_index_to_first(client: TestClient, tmp_path):
    media = Path(os.environ.get("MEDIA_DIR", tmp_path / "media"))
    _make_fake_mp4(media / "a.mp4")
    _make_fake_mp4(media / "b.mp4")
    resp = client.post("/playlist/apply", json={"items": ["a.mp4", "b.mp4"], "loop": True})
    assert resp.status_code == 200
    client.post("/playlist/jump", params={"index": 1})
    resp = client.post("/stop")
    assert resp.status_code == 200
    status = client.get("/playlist/status").json()
    assert status["index"] == 0
    assert status["current"] == str(media / "a.mp4")


def test_status_reflects_playlist_ready_fields(tmp_path, monkeypatch):
    app_mod = _load_app(tmp_path, monkeypatch)
    media_dir = app_mod.MEDIA_DIR
    media_dir.mkdir(exist_ok=True)
    _make_fake_mp4(media_dir / "ready.mp4")

    with TestClient(app_mod.app) as client:
        resp = client.post("/playlist/apply", json={"items": ["ready.mp4"], "loop": True})
        assert resp.status_code == 200
        assert resp.json().get("ok") is True

        status = client.get("/status")
        assert status.status_code == 200
        payload = status.json()

    assert payload.get("playlist_ready") is True
    assert payload.get("playlist_missing") == []
    assert payload.get("playlist_invalid") == []
    assert payload.get("playlist_hash")
    assert payload.get("playlist_loop") is True


def test_status_reports_missing_and_invalid_playlist_entries(tmp_path, monkeypatch):
    app_mod = _load_app(tmp_path, monkeypatch)
    media_dir = app_mod.MEDIA_DIR
    media_dir.mkdir(exist_ok=True)
    missing_path = media_dir / "gone.mp4"
    invalid_path = media_dir / "corrupted.mp4"
    _make_fake_mp4(missing_path)
    _make_fake_mp4(invalid_path)

    with TestClient(app_mod.app) as client:
        resp = client.post(
            "/playlist/apply",
            json={"items": [missing_path.name, invalid_path.name], "loop": False},
        )
        assert resp.status_code == 200
        assert resp.json().get("ok") is True

        # Simula drift: un file eliminato e l'altro trasformato in directory (lettura fallirà)
        missing_path.unlink()
        invalid_path.unlink()
        invalid_path.mkdir()

        status = client.get("/status")
        assert status.status_code == 200
        payload = status.json()

    assert payload.get("playlist_ready") is False
    assert payload.get("playlist_loop") is False
    assert payload.get("playlist_missing") == [missing_path.name]
    invalid_entries = payload.get("playlist_invalid") or []
    assert any(entry.startswith(f"{invalid_path.name}:") for entry in invalid_entries)
    assert payload.get("playlist_hash")
