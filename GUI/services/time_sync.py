"""Lightweight app-level time synchronization over UDP.

Provides median-of-best-samples offset estimation and a small supervisor
to track offsets per player without any app configuration.
"""

from __future__ import annotations

import socket
import struct
import json
import statistics
import threading
import time
from typing import Optional


def compute_offset_delay(t0_ns: int, t1_ns: int, t2_ns: int, t3_ns: int) -> tuple[int, int]:
    """Compute offset and round-trip delay using NTP/SNTP formulas.

    Returns (offset_ns, delay_ns).
    """
    offset = ((t1_ns - t0_ns) + (t2_ns - t3_ns)) // 2
    delay = (t3_ns - t0_ns) - (t2_ns - t1_ns)
    return int(offset), int(delay)


def _udp_time_sample(host: str, port: int, timeout: float = 0.25) -> tuple[int, int]:
    """Send a single UDP time request and compute (offset_ns, delay_ns)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        t0 = time.time_ns()
        payload = json.dumps({"cmd": "time", "t0": int(t0)}).encode("utf-8")
        sock.sendto(payload, (host, int(port)))
        pkt, _ = sock.recvfrom(256)
        t3 = time.time_ns()
        try:
            data = json.loads(pkt.decode("utf-8", errors="ignore"))
            t1 = int(data.get("t1"))
            t2 = int(data.get("t2"))
            # t0 is echoed by server but use our own to avoid trust issues
        except Exception:
            raise RuntimeError("Invalid time packet")
        return compute_offset_delay(t0, t1, t2, t3)
    finally:
        try:
            sock.close()
        except Exception:
            pass


def measure_offset_udp(host: str, port: int = 7777, attempts: int = 8, timeout: float = 0.25) -> tuple[int, int]:
    """Take multiple samples and return robust (offset_ns, delay_ns).

    Picks the 30% with smallest delay and returns medians.
    """
    samples: list[tuple[int, int]] = []
    for _ in range(max(1, attempts)):
        try:
            off, dly = _udp_time_sample(host, port, timeout)
            samples.append((off, dly))
        except Exception:
            continue
    if not samples:
        raise RuntimeError("No time samples")
    samples.sort(key=lambda x: x[1])
    keep = max(1, len(samples) // 3)
    best = samples[:keep]
    return int(statistics.median([o for o, _ in best])), int(statistics.median([d for _, d in best]))


class TimeSyncSupervisor:
    """Manage background time sync per player (by IP)."""

    def __init__(self) -> None:
        self._threads: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self._latest: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._params: dict[str, tuple[int, float, float]] = {}

    def ensure_sync(self, ip: str, port: int = 7777, period_s: float = 2.0, alpha: float = 0.2) -> None:
        with self._lock:
            if ip in self._threads:
                return
            stop = threading.Event()
            th = threading.Thread(target=self._worker, args=(ip, port, stop, period_s, alpha), name=f"time-sync-{ip}", daemon=True)
            self._threads[ip] = (th, stop)
            self._params[ip] = (int(port), float(period_s), float(alpha))
            th.start()

    def stop_sync(self, ip: str) -> None:
        with self._lock:
            item = self._threads.pop(ip, None)
            self._params.pop(ip, None)
        if not item:
            return
        th, stop = item
        stop.set()
        try:
            th.join(timeout=1.0)
        except Exception:
            pass

    def restart_sync(self, ip: str) -> None:
        """Restart sync worker for a specific IP (reset smoothing)."""
        with self._lock:
            params = self._params.get(ip) or (7777, 2.0, 0.2)
        self.stop_sync(ip)
        self.ensure_sync(ip, *params)

    def stop_all(self) -> None:
        with self._lock:
            ips = list(self._threads.keys())
        for ip in ips:
            self.stop_sync(ip)

    def get_latest(self, ip: str) -> Optional[dict]:
        with self._lock:
            return self._latest.get(ip)

    def _worker(self, ip: str, port: int, stop: threading.Event, period_s: float, alpha: float) -> None:
        offset_est: Optional[int] = None
        while not stop.is_set():
            try:
                off, dly = measure_offset_udp(ip, port, attempts=6, timeout=0.25)
                if offset_est is None:
                    offset_est = off
                else:
                    offset_est = int((1.0 - alpha) * offset_est + alpha * off)
                with self._lock:
                    self._latest[ip] = {
                        "offset_ns": int(offset_est),
                        "delay_ns": int(dly),
                        "ts": int(time.time_ns()),
                        "method": "udp",
                    }
            except Exception:
                # Keep last good value; try again later
                pass
            # Sleep in short slices for responsiveness to stop
            total = max(0.5, float(period_s))
            end = time.time() + total
            while time.time() < end and not stop.is_set():
                time.sleep(0.1)
