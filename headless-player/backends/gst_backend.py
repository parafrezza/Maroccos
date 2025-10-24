from __future__ import annotations

from . import register

@register("gst")
class GstBackend:
	"""Backend di default che delega alle funzioni GStreamer già presenti in app.py
	Il controller è il dict globals() passato dal main.
	"""
	supports_playlist = True

	def __init__(self, controller: dict):
		self.c = controller

	@property
	def name(self):
		return "gst"

	def play(self, path: str | None = None, loop: bool | None = None, fade_in: float = 0.5):
		if path:
			self.c["start_play_with_path"](path)
		else:
			self.c["start_play"](fade_in)
		if loop is not None:
			self.c["set_loop"](loop)

	def stop(self):
		self.c["stop_play"]()

	def is_playing(self) -> bool:
		return self.c["player"].get("state") == "playing"

