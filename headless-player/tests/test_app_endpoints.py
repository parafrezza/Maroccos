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
def fastapi_app(tmp_path_factory):
    _install_stubs()
    # Isola le cartelle di config/media su una directory temporanea per evitare
    # di leggere config reali dell'utente che potrebbero riabilitare OFF_AUTOSTART
    temp_root = tmp_path_factory.mktemp("headless_cfg")
    media_override = temp_root / "media"
    media_override.mkdir(parents=True, exist_ok=True)
    env_keys = ("LOCALAPPDATA", "APPDATA", "XDG_CONFIG_HOME", "MEDIA_DIR")
    env_backup = {k: os.environ.get(k) for k in env_keys}
    for key in ("LOCALAPPDATA", "APPDATA", "XDG_CONFIG_HOME"):
        os.environ[key] = str(temp_root)
    os.environ["MEDIA_DIR"] = str(media_override)
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
    os.environ["OFF_AUTOSTART"] = "0"
    spec = importlib.util.spec_from_file_location("app_module", str(app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    import builtins
    # evita esecuzioni di os.system/poweroff in /shutdown durante i test
    builtins._original_system = getattr(sys.modules.get('os'), 'system', None)
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
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


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


def test_backend_restart_endpoint_stops_and_reinits(fastapi_app, monkeypatch):
    import sys
    app_mod = sys.modules["app_module"]
    stop_called = {"count": 0}

    def fake_stop_play():
        stop_called["count"] += 1

    monkeypatch.setattr(app_mod, "stop_play", fake_stop_play, raising=False)

    class DummyBackend:
        def __init__(self):
            self.shutdown_count = 0

        def shutdown(self):
            self.shutdown_count += 1

    dummy_backend = DummyBackend()
    app_mod.current_framework["backend"] = dummy_backend
    app_mod.current_framework["name"] = "gst"
    init_calls: list[tuple[str, bool]] = []

    def fake_init(name: str, persist: bool = False):
        init_calls.append((name, persist))
        app_mod.current_framework["backend"] = DummyBackend()

    monkeypatch.setattr(app_mod, "init_backend", fake_init, raising=False)

    from fastapi.testclient import TestClient

    with TestClient(fastapi_app) as client:
        resp = client.post("/backend/restart")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("ok") is True
        assert body.get("scheduled") is False
        result = body.get("result") or {}
        assert result.get("backend") == "gst"

    assert stop_called["count"] == 1
    assert dummy_backend.shutdown_count == 1
    assert init_calls == [("gst", True)]


def test_backend_restart_endpoint_propagates_errors(fastapi_app, monkeypatch):
    import sys
    app_mod = sys.modules["app_module"]
    monkeypatch.setattr(app_mod, "stop_play", lambda: None, raising=False)
    monkeypatch.setattr(app_mod, "init_backend", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")), raising=False)

    from fastapi.testclient import TestClient

    with TestClient(fastapi_app) as client:
        resp = client.post("/backend/restart")
        assert resp.status_code == 500
        body = resp.json()
        assert body.get("ok") is False


def test_backend_restart_off_process_cycle(fastapi_app, monkeypatch):
    import sys
    app_mod = sys.modules["app_module"]

    class DummyBackend:
        def __init__(self) -> None:
            self.shutdown_calls = 0

        def shutdown(self) -> None:
            self.shutdown_calls += 1

    dummy_backend = DummyBackend()
    app_mod.current_framework["name"] = "off"
    app_mod.current_framework["backend"] = dummy_backend

    stop_play_calls = {"count": 0}

    def fake_stop_play() -> None:
        stop_play_calls["count"] += 1

    monkeypatch.setattr(app_mod, "stop_play", fake_stop_play, raising=False)

    off_stop_calls: list[dict] = []

    def fake_off_stop() -> dict:
        payload = {"ok": True, "running": False}
        off_stop_calls.append(payload)
        return payload

    monkeypatch.setattr(app_mod, "_off_stop", fake_off_stop, raising=False)

    off_start_calls: list[dict] = []

    def fake_off_start() -> dict:
        payload = {"ok": True, "running": True, "port": 8090}
        off_start_calls.append(payload)
        return payload

    monkeypatch.setattr(app_mod, "_off_start", fake_off_start, raising=False)
    monkeypatch.setattr(app_mod, "_wait_off_http_ready", lambda *a, **k: True, raising=False)

    init_calls: list[tuple[str, bool]] = []

    def fake_init(name: str, persist: bool = False) -> None:
        init_calls.append((name, persist))
        app_mod.current_framework["name"] = name
        app_mod.current_framework["backend"] = DummyBackend()

    monkeypatch.setattr(app_mod, "init_backend", fake_init, raising=False)

    from fastapi.testclient import TestClient

    with TestClient(fastapi_app) as client:
        resp = client.post("/backend/restart")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload.get("ok") is True
        assert payload.get("scheduled") is False
        result = payload.get("result") or {}
        assert result.get("off_stop") == {"ok": True, "running": False}
        assert result.get("off_start", {}).get("running") is True
        assert result.get("off_start_ready") is True
        assert result.get("errors") == []
    assert stop_play_calls["count"] == 1
    assert len(off_stop_calls) == 1
    assert len(off_start_calls) >= 1
    assert init_calls == [("off", True)]


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
    import sys
    app_mod = sys.modules["app_module"]

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
    from version_utils import aggregate_off_responses

    app_mod = importlib.import_module("app_module")
    with TestClient(fastapi_app) as client:
        # /off/version should return ok and running info
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
        assert ("off" in p2 and (p2["off"] is None or isinstance(p2["off"], str))) or ("off_raw" in p2)

    # Verify helper aggregation without starting OFF process
    headless = app_mod.VERSION
    vbody = '{"version":"v5.5.5"}'
    sbody = '{"player":{"version":"v9.9.9"}}'
    agg = aggregate_off_responses(headless, vbody, sbody)
    assert agg["off"] == "v5.5.5"
    assert agg["off_source"] == "version"
    agg2 = aggregate_off_responses(headless, None, sbody)
    assert agg2["off"] == "v9.9.9"
    assert agg2["off_source"] == "status"


def test_media_clear_requires_confirm(fastapi_app):
    from fastapi.testclient import TestClient

    with TestClient(fastapi_app) as client:
        resp = client.post("/media/clear")
        assert resp.status_code == 400
        payload = resp.json()
        assert payload.get("ok") is False
        assert "Conferma" in payload.get("error", "")


def test_media_clear_force_off_restarts(monkeypatch, fastapi_app):
    from fastapi.testclient import TestClient
    import importlib

    app_mod = importlib.import_module("app_module")
    media_dir = app_mod.MEDIA_DIR
    media_dir.mkdir(exist_ok=True)
    target = media_dir / "locked.mp4"
    _write_fake_mp4(target)

    stop_calls: dict[str, dict] = {}

    def fake_stop(timeout: float = 3.0, force: bool = True, stop_off: bool = True):
        stop_calls["kwargs"] = {"timeout": timeout, "force": force, "stop_off": stop_off}
        return {"playback_stopped": True, "off_player_stopped": True}

    restart_tracker = {"count": 0}

    def fake_start(*args, **kwargs):
        restart_tracker["count"] += 1
        return {"ok": True}

    monkeypatch.setattr(app_mod, "_stop_active_media", fake_stop, raising=False)
    monkeypatch.setattr(app_mod, "_off_start", fake_start, raising=False)

    with TestClient(fastapi_app) as client:
        resp = client.post(
            "/media/clear",
            params={"confirm": 1, "force_off": 1, "restart_off": 1},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("ok") is True
        assert body.get("off_player_stopped") is True
        assert body.get("off_player_restarted") is True
        assert body.get("removed", 0) >= 1

    assert stop_calls["kwargs"]["stop_off"] is True
    assert restart_tracker["count"] == 1
    assert not target.exists()
