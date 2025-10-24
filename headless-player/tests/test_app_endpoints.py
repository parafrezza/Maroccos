import sys
import types
from pathlib import Path

import pytest


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
    # Evita splash al startup
    mod.show_splash_until_play = lambda: None
    return mod.app


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
