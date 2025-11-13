#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VLC Remote Fader (minimal)
- Fade in/out solo con Brightness + Contrast
- Take (fade out -> load -> play -> fade in)
"""

import argparse, json, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path as _Path
from urllib.parse import urlparse, parse_qs, unquote
import vlc

# --- Adjust option indices (solo quelli necessari) ---
ADJ_CONTRAST   = 1
ADJ_BRIGHTNESS = 2

def _ease(t: float, mode: str) -> float:
    if mode == "linear": return t
    if mode == "ease": return t*t*(3-2*t)
    if mode == "ease_in": return t*t
    if mode == "ease_out": return 1-(1-t)*(1-t)
    return t

class PlayerController:
    def __init__(self, vlc_args=None):
        self.instance = vlc.Instance(*(vlc_args or ["--no-video-title-show"]))
        self.player = vlc.MediaPlayer()
        self.player.video_set_adjust_int(0, 1)  # abilita filtro adjust
        self._saved_contrast = 1.0
        # Preload
        self._pre_media = None
        self._pre_uri = None
        self._pre_lock = threading.Lock()

    # ------------ Core ------------
    def _to_uri(self, source: str):
        s = source.strip()
        if os.name == "nt" and (":\\" in s or s.startswith("\\\\")):
            try: return _Path(s).resolve().as_uri()
            except Exception: return s
        return s

    def load(self, source: str):
        uri = self._to_uri(source)
        self.player.set_media(self.instance.media_new(uri))
        return {"ok": True, "loaded": uri}

    def play(self): self.player.play(); return {"ok": True}
    def pause(self): self.player.pause(); return {"ok": True}
    def stop(self): self.player.stop(); return {"ok": True}

    # ------------ Fades (Brightness + Contrast) ------------
    def _fade_bc_sync(self, to_black: bool, dur: float, fps: int = 60, curve: str = "ease"):
        try:
            start_b = self.player.video_get_adjust_float(ADJ_BRIGHTNESS) or 1.0
        except Exception:
            start_b = 1.0
        try:
            start_c = self.player.video_get_adjust_float(ADJ_CONTRAST) or 1.0
        except Exception:
            start_c = 1.0

        if to_black:
            if start_c > 0.05:
                self._saved_contrast = start_c
            target_b = 0.0
            target_c = 0.0
        else:
            target_b = 1.0
            target_c = self._saved_contrast if start_c <= 0.05 else start_c
            if target_c < 0.2:
                target_c = 1.0

        dur = max(0.0, float(dur))
        fps = max(10, int(fps))
        if dur == 0:
            try:
                self.player.video_set_adjust_float(ADJ_BRIGHTNESS, target_b)
                self.player.video_set_adjust_float(ADJ_CONTRAST, target_c)
            except Exception:
                pass
            return {"ok": True, "instant": True, "to_black": to_black}

        gamma = 1.0 if to_black else (1.4 if start_c <= 0.05 else 1.0)
        t0 = time.time()
        step = 1.0 / fps
        while True:
            elapsed = time.time() - t0
            t = min(1.0, elapsed / dur)
            tt = _ease(t, curve)
            if gamma != 1.0:
                tt = tt ** gamma
            cur_b = start_b + (target_b - start_b) * tt
            cur_c = start_c + (target_c - start_c) * tt
            try:
                self.player.video_set_adjust_float(ADJ_BRIGHTNESS, cur_b)
                self.player.video_set_adjust_float(ADJ_CONTRAST, cur_c)
            except Exception:
                break
            if t >= 1.0:
                break
            time.sleep(step)
        if not to_black:
            try:
                fin_c = self.player.video_get_adjust_float(ADJ_CONTRAST)
                if fin_c and fin_c > 0.05:
                    self._saved_contrast = fin_c
            except Exception:
                pass
        return {"ok": True, "to_black": to_black, "duration": dur}

    # ------------ TAKE ------------
    def _wait_playing(self, timeout=2.0):
        t0 = time.time(); last = -1
        while time.time() - t0 < timeout:
            st = self.player.get_state()
            cur = self.player.get_time()
            if st == vlc.State.Playing and cur >= 0:
                if last != -1 and cur > last:
                    return True
                last = cur
            time.sleep(0.05)
        return False

    def take(self, source: str | None,
             dur_total: float = 1.2,
             dur_out: float | None = None,
             dur_in: float | None = None,
             use_preload: bool = False,
             fps: int = 60,
             curve_out: str = "ease",
             curve_in: str = "ease"):
        if dur_out is None or dur_in is None:
            half = max(0.0, float(dur_total)) / 2.0
            dur_out = half if dur_out is None else float(dur_out)
            dur_in  = half if dur_in  is None else float(dur_in)
        fade_out = self._fade_bc_sync(True, dur_out, fps=fps, curve=curve_out)
        self.pause()
        loaded_uri = None
        if use_preload:
            with self._pre_lock:
                if self._pre_media is not None and (source is None or self._pre_uri == self._to_uri(source)):
                    self.player.set_media(self._pre_media)
                    loaded_uri = self._pre_uri
                    self._pre_media = None; self._pre_uri = None
                elif source:
                    loaded_uri = self.load(source)["loaded"]
        else:
            if source:
                loaded_uri = self.load(source)["loaded"]
        self.play()
        self._wait_playing()
        fade_in = self._fade_bc_sync(False, dur_in, fps=fps, curve=curve_in)
        return {"ok": True, "loaded": loaded_uri, "fade_out": fade_out, "fade_in": fade_in}

    # ------------ Preload ------------
    def preload(self, source: str):
        uri = self._to_uri(source)
        m = self.instance.media_new(uri)
        with self._pre_lock:
            self._pre_media = m
            self._pre_uri = uri
        return {"ok": True, "preloaded": uri}

    def preload_clear(self):
        with self._pre_lock:
            self._pre_media = None; self._pre_uri = None
        return {"ok": True}

    def take_preloaded(self, dur_total=1.2, dur_out=None, dur_in=None,
                       fps=60, curve_out="ease", curve_in="ease"):
        with self._pre_lock:
            if self._pre_media is None:
                return {"ok": False, "error": "no preloaded media"}
        return self.take(None, dur_total, dur_out, dur_in, True, fps, curve_out, curve_in)

    # ------------ Status ------------
    def status(self):
        try:
            return {
                "ok": True,
                "state": str(self.player.get_state()),
                "time_ms": self.player.get_time(),
                "length_ms": self.player.get_length(),
                "contrast": self.player.video_get_adjust_float(ADJ_CONTRAST),
                "brightness": self.player.video_get_adjust_float(ADJ_BRIGHTNESS),
                "preloaded": self._pre_uri
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

controller = PlayerController()

# ------------ HTTP ------------
class Handler(BaseHTTPRequestHandler):
    def _send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            parsed = urlparse(self.path); path = parsed.path.rstrip("/") or "/"
            qs = parse_qs(parsed.query or ""); q = lambda k,d=None: qs.get(k,[d])[0]
            if path == "/":
                return self._send_json({"ok": True, "endpoints":[
                    "/load?input=PATH_OR_URL",
                    "/play","/pause","/stop",
                    "/fadeout?dur=1.2&fps=60&curve=ease",
                    "/fadein?dur=1.2&fps=60&curve=ease",
                    "/take?input=PATH_OR_URL&dur=1.2",
                    "/preload?input=PATH_OR_URL",
                    "/preload/clear",
                    "/take_preloaded?dur=1.2",
                    "/status"
                ]})
            if path=="/load": return self._send_json(controller.load(unquote(q("input",""))))
            if path=="/play": return self._send_json(controller.play())
            if path=="/pause": return self._send_json(controller.pause())
            if path=="/stop": return self._send_json(controller.stop())
            if path=="/fadeout":
                dur=float(q("dur","1.2")); fps=int(float(q("fps","60"))); curve=q("curve","ease")
                return self._send_json(controller._fade_bc_sync(True,dur,fps,curve))
            if path=="/fadein":
                dur=float(q("dur","1.2")); fps=int(float(q("fps","60"))); curve=q("curve","ease")
                return self._send_json(controller._fade_bc_sync(False,dur,fps,curve))
            if path=="/take":
                src = unquote(q("input","")) or None
                dur = float(q("dur","1.2"))
                return self._send_json(controller.take(src, dur))
            if path=="/preload":
                return self._send_json(controller.preload(unquote(q("input",""))))
            if path=="/preload/clear":
                return self._send_json(controller.preload_clear())
            if path=="/take_preloaded":
                dur=float(q("dur","1.2"))
                return self._send_json(controller.take_preloaded(dur))
            if path=="/status":
                return self._send_json(controller.status())
            return self._send_json({"ok": False, "error": "unknown"}, 404)
        except Exception as e:
            return self._send_json({"ok": False, "error": str(e)}, 500)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8081)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[HTTP] http://{args.host}:{args.port}  (Ctrl+C per uscire)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()
        srv.server_close()

if __name__ == "__main__":
    main()
