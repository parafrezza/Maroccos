import sys
import types
from pathlib import Path

import pytest
import os

# Ensure OFF autostart is disabled globally for test runs to avoid starting external binaries
os.environ["OFF_AUTOSTART"] = "0"


def _install_stubs():
    # ---- gi / GStreamer stubs ----
    class DummyBuffer:
        @staticmethod
        def new_allocate(a, size, b):
            class B:
                def __init__(self):
                    self.pts = 0
                    self.dts = 0
                def fill(self, off, data):
                    return True
            return B()

    class DummyBus:
        def add_signal_watch(self):
            pass
        def connect(self, *args, **kwargs):
            pass

    class DummyIter:
        def next(self):
            return (False, None)

    class DummyPipeline:
        def set_state(self, state):
            return True
        def get_by_name(self, name):
            return None
        def get_bus(self):
            return DummyBus()
        def iterate_elements(self):
            return DummyIter()
        def seek_simple(self, *args, **kwargs):
            return True

    class DummyGst:
        SECOND = 1000000000
        class MessageType:
            EOS = 1
            ERROR = 2
            WARNING = 3
            INFO = 4
        class State:
            NULL = 0
            PLAYING = 1
            PAUSED = 2
        class SeekFlags:
            FLUSH = 1
            KEY_UNIT = 2
        class Format:
            TIME = 0
        # For type annotation in app: Gst.Pipeline
        Pipeline = DummyPipeline
        Buffer = DummyBuffer
        @staticmethod
        def init(a):
            return None
        @staticmethod
        def parse_launch(desc):
            return DummyPipeline()

    class DummyGLib:
        class MainLoop:
            def run(self):
                return None
            def quit(self):
                return None
        @staticmethod
        def idle_add(func, *args, **kwargs):
            try:
                func(*args, **kwargs)
            except Exception:
                pass
            return 1
        @staticmethod
        def timeout_add(ms, func, *args):
            try:
                func()
            except Exception:
                pass
            return 1

    gi = types.ModuleType("gi")
    def require_version(name, ver):
        return None
    gi.require_version = require_version
    repo = types.ModuleType("gi.repository")
    repo.Gst = DummyGst
    repo.GObject = object
    repo.GLib = DummyGLib
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repo
    sys.modules["gi.repository.Gst"] = DummyGst
    sys.modules["gi.repository.GLib"] = DummyGLib

    # ---- netifaces stub ----
    netifaces = types.ModuleType("netifaces")
    netifaces.AF_INET = 2
    def interfaces():
        return ["eth0"]
    def ifaddresses(i):
        return {2: [{"addr": "192.168.1.200"}]}
    netifaces.interfaces = interfaces
    netifaces.ifaddresses = ifaddresses
    sys.modules["netifaces"] = netifaces

    # ---- PIL stub ----
    PIL = types.ModuleType("PIL")
    Image = types.ModuleType("Image")
    ImageDraw = types.ModuleType("ImageDraw")
    ImageFont = types.ModuleType("ImageFont")
    class _Img:
        def tobytes(self):
            return b""
    def new(mode, size, color):
        return _Img()
    def Draw(img):
        class D:
            def multiline_textbbox(self, *a, **k):
                return (0,0,10,10)
            def multiline_textsize(self, *a, **k):
                return (10,10)
            def multiline_text(self, *a, **k):
                return None
        return D()
    def truetype(*a, **k):
        class F: pass
        return F()
    def load_default():
        class F: pass
        return F()
    Image.new = new
    ImageDraw.Draw = Draw
    ImageFont.truetype = truetype
    ImageFont.load_default = load_default
    sys.modules["PIL"] = PIL
    sys.modules["PIL.Image"] = Image
    sys.modules["PIL.ImageDraw"] = ImageDraw
    sys.modules["PIL.ImageFont"] = ImageFont


