"""Utility per testare i comandi UDP del headless-player.

Esegue una batteria di comandi (plain text e JSON), verifica lo stato del
player via HTTP e produce un riepilogo sintetico utile per la documentazione.

Uso:
    python tools/udp_command_tester.py

Il server headless deve essere già in esecuzione su http://127.0.0.1:8080
con UDP attivo sulla porta 7777 (setup di default).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests


BASE_URL = "http://127.0.0.1:8080"
UDP_ADDR = ("127.0.0.1", 7777)


# ----------------------------- helpers ----------------------------------


def fetch_status() -> dict:
    resp = requests.get(f"{BASE_URL}/status", timeout=2)
    resp.raise_for_status()
    return resp.json()


def wait_for(predicate: Callable[[dict], bool], timeout: float = 4.0, interval: float = 0.2) -> tuple[bool, dict]:
    deadline = time.time() + timeout
    last_status: dict | None = None
    while time.time() < deadline:
        try:
            last_status = fetch_status()
        except Exception:
            time.sleep(interval)
            continue
        if predicate(last_status):
            return True, last_status
        time.sleep(interval)
    if last_status is None:
        last_status = {}
    return False, last_status


def send_plain(command: str, expect_response: bool = False, timeout: float = 1.0) -> Optional[str]:
    buf = command.encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(buf, UDP_ADDR)
        if expect_response:
            try:
                data, _ = sock.recvfrom(4096)
                return data.decode("utf-8", "ignore")
            except Exception:
                return None
    return None


def send_json(payload: dict, expect_response: bool = False, timeout: float = 1.0) -> Optional[dict]:
    raw = json.dumps(payload)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(raw.encode("utf-8"), UDP_ADDR)
        if expect_response:
            try:
                data, _ = sock.recvfrom(4096)
            except Exception:
                return None
            try:
                return json.loads(data.decode("utf-8", "ignore"))
            except Exception:
                return None
    return None


def extract_off_index(status: dict) -> Optional[int]:
    try:
        return status["off"]["player"]["index"]
    except Exception:
        return None


def extract_off_playing(status: dict) -> Optional[bool]:
    try:
        return bool(status["off"]["player"]["playing"])
    except Exception:
        return None


def overlay_state(status: dict) -> tuple[Optional[bool], Optional[float]]:
    try:
        active = bool(status.get("overlay_active"))
    except Exception:
        active = None
    try:
        alpha = float(status.get("overlay_alpha")) if status.get("overlay_alpha") is not None else None
    except Exception:
        alpha = None
    return active, alpha


def splash_state(status: dict) -> Optional[bool]:
    try:
        return bool(status.get("splash_active"))
    except Exception:
        return None


def get_off_autostart() -> Optional[bool]:
    try:
        resp = requests.get(f"{BASE_URL}/off/autostart", timeout=2)
        resp.raise_for_status()
        data = resp.json()
        return bool(data.get("enabled"))
    except Exception:
        return None


def set_off_autostart(enabled: bool) -> bool:
    try:
        resp = requests.post(f"{BASE_URL}/off/autostart", json={"enabled": bool(enabled)}, timeout=2)
        resp.raise_for_status()
        return bool(resp.json().get("enabled")) == bool(enabled)
    except Exception:
        return False


def stop_off_process() -> None:
    try:
        requests.post(f"{BASE_URL}/off/process/stop", timeout=2)
    except Exception:
        pass


def kill_off_processes_windows() -> None:
    if os.name != "nt":
        return
    for image in ("OFF-player.exe", "OFF-player_debug.exe"):
        try:
            subprocess.run(["taskkill", "/IM", image, "/F"], capture_output=True, text=True)
        except Exception:
            pass


# ----------------------------- test infra --------------------------------


@dataclass
class TestResult:
    name: str
    passed: bool
    details: dict


TestFunc = Callable[[], TestResult]


def run_test(name: str, func: Callable[[], tuple[bool, dict]]) -> TestResult:
    success, info = False, {}
    try:
        success, info = func()
    except Exception as exc:  # raccogli eccezioni per la reportistica
        return TestResult(name=name, passed=False, details={"error": type(exc).__name__, "message": str(exc)})
    return TestResult(name=name, passed=success, details=info)


# ----------------------------- plain commands -----------------------------


def test_plain_status() -> tuple[bool, dict]:
    reply = send_plain("STATUS", expect_response=True)
    ok = reply is not None and reply.startswith("OK")
    return ok, {"response": reply}


def test_plain_pause() -> tuple[bool, dict]:
    send_plain("PAUSE")
    ok, status = wait_for(lambda s: s.get("player_state") == "paused", timeout=3)
    return ok, {"state": status.get("player_state"), "off_playing": extract_off_playing(status)}


def test_plain_resume() -> tuple[bool, dict]:
    send_plain("RESUME")
    ok, status = wait_for(lambda s: s.get("player_state") == "playing", timeout=3)
    return ok, {"state": status.get("player_state"), "off_playing": extract_off_playing(status)}


def test_plain_stop() -> tuple[bool, dict]:
    send_plain("STOP")
    ok, status = wait_for(lambda s: s.get("player_state") == "stopped", timeout=3)
    return ok, {"state": status.get("player_state"), "off_playing": extract_off_playing(status)}


def test_plain_play() -> tuple[bool, dict]:
    send_plain("PLAY")
    ok, status = wait_for(lambda s: s.get("player_state") == "playing", timeout=3)
    return ok, {"state": status.get("player_state"), "off_playing": extract_off_playing(status)}


def test_plain_stop_seconds() -> tuple[bool, dict]:
    send_plain("STOP 1.0")
    ok, status = wait_for(lambda s: s.get("player_state") == "stopped", timeout=4)
    if ok:
        # ripristina playback per i test successivi
        send_plain("PLAY")
        wait_for(lambda s: s.get("player_state") == "playing", timeout=3)
    return ok, {"state": status.get("player_state"), "note": "1s fade"}


def test_plain_next_prev() -> tuple[bool, dict]:
    before = fetch_status()
    start_index = extract_off_index(before)
    send_plain("NEXT")
    ok_next, after_next = wait_for(lambda s: extract_off_index(s) is not None and extract_off_index(s) != start_index, timeout=4)
    send_plain("PREV")
    ok_prev, after_prev = wait_for(lambda s: extract_off_index(s) == start_index, timeout=4)
    return ok_next and ok_prev, {
        "index_before": start_index,
        "index_after_next": extract_off_index(after_next),
        "index_after_prev": extract_off_index(after_prev),
    }


def test_plain_set() -> tuple[bool, dict]:
    status = fetch_status()
    count = status.get("off", {}).get("player", {}).get("count", 0)
    if count < 2:
        return False, {"error": "playlist has <2 elements"}
    send_plain("SET 1")
    ok_set, after_set = wait_for(lambda s: extract_off_index(s) == 1, timeout=4)
    send_plain("SET 0")
    ok_reset, after_reset = wait_for(lambda s: extract_off_index(s) == 0, timeout=4)
    return ok_set and ok_reset, {"after_set": extract_off_index(after_set), "after_reset": extract_off_index(after_reset)}


def test_plain_loop_toggle() -> tuple[bool, dict]:
    send_plain("LOOP OFF")
    ok_off, st_off = wait_for(lambda s: not s.get("playlist_loop", True), timeout=3)
    send_plain("LOOP ON")
    ok_on, st_on = wait_for(lambda s: s.get("playlist_loop", False), timeout=3)
    return ok_off and ok_on, {"loop_off": st_off.get("playlist_loop"), "loop_on": st_on.get("playlist_loop")}


def test_plain_overlay_cycle() -> tuple[bool, dict]:
    send_plain("FADE 1 0.5")
    ok_up, st_up = wait_for(lambda s: (overlay_state(s)[1] or 0.0) >= 0.95, timeout=3)
    send_plain("FADE 0 0.5")
    ok_down, st_down = wait_for(lambda s: (overlay_state(s)[1] or 0.0) <= 0.05, timeout=3)
    send_plain("OVERLAY_SHOW")
    ok_show, st_show = wait_for(lambda s: overlay_state(s)[0] and (overlay_state(s)[1] or 0.0) >= 0.95, timeout=3)
    send_plain("OVERLAY_ALPHA 0.3")
    ok_alpha, st_alpha = wait_for(lambda s: overlay_state(s)[1] is not None and abs((overlay_state(s)[1] or 0.0) - 0.3) < 0.05, timeout=3)
    send_plain("OVERLAY_HIDE")
    ok_hide, st_hide = wait_for(lambda s: not overlay_state(s)[0], timeout=3)
    return ok_up and ok_down and ok_show and ok_alpha and ok_hide, {
        "alpha_up": overlay_state(st_up)[1] if st_up else None,
        "alpha_down": overlay_state(st_down)[1] if st_down else None,
        "alpha_show": overlay_state(st_show)[1] if st_show else None,
        "alpha_custom": overlay_state(st_alpha)[1] if st_alpha else None,
        "overlay_active_after_hide": overlay_state(st_hide)[0] if st_hide else None,
    }


def test_plain_splash_cycle() -> tuple[bool, dict]:
    send_plain("SPLASH_SHOW hello")
    ok_show, st_show = wait_for(lambda s: splash_state(s) is True, timeout=3)
    send_plain("SPLASH_HIDE")
    ok_hide, st_hide = wait_for(lambda s: splash_state(s) is False, timeout=3)
    return ok_show and ok_hide, {"after_show": splash_state(st_show), "after_hide": splash_state(st_hide)}


# ----------------------------- json commands ------------------------------


def test_json_time() -> tuple[bool, dict]:
    reply = send_json({"cmd": "time", "t0": 123456789}, expect_response=True)
    ok = isinstance(reply, dict) and reply.get("ok") is True and reply.get("t0") == 123456789
    return ok, {"response": reply}


def test_json_ping() -> tuple[bool, dict]:
    before = fetch_status()
    send_json({"cmd": "ping", "duration_ms": 200})
    time.sleep(0.6)
    after = fetch_status()
    # Consideriamo riuscito se il server è ancora raggiungibile; salviamo l'eventuale variazione overlay (tipicamente minima su Windows)
    overlay_before = overlay_state(before)
    overlay_after = overlay_state(after)
    return True, {
        "overlay_before": overlay_before,
        "overlay_after": overlay_after,
        "note": "effetto visivo non osservabile via /status su this env"
    }


def test_json_pause_resume_stop_play() -> tuple[bool, dict]:
    send_json({"cmd": "pause"})
    ok_pause, st_pause = wait_for(lambda s: s.get("player_state") == "paused", timeout=3)
    send_json({"cmd": "resume"})
    ok_resume, st_resume = wait_for(lambda s: s.get("player_state") == "playing", timeout=3)
    send_json({"cmd": "stop"})
    ok_stop, st_stop = wait_for(lambda s: s.get("player_state") == "stopped", timeout=3)
    payload = {"cmd": "play", "filename": "test_cross_720p_animated.mp4", "loop": False, "fade_in_seconds": 0.2}
    send_json(payload)
    ok_play, st_play = wait_for(lambda s: s.get("player_state") == "playing", timeout=4)
    return ok_pause and ok_resume and ok_stop and ok_play, {
        "pause_state": st_pause.get("player_state") if st_pause else None,
        "resume_state": st_resume.get("player_state") if st_resume else None,
        "stop_state": st_stop.get("player_state") if st_stop else None,
        "play_state": st_play.get("player_state") if st_play else None,
        "play_loop": st_play.get("off", {}).get("player", {}).get("loop") if st_play else None,
    }


def test_json_stop_seconds() -> tuple[bool, dict]:
    send_json({"cmd": "stop", "seconds": 1.2})
    ok, st = wait_for(lambda s: s.get("player_state") == "stopped", timeout=4)
    if ok:
        send_json({"cmd": "play", "filename": "test_cross_720p_animated.mp4", "loop": True, "fade_in_seconds": 0.1})
        wait_for(lambda s: s.get("player_state") == "playing", timeout=4)
    return ok, {"state": st.get("player_state") if st else None, "note": "fade stop 1.2s"}


def test_json_playlist_navigation() -> tuple[bool, dict]:
    status = fetch_status()
    start = extract_off_index(status)
    send_json({"cmd": "next"})
    ok_next, after_next = wait_for(lambda s: extract_off_index(s) not in (None, start), timeout=4)
    send_json({"cmd": "prev"})
    ok_prev, after_prev = wait_for(lambda s: extract_off_index(s) == start, timeout=4)
    send_json({"cmd": "jump", "index": 1})
    ok_jump, after_jump = wait_for(lambda s: extract_off_index(s) == 1, timeout=4)
    send_json({"cmd": "jump", "index": start or 0})
    ok_reset, after_reset = wait_for(lambda s: extract_off_index(s) == (start or 0), timeout=4)
    return ok_next and ok_prev and ok_jump and ok_reset, {
        "start": start,
        "after_next": extract_off_index(after_next),
        "after_prev": extract_off_index(after_prev),
        "after_jump": extract_off_index(after_jump),
        "after_reset": extract_off_index(after_reset),
    }


def test_json_loop_toggle() -> tuple[bool, dict]:
    send_json({"cmd": "loop", "on": False})
    ok_off, st_off = wait_for(lambda s: not s.get("playlist_loop", True), timeout=3)
    send_json({"cmd": "loop", "on": True})
    ok_on, st_on = wait_for(lambda s: s.get("playlist_loop", False), timeout=3)
    return ok_off and ok_on, {"loop_off": st_off.get("playlist_loop") if st_off else None, "loop_on": st_on.get("playlist_loop") if st_on else None}


def test_json_overlay_cycle() -> tuple[bool, dict]:
    send_json({"cmd": "overlay_show", "alpha": 0.8})
    ok_show, st_show = wait_for(lambda s: overlay_state(s)[0] and abs((overlay_state(s)[1] or 0.0) - 0.8) < 0.05, timeout=3)
    send_json({"cmd": "overlay_fade", "target": 0.0, "seconds": 0.5})
    ok_fade, st_fade = wait_for(lambda s: (overlay_state(s)[1] or 0.0) <= 0.05, timeout=3)
    send_json({"cmd": "overlay_hide"})
    ok_hide, st_hide = wait_for(lambda s: not overlay_state(s)[0], timeout=3)
    return ok_show and ok_fade and ok_hide, {
        "alpha_show": overlay_state(st_show)[1] if st_show else None,
        "alpha_after_fade": overlay_state(st_fade)[1] if st_fade else None,
        "overlay_active_after_hide": overlay_state(st_hide)[0] if st_hide else None,
    }


def test_json_splash_cycle() -> tuple[bool, dict]:
    send_json({"cmd": "splash_show", "text": "json"})
    ok_show, st_show = wait_for(lambda s: splash_state(s) is True, timeout=3)
    send_json({"cmd": "splash_hide"})
    ok_hide, st_hide = wait_for(lambda s: splash_state(s) is False, timeout=3)
    return ok_show and ok_hide, {"after_show": splash_state(st_show), "after_hide": splash_state(st_hide)}


def test_json_shutdown() -> tuple[bool, dict]:
    send_json({"cmd": "shutdown"})
    ok, _ = wait_for(lambda _: False, timeout=1)  # attende brevemente prima di verificare HTTP down
    try:
        requests.get(f"{BASE_URL}/status", timeout=1)
        reachable = True
    except Exception:
        reachable = False
    return (not reachable), {"reachable_after_shutdown": reachable}


PLAIN_TESTS: list[tuple[str, Callable[[], tuple[bool, dict]]]] = [
    ("plain:STATUS", test_plain_status),
    ("plain:PAUSE", test_plain_pause),
    ("plain:RESUME", test_plain_resume),
    ("plain:STOP", test_plain_stop),
    ("plain:PLAY", test_plain_play),
    ("plain:STOP seconds", test_plain_stop_seconds),
    ("plain:NEXT/PREV", test_plain_next_prev),
    ("plain:SET", test_plain_set),
    ("plain:LOOP", test_plain_loop_toggle),
    ("plain:OVERLAY", test_plain_overlay_cycle),
    ("plain:SPLASH", test_plain_splash_cycle),
]


JSON_TESTS: list[tuple[str, Callable[[], tuple[bool, dict]]]] = [
    ("json:time", test_json_time),
    ("json:ping", test_json_ping),
    ("json:pause/resume/stop/play", test_json_pause_resume_stop_play),
    ("json:stop seconds", test_json_stop_seconds),
    ("json:playlist nav", test_json_playlist_navigation),
    ("json:loop", test_json_loop_toggle),
    ("json:overlay", test_json_overlay_cycle),
    ("json:splash", test_json_splash_cycle),
    ("json:shutdown", test_json_shutdown),
]


def main() -> int:
    results: list[TestResult] = []
    original_autostart: Optional[bool] = None
    autostart_modified = False
    exit_code = 1

    stop_off_process()
    kill_off_processes_windows()

    try:
        original_autostart = get_off_autostart()
        if original_autostart is not None and original_autostart:
            if set_off_autostart(False):
                autostart_modified = True
                print("OFF autostart disabilitato temporaneamente per il test.")

        print("Attendo la disponibilità di /status...")
        ready, initial = wait_for(lambda s: bool(s), timeout=10, interval=0.5)
        if not ready:
            print("Server non raggiungibile su http://127.0.0.1:8080/status", file=sys.stderr)
            return 1
        print(f"Server pronto, stato iniziale: player_state={initial.get('player_state')} index={extract_off_index(initial)}")

        if initial.get("player_state") != "playing":
            media = initial.get("current_media")
            if not media:
                ready_for = initial.get("ready_for")
                if isinstance(ready_for, str) and ready_for:
                    media = ready_for.split("\\")[-1].split("/")[-1]
            if not media:
                media = "a.mp4"
            print(f"Avvio playback di test su {media}...")
            send_json({"cmd": "play", "filename": media, "loop": True, "fade_in_seconds": 0.2})
            ready_playing, new_status = wait_for(lambda s: s.get("player_state") == "playing", timeout=6)
            if ready_playing:
                initial = new_status
            else:
                print("Warning: impossibile avviare il playback iniziale", file=sys.stderr)

        print("== Plain text commands ==")
        for name, func in PLAIN_TESTS:
            result = run_test(name, func)
            results.append(result)
            status_word = "PASS" if result.passed else "FAIL"
            print(f"[{status_word}] {result.name} -> {json.dumps(result.details, ensure_ascii=False)}")

        print("\n== JSON commands ==")
        for name, func in JSON_TESTS:
            result = run_test(name, func)
            results.append(result)
            status_word = "PASS" if result.passed else "FAIL"
            print(f"[{status_word}] {result.name} -> {json.dumps(result.details, ensure_ascii=False)}")
            if result.name == "json:shutdown" and result.passed:
                print("Server spento via UDP: gli endpoint HTTP non saranno più raggiungibili.")

        summary = {
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": [r.name for r in results if not r.passed],
        }
        print("\n== Summary ==")
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        exit_code = 0 if summary["passed"] == summary["total"] else 1
    finally:
        stop_off_process()
        kill_off_processes_windows()
        if autostart_modified and original_autostart is not None:
            set_off_autostart(original_autostart)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
