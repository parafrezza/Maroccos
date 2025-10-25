"""Backend MPV basato su JSON IPC (--input-ipc-server).

Nota: pensato per sistemi Linux/headless. Usa socket UNIX in /tmp.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import register


@register("mpv")
class MpvBackend:
	"""Controlla un processo mpv headless tramite JSON IPC.

	API minima richiesta dal progetto:
	- play(path, loop=False, fade_in=0.0)
	- stop()
	- pause()
	- resume()
	- set_loop(enabled)
	- is_playing() -> bool
	- shutdown()
	- faststart_prepare(path)
	- faststart_go()
	"""

	supports_playlist = False

	def __init__(self, controller: dict) -> None:
		self._g = controller
		self._proc: subprocess.Popen[str] | None = None
		self._ipc_path: str | None = None
		self._sock: socket.socket | None = None
		self._lock = threading.Lock()
		self._loop = False

	# ------------------------------------------------------------------
	# Helpers
	def _make_ipc_path(self) -> str:
		tmp = tempfile.gettempdir()
		# Path univoco per processo
		return os.path.join(tmp, f"mpv-ipc-{os.getpid()}.sock")

	def _ensure_process(self, paused: bool = False, with_media: str | None = None, loop: bool | None = None) -> None:
		"""Avvia mpv se non presente. Se with_media è valorizzato, carica subito quel media.

		paused: se True, avvia in pausa (usato dal faststart_prepare).
		loop: se non None, imposta il loop-file di conseguenza.
		"""
		with self._lock:
			# Se processo vivo, assicurati che il socket sia connesso
			if self._proc is not None and self._proc.poll() is None:
				if self._sock is None:
					self._connect_ipc()
				# eventualmente sovrascrive loop
				if loop is not None:
					self._loop = bool(loop)
					self._ipc_set_property("loop-file", "inf" if self._loop else "no")
				# carica media se richiesto
				if with_media:
					self._ipc_loadfile(with_media)
					if paused:
						self._ipc_set_property("pause", True)
						self._ipc_set_property("time-pos", 0.0)
				return

			# Avvia nuovo processo
			self._ipc_path = self._make_ipc_path()
			# Rimuovi eventuale socket precedente rimasto
			try:
				if os.path.exists(self._ipc_path):
					os.remove(self._ipc_path)
			except Exception:
				pass

			args: list[str] = [
				"mpv",
				"--no-terminal",
				"--really-quiet",
				f"--input-ipc-server={self._ipc_path}",
				"--force-window=no",
			]
			if loop is None:
				loop = self._loop
			self._loop = bool(loop)
			args.append(f"--loop-file={'inf' if self._loop else 'no'}")
			if paused:
				args.append("--pause")
			if with_media:
				args.append(str(with_media))

			try:
				self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
			except FileNotFoundError as e:
				raise RuntimeError("mpv non trovato nel PATH") from e

		# Attendi socket pronto
		deadline = time.time() + 2.0
		while time.time() < deadline:
			try:
				self._connect_ipc()
				break
			except Exception:
				time.sleep(0.05)
		if self._sock is None:
			raise RuntimeError("Impossibile connettersi al socket IPC di mpv")

		# Se media non passato a riga comando ma richiesto, carica ora
		if with_media and not paused:
			self._ipc_loadfile(with_media)

	def _connect_ipc(self) -> None:
		if not self._ipc_path:
			raise RuntimeError("IPC path non inizializzato")
		s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
		s.settimeout(0.5)
		s.connect(self._ipc_path)
		self._sock = s

	def _ipc_send(self, payload: dict[str, Any]) -> dict[str, Any] | None:
		if self._sock is None:
			raise RuntimeError("Socket IPC non connesso")
		data = (json.dumps(payload) + "\n").encode("utf-8")
		self._sock.sendall(data)
		# Alcuni comandi non richiedono risposta; prova a leggere non bloccante
		self._sock.settimeout(0.2)
		try:
			raw = self._sock.recv(8192)
			if not raw:
				return None
			try:
				return json.loads(raw.decode("utf-8", errors="ignore"))
			except Exception:
				return None
		except Exception:
			return None

	def _ipc_set_property(self, name: str, value: Any) -> None:
		self._ipc_send({"command": ["set_property", name, value]})

	def _ipc_get_property(self, name: str) -> Any:
		resp = self._ipc_send({"command": ["get_property", name]})
		if isinstance(resp, dict) and resp.get("error") == "success":
			return resp.get("data")
		return None

	def _ipc_loadfile(self, path: str) -> None:
		self._ipc_send({"command": ["loadfile", str(path), "replace"]})

	def _hide_splash(self) -> None:
		splash = self._g.get("splash")
		if splash and splash.get("active"):
			try:
				self._g["hide_splash"]()
			except Exception:
				pass

	# ------------------------------------------------------------------
	# Backend API
	@property
	def name(self) -> str:
		return "mpv"

	def play(self, path: str | None = None, loop: bool | None = None, fade_in: float = 0.0) -> None:
		if not path:
			path = self._g.get("VIDEO_PATH")
		if not path:
			raise RuntimeError("Nessun file specificato per la riproduzione")

		media_path = Path(path)
		if not media_path.exists():
			raise FileNotFoundError(str(media_path))

		self._ensure_process(paused=False, with_media=str(media_path), loop=loop)
		# Assicura play e loop coerenti
		self._ipc_set_property("pause", False)
		self._ipc_set_property("loop-file", "inf" if self._loop else "no")

		self._g["VIDEO_PATH"] = str(media_path)
		try:
			self._g["player"]["state"] = "playing"
		except Exception:
			pass
		self._hide_splash()

	def stop(self) -> None:
		# Ferma riproduzione ma lascia mpv attivo per riuso veloce
		try:
			self._ensure_process()
			self._ipc_send({"command": ["stop"]})
		except Exception:
			pass
		try:
			self._g["player"]["state"] = "stopped"
		except Exception:
			pass

	def pause(self) -> None:
		self._ensure_process()
		# Piccolo backoff per gestire transizioni GO->pausa
		deadline = time.time() + 0.6
		last_exc: Exception | None = None
		while True:
			try:
				st = self._ipc_get_property("pause")
			except Exception:
				st = None
			if st is True:
				break
			try:
				self._ipc_set_property("pause", True)
			except Exception as e:
				last_exc = e
			time.sleep(0.06)
			if time.time() > deadline:
				break
		if last_exc:
			raise RuntimeError(str(last_exc)) from last_exc
		try:
			self._g["player"]["state"] = "paused"
		except Exception:
			pass

	def resume(self) -> None:
		self._ensure_process()
		self._ipc_set_property("pause", False)
		try:
			self._g["player"]["state"] = "playing"
		except Exception:
			pass

	def set_loop(self, enabled: bool) -> None:
		self._ensure_process()
		self._loop = bool(enabled)
		self._ipc_set_property("loop-file", "inf" if self._loop else "no")

	def is_playing(self) -> bool:
		if self._proc is None or self._proc.poll() is not None:
			return False
		try:
			paused = bool(self._ipc_get_property("pause"))
			return not paused
		except Exception:
			return True  # best-effort se IPC non risponde ma processo vivo

	def shutdown(self) -> None:
		with self._lock:
			proc = self._proc
			self._proc = None
			sock = self._sock
			self._sock = None
			ipc = self._ipc_path
			self._ipc_path = None
		try:
			if sock is not None:
				try:
					sock.close()
				except Exception:
					pass
			if proc is not None and proc.poll() is None:
				try:
					# Termina mpv in modo pulito
					self._ipc_send({"command": ["quit"]})
				except Exception:
					pass
				try:
					proc.terminate()
				except Exception:
					pass
				try:
					proc.wait(timeout=0.5)
				except Exception:
					pass
			if ipc and os.path.exists(ipc):
				try:
					os.remove(ipc)
				except Exception:
					pass
		except Exception:
			pass

	# ---- Fast-start helpers ----
	def faststart_prepare(self, path: str) -> None:
		media_path = Path(path)
		if not media_path.exists():
			raise FileNotFoundError(str(media_path))
		# Avvia o riusa mpv in pausa con il file caricato e prerollato
		self._ensure_process(paused=True, with_media=str(media_path), loop=self._loop)
		# Forza primo frame
		self._ipc_set_property("time-pos", 0.0)
		self._ipc_set_property("pause", True)
		self._g["VIDEO_PATH"] = str(media_path)
		try:
			self._g["player"]["state"] = "paused"
		except Exception:
			pass
		self._hide_splash()

	def faststart_go(self) -> None:
		# Semplice: togli pausa
		self._ensure_process()
		self._ipc_set_property("pause", False)
		try:
			self._g["player"]["state"] = "playing"
		except Exception:
			pass

