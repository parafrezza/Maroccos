import json
import socket
import threading
import time
import sys
from pathlib import Path as _P

# Adjust sys.path to import local backends package
sys.path.insert(0, str((_P(__file__).resolve().parents[1])))
from backends.off_backend import OffBackend


def _udp_echo_server(bind_port: int, bucket: dict):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", bind_port))
    sock.settimeout(1.0)
    try:
        data, addr = sock.recvfrom(8192)
        try:
            payload = json.loads(data.decode("utf-8", errors="ignore"))
        except Exception:
            payload = None
        bucket["last"] = payload
        # Always reply ok for brightness
        resp = {"ok": True, "cmd": (payload or {}).get("cmd")}
        sock.sendto(json.dumps(resp).encode("utf-8"), addr)
    finally:
        try:
            sock.close()
        except Exception:
            pass


def test_off_backend_udp_fallback(monkeypatch):
    # Pick a free UDP port
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    srv_port = s.getsockname()[1]
    s.close()

    bucket = {}
    t = threading.Thread(target=_udp_echo_server, args=(srv_port, bucket), daemon=True)
    t.start()

    ctrl = {"OFF_HOST": "127.0.0.1", "OFF_PORT": 8082, "OFF_UDP_PORT": srv_port}
    be = OffBackend(ctrl)

    # Force HTTP path to raise, to trigger UDP fallback
    def _boom(*_a, **_kw):
        raise RuntimeError("HTTP not available in test")
    monkeypatch.setattr(be, "_post", _boom)

    # Should not raise
    be.visual_fade_to(0.5, 0.2)
    # Give server a moment
    time.sleep(0.05)
    assert isinstance(bucket.get("last"), dict)
    assert bucket["last"].get("cmd") == "brightness"

def test_off_backend_udp_goto_start(monkeypatch):
    # Pick a free UDP port
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    srv_port = s.getsockname()[1]
    s.close()

    bucket = {}
    t = threading.Thread(target=_udp_echo_server, args=(srv_port, bucket), daemon=True)
    t.start()

    ctrl = {"OFF_HOST": "127.0.0.1", "OFF_PORT": 8082, "OFF_UDP_PORT": srv_port}
    be = OffBackend(ctrl)

    def _boom(*_a, **_kw):
        raise RuntimeError("HTTP not available in test")
    monkeypatch.setattr(be, "_post", _boom)

    be.go_to_start()
    time.sleep(0.05)
    assert isinstance(bucket.get("last"), dict)
    assert bucket["last"].get("cmd") == "go_to_start"
