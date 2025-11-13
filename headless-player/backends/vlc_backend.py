from __future__ import annotations

import threading, time
from pathlib import Path
from . import register

try:
	import vlc  # type: ignore
except Exception:  # pragma: no cover - se non installato
	vlc = None

@register("vlc")
class VlcBackend:
	"""Backend VLC semplice (niente overlay splash / fade gestiti internamente).
	Richiede python-vlc.
	"""
	supports_playlist = False

	def __init__(self, controller: dict):
		self.c = controller
		self._instance = None
		self._player = None
		self._loop = False
		self._fade_lock = threading.Lock()
		self._active_fade_id = 0
		if vlc is not None:
			try:
				self._instance = vlc.Instance()
				self._player = self._instance.media_player_new()
			except Exception as e:  # pragma: no cover
				print(f"[VLC] Init error: {e}")

		# thread per loop
		t = threading.Thread(target=self._loop_watch, daemon=True)
		t.start()

	@property
	def name(self):
		return "vlc"

	def _loop_watch(self):  # pragma: no cover - loop runtime
		while True:
			try:
				if self._loop and self._player:
					st = self._player.get_state()
					if st in (vlc.State.Ended, vlc.State.Stopped):
						self._player.stop()
						self._player.play()
				time.sleep(0.2)
			except Exception:
				time.sleep(0.5)

	def play(self, path: str | None = None, loop: bool | None = None, fade_in: float = 0.0):
		if vlc is None:
			raise RuntimeError("python-vlc non installato")
		if not path:
			path = self.c.get("VIDEO_PATH")
		if not path:
			raise RuntimeError("Nessun file specificato")
		p = Path(path)
		if not p.exists():
			raise FileNotFoundError(path)
		media = self._instance.media_new(str(p))
		self._player.set_media(media)
		self._player.play()
		if loop is not None:
			self._loop = bool(loop)
		# Disattiva splash se attivo
		if self.c["splash"].get("active"):
			try:
				self.c["hide_splash"]()
			except Exception:
				pass
		self.c["player"]["state"] = "playing"
		# Fade-in software se richiesto
		if fade_in and fade_in > 0:
			try:
				self.fade(0.0, 1.0, fade_in)
			except Exception:
				pass

	def stop(self):
		try:
			if self._player:
				self._player.stop()
		except Exception:
			pass
		self.c["player"]["state"] = "stopped"

	def pause(self):
		"""Mette in pausa la riproduzione se possibile (idempotente)."""
		try:
			if self._player:
				# Usa set_pause per evitare toggle non desiderati
				try:
					self._player.set_pause(1)
				except Exception:
					# Fallback: alcune build applicano toggle su pause()
					self._player.pause()
		except Exception:
			pass
		self.c["player"]["state"] = "paused"

	def resume(self):
		"""Riprende la riproduzione se in pausa (idempotente)."""
		try:
			if self._player:
				try:
					self._player.set_pause(0)
				except Exception:
					# Fallback: alcuni binding richiedono play() per riprendere
					self._player.play()
		except Exception:
			pass
		self.c["player"]["state"] = "playing"

	def is_playing(self) -> bool:
		try:
			return self._player and self._player.is_playing() == 1
		except Exception:
			return False

	# ---- Fade software ----
	def fade(self, start: float, end: float, duration: float, steps: int | None = None, on_complete=None):
		"""Fade software usando controlli video (brightness). start/end in [0..1]."""
		if vlc is None or not self._player:
			return
		if steps is None:
			steps = max(1, int(duration / 0.05))  # 50ms
		with self._fade_lock:
			self._active_fade_id += 1
			fid = self._active_fade_id
		try:
			self._player.video_set_adjust_int(vlc.VideoAdjustOption.Enable, 1)
		except Exception:
			return
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
					self._player.video_set_adjust_float(vlc.VideoAdjustOption.Brightness, val)
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

	# ---- Visual fades (alias su brightness) ----
	def visual_fade_out(self, seconds: float) -> None:
		"""Fade-to-black visivo usando brightness (0..1)."""
		self.fade_out(seconds)

	def visual_fade_in(self, seconds: float) -> None:
		"""Fade-in visivo usando brightness (0..1)."""
		self.fade_in(seconds)

