"""Regenera docs/headless-player.postman_collection.json a partire da headless-player/app.py."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_FILE = REPO_ROOT / "headless-player" / "app.py"
COLLECTION_FILE = REPO_ROOT / "docs" / "headless-player.postman_collection.json"

ROUTE_REGEX = re.compile(
    r"@app\.(get|post|put|delete|patch)\(\s*(?P<quote>\"|')(.*?)(?P=quote)",
    re.IGNORECASE,
)

GROUP_LABELS = {
    "": "Misc",
    "healthz": "Health",
    "status": "Health",
    "components": "Health",
    "logs": "Logs",
    "media": "Media",
    "playlist": "Playlist",
    "faststart": "Playback",
    "faststart_prepare": "Playback",
    "faststart_go": "Playback",
    "play": "Playback",
    "play_at": "Playback",
    "pause": "Playback",
    "resume": "Playback",
    "stop": "Playback",
    "loop": "Playback",
    "go_to_start": "Playback",
    "ping": "Playback",
    "visual": "Visual",
    "overlay": "Overlay",
    "display": "Display",
    "framework": "Framework",
    "change_framework": "Framework",
    "autoplay": "Autoplay",
    "settings": "Settings",
    "device": "Device",
    "download_update": "Updates",
    "update": "Updates",
    "maintenance": "Maintenance",
    "system": "System",
    "network": "Network",
    "packages": "System",
    "off": "OFF Backend",
    "hud": "Visual",
}


def classify(path: str) -> str:
    clean = path.lstrip("/")
    first = clean.split("/", 1)[0] if clean else ""
    return GROUP_LABELS.get(first, first.replace("_", " ").title() or "Root")


def split_segments(path: str) -> list[str]:
    clean = path.lstrip("/")
    return [segment for segment in clean.split("/") if segment]


def build_request(path: str, method: str) -> dict:
    header: list[dict[str, str]] = []
    if method in {"POST", "PUT", "PATCH"}:
        header = [{"key": "Content-Type", "value": "application/json"}]
    return {
        "name": f"{method} {path}",
        "request": {
            "method": method,
            "header": header,
            "url": {
                "raw": f"{{{{baseUrl}}}}{path}",
                "host": ["{{baseUrl}}"],
                "path": split_segments(path),
            },
        },
    }


def parse_routes() -> list[tuple[str, str]]:
    text = APP_FILE.read_text(encoding="utf-8")
    endpoints: set[tuple[str, str]] = set()
    for match in ROUTE_REGEX.finditer(text):
        method = match.group(1).upper()
        path = match.group(3).strip()
        if not path:
            continue
        endpoints.add((path, method))
    return sorted(endpoints)


def build_collection(endpoints: list[tuple[str, str]]) -> dict:
    grouped: defaultdict[str, list[dict]] = defaultdict(list)
    for path, method in endpoints:
        grouped[classify(path)].append(build_request(path, method))

    items = []
    for group_name in sorted(grouped.keys()):
        group_items = sorted(
            grouped[group_name],
            key=lambda item: (item["request"]["method"], item["name"]),
        )
        items.append({"name": group_name, "item": group_items})

    return {
        "info": {
            "name": "Headless Player API",
            "description": "Collezione completa auto-generata dalle route FastAPI di headless-player.",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "version": {"major": 2, "minor": 0, "patch": 0},
        },
        "variable": [
            {"key": "baseUrl", "value": "http://127.0.0.1:8080"},
            {"key": "udpHost", "value": "127.0.0.1"},
            {"key": "udpPort", "value": "7777"},
        ],
        "item": items,
    }


def main() -> None:
    endpoints = parse_routes()
    collection = build_collection(endpoints)
    COLLECTION_FILE.write_text(json.dumps(collection, indent=2), encoding="utf-8")
    print(f"Wrote {len(endpoints)} endpoints to {COLLECTION_FILE}")


if __name__ == "__main__":
    main()
