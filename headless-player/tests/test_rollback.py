import io, os, zipfile, importlib, time
from pathlib import Path
from fastapi.testclient import TestClient
import pytest


def make_zip(tmp_path: Path, files: dict[str, bytes]) -> Path:
    zpath = tmp_path / "update.zip"
    with zipfile.ZipFile(str(zpath), 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    return zpath


@pytest.fixture(scope="module")
def app_mod():
    # Riusa lo stesso meccanismo del test updates_flow per importare il modulo app in modo isolato
    import sys, types, importlib.util, builtins
    # stubs minimi per gi/glib/gst e netifaces
    class DummyBuffer:
        @staticmethod
        def new_allocate(a, size, b):
            class B:
                def __init__(self):
                    self.pts = 0; self.dts = 0
                def fill(self, off, data):
                    return True
            return B()
    class DummyBus:
        def add_signal_watch(self): pass
        def connect(self, *a, **k): pass
    class DummyIter:
        def next(self): return (False, None)
    class DummyPipeline:
        def set_state(self, s): return True
        def get_by_name(self, n): return None
        def get_bus(self): return DummyBus()
        def iterate_elements(self): return DummyIter()
        def seek_simple(self, *a, **k): return True
    class DummyGst:
        SECOND = 1000000000
        class MessageType: ERROR=0; EOS=1; WARNING=2; INFO=3
        class State: NULL=0; PLAYING=1; PAUSED=2
        class SeekFlags: FLUSH=1; KEY_UNIT=2
        class Format: TIME=0
        Pipeline = DummyPipeline; Buffer = DummyBuffer
        @staticmethod
        def init(a): return None
        @staticmethod
        def parse_launch(desc): return DummyPipeline()
    class DummyGLib:
        class MainLoop:
            def run(self): return None
            def quit(self): return None
        @staticmethod
        def idle_add(func, *args, **kwargs):
            try: func(*args, **kwargs)
            except Exception: pass
            return 1
        @staticmethod
        def timeout_add(ms, func, *args):
            try: func(*args)
            except Exception: pass
            return 1
    gi = types.ModuleType("gi")
    def require_version(name, ver): return None
    gi.require_version = require_version
    repo = types.ModuleType("gi.repository")
    repo.Gst = DummyGst; repo.GLib = DummyGLib; repo.GObject = object
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repo
    sys.modules["gi.repository.Gst"] = DummyGst
    sys.modules["gi.repository.GLib"] = DummyGLib
    # netifaces stub
    netifaces = types.ModuleType("netifaces")
    netifaces.AF_INET = 2
    def interfaces(): return ["eth0"]
    def ifaddresses(i): return {2: [{"addr": "10.0.0.5"}]}
    netifaces.interfaces = interfaces; netifaces.ifaddresses = ifaddresses
    sys.modules["netifaces"] = netifaces
    # PIL stub minimo
    PIL = types.ModuleType("PIL")
    Image = types.ModuleType("Image"); ImageDraw = types.ModuleType("ImageDraw"); ImageFont = types.ModuleType("ImageFont")
    class _Img:
        def tobytes(self): return b""
    def new(mode, size, color): return _Img()
    def Draw(img):
        class D:
            def multiline_textbbox(self,*a,**k): return (0,0,10,10)
            def multiline_textsize(self,*a,**k): return (10,10)
            def multiline_text(self,*a,**k): return None
        return D()
    def truetype(*a,**k):
        class F: pass
        return F()
    def load_default():
        class F: pass
        return F()
    Image.new=new; ImageDraw.Draw=Draw; ImageFont.truetype=truetype; ImageFont.load_default=load_default
    sys.modules["PIL"] = PIL; sys.modules["PIL.Image"] = Image; sys.modules["PIL.ImageDraw"] = ImageDraw; sys.modules["PIL.ImageFont"] = ImageFont

    base = Path(__file__).resolve().parents[1]
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    app_path = base / "app.py"
    spec = importlib.util.spec_from_file_location("app_update_module_rb", str(app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["app_update_module_rb"] = mod
    spec.loader.exec_module(mod)  # type: ignore
    mod.show_splash_until_play = lambda: None
    return mod


def test_apply_rollback_on_copy_error(tmp_path: Path, app_mod):
    mod = app_mod
    # Reindirizza APP_DIR su cartella temporanea
    app_dir = tmp_path / "appdir"
    app_dir.mkdir(parents=True, exist_ok=True)
    mod.APP_DIR = app_dir
    # Preesistente a.txt
    (app_dir / "a.txt").write_text("OLD", encoding="utf-8")
    # Zip con a.txt (nuovo) e b.txt (fallisce)
    z = make_zip(tmp_path, {"a.txt": b"NEW", "b.txt": b"DATA"})

    # Monkeypatch shutil.copy2 per fallire su b.txt
    import shutil as _sh
    real_copy2 = _sh.copy2
    def fake_copy2(src, dst, *a, **k):
        if str(src).endswith("b.txt"):
            raise IOError("simulated copy error")
        return real_copy2(src, dst, *a, **k)
    _sh.copy2 = fake_copy2
    try:
        with pytest.raises(RuntimeError):
            mod.apply_update(z)
    finally:
        _sh.copy2 = real_copy2
    # Verifica rollback: a.txt deve rimanere OLD, b.txt non creato
    assert (app_dir / "a.txt").read_text(encoding="utf-8") == "OLD"
    assert not (app_dir / "b.txt").exists()


def test_apply_success_creates_backup(tmp_path: Path, app_mod):
    mod = app_mod
    app_dir = tmp_path / "appdir2"
    app_dir.mkdir(parents=True, exist_ok=True)
    old = app_dir / "conf.ini"
    old.write_text("param=1", encoding="utf-8")
    z = make_zip(tmp_path, {"conf.ini": b"param=2", "new.txt": b"x"})
    mod.APP_DIR = app_dir
    mod.apply_update(z)
    # Contenuto aggiornato
    assert (app_dir / "conf.ini").read_text(encoding="utf-8") == "param=2"
    assert (app_dir / "new.txt").read_text(encoding="utf-8") == "x"
    # Backup esposto
    bdir = Path(mod.current_update.get("backup_dir") or "")
    assert bdir and bdir.exists()
    # Il backup dell'originale dovrebbe esistere
    backup_conf = list(bdir.rglob("conf.ini"))
    assert backup_conf, "Backup del file sostituito assente"
    assert backup_conf[0].read_text(encoding="utf-8") == "param=1"
