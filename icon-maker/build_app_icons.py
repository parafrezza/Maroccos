#!/usr/bin/env python3
"""
Generatore di icone per Windows (ICO multi-size) e PNG derivati.

Uso rapido da PowerShell o CMD:
  python build_app_icons.py --src icon.png --name morocco-player --outdir dist

Output:
  - dist/morocco-player/morocco-player.ico  (icone 16..256 px)
  - dist/morocco-player/png/<size>.png      (derivati PNG)
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Iterable, Tuple

try:
	from PIL import Image, ImageFilter
except Exception as exc:  # pragma: no cover
	raise SystemExit("Pillow (PIL) non installato. Installa con: pip install pillow") from exc


DEFAULT_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _ensure_square(img: Image.Image, *, bg: Tuple[int, int, int, int] = (0, 0, 0, 0)) -> Image.Image:
	"""Se necessario, centra l'immagine in un canvas quadrato trasparente."""
	w, h = img.size
	if w == h:
		# Converti in RGBA per sicurezza durante il resize/compositing
		return img.convert("RGBA")
	side = max(w, h)
	canvas = Image.new("RGBA", (side, side), bg)
	ox = (side - w) // 2
	oy = (side - h) // 2
	canvas.paste(img.convert("RGBA"), (ox, oy))
	return canvas


def build_ico(src: Path, out_dir: Path, name: str, sizes: Iterable[int] = DEFAULT_SIZES, *, sharpen_small: bool = True, small_threshold: int = 48) -> Path:
	out_dir.mkdir(parents=True, exist_ok=True)
	png_dir = out_dir / "png"
	png_dir.mkdir(parents=True, exist_ok=True)

	with Image.open(src) as im:
		base = _ensure_square(im)
		resized: list[Image.Image] = []
		ico_sizes: list[tuple[int, int]] = []
		for s in sizes:
			# Lanczos per qualità
			r = base.resize((s, s), Image.Resampling.LANCZOS)
			# Migliora la definizione delle dimensioni piccole con una leggera maschera di contrasto
			if sharpen_small and s <= int(small_threshold):
				try:
					# amount ~1.2 dà un buon compromesso; threshold basso per non esagerare sul testo
					r = r.filter(ImageFilter.UnsharpMask(radius=0.5, percent=120, threshold=2))
				except Exception:
					pass
			resized.append(r)
			ico_sizes.append((s, s))
			# Salva PNG derivato
			(png_dir / f"{s}.png").write_bytes(_img_to_png_bytes(r))

	ico_path = out_dir / f"{name}.ico"
	# La prima immagine è usata come base, le altre tramite "append_images"
	resized[0].save(ico_path, format="ICO", sizes=ico_sizes, append_images=resized[1:])
	return ico_path


def _img_to_png_bytes(img: Image.Image) -> bytes:
	from io import BytesIO

	buf = BytesIO()
	img.save(buf, format="PNG")
	return buf.getvalue()


def main() -> None:
	ap = argparse.ArgumentParser(description="Costruisce ICO multi-size e PNG derivati da un'immagine sorgente")
	ap.add_argument("--src", required=True, help="Percorso immagine sorgente (PNG preferito)")
	ap.add_argument("--name", required=True, help="Nome base per l'output (es. morocco-player)")
	ap.add_argument("--outdir", default="dist", help="Cartella output, default=dist")
	ap.add_argument(
		"--sizes",
		default=','.join(map(str, DEFAULT_SIZES)),
		help=f"Lista dimensioni ICO separate da virgola (default: {','.join(map(str, DEFAULT_SIZES))})",
	)
	ap.add_argument("--no-sharpen", action="store_true", help="Disabilita l'accentuazione per le dimensioni piccole")
	ap.add_argument("--small-threshold", type=int, default=48, help="Sotto questa dimensione (px) applica sharpening (default 48)")
	args = ap.parse_args()

	src = Path(args.src)
	if not src.exists():
		raise SystemExit(f"Sorgente non trovata: {src}")
	try:
		sizes = tuple(int(x.strip()) for x in str(args.sizes).split(',') if x.strip())
		sizes = tuple(sorted(set(sizes)))
	except Exception:
		sizes = DEFAULT_SIZES

	out_base = Path(args.outdir) / args.name
	ico = build_ico(
		src,
		out_base,
		args.name,
		sizes,
		sharpen_small=(not bool(args.no_sharpen)),
		small_threshold=int(args.small_threshold),
	)
	print(f"[ICON] Creato: {ico}")
	print(f"[ICON] PNG derivati in: {out_base / 'png'}")


if __name__ == "__main__":
	main()
