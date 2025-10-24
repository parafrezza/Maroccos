from __future__ import annotations

import threading, sys, time
from pathlib import Path
from . import register

try:
	from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout
	from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
	from PyQt5.QtMultimediaWidgets import QVideoWidget
	from PyQt5.QtCore import QUrl
except Exception:  # pragma: no cover
	QApplication = None  # type: ignore

@register("pyqt")
class PyQtBackend:
	"""Backend basato su QMediaPlayer.
	Nota: avvia un QApplication singleton in thread separato.
	"""
	supports_playlist = False

	def __init__(self, controller: dict):
		self.c = controller
		self._app = None
		self._player = None
		self._widget = None
		self._loop = False
		self._fade_lock = threading.Lock()
		self._active_fade_id = 0
		if QApplication is not None:
			t = threading.Thread(target=self._init_qt, daemon=True)
			t.start()
		else:
			print("[PYQT] PyQt5 non disponibile")

	@property
	def name(self):
		return "pyqt"

	def _init_qt(self):  # pragma: no cover - runtime GUI
		try:
			self._app = QApplication.instance() or QApplication(sys.argv)
			self._player = QMediaPlayer(None, QMediaPlayer.VideoSurface)
			self._widget = QVideoWidget()
			w = QWidget()
			lay = QVBoxLayout(w)
			lay.setContentsMargins(0, 0, 0, 0)
			lay.addWidget(self._widget)
			self._player.setVideoOutput(self._widget)
			w.showFullScreen()
			threading.Thread(target=self._qt_loop, daemon=True).start()
			self._app.exec_()
		except Exception as e:
			print(f"[PYQT] Init error: {e}")

	def _qt_loop(self):  # pragma: no cover
		while True:
			try:
				if self._loop and self._player and self._player.state() == QMediaPlayer.StoppedState:
					self._player.play()
			except Exception:
				pass
			time.sleep(0.2)

	def play(self, path: str | None = None, loop: bool | None = None, fade_in: float = 0.0):
		if QApplication is None:
			raise RuntimeError("PyQt5 non installato")
		if not path:
			path = self.c.get("VIDEO_PATH")
		if not path:
			raise RuntimeError("Nessun file specificato")
		p = Path(path)
		if not p.exists():
			raise FileNotFoundError(path)
		# Attendi inizializzazione player
		for _ in range(50):  # ~5s
			if self._player:
				break
			time.sleep(0.1)
		if not self._player:
			raise RuntimeError("QMediaPlayer non inizializzato")
		url = QUrl.fromLocalFile(str(p.resolve()))
		self._player.setMedia(QMediaContent(url))
		self._player.play()
		if loop is not None:
			self._loop = bool(loop)
		if self.c["splash"].get("active"):
			try:
				self.c["hide_splash"]()
			except Exception:
				pass
		self.c["player"]["state"] = "playing"
		if fade_in and fade_in > 0:
			self.fade(0.0, 1.0, fade_in)

	def stop(self):
		try:
			if self._player:
				self._player.stop()
		except Exception:
			pass
		self.c["player"]["state"] = "stopped"

	def is_playing(self) -> bool:
		try:
			return self._player and self._player.state() == QMediaPlayer.PlayingState
		except Exception:
			return False

	# ---- Fade software ----
	def fade(self, start: float, end: float, duration: float, steps: int | None = None, on_complete=None):
		if not self._widget:
			return
		if steps is None:
			steps = max(1, int(duration / 0.05))
		with self._fade_lock:
			self._active_fade_id += 1
			fid = self._active_fade_id
		delta = end - start
		start_time = time.time()
		def worker():
			for i in range(steps + 1):
				with self._fade_lock:
					if fid != self._active_fade_id:
						return
					frac = i / steps
					val = max(0.0, min(1.0, start + delta * frac))
				try:
					self._widget.setWindowOpacity(val)
				except Exception:
					return
				target_time = start_time + duration * frac
				remain = target_time - time.time()
				if remain > 0:
					time.sleep(min(0.05, remain))
			if on_complete:
				try: on_complete()
				except Exception: pass
		threading.Thread(target=worker, daemon=True).start()

	def fade_out(self, seconds: float):
		self.fade(1.0, 0.0, seconds)

	def fade_in(self, seconds: float):
		self.fade(0.0, 1.0, seconds)