@pytest.fixture(scope="module")
def fastapi_app():
    _install_stubs()
    # Import dinamico dell'app FastAPI
    base = Path(__file__).resolve().parents[1]
    # Assicura che import assoluti come 'backends' risolvano dalla root dell'app
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    app_path = base / "app.py"
    import importlib.util
    import threading
    # Prevent app from spawning background threads (autostart, watchdog) during module import
    orig_thread_class = threading.Thread
    class _DummyThread:
        def __init__(self, *args, **kwargs):
            self._target = kwargs.get('target') if 'target' in kwargs else (args[0] if args else None)
            self._args = kwargs.get('args', ())
            self._kwargs = kwargs.get('kwargs', {})
            self.daemon = kwargs.get('daemon', True)
        def start(self):
            # do not run target synchronously; avoid side-effects during import
            return None
        def join(self, *a, **k):
            return None
    threading.Thread = _DummyThread
    # Ensure OFF autostart is disabled during tests to avoid launching external binary
    import os
    os.environ["OFF_AUTOSTART"] = "0"
    spec = importlib.util.spec_from_file_location("app_module", str(app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    import builtins
    # evita esecuzioni di os.system/poweroff in /shutdown durante i test
    builtins._original_system = getattr(sys.modules.get('os'), 'system', None)
    import os
    os.system = lambda *a, **k: 0
    sys.modules["app_module"] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    # restore native threading.Thread after module import
    threading.Thread = orig_thread_class
    # Evita splash al startup
    mod.show_splash_until_play = lambda: None
    try:
        yield mod.app
    finally:
        try:
            mod.request_headless_exit("pytest cleanup")
        except Exception:
            pass


def test_framework_get_and_change(fastapi_app):
    from fastapi.testclient import TestClient
    with TestClient(fastapi_app) as client:
        r = client.get("/framework")
        assert r.status_code == 200
        payload = r.json()
        assert payload.get("ok") is True
        assert "current" in payload and "available" in payload
        # change to same framework (no-op)
        r2 = client.post("/change_framework", json={"name": payload["current"]})
        assert r2.status_code == 200
        assert r2.json().get("ok") is True


def test_ping_and_status(fastapi_app):
    from fastapi.testclient import TestClient
    with TestClient(fastapi_app) as client:
        r = client.post("/ping", params={"duration_ms": 100})
        assert r.status_code == 200
        assert r.json().get("ok") is True
        st = client.get("/status")
        assert st.status_code == 200
        assert "player_state" in st.json()


def test_status_loop_defaults_enabled(fastapi_app):
    from fastapi.testclient import TestClient
    with TestClient(fastapi_app) as client:
        st = client.get("/status")
        assert st.status_code == 200
        data = st.json()
        assert data.get("loop_enabled") is True
        assert data.get("playlist_loop") is True


def test_status_syncs_off_center_flag(monkeypatch, fastapi_app):
    import importlib

    app_mod = importlib.import_module("app_module")
    center_state = {"value": True}

    def _fake_off_status():
        return {"ok": True, "player": {"centerVideo": center_state["value"]}}

    monkeypatch.setattr(app_mod, "off_status", _fake_off_status)

    from fastapi.testclient import TestClient

    with TestClient(fastapi_app) as client:
        center_state["value"] = True
        resp = client.get("/status")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload.get("display_center", {}).get("enabled") is True
        assert app_mod.DISPLAY_CENTER_VIDEO is True

        center_state["value"] = False
        resp2 = client.get("/status")
        assert resp2.status_code == 200
        payload2 = resp2.json()
        assert payload2.get("display_center", {}).get("enabled") is False
        assert app_mod.DISPLAY_CENTER_VIDEO is False


def test_autoplay_disabling_restores_loop(fastapi_app):
    import importlib
    app_mod = importlib.import_module("app_module")
    # Simula autoplay che ha forzato loop off
    app_mod.player["loop"] = False
    app_mod.autoplay["enabled"] = True
    app_mod.autoplay["forced_loop_prev"] = True
    app_mod._autoplay_set_enabled(False)
    assert app_mod.player["loop"] is True
    assert app_mod.autoplay.get("forced_loop_prev") is None


def test_status_exposes_udp_lock(fastapi_app):
    from fastapi.testclient import TestClient
    with TestClient(fastapi_app) as client:
        resp = client.get("/status")
        assert resp.status_code == 200
        payload = resp.json()
        udp = payload.get("udp") or {}
        assert udp.get("configured_port") == 7777
        assert udp.get("locked") is True
        assert udp.get("enabled") is True


def test_manual_bootstrap_disabled_when_autoplay_off(fastapi_app, monkeypatch):
    import app_module as app_mod

    # Simula scenario con autoplay disabilitato e media presenti
    app_mod.autoplay["enabled"] = False
    app_mod.playlist["items"] = []
    app_mod.playlist["index"] = -1
    fake_items = ["file1.mp4", "file2.mp4"]
    monkeypatch.setattr(app_mod, "_autoplay_collect_media", lambda: list(fake_items))

    app_mod._autoplay_prepare_manual_bootstrap()

    # Poiché BOOTSTRAP_PLAYLIST è disabilitato, la playlist non deve essere popolata
    assert app_mod.playlist["items"] == []
    assert app_mod.playlist["index"] == -1


def test_settings_reload_toggle_flag(fastapi_app):
    from fastapi.testclient import TestClient
    with TestClient(fastapi_app) as client:
        r = client.post("/settings/reload", json={"SPLASH_BLACK": True, "restart_play": False})
        assert r.status_code == 200
        assert r.json().get("ok") is True


def _write_fake_mp4(path: Path):
    # Crea un header con 'ftyp' a offset 4
    data = bytearray(16)
    data[4:8] = b"ftyp"
    path.write_bytes(bytes(data))


def _write_fake_png(path: Path):
    header = b"\x89PNG\r\n\x1a\n"
    path.write_bytes(header + b"\x00" * 16)


def test_play_immediate_with_filename(fastapi_app, tmp_path):
    from fastapi.testclient import TestClient
    # crea un file video valido in media/
    import importlib
    app_mod = importlib.import_module("app_module")
    media_dir = app_mod.MEDIA_DIR
    media_dir.mkdir(exist_ok=True)
    f = media_dir / "clip.mp4"
    _write_fake_mp4(f)
    with TestClient(fastapi_app) as client:
        r = client.post("/play", json={"filename": "clip.mp4", "loop": False, "fade_in_seconds": 0.1})
        assert r.status_code == 200
        assert r.json().get("ok") is True


def test_play_respects_existing_loop_state(fastapi_app, tmp_path):
    from fastapi.testclient import TestClient
    import importlib
    app_mod = importlib.import_module("app_module")
    media_dir = app_mod.MEDIA_DIR
    media_dir.mkdir(exist_ok=True)
    f = media_dir / "loop_guard.mp4"
    _write_fake_mp4(f)
    with TestClient(fastapi_app) as client:
        resp = client.post("/loop", params={"on": 0})
        assert resp.status_code == 200
        assert resp.json().get("loop") is False
        play = client.post("/play", json={"filename": f.name})
        assert play.status_code == 200
        status = client.get("/status")
        assert status.status_code == 200
        payload = status.json()
        assert payload.get("loop_enabled") is False
    assert app_mod.player.get("loop") is False


def test_image_loop_toggle_updates_timer(fastapi_app, tmp_path):
    from fastapi.testclient import TestClient
    import importlib

    app_mod = importlib.import_module("app_module")
    media_dir = app_mod.MEDIA_DIR
    media_dir.mkdir(exist_ok=True)
    img = media_dir / "loop_image.png"
    _write_fake_png(img)
    app_mod.image_duration_state["seconds"] = 5.0

    with TestClient(fastapi_app) as client:
        client.post("/loop", params={"on": 0})
        play = client.post("/play", json={"filename": img.name})
        assert play.status_code == 200
        assert app_mod.image_duration_state.get("path", "").endswith(img.name)
        assert app_mod.image_duration_state.get("timer") is not None

        client.post("/loop", params={"on": 1})
        status = client.get("/status")
        info = status.json().get("image_duration") or {}
        assert info.get("path", "").endswith(img.name)
        assert info.get("deadline") is None
        assert app_mod.image_duration_state.get("timer") is None

        client.post("/loop", params={"on": 0})
        assert app_mod.image_duration_state.get("timer") is not None
        client.post("/stop")


def test_play_scheduled_with_in_time(fastapi_app):
    from fastapi.testclient import TestClient
    import time
    import importlib
    app_mod = importlib.import_module("app_module")
    media_dir = app_mod.MEDIA_DIR
    media_dir.mkdir(exist_ok=True)
    f = media_dir / "sched.mp4"
    _write_fake_mp4(f)
    start_at = time.time() + 1.0
    with TestClient(fastapi_app) as client:
        r = client.post("/play", json={"filename": "sched.mp4", "in_time": start_at, "fade_out_seconds": 0.2})
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert body.get("scheduled") is True


def test_playlist_apply_sets_show_ready(fastapi_app):
    from fastapi.testclient import TestClient
    import importlib
    app_mod = importlib.import_module("app_module")
    media_dir = app_mod.MEDIA_DIR
    media_dir.mkdir(exist_ok=True)
    f1 = media_dir / "a.mp4"
    f2 = media_dir / "b.mp4"
    _write_fake_mp4(f1)
    _write_fake_mp4(f2)
    with TestClient(fastapi_app) as client:
        r = client.post("/playlist/apply", json={"items": ["a.mp4", "b.mp4"], "loop": False})
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert body.get("prepared")
        st = client.get("/status")
        assert st.status_code == 200
        sbody = st.json()
        assert sbody.get("show_ready") is True


def test_upload_asset_stores_file_exact_bytes(fastapi_app):
    from fastapi.testclient import TestClient
    import importlib
    app_mod = importlib.import_module("app_module")
    media_dir = app_mod.MEDIA_DIR
    media_dir.mkdir(exist_ok=True)
    target = media_dir / "uploaded_test.bin"
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    payload = b"media-bytes"
    with TestClient(fastapi_app) as client:
        resp = client.post(
            "/upload_asset",
            params={"filename": target.name},
            files={"file": ("clip.bin", payload, "application/octet-stream")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("ok") is True
        assert body.get("size") == len(payload)
        assert target.exists()
        assert target.read_bytes() == payload
    target.unlink(missing_ok=True)


def test_upload_asset_rejects_directory_escape(fastapi_app):
    from fastapi.testclient import TestClient
    with TestClient(fastapi_app) as client:
        resp = client.post(
            "/upload_asset",
            params={"filename": "../forbidden.bin"},
            files={"file": ("evil.bin", b"x", "application/octet-stream")},
        )
        assert resp.status_code == 400
        payload = resp.json()
        assert payload.get("ok") is False
        assert "non valido" in payload.get("error", "") or "non consentito" in payload.get("error", "")


def test_media_prune_to_playlist_removes_extra_files(fastapi_app):
    from fastapi.testclient import TestClient
    import importlib
    app_mod = importlib.import_module("app_module")
    media_dir = app_mod.MEDIA_DIR
    media_dir.mkdir(exist_ok=True)
    keep = media_dir / "keep_clip.mp4"
    drop = media_dir / "drop_clip.mp4"
    _write_fake_mp4(keep)
    _write_fake_mp4(drop)
    with TestClient(fastapi_app) as client:
        resp = client.post("/media/prune_to_playlist", json={"items": [keep.name]})
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("ok") is True
        assert body.get("removed", 0) >= 1
    assert keep.exists()
    assert not drop.exists()

def test_off_version_endpoints(fastapi_app):
    from fastapi.testclient import TestClient
    import importlib
    app_mod = importlib.import_module("app_module")
    with TestClient(fastapi_app) as client:
            # /off/version should return ok and running info, if off backend is running it should include player or player_raw
            r = client.get("/off/version")
            assert r.status_code == 200
            payload = r.json()
            assert payload.get("ok") is True
            assert "running" in payload
            # /version should include headless version and may include off or off_raw
            r2 = client.get("/version")
            assert r2.status_code == 200
            p2 = r2.json()
            assert "headless" in p2 and isinstance(p2["headless"], str)
            assert p2["headless"] == app_mod.VERSION
            # off could be None or a version string, confirm shape
            assert ("off" in p2 and (p2["off"] is None or isinstance(p2["off"], str))) or ("off_raw" in p2)

    # Verify fallback behavior using aggregate helper directly (without starting OFF process)
    from version_utils import aggregate_off_responses
    headless = app_mod.VERSION
    # prefer /version when available
    vbody = '{"version":"v5.5.5"}'
    sbody = '{"player":{"version":"v9.9.9"}}'
    agg = aggregate_off_responses(headless, vbody, sbody)
    assert agg["off"] == "v5.5.5"
    assert agg["off_source"] == "version"
    # fallback to status if version absent
    agg2 = aggregate_off_responses(headless, None, sbody)
    assert agg2["off"] == "v9.9.9"
    assert agg2["off_source"] == "status"
