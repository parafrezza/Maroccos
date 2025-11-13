import sys, types, time, json
from pathlib import Path
import pytest

# Reuse stub installer similar to other tests to avoid heavy deps

def _install_stubs():
    class DummyBuffer:
        @staticmethod
        def new_allocate(a, size, b):
            class B:
                def __init__(self):
                    self.pts = 0; self.dts = 0
                def fill(self, off, data):
                    return True
            return B()
    class DummyBus:  # minimal
        def add_signal_watch(self): pass
        def connect(self, *a, **k): pass
    class DummyIter:  # minimal
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
    # PIL stub
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

@pytest.fixture(scope="module")
def fastapi_app():
    _install_stubs()
    base = Path(__file__).resolve().parents[1]
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    import importlib.util, builtins, os
    app_path = base / "app.py"
    spec = importlib.util.spec_from_file_location("app_update_module", str(app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    builtins._original_system = getattr(sys.modules.get('os'), 'system', None)
    os.system = lambda *a, **k: 0
    sys.modules["app_update_module"] = mod
    spec.loader.exec_module(mod)  # type: ignore
    mod.show_splash_until_play = lambda: None
    return mod.app

def test_components_endpoint(fastapi_app):
    from fastapi.testclient import TestClient
    with TestClient(fastapi_app) as client:
        r = client.get("/components")
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        comps = body.get("components") or {}
        assert "headless_player" in comps
        assert comps["headless_player"].get("version")

def test_update_plan_validation(fastapi_app):
    from fastapi.testclient import TestClient
    with TestClient(fastapi_app) as client:
        r = client.post("/update/plan", json={"assets": []})
        assert r.status_code == 400
        # piano valido
        dummy_asset = {"id": "headless", "url": "http://example.invalid/dummy.bin", "size": 10}
        r2 = client.post("/update/plan", json={"assets": [dummy_asset]})
        assert r2.status_code == 200
        assert r2.json().get("ok") is True
        st = client.get("/status")
        assert st.status_code == 200
        sbody = st.json()
        assert sbody.get("update_stage") == "planned"
        assert sbody.get("update_plan")

def test_update_fetch_flow_monkeypatched(fastapi_app, tmp_path):
    """Simula download multi-asset senza rete, verifica transizioni e progress."""
    from fastapi.testclient import TestClient
    import importlib
    mod = importlib.import_module("app_update_module")
    # Monkeypatch _download_with_progress per creare file fittizi e progress artificiale
    def fake_download(url: str, dest: Path, on_progress=None):
        total = 100
        for done in (10, 40, 70, 100):
            if on_progress:
                on_progress(done, total)
            time.sleep(0.01)
        # Crea un archivio zip valido così apply_update non fallisce
        import zipfile, io
        with zipfile.ZipFile(str(dest), 'w') as z:
            z.writestr('dummy.txt', 'hello')
        # Aggiorna size dichiarata se necessario
        return dest
    mod._download_with_progress = fake_download  # type: ignore
    # Prepara piano con asset locale
    assets = [
        {"id": "headless", "url": "http://invalid.local/headless.zip", "size": 100, "sha256": None, "type": "zip"},
        {"id": "provision", "url": "http://invalid.local/provision.ps1", "size": 100, "sha256": None, "type": "txt"},
    ]
    with TestClient(fastapi_app) as client:
        rp = client.post("/update/plan", json={"assets": assets})
        assert rp.status_code == 200
        rf = client.post("/update/fetch", json={"start_immediately": True})
        assert rf.status_code == 200 and rf.json().get("started") is True
        # Poll fino a ok o error (timeout di sicurezza)
        deadline = time.time() + 5
        stage_seen = set()
        final = None
        while time.time() < deadline:
            st = client.get("/status")
            assert st.status_code == 200
            body = st.json(); stage = body.get("update_stage")
            if stage: stage_seen.add(stage)
            if stage in {"ok", "error"}: final = stage; break
            time.sleep(0.05)
        assert final == "ok", f"Final stage {final} unexpected; stages visited: {stage_seen}"
        assert "downloading" in stage_seen and "applying" in stage_seen
        assert body.get("update_assets") and len(body["update_assets"]) == 2
        # progress deve essere 100
        assert body.get("update_progress") in (100, 100.0)

def test_sha256_mismatch_triggers_error(fastapi_app, tmp_path):
    from fastapi.testclient import TestClient
    import importlib, hashlib
    mod = importlib.import_module("app_update_module")
    def fake_download(url: str, dest: Path, on_progress=None):
        dest.write_bytes(b"ABCDEFG")
        if on_progress:
            on_progress(7, 7)
        return dest
    mod._download_with_progress = fake_download  # type: ignore
    bad_hash = "0" * 64
    assets = [{"id": "bad", "url": "http://invalid.local/bad.bin", "size": 7, "sha256": bad_hash, "type": "bin"}]
    with TestClient(fastapi_app) as client:
        client.post("/update/plan", json={"assets": assets})
        client.post("/update/fetch", json={"start_immediately": True})
        deadline = time.time() + 3
        final_error = None
        while time.time() < deadline:
            st = client.get("/status")
            body = st.json(); stage = body.get("update_stage")
            if stage == "error":
                final_error = body.get("update_error") or body.get("update_log", [])[-1]
                break
            time.sleep(0.05)
        assert final_error, "Sha256 mismatch non ha generato errore"
        assert "sha256" in final_error.lower()

def test_windows_zip_elevated_apply_path(fastapi_app, tmp_path, monkeypatch):
    """Verifica che su 'windows' un asset zip in /update/fetch imposti stage 'restarting' e campo 'elevated'.
    Non esegue realmente PowerShell: monkeypatch helper e os._exit.
    """
    from fastapi.testclient import TestClient
    import importlib, time, types
    mod = importlib.import_module("app_update_module")

    # Forza ambiente windows
    monkeypatch.setenv("PYTEST_FAKE_WINDOWS", "1")
    monkeypatch.setattr(mod.os, "name", "nt", raising=False)

    # Monkeypatch download: crea zip reale
    def fake_download(url: str, dest: Path, on_progress=None):
        import zipfile
        with zipfile.ZipFile(str(dest), 'w') as z:
            z.writestr('file.txt', 'data')
        if on_progress:
            on_progress(50, 50)
        return dest
    mod._download_with_progress = fake_download  # type: ignore

    # Monkeypatch elevated helper: registra chiamata senza lanciare nulla
    calls = {}
    def fake_elevated(zip_path, target_dir, restart=True):
        calls['zip_path'] = str(zip_path)
        calls['target_dir'] = str(target_dir)
        calls['restart'] = restart
        return {"ok": True, "script": "C:/temp/fake.ps1"}
    mod._windows_elevated_apply_zip = fake_elevated  # type: ignore

    # Intercetta os._exit per evitare terminazione del test process
    exits = {}
    def fake_exit(code):
        exits['code'] = code
        raise RuntimeError("_exit intercepted")
    monkeypatch.setattr(mod.os, "_exit", fake_exit, raising=False)

    assets = [
        {"id": "core", "url": "http://invalid/core.zip", "size": 50, "sha256": None, "type": "zip"},
    ]
    with TestClient(fastapi_app) as client:
        rp = client.post("/update/plan", json={"assets": assets})
        assert rp.status_code == 200
        rf = client.post("/update/fetch", json={"start_immediately": True})
        assert rf.status_code == 200
        deadline = time.time() + 3
        restarting_seen = False
        stage = None
        while time.time() < deadline:
            st = client.get("/status")
            body = st.json()
            stage = body.get("update_stage")
            if stage == "restarting":
                restarting_seen = True
                elev = body.get("update_elevated") or body.get("elevated")
                if isinstance(elev, dict):
                    assert "backup_dir" in elev
                break
            if stage in {"error","ok"}:
                break
            time.sleep(0.05)
        assert restarting_seen, f"Stage 'restarting' non visto; ultimo stage={stage}"
        assert calls.get('zip_path') and calls.get('target_dir')
        assert exits.get('code') == 0 or exits.get('code') is None  # intercettato


def test_apply_update_stops_off_before_copy(fastapi_app, tmp_path, monkeypatch):
    """Verifica che apply_update tenti di fermare OFF-player prima di copiare i file.
    Simula un processo OFF vivo e controlla che _off_stop venga invocato.
    """
    import importlib, zipfile
    from pathlib import Path
    mod = importlib.import_module("app_update_module")

    # Prepara zip con un file fittizio
    archive = tmp_path / "pkg.zip"
    with zipfile.ZipFile(str(archive), 'w') as z:
        z.writestr('dummy.txt', 'x')

    # Simula OFF attivo: crea un oggetto con poll() -> None
    class FakeProc:
        def __init__(self): self.killed=False
        def poll(self): return None
        def kill(self): self.killed=True

    mod.off_proc["p"] = FakeProc()

    # Spy su _off_stop
    called = {"stop": 0}
    real_off_stop = mod._off_stop
    def spy_stop():
        called["stop"] += 1
        return {"ok": True}
    monkeypatch.setattr(mod, "_off_stop", spy_stop, raising=False)

    # Esegui apply_update
    mod.apply_update(archive)

    assert called["stop"] >= 1, "_off_stop non è stato chiamato prima della copia"

def test_windows_zip_elevated_helper_failure_fallback(fastapi_app, tmp_path, monkeypatch):
    """Se helper ritorna ok=False, verifica che si proceda con apply_update normale (non 'restarting')."""
    from fastapi.testclient import TestClient
    import importlib, time
    mod = importlib.import_module("app_update_module")
    monkeypatch.setattr(mod.os, "name", "nt", raising=False)
    # Download zip
    def fake_download(url: str, dest: Path, on_progress=None):
        import zipfile
        with zipfile.ZipFile(str(dest), 'w') as z:
            z.writestr('file.txt', 'data')
        if on_progress:
            on_progress(10, 10)
        return dest
    mod._download_with_progress = fake_download  # type: ignore
    # Helper fallisce
    def fake_elevated_fail(*a, **k):
        return {"ok": False, "error": "denied"}
    mod._windows_elevated_apply_zip = fake_elevated_fail  # type: ignore
    # Spy apply_update
    applied = {}
    real_apply = mod.apply_update
    def spy_apply(path):
        applied['path'] = str(path)
        return real_apply(path)
    mod.apply_update = spy_apply  # type: ignore

    assets = [
        {"id": "core", "url": "http://invalid/core.zip", "size": 10, "sha256": None, "type": "zip"},
    ]
    with TestClient(fastapi_app) as client:
        client.post("/update/plan", json={"assets": assets})
        client.post("/update/fetch", json={"start_immediately": True})
        deadline = time.time() + 3
        final = None
        while time.time() < deadline:
            body = client.get("/status").json()
            stage = body.get("update_stage")
            if stage in {"ok","error"}:
                final = stage
                break
            time.sleep(0.05)
        assert final == "ok"
        assert applied.get('path'), "Fallback apply_update non eseguito"
        # Non deve essere 'restarting'
        assert body.get('update_stage') == 'ok'
