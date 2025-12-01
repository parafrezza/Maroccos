"""Backend che controlla un'istanza esterna di cvlc tramite HTTP."""

from __future__ import annotations

import logging
import threading
from importlib import import_module
from pathlib import Path
from typing import Any

from . import register

_log = logging.getLogger(__name__)
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
        self._prefade_started = False
        self._endpause_done = False
        self._stop_requested = False
        self._last_state: str | None = None
        _log.info("Backend Cvlc inizializzato.")

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
                _log.info("Creazione di una nuova istanza di CvlcController.")
                self._controller = CvlcController(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    extra_args=extra_args,
                )
            else:
                _log.debug("Riconfigurazione dell'istanza esistente di CvlcController.")
                self._controller.reconfigure(host, port, user, password, extra_args)
        self._controller.ensure_running()
        self._ensure_monitor()
        return self._controller

    def _ensure_monitor(self) -> None:
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        _log.info("Avvio del thread di monitoraggio per CvlcBackend.")
        def _runner():
            while not self._monitor_stop:
                try:
                    ctrl = self._controller
                    if ctrl is None:
                        pass
                    else:
                        try:
                            status = ctrl.status()
                            st = status.get("state")
                        except Exception:
                            status = {}; st = None
                        prev_state = self._last_state
                        self._last_state = st
                        # Pre-fade overlay prima della fine clip: quando playing e il tempo residuo <= durata fade-in su stop
                        try:
                            if st == "playing":
                                self._stop_requested = False
                                # Leggi tempo e durata
                                cur = status.get("time")
                                total = status.get("length") or status.get("duration")
                                rem: float | None = None
                                try:
                                    if isinstance(cur, (int, float)) and isinstance(total, (int, float)) and float(total) > 0:
                                        rem = max(0.0, float(total) - float(cur))
                                except Exception:
                                    rem = None
                                if rem is not None and rem <= max(0.1, float(self._globals.get("OVERLAY_FADE_IN_ON_STOP_S", 1.0)) + 0.05):
                                    if not self._prefade_started:
                                        self._prefade_started = True
                                        _log.debug("Avvio pre-fade dell'overlay prima della fine della clip (rimanente: %.2fs)", rem)
                                        try:
                                            overlay_fade = self._globals.get("overlay_fade_to")
                                            if callable(overlay_fade):
                                                dur = min(max(0.05, float(self._globals.get("OVERLAY_FADE_IN_ON_STOP_S", 1.0))), max(0.05, rem))
                                                overlay_fade(1.0, dur)
                                                try:
                                                    g_log = self._globals.get("gui_log")
                                                    if callable(g_log):
                                                        g_log("overlay_prefade_before_end", data={"remaining": rem, "duration": dur})
                                                except Exception:
                                                    pass
                                        except Exception as e:
                                            _log.warning("Errore durante il pre-fade dell'overlay: %s", e)
                                            pass
                                # 2) Pausa all'ultimo frame (se richiesto)
                                if (
                                    rem is not None
                                    and rem <= 0.15
                                    and not self._endpause_done
                                    and not bool(self._loop)
                                    and bool(self._globals.get("STOP_AT_END"))
                                ):
                                    try:
                                        _log.debug("Pausa all'ultimo frame (rimanente: %.2fs)", rem)
                                        ctrl.pause()
                                        self._endpause_done = True
                                        self._globals["player"]["state"] = "paused"
                                        # assicura idle black e overlay nero già attivo
                                        try:
                                            show_idle = self._globals.get("show_idle_black")
                                            if callable(show_idle):
                                                show_idle()
                                        except Exception:
                                            pass
                                        try:
                                            g_log = self._globals.get("gui_log")
                                            if callable(g_log):
                                                g_log("paused_at_last_frame", data={"remaining": rem})
                                        except Exception:
                                            pass
                                    except Exception as e:
                                        _log.warning("Errore durante la pausa all'ultimo frame: %s", e)
                                        pass
                            else:
                                # Reset prefade flag quando non in playing
                                if self._prefade_started:
                                    self._prefade_started = False
                                if self._endpause_done:
                                    self._endpause_done = False
                        except Exception:
                            pass
                        if st == "stopped":
                            attempted_autoadvance = False
                            try:
                                if prev_state in {"playing", "paused"} and not self._stop_requested and not bool(self._globals.get("STOP_AT_END")):
                                    handler = self._globals.get("_handle_backend_end")
                                    if callable(handler):
                                        attempted_autoadvance = True
                                        try:
                                            glib = self._globals.get("GLib")
                                            if glib and hasattr(glib, "idle_add"):
                                                glib.idle_add(handler, "cvlc_eos")
                                            else:
                                                handler("cvlc_eos")
                                        except Exception:
                                            handler("cvlc_eos")
                            except Exception:
                                pass
                            self._stop_requested = False
                            if attempted_autoadvance:
                                continue
                            try:
                                splash = self._globals.get("splash")
                                if splash and not splash.get("active"):
                                    _log.debug("Player fermo e splash non attivo, mostro il nero.")
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
                except Exception as e:
                    _log.error("Errore nel thread di monitoraggio di CvlcBackend: %s", e, exc_info=True)
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
                _log.debug("Nascondo lo splash screen.")
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
            _log.error("Comando 'play' fallito: nessun file specificato.")
            raise RuntimeError("Nessun file specificato per la riproduzione")

        media_path = Path(path)
        if not media_path.exists():
            _log.error("File non trovato: %s", media_path)
            raise FileNotFoundError(str(media_path))

        _log.info("Avvio riproduzione per: %s (loop: %s, fade-in: %.2fs)", media_path, loop, fade_in)
        controller = self._get_controller()
        final_loop = self._loop if loop is None else bool(loop)
        try:
            controller.play(str(media_path), loop=final_loop, fade_in=fade_in)
        except VlcError as exc:
            _log.error("Errore durante l'avvio della riproduzione: %s", exc)
            raise RuntimeError(str(exc)) from exc

        self._loop = final_loop
        self._globals["VIDEO_PATH"] = str(media_path)
        self._globals["player"]["state"] = "playing"
        self._prefade_started = False
        self._endpause_done = False
        self._stop_requested = False
        self._last_state = "playing"
        self._hide_splash()

    def stop(self) -> None:
        _log.info("Arresto della riproduzione.")
        controller = self._get_controller()
        self._stop_requested = True
        try:
            controller.stop()
        except VlcError as exc:
            _log.error("Errore durante l'arresto: %s", exc)
            raise RuntimeError(str(exc)) from exc
        self._globals["player"]["state"] = "stopped"
        self._prefade_started = False
        self._endpause_done = False
        self._last_state = "stopped"

    def pause(self) -> None:
        """Pausa robusta: gestisce la corsa di stato immediatamente dopo un GO.

        Subito dopo un faststart_go/Resume, lo status HTTP di VLC può restare
        per pochi istanti su "paused"; una richiesta di pausa in quel momento
        verrebbe ignorata. Qui aspettiamo brevemente che lo stato sia playing
        (se in transito) e poi applichiamo la pausa, con piccoli retry bounded.
        """
        import time as _t

        _log.debug("Tentativo di pausa robusta...")
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
                _log.debug("Player già in pausa.")
                break
            if st == "playing":
                # Applica pausa
                _log.debug("Player in riproduzione, invio comando di pausa.")
                try:
                    controller.pause()
                except VlcError as exc:
                    last_exc = exc
                # ricontrolla dopo un battito
                _t.sleep(0.08)
            else:
                # Stato transitorio (stopped/unknown): attendi un attimo
                _log.debug("Stato del player transitorio ('%s'), in attesa...", st)
                _t.sleep(0.06)

            if _t.time() > deadline:
                # Ultimo tentativo best-effort
                _log.warning("Timeout per la pausa robusta, ultimo tentativo...")
                try:
                    controller.pause()
                except VlcError as exc:
                    last_exc = exc
                break

        if last_exc is not None:
            _log.error("Errore finale durante il tentativo di pausa: %s", last_exc)
            raise RuntimeError(str(last_exc)) from last_exc
        self._globals["player"]["state"] = "paused"
        self._prefade_started = False
        self._endpause_done = False
        self._last_state = "paused"

    def resume(self) -> None:
        _log.info("Ripresa della riproduzione.")
        controller = self._get_controller()
        try:
            controller.resume()
        except VlcError as exc:
            _log.error("Errore durante la ripresa: %s", exc)
            raise RuntimeError(str(exc)) from exc
        self._globals["player"]["state"] = "playing"
        self._prefade_started = False
        self._endpause_done = False
        self._stop_requested = False
        self._last_state = "playing"

    def set_loop(self, enabled: bool) -> None:
        _log.info("Impostazione loop a: %s", enabled)
        controller = self._get_controller()
        self._loop = bool(enabled)
        try:
            controller.set_loop(self._loop)
        except VlcError as exc:
            _log.error("Errore durante l'impostazione del loop: %s", exc)
            raise RuntimeError(str(exc)) from exc

    def fade_out(self, seconds: float) -> None:
        _log.info("Avvio fade-out del volume in %.2fs", seconds)
        controller = self._get_controller()
        try:
            controller.fade_out(seconds)
        except VlcError as exc:
            _log.error("Errore durante il fade-out: %s", exc)
            raise RuntimeError(str(exc)) from exc

    def fade_in(self, seconds: float) -> None:
        _log.info("Avvio fade-in del volume in %.2fs", seconds)
        controller = self._get_controller()
        try:
            controller.fade_in(seconds)
        except VlcError as exc:
            _log.error("Errore durante il fade-in: %s", exc)
            raise RuntimeError(str(exc)) from exc

    # --- Visual fades (brightness) ---
    def visual_fade_out(self, seconds: float) -> None:
        _log.info("Avvio visual fade-out (luminosità) in %.2fs", seconds)
        controller = self._get_controller()
        try:
            controller.visual_fade_to(0.0, seconds)
        except VlcError as exc:
            _log.error("Errore durante il visual fade-out: %s", exc)
            raise RuntimeError(str(exc)) from exc

    def visual_fade_in(self, seconds: float) -> None:
        _log.info("Avvio visual fade-in (luminosità) in %.2fs", seconds)
        controller = self._get_controller()
        try:
            controller.visual_fade_to(1.0, seconds)
        except VlcError as exc:
            _log.error("Errore durante il visual fade-in: %s", exc)
            raise RuntimeError(str(exc)) from exc

    def is_playing(self) -> bool:
        try:
            return self._get_controller().is_playing()
        except RuntimeError:
            return False

    def shutdown(self) -> None:
        _log.info("Arresto del backend Cvlc...")
        with self._lock:
            if self._controller is None:
                _log.debug("Nessun controller da arrestare.")
                return
            controller = self._controller
            self._controller = None
            self._monitor_stop = True
            _log.debug("Thread di monitoraggio fermato.")
        controller.shutdown()
        self._prefade_started = False
        self._endpause_done = False
        self._stop_requested = False
        self._last_state = None
        _log.info("Backend Cvlc arrestato.")

    # ---- Fast-start helpers ----
    def faststart_prepare(self, path: str) -> None:
        media_path = Path(path)
        if not media_path.exists():
            _log.error("Fast-start prepare fallito, file non trovato: %s", media_path)
            raise FileNotFoundError(str(media_path))
        _log.info("Preparazione media per fast-start: %s", media_path)
        controller = self._get_controller()
        try:
            controller.prepare_media(str(media_path))
        except VlcError as exc:
            _log.error("Errore durante la preparazione del media per fast-start: %s", exc)
            raise RuntimeError(str(exc)) from exc
        # aggiorna stato globale e nascondi splash
        self._globals["VIDEO_PATH"] = str(media_path)
        self._globals["player"]["state"] = "paused"
        self._prefade_started = False
        self._endpause_done = False
        self._stop_requested = False
        self._last_state = "paused"
        self._hide_splash()

    def faststart_go(self) -> None:
        _log.info("Esecuzione fast-start...")
        controller = self._get_controller()
        try:
            # Se già in pausa (da prepare), riprendi; altrimenti prova a play sull'ultimo media noto
            if controller.is_paused():
                _log.debug("Il player è in pausa, ripresa della riproduzione.")
                controller.resume()
            else:
                last = self._globals.get("VIDEO_PATH")
                if not last:
                    _log.error("Fast-start fallito: nessun media preparato.")
                    raise RuntimeError("Nessun media preparato")
                _log.debug("Il player non era in pausa, avvio della riproduzione dell'ultimo media: %s", last)
                controller.play(str(last), loop=self._loop, fade_in=0.0)
        except VlcError as exc:
            _log.error("Errore durante l'esecuzione del fast-start: %s", exc)
            raise RuntimeError(str(exc)) from exc
        self._prefade_started = False
        self._endpause_done = False
        self._stop_requested = False
        self._last_state = "playing"
