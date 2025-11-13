Time Synchronization (App-level)

Overview
- Goal: share a common logical clock between GUI and headless-player to schedule actions like play_at without touching the system clock.
- Approach: a lightweight SNTP-like exchange over UDP plus an HTTP /time endpoint for diagnostics.
- Precision: typically 0.5–3 ms on a stable LAN; good enough for phase-consistent starts.

Protocol
- UDP request to headless (default port 7777):
  - Request: {"cmd": "time", "t0": <client_ns>}
  - Response: {"ok": true, "t0": <echo>, "t1": <server_recv_ns>, "t2": <server_send_ns>}
- HTTP GET /time on headless returns {"ok": true, "now_ns": <server_ns>}.

Offset estimation (client-side)
- For each sample:
  - t0 = client send, t1 = server recv, t2 = server send, t3 = client recv
  - offset = ((t1 - t0) + (t2 - t3)) / 2
  - delay = (t3 - t0) - (t2 - t1)
- Take multiple samples, keep those with smallest delay, then median the offset; apply a gentle EMA to smooth drift.

Implementation
- Headless: implements UDP time reply in app.py and /time endpoint. No configuration required.
- GUI: starts a background sync worker per discovered player (GUI/services/time_sync.py) and injects the measured skew into the payload["timing"] before rendering.

Status payload
- Headless /status includes a timing section by default:
  - timing: { synced: false, method: null, offset_ms: 0, skew_ms: 0 }
- GUI augments this with the measured skew: timing.ok, timing.skew_ms, timing.method="udp".
- Other clients may use timing.skew_ms directly.

Windows notes
- No admin privileges needed; uses user-space sockets and time.time_ns().
- No system clock adjustments are performed.

Migration to OS PTP (optional)
- If sub-millisecond precision is required, rely on OS services (Windows Time with PTP, linuxptp) and keep the app-level mapping as a sanity check.

API Reference
-------------

HTTP
- GET `/time`
  - Request: none
  - Response: `200 OK`
    - `{ "ok": true, "now_ns": <int> }`
  - Example:
    - `curl -fsS http://<player-ip>:8080/time | jq`

- GET `/status` (excerpt)
  - Contains: `timing` object
    - `{ "timing": { "synced": false, "method": null, "offset_ms": 0, "skew_ms": 0 } }`
  - The GUI may augment this with `{ "ok": <bool>, "skew_ms": <int>, "method": "udp" }`.

UDP (default port 7777)
- Datagram to `<player-ip>:7777`
  - Request (UTF‑8 JSON):
    - `{ "cmd": "time", "t0": <client_ns> }`
  - Response (UTF‑8 JSON):
    - `{ "ok": true, "t0": <echo>, "t1": <server_recv_ns>, "t2": <server_send_ns> }`
  - Example with Python:
    ```python
    import socket, json, time
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    t0 = time.time_ns()
    s.sendto(json.dumps({"cmd":"time","t0":t0}).encode("utf-8"), ("<player-ip>", 7777))
    pkt, _ = s.recvfrom(256)
    t3 = time.time_ns()
    data = json.loads(pkt.decode("utf-8"))
    t1, t2 = data["t1"], data["t2"]
    offset = ((t1 - t0) + (t2 - t3)) // 2
    delay = (t3 - t0) - (t2 - t1)
    print("offset_ns=", offset, "delay_ns=", delay)
    ```

Troubleshooting
---------------
- UDP bind errors on the headless (port in use): the service logs a message and may fall back to no UDP; `/time` remains available.
- Firewalls: allow UDP to port 7777 from the GUI host if measuring over different subnets.
- Timebases: use `time.time_ns()` (monotonic-ish wall time). Do not mix with `time.perf_counter_ns()` in clients.
