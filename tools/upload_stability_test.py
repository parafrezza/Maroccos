"""Simple stability test for headless-player upload endpoints.

Run after starting headless-player locally:
  powershell> $env:APP_PORT=8090; $env:MEDIA_DIR="path_to_test_media"; python headless-player/app.py
Then execute:
  python tools/upload_stability_test.py --host http://127.0.0.1:8090 --media _test_media

It will:
1. Create a temporary binary file.
2. Test direct push (/upload_asset).
3. Test pull download (/download_asset) via a small transient HTTP server.
4. Print results and basic assertions.
"""
from __future__ import annotations
import argparse, tempfile, os, threading, time, http.server, socketserver, pathlib, sys, hashlib
import requests

DEF_SIZE = 64 * 1024  # 64KB test payload

class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args):
        pass

def _start_file_server(directory: str, port: int) -> tuple[threading.Thread, socketserver.TCPServer]:
    handler = _QuietHandler
    os.chdir(directory)
    httpd = socketserver.TCPServer(("", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return t, httpd

def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:8090", help="Headless base URL")
    ap.add_argument("--media", default=None, help="MEDIA_DIR path (for verification)")
    ap.add_argument("--port", type=int, default=8765, help="Ephemeral local file server port for pull test")
    args = ap.parse_args()
    base = args.host.rstrip("/")
    media_dir = pathlib.Path(args.media) if args.media else None

    # 1. Create temp file
    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="upload_stab_"))
    # Windows non consente '?' nel filename: usiamo '#' e spazi per verificare sanitizzazione
    test_file = tmp_dir / "prova#file spazi video.mp4"
    payload = os.urandom(DEF_SIZE)
    try:
        test_file.write_bytes(payload)
    except Exception as e:
        print(f"[TEST] Impossibile creare file di prova: {e}")
        return
    print(f"[TEST] Created test file {test_file} size={len(payload)} bytes sha256={sha256(test_file)}")

    # 2. Direct push
    push_name = "push_test.mp4"
    with open(test_file, "rb") as fh:
        files = {"file": (push_name, fh, "application/octet-stream")}
        r = requests.post(f"{base}/upload_asset", files=files, params={"filename": push_name}, timeout=60)
    print("[TEST] Push status", r.status_code, r.text)
    r.raise_for_status()
    j = r.json(); assert j.get("ok"), "Push risposta non ok"
    if media_dir:
        dest = media_dir / push_name
        assert dest.exists(), f"File push non trovato in MEDIA_DIR: {dest}";
        assert dest.stat().st_size == DEF_SIZE, "Dimensione file push errata"
    print("[TEST] Push OK")

    # 3. Pull download using temporary HTTP server
    server_dir = tmp_dir
    server_thread, httpd = _start_file_server(str(server_dir), args.port)
    time.sleep(0.5)
    pull_url = f"http://{requests.get('https://api.ipify.org').text if False else '127.0.0.1'}:{args.port}/{test_file.name}"
    # Use original complex name
    r2 = requests.post(f"{base}/download_asset", json={"url": pull_url, "filename": "pull_test.mp4"}, timeout=60)
    print("[TEST] Pull status", r2.status_code, r2.text)
    r2.raise_for_status(); j2 = r2.json(); assert j2.get("ok"), "Pull risposta non ok"
    if media_dir:
        dest2 = media_dir / "pull_test.mp4"
        assert dest2.exists(), f"File pull non trovato: {dest2}"
        assert dest2.stat().st_size == DEF_SIZE, "Dimensione file pull errata"
    print("[TEST] Pull OK")

    httpd.shutdown()
    print("[TEST] Completato. Tutte le verifiche passate.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[TEST] ERRORE: {e}", file=sys.stderr)
        sys.exit(1)
