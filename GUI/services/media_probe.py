"""Best-effort media duration probe using ffprobe, with safe fallbacks.

Returns duration in seconds as float, 0.0 for images, or None if unknown.
"""

from __future__ import annotations

import json
import subprocess
import shutil
from pathlib import Path
from typing import Optional


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}


def probe_duration_seconds(path: str | Path) -> Optional[float]:
	"""Probe media duration in seconds.

	- Images: returns 0.0
	- Video/Audio: tries ffprobe; returns float seconds or None if not available
	- Non-existing or inaccessible files: None
	"""
	try:
		p = Path(path)
	except Exception:
		return None
	try:
		if not p.exists() or not p.is_file():
			return None
	except Exception:
		return None

	# Images: treat as 0.0 duration
	try:
		ext = p.suffix.lower()
	except Exception:
		ext = ""
	if ext in _IMAGE_EXTS:
		return 0.0

	# ffprobe available?
	ffprobe = shutil.which("ffprobe")
	if not ffprobe:
		return None

	# Try probing with ffprobe (JSON output)
	cmd = [
		ffprobe,
		"-v",
		"error",
		"-show_entries",
		"format=duration",
		"-of",
		"json",
		str(p),
	]
	try:
		out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
		data = json.loads(out.decode(errors="ignore"))
		dur = None
		# Prefer format.duration
		fmt = data.get("format") or {}
		if isinstance(fmt, dict):
			d = fmt.get("duration")
			if isinstance(d, (int, float)):
				dur = float(d)
			elif isinstance(d, str):
				try:
					dur = float(d)
				except Exception:
					dur = None
		# Sanity check
		if isinstance(dur, (int, float)) and dur >= 0:
			return float(dur)
	except Exception:
		# As a weaker fallback, try stream duration
		try:
			cmd2 = [
				ffprobe,
				"-v",
				"error",
				"-select_streams",
				"v:0",
				"-show_entries",
				"stream=duration",
				"-of",
				"json",
				str(p),
			]
			out2 = subprocess.check_output(cmd2, stderr=subprocess.STDOUT)
			data2 = json.loads(out2.decode(errors="ignore"))
			dur2 = None
			streams = data2.get("streams") or []
			if isinstance(streams, list) and streams:
				s0 = streams[0] or {}
				d2 = s0.get("duration")
				if isinstance(d2, (int, float)):
					dur2 = float(d2)
				elif isinstance(d2, str):
					try:
						dur2 = float(d2)
					except Exception:
						dur2 = None
			if isinstance(dur2, (int, float)) and dur2 >= 0:
				return float(dur2)
		except Exception:
			return None

	return None

