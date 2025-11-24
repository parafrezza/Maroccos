#!/usr/bin/env python3
from __future__ import annotations

import base64
import os
import shlex
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import requests

PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8AFAgMBAf"
    "v8H+UAAAAASUVORK5CYII="
)


def _decode_png() -> bytes:
    return base64.b64decode(PNG_BASE64)


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_decode_png())


def _ensure_healthz(base_url: str) -> None:
    resp = requests.get(f"{base_url}/healthz", timeout=5)
    resp.raise_for_status()


def _apply_playlist(base_url: str, items: list[Path]) -> dict[str, any]:
    payload = {"items": [str(item) for item in items], "loop": False}
    resp = requests.post(f"{base_url}/playlist/apply", json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _status(base_url: str) -> dict[str, any]:
    resp = requests.get(f"{base_url}/playlist/status", timeout=5)
    resp.raise_for_status()
    return resp.json()


def _prune(base_url: str, keep: list[Path]) -> dict[str, any]:
    payload = {"items": [str(p) for p in keep]}
    resp = requests.post(f"{base_url}/media/prune_to_playlist", json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _report_status(message: str) -> None:
    print(f"[QA] {message}", flush=True)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _tail_log(log_path: Path, lines: int = 40) -> str:
    try:
        data = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""
    return "\n".join(data[-lines:])


def _start_headless_process(repo_root: Path, port: int) -> subprocess.Popen:
    cmd_env = os.environ.get("HEADLESS_CMD")
    if cmd_env:
        args = shlex.split(cmd_env)
    else:
        args = [sys.executable, "app.py"]
    headless_dir = repo_root / "headless-player"
    log_dir = headless_dir / "_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "qa_headless.log"
    env = os.environ.copy()
    env["MEDIA_DIR"] = str(headless_dir / "media")
    env["APP_PORT"] = str(port)
    proc = subprocess.Popen(
        args,
        cwd=str(headless_dir),
        stdout=log_path.open("a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        env=env,
    )
    # Wait for health endpoint
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(30):
        try:
            _ensure_healthz(base_url)
            return proc
        except Exception:
            if proc.poll() is not None:
                tail = _tail_log(log_path)
                raise RuntimeError(f"Headless player exited prematurely.\nLog ({log_path}):\n{tail}")
            time.sleep(1.0)
    tail = _tail_log(log_path)
    raise RuntimeError(f"Headless player did not become ready in time.\nLog ({log_path}):\n{tail}")


@contextmanager
def headless_runner(repo_root: Path, port: int):
    proc = _start_headless_process(repo_root, port)
    try:
        yield None
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def _cleanup_files(files: list[Path]) -> None:
    for path in files:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass


def _run_scenario(base_url: str, media_dir: Path, qa_files: list[Path]) -> None:
    _report_status(f"Health check {base_url}/healthz")
    _ensure_healthz(base_url)

    _report_status(f"Creating QA media files at {media_dir}")
    for path in qa_files:
        _write_png(path)
    time.sleep(0.1)

    _report_status("Applying playlist with QA files")
    apply_resp = _apply_playlist(base_url, qa_files)
    print(apply_resp)

    status = _status(base_url)
    status_names = {Path(item).name for item in status.get("items", [])}
    expected_names = {f.name for f in qa_files}
    if not expected_names.issubset(status_names):
        raise RuntimeError(f"Playlist status missing entries: {status_names}")
    _report_status("Playlist contains QA entries.")

    _report_status("Pruning to keep only the first item")
    prune_resp = _prune(base_url, [qa_files[0]])
    print(prune_resp)
    if qa_files[1].exists():
        raise RuntimeError(f"{qa_files[1]} should have been pruned but still exists")

    status = _status(base_url)
    status_names = {Path(item).name for item in status.get("items", [])}
    if Path(qa_files[0]).name not in status_names:
        raise RuntimeError("First QA file was removed from playlist unexpectedly")

    _report_status("Clearing all media via prune_to_playlist(empty list)")
    prune_resp = _prune(base_url, [])
    print(prune_resp)
    if qa_files[0].exists():
        raise RuntimeError("Media clear should have removed leftover QA files")

    status = _status(base_url)
    if status.get("items"):
        raise RuntimeError("Playlist still contains items after pruning to empty set")

    _report_status("QA scenario completed successfully.")


def _verify_post_restart(base_url: str, media_dir: Path, qa_files: list[Path]) -> None:
    _report_status(f"Health check after restart {base_url}/healthz")
    _ensure_healthz(base_url)
    status = _status(base_url)
    if status.get("items"):
        raise RuntimeError(f"Playlist not empty after restart: {status.get('items')}")
    for path in qa_files:
        if path.exists():
            raise RuntimeError(f"{path} still exists after restart cleanup")
    _report_status("Post-restart verification passed (playlist empty, media clean).")


def main() -> int:
    base_url = os.environ.get("HEADLESS_URL")
    repo_root = Path(__file__).resolve().parent.parent
    media_dir = repo_root / "headless-player" / "media"
    qa_files = [media_dir / f"qa_asset_{i}.png" for i in range(1, 3)]
    auto_headless = base_url is None
    if auto_headless:
        port = _find_free_port()
        base_url = f"http://127.0.0.1:{port}"
    else:
        parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if "://" not in base_url:
            base_url = f"{parsed.scheme}://{parsed.netloc}"
    base_url = base_url.rstrip("/")

    try:
        if auto_headless:
            ctx = headless_runner(repo_root, port)
        else:
            @contextmanager
            def _dummy():
                yield None
            ctx = _dummy()
        with ctx:
            _run_scenario(base_url, media_dir, qa_files)
        if auto_headless:
            _report_status("Restarting player to verify state after reboot")
            with headless_runner(repo_root, port):
                _verify_post_restart(base_url, media_dir, qa_files)
        return 0
    except Exception as exc:
        print(f"[QA][ERROR] {exc}", file=sys.stderr)
        return 1
    finally:
        _cleanup_files(qa_files)


if __name__ == "__main__":
    raise SystemExit(main())
