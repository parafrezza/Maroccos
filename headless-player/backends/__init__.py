"""Registry dei backend video.

Ogni backend deve fornire almeno:
 - class Backend(controller: dict)
   metodi: play(path, loop=False), stop(), is_playing(), name (property), supports_playlist (bool)
"""

from __future__ import annotations

BACKEND_CLASSES = {}

def register(name):
	def deco(cls):
		BACKEND_CLASSES[name] = cls
		return cls
	return deco

def available_backends():
	return list(BACKEND_CLASSES.keys())

# Importa moduli (se presenti le dipendenze non falliscono; altrimenti verranno semplicemente ignorati dal chiamante)
try:
	from . import gst_backend  # noqa: F401
except Exception:
	pass
try:
	from . import vlc_backend  # noqa: F401
except Exception:
	pass
try:
	from . import cvlc_backend  # noqa: F401
except Exception:
	pass
try:
	from . import pyqt_backend  # noqa: F401
except Exception:
	pass
try:
	from . import omxplayer_backend  # noqa: F401
except Exception:
	pass

