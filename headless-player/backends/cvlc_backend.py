"""Backend che controlla un'istanza esterna di cvlc tramite HTTP."""

from __future__ import annotations

import threading
from importlib import import_module
from pathlib import Path
from typing import Any

from . import register

_cvlc_module = import_module("cvlc_controller")
CvlcController = getattr(_cvlc_module, "CvlcController")
VlcError = getattr(_cvlc_module, "VlcError")


@register("cvlc")
class CvlcBackend:
    """Backend leggero basato su cvlc headless."""

    supports_playlist = False

    def __init__(self, controller: dict) -> None:
        self._globals = controller
        self._lock = threading.Lock()
        self._controller: Any | None = None
        self._loop = False
        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop = False

    # ------------------------------------------------------------------
    # Helpers

    def _current_config(self) -> tuple[str, int, str, str, list[str]]:
        g = self._globals
        host = g.get("VLC_HTTP_HOST", "127.0.0.1")
        port = int(g.get("VLC_HTTP_PORT", 8090))
        user = g.get("VLC_HTTP_USER", "")
        password = g.get("VLC_HTTP_PASSWORD", "vlcpass")
        extra_args = list(g.get("VLC_EXTRA_ARGS", []))
        return host, port, user, password, extra_args

    def _get_controller(self):
        host, port, user, password, extra_args = self._current_config()
        with self._lock:
            if self._controller is None:
                self._controller = CvlcController(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    extra_args=extra_args,
                )
            else:
                self._controller.reconfigure(host, port, user, password, extra_args)
        self._controller.ensure_running()
        self._ensure_monitor()
        return self._controller

    def _ensure_monitor(self) -> None:
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        def _runner():
            # Monitora lo stato VLC e, quando risulta fermo e lo splash è nascosto, mostra il nero in pausa
            while not self._monitor_stop:
                try:
                    ctrl = self._controller
                    if ctrl is None:
                        pass
                    else:
                        try:
                            st = ctrl.status().get("state")
                        except Exception:
                            st = None
                        if st == "stopped":
                            try:
                                splash = self._globals.get("splash")
                                if splash and not splash.get("active"):
                                    # Prima riporta overlay a nero con un fade morbido; poi assicurati del nero idle
                                    try:
                                        overlay_fade = self._globals.get("overlay_fade_to")
                                        if callable(overlay_fade):
                                            dur = float(self._globals.get("OVERLAY_FADE_IN_ON_STOP_S", 1.0))
                                            overlay_fade(1.0, max(0.05, dur))
                                            try:
                                                g_log = self._globals.get("gui_log")
                                                if callable(g_log):
                                                    g_log("overlay_fade_in_on_stop", data={"duration": dur})
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                                    show_idle = self._globals.get("show_idle_black")
                                    if callable(show_idle):
                                        show_idle()
                                        # Aggiorna stato globale
                                        try:
                                            self._globals["player"]["state"] = "stopped"
                                        except Exception:
                                            pass
                                        try:
                                            g_log = self._globals.get("gui_log")
                                            if callable(g_log):
                                                g_log("idle_black_show")
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                except Exception:
                    pass
                finally:
                    try:
                        import time as _t
                        _t.sleep(0.5)
                    except Exception:
                        pass
        self._monitor_thread = threading.Thread(target=_runner, name="cvlc-monitor", daemon=True)
        self._monitor_thread.start()

    def _hide_splash(self) -> None:
        splash = self._globals.get("splash")
        if splash and splash.get("active"):
            try:
                self._globals["hide_splash"]()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Backend API
    @property
    def name(self) -> str:
        return "cvlc"

    def play(self, path: str | None = None, loop: bool | None = None, fade_in: float = 0.0) -> None:
        if not path:
            path = self._globals.get("VIDEO_PATH")
        if not path:
            raise RuntimeError("Nessun file specificato per la riproduzione")

        media_path = Path(path)
        if not media_path.exists():
            raise FileNotFoundError(str(media_path))

        controller = self._get_controller()
        final_loop = self._loop if loop is None else bool(loop)
        try:
            controller.play(str(media_path), loop=final_loop, fade_in=fade_in)
        except VlcError as exc:
            raise RuntimeError(str(exc)) from exc

        self._loop = final_loop
        self._globals["VIDEO_PATH"] = str(media_path)
        self._globals["player"]["state"] = "playing"
        self._hide_splash()

    def stop(self) -> None:
        controller = self._get_controller()
        try:
            controller.stop()
        except VlcError as exc:
            raise RuntimeError(str(exc)) from exc
        self._globals["player"]["state"] = "stopped"

    def pause(self) -> None:
        """Pausa robusta: gestisce la corsa di stato immediatamente dopo un GO.

        Subito dopo un faststart_go/Resume, lo status HTTP di VLC può restare
        per pochi istanti su "paused"; una richiesta di pausa in quel momento
        verrebbe ignorata. Qui aspettiamo brevemente che lo stato sia playing
        (se in transito) e poi applichiamo la pausa, con piccoli retry bounded.
        """
        import time as _t

        controller = self._get_controller()
        deadline = _t.time() + 0.6  # max ~600ms di tolleranza
        last_exc: Exception | None = None
        while True:
            try:
                st = controller.status().get("state")
            except Exception:
                st = None

            if st == "paused":
                # Già in pausa: fatto
                break
            if st == "playing":
                # Applica pausa
                try:
                    controller.pause()
                except VlcError as exc:
                    last_exc = exc
                # ricontrolla dopo un battito
                _t.sleep(0.08)
            else:
                # Stato transitorio (stopped/unknown): attendi un attimo
                _t.sleep(0.06)

            if _t.time() > deadline:
                # Ultimo tentativo best-effort
                try:
                    controller.pause()
                except VlcError as exc:
                    last_exc = exc
                break

        if last_exc is not None:
            raise RuntimeError(str(last_exc)) from last_exc
        self._globals["player"]["state"] = "paused"

    def resume(self) -> None:
        controller = self._get_controller()
        try:
            controller.resume()
        except VlcError as exc:
            raise RuntimeError(str(exc)) from exc
        self._globals["player"]["state"] = "playing"

    def set_loop(self, enabled: bool) -> None:
        controller = self._get_controller()
        self._loop = bool(enabled)
        try:
            controller.set_loop(self._loop)
        except VlcError as exc:
            raise RuntimeError(str(exc)) from exc

    def fade_out(self, seconds: float) -> None:
        controller = self._get_controller()
        try:
            controller.fade_out(seconds)
        except VlcError as exc:
            raise RuntimeError(str(exc)) from exc

    def fade_in(self, seconds: float) -> None:
        controller = self._get_controller()
        try:
            controller.fade_in(seconds)
        except VlcError as exc:
            raise RuntimeError(str(exc)) from exc

    # --- Visual fades (brightness) ---
    def visual_fade_out(self, seconds: float) -> None:
        controller = self._get_controller()
        try:
            controller.visual_fade_to(0.0, seconds)
        except VlcError as exc:
            raise RuntimeError(str(exc)) from exc

    def visual_fade_in(self, seconds: float) -> None:
        controller = self._get_controller()
        try:
            controller.visual_fade_to(1.0, seconds)
        except VlcError as exc:
            raise RuntimeError(str(exc)) from exc

    def is_playing(self) -> bool:
        try:
            return self._get_controller().is_playing()
        except RuntimeError:
            return False

    def shutdown(self) -> None:
        with self._lock:
            if self._controller is None:
                return
            controller = self._controller
            self._controller = None
            self._monitor_stop = True
        controller.shutdown()

    # ---- Fast-start helpers ----
    def faststart_prepare(self, path: str) -> None:
        media_path = Path(path)
        if not media_path.exists():
            raise FileNotFoundError(str(media_path))
        controller = self._get_controller()
        try:
            controller.prepare_media(str(media_path))
        except VlcError as exc:
            raise RuntimeError(str(exc)) from exc
        # aggiorna stato globale e nascondi splash
        self._globals["VIDEO_PATH"] = str(media_path)
        self._globals["player"]["state"] = "paused"
        self._hide_splash()

    def faststart_go(self) -> None:
        controller = self._get_controller()
        try:
            # Se già in pausa (da prepare), riprendi; altrimenti prova a play sull'ultimo media noto
            if controller.is_paused():
                controller.resume()
            else:
                last = self._globals.get("VIDEO_PATH")
                if not last:
                    raise RuntimeError("Nessun media preparato")
                controller.play(str(last), loop=self._loop, fade_in=0.0)
        except VlcError as exc:
            raise RuntimeError(str(exc)) from exc
