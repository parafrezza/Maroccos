#!/usr/bin/env python3
"""
Genera un index.json conforme a docs/manifest_index.schema.json.

Uso (PowerShell/Unix):
  python scripts/generate_index_manifest.py --base-url https://updates.example.com/maroccos/ --out release/index.json \
      --win-exe installer/Maroccos-Setup-vX.exe \
      --headless-zip release/headless-player-vX-win64.zip

In assenza di parametri, crea un manifest minimale usando VERSION e segnaposto URL.
"""
from __future__ import annotations
import argparse, json, hashlib, os
from pathlib import Path
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parents[1]
HP = REPO / "headless-player"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def build_asset(path: Path, url: str, id: str, type_: str) -> dict:
    return {
        "id": id,
        "type": type_,
        "url": url,
        "size": path.stat().st_size if path.exists() else 0,
        "sha256": sha256_of(path) if path.exists() else ("0" * 64),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="https://updates.example.com/maroccos/", help="Base URL pubblica dei file")
    ap.add_argument("--out", default=str(REPO / "release" / "index.json"))
    ap.add_argument("--headless-zip", default=None, help="Path zip headless-player per Windows")
    ap.add_argument("--win-exe", default=None, help="Path installer OFF/Bundle per Windows (.exe)")
    ap.add_argument("--prov-ps1", default=None, help="Path provisioning script .ps1")
    args = ap.parse_args()

    version = (HP / "VERSION").read_text().strip() if (HP / "VERSION").exists() else "v0.1.0"

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    comps = []
    # headless_player component with windows/amd64 build (zip)
    hp_zip = Path(args.headless_zip or (REPO / "headless-player" / "releases" / f"headless-player-{version}-win64.zip"))
    hp_url = args.base_url.rstrip('/') + f"/headless/{version}/{hp_zip.name}"
    comps.append({
        "id": "headless_player",
        "version": version,
        "builds": [{
            "os": "windows",
            "arch": "amd64",
            "assets": [build_asset(hp_zip, hp_url, "headless_zip", "zip")],
        }],
    })

    # off_player component (optional exe)
    if args.win_exe:
        exe_p = Path(args.win_exe)
        exe_url = args.base_url.rstrip('/') + f"/off/{version}/{exe_p.name}"
        comps.append({
            "id": "off_player",
            "version": version,
            "builds": [{
                "os": "windows",
                "arch": "amd64",
                "assets": [build_asset(exe_p, exe_url, "off_installer", "exe")],
            }],
        })

    # provision_script (optional)
    if args.prov_ps1:
        ps1 = Path(args.prov_ps1)
        ps1_url = args.base_url.rstrip('/') + f"/provision/{ps1.name}"
        comps.append({
            "id": "provision_script",
            "version": datetime.now().strftime("%Y.%m.%d"),
            "builds": [{
                "os": "windows",
                "arch": "amd64",
                "assets": [build_asset(ps1, ps1_url, "provision_ps1", "ps1")],
            }],
        })

    manifest = {
        "manifest_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": args.base_url,
        "components": comps,
        "recommendations": [{
            "os": "windows",
            "arch": "amd64",
            "components": {c["id"]: c["version"] for c in comps},
        }],
    }

    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Manifest scritto in: {out_path}")


if __name__ == "__main__":
    main()
