"""Helper per gestire un'istanza di cvlc controllata via interfaccia HTTP Lua."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional
import socket

import requests
from requests.auth import HTTPBasicAuth


class VlcError(RuntimeError):
    """Errore generico del controller VLC."""


class CvlcController:
    """Gestisce il ciclo di vita di cvlc e fornisce primitive di controllo."""

    DEFAULT_VOLUME = 256  # Valore volume "normale" per VLC (100%).

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8090,
        user: str = "",
        password: str = "vlcpass",
        extra_args: Optional[list[str]] = None,
        executable: str = "cvlc",
    ) -> None:
        self.host = host
        self.port = int(port)
        self.user = user or ""
        self.password = password
        self.extra_args = list(extra_args or [])
        self.executable = executable

        self._proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()
        self._fade_lock = threading.Lock()
        self._fade_token = 0
        self._last_volume = self.DEFAULT_VOLUME
        auth_user = self.user or ""
        self._auth = HTTPBasicAuth(auth_user, self.password) if auth_user or self.password else None

    # ------------------------------------------------------------------
    # Process lifecycle
    def ensure_running(self) -> None:
        """Avvia cvlc se non è in esecuzione."""

        with self._proc_lock:
            if self._proc and self._proc.poll() is None:
                return

            env = os.environ.copy()
            env.setdefault("VLC_HTTP_HOST", self.host)
            env.setdefault("VLC_HTTP_PORT", str(self.port))
            env.setdefault("VLC_HTTP_PASSWORD", self.password)
            # Mitiga errori legati a PulseAudio/XDG su ambienti headless
            env.setdefault("XDG_RUNTIME_DIR", "/tmp")

            # Log file per diagnosi avvio VLC
            log_path = env.get("CVLC_LOG", "/tmp/cvlc_stderr.log")
            try:
                self._log_file = open(log_path, "ab", buffering=0)
            except Exception:
                self._log_file = None

            def launch(cmd: list[str]) -> None:
                try:
                    self._proc = subprocess.Popen(  # noqa: S603,S607 - comando controllato
                        cmd,
                        stdout=self._log_file or subprocess.DEVNULL,
                        stderr=self._log_file or subprocess.DEVNULL,
                        env=env,
                    )
                except FileNotFoundError as exc:  # pragma: no cover - dipende da runtime
                    raise VlcError(f"Impossibile eseguire '{self.executable}': {exc}") from exc
                except Exception as exc:  # pragma: no cover
                    raise VlcError(f"Errore avviando cvlc: {exc}") from exc

            # Config completa (luahttp+rc) - niente opzioni 'adjust-*' CLI per massima compatibilità
            cmd_full: list[str] = [
                self.executable,
                "--intf",
                "dummy",
                "--extraintf",
                "luahttp,rc",
                "--http-host",
                self.host,
                "--http-port",
                str(self.port),
                "--http-password",
                self.password,
                "--rc-host",
                "127.0.0.1:9090",
                "--no-video-title-show",
                "--no-osd",
                "--quiet",
            ]
            if self.user:
                cmd_full.extend(
                    [
                        "--lua-config",
                        f"http={{host='{self.host}:{self.port}', user='{self.user}', passwd='{self.password}'}}",
                    ]
                )
            if self.extra_args:
                cmd_full.extend(self.extra_args)

            # Config fallback minimale (solo luahttp)
            cmd_fallback: list[str] = [
                self.executable,
                "--intf",
                "dummy",
                "--extraintf",
                "luahttp",
                "--http-host",
                self.host,
                "--http-port",
                str(self.port),
                "--http-password",
                self.password,
                "--no-video-title-show",
                "--no-osd",
                "--quiet",
            ]
            if self.user:
                cmd_fallback.extend(
                    [
                        "--lua-config",
                        f"http={{host='{self.host}:{self.port}', user='{self.user}', passwd='{self.password}'}}",
                    ]
                )
            if self.extra_args:
                cmd_fallback.extend(self.extra_args)

            # Prova lancio completo, poi fallback
            try:
                launch(cmd_full)
                self._wait_ready()
            except Exception as exc1:
                # Chiudi eventuale processo e prova fallback
                try:
                    if self._proc and self._proc.poll() is None:
                        self._proc.terminate()
                except Exception:
                    pass
                self._proc = None
                try:
                    launch(cmd_fallback)
                    self._wait_ready()
                except Exception as exc2:
                    raise VlcError(f"Errore avvio cvlc (full: {exc1}) (fallback: {exc2})") from exc2

    # ------------------------------------------------------------------
    # RC (remote control) helpers
    def _rc_send(self, line: str, timeout: float = 1.0) -> str:
        """Invia un comando all'interfaccia RC; ritorna l'ultima linea di risposta."""
        try:
            with socket.create_connection(("127.0.0.1", 9090), timeout=timeout) as s:
                s.settimeout(timeout)
                # Consuma eventuale banner
                try:
                    _ = s.recv(1024)
                except Exception:
                    pass
                msg = (line.strip() + "\n").encode("utf-8", "ignore")
                s.sendall(msg)
                try:
                    resp = s.recv(4096)
                except socket.timeout:
                    resp = b""
                text = resp.decode("utf-8", "ignore")
                lt = text.lower()
                # Heuristics: se RC segnala comando sconosciuto/errore, solleva eccezione
                if any(bad in lt for bad in ("unknown", "not found", "invalid", "usage", "error")):
                    raise VlcError(f"RC rejected '{line}': {text.strip()}")
                return text
        except Exception as exc:
            raise VlcError(f"RC command failed '{line}': {exc}")

    def ensure_adjust_enabled(self) -> None:
        """Assicura che il filtro adjust sia attivo."""
        # Prova vari modi RC per attivare il filtro 'adjust' a runtime
        for cmd in (
            "vfilter adjust",              # alcune build accettano questo per abilitare il filtro
            "set video-filter adjust",     # variabile globale
            "set vfilter adjust",          # alias
            "set adjust-enabled 1",        # abilita flag se già presente
        ):
            try:
                self._rc_send(cmd)
                return
            except VlcError:
                continue

    def set_brightness(self, value: float) -> None:
        """Imposta la luminosità (0.0..2.0) del filtro adjust."""
        v = max(0.0, min(2.0, float(value)))
        self.ensure_running()
        self.ensure_adjust_enabled()
        # Prova vari formati RC comuni
        last_err: Optional[str] = None
        for cmd in (
            f"set adjust-brightness {v}",
            f"adjust brightness {v}",
        ):
            try:
                self._rc_send(cmd)
                return
            except VlcError as exc:
                last_err = str(exc)
        raise VlcError(last_err or "impossibile impostare brightness")

    def set_gamma(self, value: float) -> None:
        g = max(0.01, min(10.0, float(value)))
        self.ensure_running()
        self.ensure_adjust_enabled()
        last_err: Optional[str] = None
        for cmd in (
            f"set adjust-gamma {g}",
            f"adjust gamma {g}",
        ):
            try:
                self._rc_send(cmd)
                return
            except VlcError as exc:
                last_err = str(exc)
        raise VlcError(last_err or "impossibile impostare gamma")

    def visual_fade_to(self, target: float, duration: float) -> None:
        """Esegue fade della luminosità (adjust-brightness) al valore target."""
        self.ensure_running()
        self.ensure_adjust_enabled()
        try:
            # Ottieni uno starting point 'ragionevole' chiedendo lo status del player
            # NB: HTTP status non espone brightness; usiamo un probe: tentiamo set e procediamo.
            pass
        except Exception:
            pass
        duration = max(0.0, float(duration))
        if duration == 0:
            self.set_brightness(target)
            return

        steps = max(1, int(duration / 0.1))
        step_duration = duration / steps

        # Non interrompere fade volume; manteniamo token separato (riuso _fade_lock/_fade_token)
        with self._fade_lock:
            self._fade_token += 1
            token = self._fade_token

        # Per semplicità, calcoliamo da valore corrente teorico; se non noto, assumiamo 1.0
        try:
            start = 1.0
            # Tentativo: una piccola sonda non disponibile; iniziamo lineare da 1.0
        except Exception:
            start = 1.0
        delta = float(target) - float(start)

        def worker() -> None:
            for i in range(steps + 1):
                with self._fade_lock:
                    if token != self._fade_token:
                        return
                fraction = i / steps
                value = start + delta * fraction
                try:
                    self.set_brightness(value)
                except VlcError:
                    return
                time.sleep(max(0.02, step_duration))

        threading.Thread(target=worker, daemon=True).start()

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._proc_lock:
            if not self._proc:
                return
            proc = self._proc
            self._proc = None
            logf = getattr(self, "_log_file", None)
        try:
            proc.terminate()
        except Exception:
            return
        try:
            proc.wait(timeout=timeout)
        except Exception:
            proc.kill()
        try:
            if logf:
                logf.flush(); logf.close()
        except Exception:
            pass

    def restart(self) -> None:
        self.shutdown()
        self.ensure_running()

    # ------------------------------------------------------------------
    # HTTP helpers
    def _base_url(self, path: str) -> str:
        return f"http://{self.host}:{self.port}/requests/{path.lstrip('/')}"

    def _http_get(self, path: str, params: Optional[Dict[str, object]] = None) -> requests.Response:
        url = self._base_url(path)
        try:
            resp = requests.get(url, params=params, auth=self._auth, timeout=2.0)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as exc:
            raise VlcError(f"HTTP request failed: {exc}") from exc

    def _command(self, command: str, params: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        payload: Dict[str, object] = {"command": command}
        if params:
            payload.update(params)
        response = self._http_get("status.json", payload)
        try:
            return response.json()
        except ValueError as exc:
            raise VlcError(f"Risposta JSON non valida: {exc}") from exc

    def status(self) -> Dict[str, object]:
        response = self._http_get("status.json")
        try:
            data: Dict[str, object] = response.json()
        except ValueError as exc:
            raise VlcError(f"Risposta JSON non valida: {exc}") from exc
        if "volume" in data:
            try:
                self._last_volume = int(data["volume"])
            except Exception:
                pass
        return data

    # ------------------------------------------------------------------
    # Playback primitives
    def play(self, path: str, loop: bool = False, fade_in: float = 0.0) -> None:
        media_uri = self._to_uri(path)
        self.ensure_running()

        # Svuota playlist e riproduci immediatamente
        try:
            self._command("pl_stop")
        except VlcError:
            pass
        try:
            self._command("pl_empty")
        except VlcError:
            pass

        self._command("in_play", {"input": media_uri})
        self.set_loop(loop)

        if fade_in and fade_in > 0:
            # Porta volume a 0 prima di iniziare il fade
            self.set_volume(0)
            self.fade_to(self.DEFAULT_VOLUME, fade_in)
        else:
            self.set_volume(self.DEFAULT_VOLUME)

    def enqueue(self, path: str) -> None:
        media_uri = self._to_uri(path)
        self.ensure_running()
        self._command("in_enqueue", {"input": media_uri})

    def pause(self) -> None:
        self.ensure_running()
        state = self.status().get("state")
        if state != "paused":
            self._command("pl_pause")

    def resume(self) -> None:
        self.ensure_running()
        state = self.status().get("state")
        if state == "paused":
            self._command("pl_play")

    def stop(self) -> None:
        self.ensure_running()
        self._command("pl_stop")

    def set_loop(self, enabled: bool) -> None:
        self.ensure_running()
        status = self.status()
        raw_loop = status.get("loop")
        try:
            current_loop = bool(int(raw_loop))
        except Exception:
            current_loop = bool(raw_loop)
        if current_loop != bool(enabled):
            self._command("pl_loop")

    def clear(self) -> None:
        self.ensure_running()
        self._command("pl_empty")

    def is_playing(self) -> bool:
        try:
            return self.status().get("state") == "playing"
        except VlcError:
            return False

    def is_paused(self) -> bool:
        try:
            return self.status().get("state") == "paused"
        except VlcError:
            return False

    def prepare_media(self, path: str, wait_timeout: float = 2.0) -> None:
        """Carica un media, avvia la riproduzione e metti in pausa a time 0 per mostrare il primo frame.
        Usa in_play -> attende playing -> pausa -> seek 0.
        """
        media_uri = self._to_uri(path)
        self.ensure_running()
        # Svuota e riproduci
        try:
            self._command("pl_stop")
        except VlcError:
            pass
        try:
            self._command("pl_empty")
        except VlcError:
            pass
        self._command("in_play", {"input": media_uri})
        # Attendi stato playing o fino al timeout
        deadline = time.time() + max(0.2, float(wait_timeout))
        while time.time() < deadline:
            try:
                st = self.status().get("state")
                if st == "playing":
                    break
            except Exception:
                pass
            time.sleep(0.05)
        # Metti in pausa e posiziona a 0
        try:
            self._command("pl_pause")
        except VlcError:
            pass
        try:
            self._command("seek", {"val": "0"})
        except VlcError:
            pass

    # ------------------------------------------------------------------
    # Volume / fade helpers
    def set_volume(self, value: int) -> None:
        clamped = max(0, min(512, int(value)))
        self._command("volume", {"val": str(clamped)})
        self._last_volume = clamped

    def fade_out(self, duration: float) -> None:
        self.fade_to(0, duration)

    def fade_in(self, duration: float) -> None:
        self.fade_to(self.DEFAULT_VOLUME, duration)

    def fade_to(self, target: int, duration: float) -> None:
        duration = max(0.0, float(duration))
        if duration == 0:
            self.set_volume(target)
            return

        with self._fade_lock:
            self._fade_token += 1
            token = self._fade_token

        try:
            current_volume = int(self.status().get("volume", self._last_volume))
        except Exception:
            current_volume = self._last_volume

        start = current_volume
        delta = target - start
        steps = max(1, int(duration / 0.1))
        step_duration = duration / steps

        def worker() -> None:
            for i in range(steps + 1):
                with self._fade_lock:
                    if token != self._fade_token:
                        return
                fraction = i / steps
                value = int(start + delta * fraction)
                try:
                    self.set_volume(value)
                except VlcError:
                    return
                time.sleep(max(0.02, step_duration))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Helpers
    def _wait_ready(self, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        last_exc: Optional[Exception] = None
        while time.time() < deadline:
            try:
                resp = requests.get(
                    self._base_url("status.json"),
                    auth=self._auth,
                    timeout=1.0,
                )
                if resp.status_code == 200:
                    return
            except Exception as exc:  # pragma: no cover - dipende da runtime
                last_exc = exc
            time.sleep(0.2)
        raise VlcError(f"cvlc non risponde su {self.host}:{self.port}") from last_exc

    @staticmethod
    def _to_uri(path: str) -> str:
        p = Path(path)
        if not p.is_absolute():
            p = p.resolve()
        return p.as_uri()

    def matches(self, host: str, port: int, user: str, password: str, extra_args: Optional[list[str]]) -> bool:
        return (
            self.host == host
            and self.port == int(port)
            and self.user == user
            and self.password == password
            and list(self.extra_args or []) == list(extra_args or [])
        )

    def reconfigure(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        extra_args: Optional[list[str]] = None,
    ) -> None:
        if self.matches(host, port, user, password, extra_args):
            return
        self.shutdown()
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.extra_args = list(extra_args or [])
        self._auth = HTTPBasicAuth(self.user, self.password) if self.user or self.password else None

