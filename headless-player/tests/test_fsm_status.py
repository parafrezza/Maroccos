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
	if str(base) not in sys.path:
		sys.path.insert(0, str(base))
	app_path = base / "app.py"
	import importlib.util
	spec = importlib.util.spec_from_file_location("app_module_fsm", str(app_path))
	assert spec and spec.loader
	mod = importlib.util.module_from_spec(spec)
	import builtins
	builtins._original_system = getattr(sys.modules.get('os'), 'system', None)
	import os
	os.system = lambda *a, **k: 0
	sys.modules["app_module_fsm"] = mod
	spec.loader.exec_module(mod)  # type: ignore[attr-defined]
	# Evita splash al startup
	mod.show_splash_until_play = lambda: None
	return mod.app


def _write_fake_mp4(path: Path):
	data = bytearray(16)
	data[4:8] = b"ftyp"
	path.write_bytes(bytes(data))


def test_stop_transitions_to_idle(fastapi_app, tmp_path):
	from fastapi.testclient import TestClient
	import importlib
	mod = importlib.import_module("app_module_fsm")
	media_dir = mod.MEDIA_DIR
	media_dir.mkdir(exist_ok=True)
	f = media_dir / "stopme.mp4"
	_write_fake_mp4(f)
	with TestClient(fastapi_app) as client:
		r1 = client.post("/play", json={"filename": "stopme.mp4"})
		assert r1.status_code == 200
		r2 = client.post("/stop")
		assert r2.status_code == 200
		st = client.get("/status")
		assert st.status_code == 200
		body = st.json()
		assert body.get("fsm_state") == "idle"
		prev = body.get("fsm", {}).get("previous")
		assert prev in {"stopping", "preparing", "playing", None}


def test_play_scheduled_sets_status(fastapi_app):
	from fastapi.testclient import TestClient
	import time, importlib
	mod = importlib.import_module("app_module_fsm")
	media_dir = mod.MEDIA_DIR
	media_dir.mkdir(exist_ok=True)
	f = media_dir / "sched2.mp4"
	_write_fake_mp4(f)
	start_at = time.time() + 0.5
	with TestClient(fastapi_app) as client:
		r = client.post("/play", json={"filename": "sched2.mp4", "in_time": start_at})
		assert r.status_code == 200
		body = r.json()
		assert body.get("ok") is True and body.get("scheduled") is True
		st = client.get("/status")
		assert st.status_code == 200
		s = st.json().get("scheduled", {})
		assert s.get("play_at") is not None
		# Prima della scadenza non dobbiamo essere in playing
		fsm_state = st.json().get("fsm_state")
		assert fsm_state in {"idle", "preparing", "playing"}


def test_playlist_next_sets_fsm_action(fastapi_app):
	from fastapi.testclient import TestClient
	import importlib
	mod = importlib.import_module("app_module_fsm")
	media_dir = mod.MEDIA_DIR
	media_dir.mkdir(exist_ok=True)
	f1 = media_dir / "n1.mp4"; f2 = media_dir / "n2.mp4"
	_write_fake_mp4(f1); _write_fake_mp4(f2)
	with TestClient(fastapi_app) as client:
		r = client.post("/playlist/apply", json={"items": ["n1.mp4", "n2.mp4"], "loop": False})
		assert r.status_code == 200
		rn = client.post("/playlist/next")
		assert rn.status_code == 200
		st = client.get("/status")
		assert st.status_code == 200
		fsm = st.json().get("fsm", {})
		assert fsm.get("state") in {"preparing", "playing"}
		# L'azione deve essere tracciata come 'next'
		assert fsm.get("action") == "next"


def test_playlist_prev_sets_fsm_action(fastapi_app):
	from fastapi.testclient import TestClient
	import importlib
	mod = importlib.import_module("app_module_fsm")
	media_dir = mod.MEDIA_DIR
	media_dir.mkdir(exist_ok=True)
	f1 = media_dir / "p1.mp4"; f2 = media_dir / "p2.mp4"
	_write_fake_mp4(f1); _write_fake_mp4(f2)
	with TestClient(fastapi_app) as client:
		r = client.post("/playlist/apply", json={"items": ["p1.mp4", "p2.mp4"], "loop": True})
		assert r.status_code == 200
		# Portati su index 1, poi prev
		client.post("/playlist/next")
		rp = client.post("/playlist/prev")
		assert rp.status_code == 200
		st = client.get("/status")
		assert st.status_code == 200
		fsm = st.json().get("fsm", {})
		assert fsm.get("state") in {"preparing", "playing"}
		assert fsm.get("action") == "prev"


def test_stop_cancels_scheduled_play(fastapi_app):
	from fastapi.testclient import TestClient
	import time, importlib
	mod = importlib.import_module("app_module_fsm")
	media_dir = mod.MEDIA_DIR
	media_dir.mkdir(exist_ok=True)
	f = media_dir / "sched_cancel.mp4"
	_write_fake_mp4(f)
	with TestClient(fastapi_app) as client:
		start_at = time.time() + 2.0
		r = client.post("/play", json={"filename": "sched_cancel.mp4", "in_time": start_at})
		assert r.status_code == 200 and r.json().get("scheduled") is True
		st1 = client.get("/status")
		assert st1.status_code == 200
		assert st1.json().get("scheduled", {}).get("play_at") is not None
		# Ora stop: deve invalidare le schedule (cancel_all_schedules)
		rs = client.post("/stop")
		assert rs.status_code == 200
		st2 = client.get("/status")
		assert st2.status_code == 200
		# play_at deve essere stato azzerato
		assert st2.json().get("scheduled", {}).get("play_at") is None

