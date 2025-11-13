import sys
import types
from pathlib import Path

import pytest


def _install_minimal_stubs():
    # gi / GLib / Gst minimal stubs to allow importing app.py
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

    # netifaces stub
    netifaces = types.ModuleType("netifaces")
    netifaces.AF_INET = 2
    def interfaces():
        return ["eth0"]
    def ifaddresses(i):
        return {2: [{"addr": "192.168.1.200"}]}
    netifaces.interfaces = interfaces
    netifaces.ifaddresses = ifaddresses
    sys.modules["netifaces"] = netifaces

    # PIL minimal stubs used by app.py
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
    _install_minimal_stubs()
    # Dynamic import of FastAPI app
    base = Path(__file__).resolve().parents[1]
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    app_path = base / "app.py"
    import importlib.util
    spec = importlib.util.spec_from_file_location("app_module_time", str(app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["app_module_time"] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod.app


def test_time_endpoint_present_and_reasonable(fastapi_app):
    from fastapi.testclient import TestClient
    import time
    with TestClient(fastapi_app) as client:
        r = client.get("/time")
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        now_ns = int(body.get("now_ns"))
        assert now_ns > 10**12  # plausible epoch in ns
        # Within +/- 5 seconds of local time
        diff_s = abs((now_ns - int(time.time() * 1e9)) / 1e9)
        assert diff_s < 5.0
