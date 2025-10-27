import os, io, threading, socket, shutil, tarfile, zipfile, time, tempfile
from pathlib import Path
import subprocess
from typing import Optional, Any
from urllib.request import urlopen, Request

from fastapi import FastAPI, Query, Body
from fastapi import UploadFile, File
from fastapi.responses import JSONResponse
import uvicorn

from PIL import Image, ImageDraw, ImageFont
import netifaces
import json

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GObject", "2.0")
from gi.repository import Gst, GObject, GLib

# ---------- Config ----------
APP_DIR = Path(__file__).resolve().parent
MEDIA_DIR = APP_DIR / "media"
MEDIA_DIR.mkdir(exist_ok=True)
VERSION_FILE = APP_DIR / "VERSION"
VERSION = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else "v0.1.0"
USE_KMS = os.environ.get("USE_KMS", "1") == "1"
USE_HW_DECODER = os.environ.get("USE_HW_DECODER", "1") == "1"
TARGET_WIDTH = int(os.environ.get("TARGET_WIDTH", "1280"))
TARGET_HEIGHT = int(os.environ.get("TARGET_HEIGHT", "720"))
TARGET_FPS = os.environ.get("TARGET_FPS", "25/1")
FADE_INTERVAL_MS = 50
VIDEO_PATH = str(MEDIA_DIR / "orcanmado.mp4")
APP_PORT = int(os.environ.get("APP_PORT", "8080"))
SPLASH_BLACK = os.environ.get("SPLASH_BLACK", "0") == "1"
CONFIG_FILE = APP_DIR / "config.json"
BLACK_IMAGE = MEDIA_DIR / "black_1280_720.png"

# Directory preferita per file di update scaricati (writable dall'utente del servizio)
def _pick_update_dir() -> Path:
    candidates = []
    # 1) Sotto /var/tmp/headless-player
    candidates.append(Path("/var/tmp/headless-player"))
    # 2) Sotto temp di sistema
    candidates.append(Path(tempfile.gettempdir()) / "headless-player")
    # 3) APP_DIR se scrivibile (ultimo fallback)
    candidates.append(APP_DIR)
    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            test = d / ".wcheck"
            with open(test, "w") as f:
                f.write("ok")
            test.unlink(missing_ok=True)
            return d
        except Exception:
            continue
    # proprio ultimo fallback: temp corrente
    return Path(tempfile.gettempdir())

# ---- Overlay KMS opzionale (plane separato) ----
# Abilita un overlay full-screen nero con alpha regolabile via kmssink, utile per FTB/fade-in
# anche quando il backend video principale non è GStreamer (es. cvlc).
OVERLAY_ENABLED = os.environ.get("OVERLAY_ENABLED", "0") == "1"
OVERLAY_USE_KMS = os.environ.get("OVERLAY_USE_KMS", "1") == "1"
# Impostare un plane-id valido è consigliato per garantire l'uso di un overlay plane sopra al video.
# Usa `modetest` o equivalente per scoprire plane disponibili. 0 = auto/non specificato.
OVERLAY_KMS_PLANE_ID = int(os.environ.get("OVERLAY_KMS_PLANE_ID", "0"))
# Di default non impostiamo zpos per massima compatibilità (alcune build di kmssink non espongono questa property)
OVERLAY_ZPOS = int(os.environ.get("OVERLAY_ZPOS", "-1"))

from backends import BACKEND_CLASSES, available_backends

# Default framework: usa mpv di default (persistenza in config.json se presente)
current_framework = {"name": "mpv", "backend": None}
UDP_ENABLED = True

# --- UDP Log streaming to GUI ---
LOG_UDP_ENABLED = False
LOG_UDP_HOST = ""
LOG_UDP_PORT = 7788

# Identità dispositivo (persistita in config)
DEVICE_NAME = os.environ.get("DEVICE_NAME", "")

VLC_HTTP_HOST = os.environ.get("VLC_HTTP_HOST", "127.0.0.1")
VLC_HTTP_PORT = int(os.environ.get("VLC_HTTP_PORT", "8090"))
VLC_HTTP_USER = os.environ.get("VLC_HTTP_USER", "")
VLC_HTTP_PASSWORD = os.environ.get("VLC_HTTP_PASSWORD", "vlcpass")
VLC_EXTRA_ARGS = [arg for arg in os.environ.get("VLC_EXTRA_ARGS", "").split() if arg]
# Assicura che le immagini restino a schermo: imposta image-duration lungo se non presente
if not any(a.startswith("--image-duration") for a in VLC_EXTRA_ARGS):
    VLC_EXTRA_ARGS.extend(["--image-duration=36000"])  # 10 ore

# ---------- Config persistence & backend init (ripristinate) ----------
def load_persisted_framework():
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            fw = data.get("framework")
            if fw and fw in BACKEND_CLASSES:
                current_framework["name"] = fw
                print(f"[CONFIG] Framework persistito: {fw}", flush=True)
            global USE_KMS, USE_HW_DECODER, TARGET_WIDTH, TARGET_HEIGHT, TARGET_FPS, SPLASH_BLACK, UDP_ENABLED, VIDEO_PATH, VLC_HTTP_PASSWORD, DEVICE_NAME, LOG_UDP_ENABLED, LOG_UDP_HOST, LOG_UDP_PORT
            if "USE_KMS" in data: USE_KMS = bool(data.get("USE_KMS"))
            if "USE_HW_DECODER" in data: USE_HW_DECODER = bool(data.get("USE_HW_DECODER"))
            if "TARGET_WIDTH" in data: TARGET_WIDTH = int(data.get("TARGET_WIDTH") or TARGET_WIDTH)
            if "TARGET_HEIGHT" in data: TARGET_HEIGHT = int(data.get("TARGET_HEIGHT") or TARGET_HEIGHT)
            if "TARGET_FPS" in data: TARGET_FPS = str(data.get("TARGET_FPS") or TARGET_FPS)
            if "SPLASH_BLACK" in data: SPLASH_BLACK = bool(data.get("SPLASH_BLACK"))
            if data.get("last_media"):
                lp = Path(data.get("last_media"))
                if lp.exists():
                    VIDEO_PATH = str(lp)
            UDP_ENABLED = bool(data.get("udp_enabled", data.get("UDP_ENABLED", True)))
            autoplay["enabled"] = bool(data.get("autoplay_enabled", data.get("AUTOPLAY_ENABLED", False)))
            if password := data.get("vlc_password"):
                VLC_HTTP_PASSWORD = str(password)
            if name := data.get("device_name"):
                DEVICE_NAME = str(name)
            # Log UDP config (optional)
            LOG_UDP_ENABLED = bool(data.get("log_udp_enabled", data.get("LOG_UDP_ENABLED", False)))
            LOG_UDP_HOST = str(data.get("log_udp_host", data.get("LOG_UDP_HOST", LOG_UDP_HOST or "")))
            try:
                LOG_UDP_PORT = int(data.get("log_udp_port", data.get("LOG_UDP_PORT", LOG_UDP_PORT)))
            except Exception:
                pass
        except Exception as e:
            print(f"[CONFIG] Lettura config fallita: {e}", flush=True)

def _persist_framework(name: str):
    try:
        data = {}
        if CONFIG_FILE.exists():
            try: data = json.loads(CONFIG_FILE.read_text())
            except Exception: data = {}
        data["framework"] = name
        CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"[CONFIG] Salvato framework: {name}", flush=True)
    except Exception as e:
        print(f"[CONFIG] Errore salvataggio: {e}", flush=True)

def persist_settings(extra: dict | None = None):
    try:
        data = {}
        if CONFIG_FILE.exists():
            try: data = json.loads(CONFIG_FILE.read_text())
            except Exception: data = {}
        data.update({
            "framework": current_framework["name"],
            "USE_KMS": USE_KMS,
            "USE_HW_DECODER": USE_HW_DECODER,
            "TARGET_WIDTH": TARGET_WIDTH,
            "TARGET_HEIGHT": TARGET_HEIGHT,
            "TARGET_FPS": TARGET_FPS,
            "SPLASH_BLACK": SPLASH_BLACK,
            "last_media": VIDEO_PATH,
            "udp_enabled": UDP_ENABLED,
            "autoplay_enabled": autoplay.get("enabled", False),
            "vlc_password": VLC_HTTP_PASSWORD,
            "device_name": DEVICE_NAME,
            "log_udp_enabled": LOG_UDP_ENABLED,
            "log_udp_host": LOG_UDP_HOST,
            "log_udp_port": LOG_UDP_PORT,
        })
        # Rimuovi chiavi legacy non più supportate
        for legacy_key in ("osc_enabled", "OSC_ENABLED"):
            data.pop(legacy_key, None)
        if extra: data.update(extra)
        CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"[CONFIG] Persist settings error: {e}", flush=True)

# ---------- GUI UDP Log helpers ----------
def set_log_udp_target(host: str, port: int, enabled: bool, persist: bool = False):
    global LOG_UDP_HOST, LOG_UDP_PORT, LOG_UDP_ENABLED
    LOG_UDP_HOST = str(host or "")
    try:
        LOG_UDP_PORT = int(port)
    except Exception:
        pass
    LOG_UDP_ENABLED = bool(enabled)
    if persist:
        try:
            persist_settings({
                "log_udp_enabled": LOG_UDP_ENABLED,
                "log_udp_host": LOG_UDP_HOST,
                "log_udp_port": LOG_UDP_PORT,
            })
        except Exception:
            pass

def gui_log(msg: str, level: str = "INFO", kind: str = "event", data: dict | None = None):
    """Invia una riga di log alla GUI via UDP se configurato.
    Formato: JSON { t, level, kind, device, msg, data }
    """
    if not LOG_UDP_ENABLED or not LOG_UDP_HOST:
        return
    try:
        payload = {
            "t": time.time(),
            "level": level,
            "kind": kind,
            "device": DEVICE_NAME or get_ip(),
            "msg": str(msg),
        }
        if data:
            try:
                # evitiamo oggetti non serializzabili
                payload["data"] = json.loads(json.dumps(data, default=str))
            except Exception:
                payload["data"] = {"_raw": str(data)}
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.05)
        s.sendto(json.dumps(payload, ensure_ascii=False).encode("utf-8"), (LOG_UDP_HOST, int(LOG_UDP_PORT)))
        try:
            s.close()
        except Exception:
            pass
    except Exception:
        # non interrompere il flusso in caso di errori di rete
        pass

def init_backend(name: str, persist: bool = False):
    """Inizializza il backend richiesto con fallback sicuri.
    Ordine fallback: richiesto → gst → cvlc. Evita blocchi se pyqt/Qt non è disponibile.
    """
    tried: list[str] = []
    def _try(n: str) -> bool:
        cls = BACKEND_CLASSES.get(n)
        if cls is None:
            return False
        try:
            be = cls(controller=globals())
        except Exception as exc:
            print(f"[BACKEND] Init fallita per {n}: {exc}", flush=True)
            return False
        current_framework["name"] = n
        current_framework["backend"] = be
        print(f"[BACKEND] Inizializzato backend: {n}", flush=True)
        if persist:
            _persist_framework(n)
        return True

    # 1) Prova quello richiesto
    if name and name not in tried:
        tried.append(name)
        if _try(name):
            return
    # 2) Fallback a gst
    if "gst" not in tried and _try("gst"):
        return
    # 3) Fallback a cvlc
    if "cvlc" not in tried and _try("cvlc"):
        return
    # 4) Nessun backend disponibile
    raise RuntimeError("Nessun backend inizializzabile (pyqt non disponibile o gst/cvlc mancanti)")

def ensure_backend():
    if current_framework["backend"] is None:
        # Persist per correggere eventuali framework non avviabili (es. pyqt senza plugin Qt)
        init_backend(current_framework["name"], persist=True)

# Carica config e backend verrà fatto dopo l'inizializzazione degli stati globali

# ---------- UDP Listener opzionale ----------
UDP_PORT = 7777
def _udp_thread():
    print(f"[UDP] Thread avviato su porta {UDP_PORT}", flush=True)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("0.0.0.0", UDP_PORT))
    except Exception as e:
        print(f"[UDP] Errore bind: {e}", flush=True)
        return
    while UDP_ENABLED:
        try:
            data, addr = s.recvfrom(8192)
            raw = data.decode("utf-8", errors="ignore").strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                print(f"[UDP] JSON invalido da {addr}: {raw[:120]}", flush=True)
                continue
            cmd = payload.get("cmd")
            if not cmd:
                continue
            in_time = payload.get("in_time")
            print(f"[UDP] Cmd={cmd} from={addr} in_time={in_time}", flush=True)
            try:
                gui_log(f"UDP cmd={cmd}", level="INFO", kind="udp", data={"from": addr[0], "in_time": in_time})
            except Exception:
                pass
            if cmd == "ping":
                duration = int(payload.get("duration_ms", 200))
                schedule_action(in_time, lambda: api_ping(duration))
            elif cmd == "play":
                path = payload.get("path")
                filename = payload.get("filename")
                loop = payload.get("loop")
                fade_in = float(payload.get("fade_in_seconds", 0.5))
                fade_out = payload.get("fade_out_seconds")
                def do_play():
                    try:
                        api_play(path=path, filename=filename, loop=loop, hide_splash_first=True,
                                 fade_in_seconds=fade_in, fade_out_seconds=fade_out, in_time=None)
                    except Exception as e:
                        print(f"[UDP] Errore play: {e}", flush=True)
                schedule_action(in_time, do_play)
            elif cmd == "play_at":
                at = payload.get("at") or in_time
                path = payload.get("path")
                filename = payload.get("filename")
                loop = payload.get("loop")
                fade_in = float(payload.get("fade_in_seconds", 0.5))
                fade_out = payload.get("fade_out_seconds")
                schedule_action(at, lambda: api_play(path=path, filename=filename, loop=loop, hide_splash_first=True,
                                                     fade_in_seconds=fade_in, fade_out_seconds=fade_out, in_time=None))
            elif cmd == "pause":
                schedule_action(in_time, pause_play)
            elif cmd == "resume":
                schedule_action(in_time, resume_play)
            elif cmd == "stop":
                schedule_action(in_time, api_stop)
            elif cmd == "ftb":
                seconds = float(payload.get("seconds", 1.0))
                schedule_action(in_time, lambda: api_visual_ftb(seconds))
            elif cmd == "fade_in":
                seconds = float(payload.get("seconds", 1.0))
                schedule_action(in_time, lambda: api_fade_in(seconds))
            elif cmd == "fade_out":
                seconds = float(payload.get("seconds", 1.0))
                schedule_action(in_time, lambda: api_fade_out(seconds))
            elif cmd == "overlay_show":
                alpha = float(payload.get("alpha", 1.0))
                schedule_action(in_time, lambda: api_overlay_show(alpha))
            elif cmd == "overlay_hide":
                schedule_action(in_time, api_overlay_hide)
            elif cmd == "overlay_fade":
                target = float(payload.get("target", 0.0))
                seconds = float(payload.get("seconds", 1.0))
                schedule_action(in_time, lambda: api_overlay_fade(target, seconds))
            elif cmd == "overlay_fade_at":
                at = payload.get("at") or in_time
                target = float(payload.get("target", 0.0))
                seconds = float(payload.get("seconds", 1.0))
                schedule_action(at, lambda: api_overlay_fade(target, seconds))
            elif cmd == "faststart_prepare":
                path = payload.get("path")
                filename = payload.get("filename")
                schedule_action(in_time, lambda: api_faststart_prepare(path=path, filename=filename))
            elif cmd == "faststart_go":
                seconds = float(payload.get("seconds", 1.0))
                schedule_action(in_time, lambda: api_faststart_go(seconds))
            elif cmd == "log_subscribe":
                # Client GUI chiede log: usa IP del sender e opzionale porta
                port = int(payload.get("port", LOG_UDP_PORT))
                host = payload.get("host") or addr[0]
                def _sub():
                    set_log_udp_target(host, port, enabled=True, persist=False)
                    try:
                        gui_log("GUI subscribed to UDP log", level="INFO", kind="log", data={"host": host, "port": port})
                    except Exception:
                        pass
                schedule_action(None, _sub)
            elif cmd == "log_unsubscribe":
                def _unsub():
                    set_log_udp_target("", LOG_UDP_PORT, enabled=False, persist=False)
                schedule_action(None, _unsub)
            elif cmd == "change_framework":
                target = payload.get("name")
                if target:
                    schedule_action(in_time, lambda: change_framework({"name": target}))
            elif cmd == "framework":
                print(f"[UDP] Framework corrente: {current_framework['name']}", flush=True)
            # test_on/test_off non esposti via UDP (richiedono pipeline dedicata)
            elif cmd == "device_name_set":
                name = payload.get("name")
                if name:
                    schedule_action(in_time, lambda: api_set_device_name(name=name))
            elif cmd == "hide_splash":
                schedule_action(in_time, api_hide_splash)
            elif cmd == "shutdown":
                schedule_action(in_time, api_shutdown)
        except Exception as e:
            print(f"[UDP] Errore loop: {e}", flush=True)
    print("[UDP] Thread terminato", flush=True)

# ---------- Scheduling helper ----------
def schedule_action(in_time: float | None, func, *args, **kwargs):
    """Programma l'esecuzione di func.
    in_time può essere:
      - None o 0: immediato
      - valore >= 1e9: epoch secondi
      - valore piccolo (<1e9): interpretato come delay relativo in secondi
    Ritorna delay effettivo.
    """
    if not in_time:
        GLib.idle_add(lambda: func(*args, **kwargs))
        return 0.0
    try:
        t = float(in_time)
    except Exception:
        GLib.idle_add(lambda: func(*args, **kwargs))
        return 0.0
    now = time.time()
    if t >= 1e9:  # epoch
        delay = max(0.0, t - now)
    else:
        delay = max(0.0, t)
    if delay == 0:
        GLib.idle_add(lambda: func(*args, **kwargs))
    else:
        ms = int(delay * 1000)
        GLib.timeout_add(ms, lambda: (func(*args, **kwargs), False)[1])
    return delay
# Crea un video di test se non esiste
def create_test_video():
    if not Path(VIDEO_PATH).exists():
        print(f"[INIT] Creo video di test: {VIDEO_PATH}", flush=True)
        # Crea un video di test usando GStreamer
        cmd = f'gst-launch-1.0 videotestsrc pattern=0 num-buffers=150 ! "video/x-raw,width=1280,height=720,framerate=25/1" ! x264enc ! h264parse ! mp4mux ! filesink location="{VIDEO_PATH}"'
        os.system(cmd)
        if Path(VIDEO_PATH).exists():
            print(f"[INIT] Video di test creato: {VIDEO_PATH}", flush=True)
        else:
            print(f"[INIT] ERRORE: Impossibile creare video di test", flush=True)

# Disabilita la creazione automatica del video test salvo esplicita richiesta (CREATE_TEST_VIDEO=1)
if os.environ.get("CREATE_TEST_VIDEO", "0") == "1":
    create_test_video()
# ----------------------------

Gst.init(None)
# GObject.threads_init()

app = FastAPI(title="Headless Video Player")

main_loop = GLib.MainLoop()
player = {"pipeline": None, "vb": None, "alpha": None, "loop": False, "state": "stopped"}
playlist = {"items": [], "index": -1, "loop": True}
splash = {"pipeline": None, "active": False}  # Tracking splash (se attivo copre lo schermo)
preloaded = {"path": None, "pipeline": None, "vb": None, "alpha": None}  # Pipeline pre-caricata per prossimo elemento playlist
fade_seq = 0  # Sequenziatore per cancellare fade sovrapposti

# Overlay controller (pipeline separata su plane dedicato)
overlay = {"pipeline": None, "alpha": None, "active": False}

# Fast-start state
faststart = {"prepared_path": None}

# Show readiness state (usato dalla GUI per LED verde dopo push playlist)
show_state = {"ready": False, "for": None, "ts": None}

# Test mode state
test_mode = {"pipeline": None, "src": None, "active": False, "ticker": None, "x": 0, "y": 0}

# Schedules (per exporre in /status e cancellare al completamento)
schedule_info = {
    "play_at": None,          # epoch seconds
    "overlay_fade_at": None,  # epoch seconds
}

AUTOPLAY_SPLASH_DELAY_S = 5.0
AUTOPLAY_FADE_SECONDS = 1.0
AUTOPLAY_END_LEAD_NS = int(1 * Gst.SECOND)
autoplay = {"enabled": False, "timer_id": None, "monitor_id": None, "fade_started": False}

# Carica configurazione persistita e backend una volta che gli stati globali sono pronti
load_persisted_framework()
ensure_backend()

current_update = {
    "available": None,
    "status": "idle",
    "log": [],
    "callback_url": None,
    "progress": 0,
    "error": None,
    "bytes_total": 0,
    "bytes_done": 0,
}

# ---------- Idle black helper (sempre nero quando non c'è splash e non suona nulla) ----------
def _ensure_black_image() -> Path:
    """Assicura la presenza di un'immagine nera 1280x720 in MEDIA_DIR e ritorna il path."""
    try:
        if BLACK_IMAGE.exists():
            return BLACK_IMAGE
        MEDIA_DIR.mkdir(exist_ok=True)
        # Genera PNG nero
        img = Image.new("RGB", (TARGET_WIDTH or 1280, TARGET_HEIGHT or 720), (0, 0, 0))
        img.save(BLACK_IMAGE)
        return BLACK_IMAGE
    except Exception:
        # Fallback: punta a un path in MEDIA_DIR anche se non esiste (VLC mostrerà nero di default)
        return BLACK_IMAGE

def show_idle_black() -> bool:
    """Mostra un nero stabile quando il player è idle.
    Priorità:
      1) Overlay KMS, se abilitato (copre sempre lo schermo indipendentemente dal backend)
      2) Backend CVLC: prepara PNG nero e metti in pausa sul primo frame
      3) Backend GST: avvia una pipeline su PNG nero (imagefreeze) in PLAYING
    Ritorna True se è riuscito a mostrare il nero, False altrimenti.
    """
    try:
        # Non interferire se splash è attivo: lo splash copre già lo schermo
        if splash.get("active"):
            return True
        # 1) Overlay se disponibile
        if OVERLAY_ENABLED and OVERLAY_USE_KMS:
            try:
                overlay_show(alpha=1.0)
                return True
            except Exception as oe:
                print(f"[IDLE] Overlay nero non disponibile: {oe}", flush=True)
        # Assicura un'immagine nera disponibile
        black = _ensure_black_image()
        ensure_backend()
        be = current_framework.get("backend")
        name = current_framework.get("name")
        if name == "cvlc" and be:
            try:
                be.faststart_prepare(str(black))
                player["state"] = "paused"
                return True
            except Exception as exc:
                print(f"[IDLE] cvlc prepare black fallita: {exc}", flush=True)
        # 3) GST fallback: costruisci pipeline di immagine nera
        if name == "gst":
            try:
                # Usa la normale pipeline per immagini (imagefreeze)
                p, vb, af = build_player_pipeline(str(black))
                # Se c'è una pipeline attiva, fermala
                if player.get("pipeline"):
                    try:
                        player["pipeline"].set_state(Gst.State.NULL)
                    except Exception:
                        pass
                player["pipeline"] = p
                player["vb"] = vb
                player["alpha"] = af
                # Mostra immediatamente il frame nero
                p.set_state(Gst.State.PLAYING)
                player["state"] = "paused"
                return True
            except Exception as ge:
                print(f"[IDLE] GST nero fallback fallito: {ge}", flush=True)
        return False
    except Exception as e:
        print(f"[IDLE] Errore show_idle_black: {e}", flush=True)
        return False

# Avvia in background un tentativo iniziale di mostrare il nero se lo splash non è attivo
def _idle_bootstrap():
    try:
        # breve attesa per permettere al servizio di inizializzarsi
        time.sleep(1.0)
        show_idle_black()
    except Exception:
        pass

threading.Thread(target=_idle_bootstrap, name="idle-black", daemon=True).start()

# ---------- Autoplay helpers ----------
def _autoplay_log(msg: str) -> None:
    print(f"[AUTOPLAY] {msg}", flush=True)


def _autoplay_cancel_timer() -> None:
    timer_id = autoplay.get("timer_id")
    if timer_id:
        try:
            GLib.source_remove(timer_id)
        except Exception:
            pass
        autoplay["timer_id"] = None


def _autoplay_cancel_monitor() -> None:
    monitor_id = autoplay.get("monitor_id")
    if monitor_id:
        try:
            GLib.source_remove(monitor_id)
        except Exception:
            pass
        autoplay["monitor_id"] = None


def _autoplay_collect_media() -> list[str]:
    items: list[str] = []
    for entry in sorted(MEDIA_DIR.iterdir()):
        if not entry.is_file():
            continue
        try:
            if validate_media_file(entry):
                items.append(str(entry))
        except Exception as exc:
            _autoplay_log(f"Errore analizzando {entry.name}: {exc}")
    return items


def _autoplay_schedule_initial(delay: float | None = None) -> None:
    if not autoplay.get("enabled"):
        return
    delay = AUTOPLAY_SPLASH_DELAY_S if delay is None else max(0.0, delay)
    _autoplay_cancel_timer()

    def _launcher() -> bool:
        autoplay["timer_id"] = None
        _autoplay_launch()
        return False

    timeout_ms = max(1, int(delay * 1000))
    autoplay["timer_id"] = GLib.timeout_add(timeout_ms, _launcher)


def _autoplay_launch() -> None:
    if not autoplay.get("enabled"):
        return
    if not splash.get("active"):
        show_splash_until_play()
    items = _autoplay_collect_media()
    if not items:
        _autoplay_log("Nessun file valido in media/: riprovo tra 30s")
        _autoplay_schedule_initial(delay=30.0)
        return
    playlist["items"] = items
    playlist["index"] = 0
    playlist["loop"] = True
    set_loop(False)
    stop_play()
    global VIDEO_PATH
    VIDEO_PATH = items[0]
    _autoplay_log(f"Avvio autoplay ({len(items)} elementi) -> {Path(VIDEO_PATH).name}")
    start_play(AUTOPLAY_FADE_SECONDS)


def _autoplay_start_monitor() -> None:
    if autoplay.get("monitor_id"):
        return

    def _tick() -> bool:
        if not autoplay.get("enabled"):
            autoplay["monitor_id"] = None
            return False
        pipe = player.get("pipeline")
        if not pipe or player.get("state") != "playing":
            autoplay["fade_started"] = False
            return True
        try:
            ok_dur, duration = pipe.query_duration(Gst.Format.TIME)  # type: ignore[attr-defined]
        except Exception:
            ok_dur, duration = False, 0
        try:
            ok_pos, position = pipe.query_position(Gst.Format.TIME)  # type: ignore[attr-defined]
        except Exception:
            ok_pos, position = False, 0
        if ok_dur and ok_pos and duration and duration > 0:
            remaining = duration - position
            if 0 <= remaining <= AUTOPLAY_END_LEAD_NS and not autoplay.get("fade_started"):
                _autoplay_trigger_fade_out()
        return True

    autoplay["monitor_id"] = GLib.timeout_add(250, _tick)


def _autoplay_trigger_fade_out() -> None:
    autoplay["fade_started"] = True
    _autoplay_log("Fade-out finale asset")
    if player.get("alpha"):
        fade_opacity_to(0.0, AUTOPLAY_FADE_SECONDS)
    if player.get("vb"):
        fade_to(-1.0, AUTOPLAY_FADE_SECONDS)


def _autoplay_on_play_started(path: str) -> None:
    if not autoplay.get("enabled"):
        return
    autoplay["fade_started"] = False
    _autoplay_start_monitor()
    try:
        name = Path(path).name
    except Exception:
        name = path
    _autoplay_log(f"Riproduzione {name}")


def _autoplay_set_enabled(enabled: bool, restart: bool = True, delay: float | None = None) -> None:
    enabled = bool(enabled)
    if enabled == autoplay.get("enabled") and not restart:
        persist_settings({"autoplay_enabled": enabled})
        return
    autoplay["enabled"] = enabled
    if not enabled:
        _autoplay_log("Autoplay disattivato")
        _autoplay_cancel_timer()
        _autoplay_cancel_monitor()
        autoplay["fade_started"] = False
        persist_settings({"autoplay_enabled": False})
        return
    _autoplay_log("Autoplay attivato")
    _autoplay_cancel_timer()
    autoplay["fade_started"] = False
    if restart:
        _autoplay_cancel_monitor()
        if player.get("state") == "playing":
            stop_play()
        if not splash.get("active"):
            show_splash_until_play()
        _autoplay_schedule_initial(delay)
    else:
        _autoplay_start_monitor()
    persist_settings({"autoplay_enabled": True})

# ---------- Maintenance ----------
maintenance = {
    "status": "idle",  # idle | running | ok | error
    "log": [],
    "error": None,
}

def log_maintenance(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    maintenance["log"].append(line)
    maintenance["log"] = maintenance["log"][-300:]
def log_update(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    current_update["log"].append(line)
    current_update["log"] = current_update["log"][-200:]
    if current_update["callback_url"]:
        try:
            import urllib.request
            req = urllib.request.Request(
                current_update["callback_url"],
                data=json.dumps({"message": line, "status": current_update["status"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass

def get_ip():
    for iface in netifaces.interfaces():
        addrs = netifaces.ifaddresses(iface).get(netifaces.AF_INET, [])
        for a in addrs:
            ip = a.get('addr')
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    return "0.0.0.0"

def get_eth_wifi_ips():
    """Ritorna (eth_ip, wifi_ip) se presenti, altrimenti None."""
    eth_ip = None
    wifi_ip = None
    for iface in netifaces.interfaces():
        iname = (iface or "").lower()
        addrs = netifaces.ifaddresses(iface).get(netifaces.AF_INET, [])
        for a in addrs:
            ip = a.get('addr')
            if not ip or ip.startswith("127.") or ip.startswith("169.254."):
                continue
            if iname.startswith(("eth", "enp", "eno", "ens")):
                if not eth_ip:
                    eth_ip = ip
            elif iname.startswith(("wlan", "wl")):
                if not wifi_ip:
                    wifi_ip = ip
    return eth_ip, wifi_ip

# ---------- Splash via appsrc ----------
def build_splash_pipeline():
    """Crea la pipeline splash con fallback automatico da kmssink ad autovideosink."""
    preferred = "kmssink" if USE_KMS else "autovideosink"
    tried = []
    for sink in [preferred, "autovideosink"]:
        if sink in tried:
            continue
        tried.append(sink)
        try:
            desc = (
                f"appsrc name=src is-live=true format=time caps=video/x-raw,format=RGB,width={TARGET_WIDTH},height={TARGET_HEIGHT},framerate=30/1 "
                f"! videoconvert ! videobalance name=svb ! imagefreeze ! {sink}"
            )
            pipeline = Gst.parse_launch(desc)
            return pipeline, pipeline.get_by_name("src"), pipeline.get_by_name("svb")
        except Exception as e:
            print(f"[SPLASH] Errore creazione pipeline con {sink}: {e}", flush=True)
            continue
    raise RuntimeError("Impossibile creare pipeline splash")

def push_splash_frame(appsrc):
    print("[SPLASH] Generazione frame splash", flush=True)
    img = Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (0, 0, 0))
    if not SPLASH_BLACK:
        eth_ip, wifi_ip = get_eth_wifi_ips()
        name_line = f"Nome: {DEVICE_NAME}" if DEVICE_NAME else None
        text = (
            (f"{name_line}\n" if name_line else "") +
            f"eth -> {eth_ip or '-'}\n"
            f"wifi -> {wifi_ip or '-'}\n"
            f"Versione: {VERSION}"
        )
        print(f"[SPLASH] Testo: {text}", flush=True)
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 32)
        except:
            font = ImageFont.load_default()
        # Centra il testo
        try:
            w, h = d.multiline_textbbox((0,0), text, font=font, align="center")[2:]
        except Exception:
            w, h = d.multiline_textsize(text, font=font)
        d.multiline_text(((TARGET_WIDTH - w) // 2, (TARGET_HEIGHT - h) // 2), text, fill=(255, 255, 255), font=font, align="center")
    buf = Gst.Buffer.new_allocate(None, TARGET_WIDTH * TARGET_HEIGHT * 3, None)
    buf.fill(0, img.tobytes())
    now = int(time.time() * Gst.SECOND)
    buf.pts = buf.dts = now
    result = appsrc.emit("push-buffer", buf)
    print(f"[SPLASH] Frame inviato, risultato: {result}", flush=True)

def push_custom_splash_frame(appsrc, extra_line: str | None = None):
    """Disegna lo stesso splash ma con una riga extra (es. PING!)"""
    img = Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (0, 0, 0))
    eth_ip, wifi_ip = get_eth_wifi_ips()
    name_line = f"Nome: {DEVICE_NAME}" if DEVICE_NAME else None
    base = (
        (f"{name_line}\n" if name_line else "") +
        f"eth -> {eth_ip or '-'}\n"
        f"wifi -> {wifi_ip or '-'}\n"
        f"Versione: {VERSION}"
    )
    text = base if not extra_line else (base + f"\n{extra_line}")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 32)
    except:
        font = ImageFont.load_default()
    try:
        w, h = d.multiline_textbbox((0,0), text, font=font, align="center")[2:]
    except Exception:
        w, h = d.multiline_textsize(text, font=font)
    d.multiline_text(((TARGET_WIDTH - w) // 2, (TARGET_HEIGHT - h) // 2), text, fill=(255, 255, 255), font=font, align="center")
    buf = Gst.Buffer.new_allocate(None, TARGET_WIDTH * TARGET_HEIGHT * 3, None)
    buf.fill(0, img.tobytes())
    now = int(time.time() * Gst.SECOND)
    buf.pts = buf.dts = now
    appsrc.emit("push-buffer", buf)

def show_splash_until_play():
    print("[SPLASH] Avvio splash permanente", flush=True)
    pipeline, src, vb = build_splash_pipeline()
    splash["pipeline"] = pipeline
    splash["src"] = src
    splash["vb"] = vb
    splash["active"] = True
    pipeline.set_state(Gst.State.PLAYING)
    GLib.idle_add(push_splash_frame, src)
    print("[SPLASH] Splash avviato (attivo fino al primo play)", flush=True)

def hide_splash():
    if splash["active"] and splash["pipeline"]:
        print("[SPLASH] Chiudo splash", flush=True)
        splash["pipeline"].set_state(Gst.State.NULL)
        splash["pipeline"] = None
        splash["active"] = False
        print("[SPLASH] Splash chiuso", flush=True)

def show_idle_black() -> bool:
    """Mostra l'immagine nera di default in pausa quando non c'è splash e non si sta riproducendo nulla."""
    try:
        if splash.get("active"):
            return False
        if not BLACK_IMAGE.exists():
            return False
        if current_framework["name"] == "cvlc":
            ensure_backend()
            backend = current_framework.get("backend")
            if backend and hasattr(backend, "faststart_prepare"):
                try:
                    backend.faststart_prepare(str(BLACK_IMAGE))  # type: ignore[attr-defined]
                    faststart["prepared_path"] = str(BLACK_IMAGE)
                    return True
                except Exception as e:
                    print(f"[IDLE] cvlc prepare fallita: {e}", flush=True)
        # GStreamer
        p, vb, af = build_player_pipeline(str(BLACK_IMAGE))
        p.set_state(Gst.State.PAUSED)
        try:
            p.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)
        except Exception:
            pass
        def _kick():
            try:
                p.set_state(Gst.State.PLAYING)
            except Exception:
                pass
            def _pause_back():
                try:
                    p.set_state(Gst.State.PAUSED)
                except Exception:
                    pass
                return False
            GLib.timeout_add(80, _pause_back)
            return False
        GLib.idle_add(_kick)
        if player.get("pipeline"):
            try:
                player["pipeline"].set_state(Gst.State.NULL)
            except Exception:
                pass
        player.update({"pipeline": p, "vb": vb, "alpha": af, "state": "paused"})
        return True
    except Exception as e:
        print(f"[IDLE] Errore show_idle_black: {e}", flush=True)
        return False

# ---------- Overlay KMS (plane separato) ----------
def build_overlay_pipeline():
    """Crea pipeline overlay full-screen nero con alpha regolabile.
    - Preferisce kmssink; in fallback usa autovideosink
    - Imposta plane-id/zpos solo se la property è supportata dal sink (evita parse_error)
    """
    sink_name = "kmssink" if OVERLAY_USE_KMS else "autovideosink"
    caps = f"video/x-raw,format=ARGB,width={TARGET_WIDTH},height={TARGET_HEIGHT},framerate={TARGET_FPS}"
    # Non passare plane-id/zpos nella stringa; imposteremo le property via set_property se disponibili
    desc = (
        f"videotestsrc pattern=black is-live=true ! {caps} ! alpha name=olpha alpha=1.0 ! {sink_name} name=olsink"
    )
    print(f"[OVERLAY] Pipeline: {desc}", flush=True)
    p = Gst.parse_launch(desc)
    alpha = p.get_by_name("olpha")
    sink = p.get_by_name("olsink")
    # Imposta proprietà se presenti
    try:
        if OVERLAY_USE_KMS and sink is not None:
            if OVERLAY_KMS_PLANE_ID > 0:
                try:
                    sink.set_property("plane-id", int(OVERLAY_KMS_PLANE_ID))
                    print(f"[OVERLAY] plane-id={OVERLAY_KMS_PLANE_ID}", flush=True)
                except Exception as e:
                    print(f"[OVERLAY] plane-id non supportato: {e}", flush=True)
            if OVERLAY_ZPOS >= 0:
                try:
                    sink.set_property("zpos", int(OVERLAY_ZPOS))
                    print(f"[OVERLAY] zpos={OVERLAY_ZPOS}", flush=True)
                except Exception as e:
                    print(f"[OVERLAY] zpos non supportato: {e}", flush=True)
    except Exception:
        pass
    return p, alpha

def overlay_show(alpha: float = 1.0):
    """Mostra overlay nero (alpha 1.0 = pieno nero)."""
    if not OVERLAY_ENABLED:
        raise RuntimeError("Overlay disabilitato")
    if overlay["active"] and overlay["pipeline"]:
        try:
            if overlay["alpha"]:
                overlay["alpha"].set_property("alpha", max(0.0, min(1.0, alpha)))
        except Exception:
            pass
        return
    try:
        p, a = build_overlay_pipeline()
        overlay["pipeline"] = p
        overlay["alpha"] = a
        overlay["active"] = True
        if overlay["alpha"]:
            overlay["alpha"].set_property("alpha", max(0.0, min(1.0, alpha)))
        p.set_state(Gst.State.PLAYING)
        print("[OVERLAY] Attivato", flush=True)
    except Exception as e:
        overlay["pipeline"] = None
        overlay["alpha"] = None
        overlay["active"] = False
        print(f"[OVERLAY] ERRORE creazione: {e}", flush=True)
        raise

def overlay_hide():
    if overlay["active"] and overlay["pipeline"]:
        try:
            overlay["pipeline"].set_state(Gst.State.NULL)
        except Exception:
            pass
        overlay["pipeline"] = None
        overlay["alpha"] = None
        overlay["active"] = False
        print("[OVERLAY] Disattivato", flush=True)

def overlay_set_alpha(value: float):
    if not overlay["active"] or not overlay["alpha"]:
        return False
    try:
        overlay["alpha"].set_property("alpha", max(0.0, min(1.0, float(value))))
        return True
    except Exception:
        return False

def overlay_fade_to(target: float, duration_s: float = 1.0):
    if not overlay["active"] or not overlay["alpha"]:
        overlay_show(alpha=1.0 if target >= 1.0 else 0.0)
    if duration_s <= 0:
        overlay_set_alpha(target)
        return True
    try:
        cur = float(overlay["alpha"].get_property("alpha"))
    except Exception:
        cur = 1.0
    steps = max(1, int(duration_s / (FADE_INTERVAL_MS / 1000.0)))
    step_val = (max(0.0, min(1.0, target)) - cur) / steps
    seq = {"i": 0, "val": cur}
    def stepper():
        if not overlay["active"] or not overlay["alpha"]:
            return False
        seq["val"] += step_val
        final = (seq["i"] + 1) >= steps
        overlay["alpha"].set_property("alpha", max(0.0, min(1.0, target if final else seq["val"])) )
        seq["i"] += 1
        return not final
    GLib.timeout_add(FADE_INTERVAL_MS, stepper)
    return True

# ---------- Player pipeline ----------
def decoder_name():
    res = os.popen("gst-inspect-1.0 v4l2h264dec 2>/dev/null").read()
    return "v4l2h264dec" if "v4l2h264dec" in res else "avdec_h264"

# Handler bus GStreamer minimale, definito in anticipo per evitare NameError in ambienti/test che invocano pipeline presto
def on_bus_message(bus, message, data):
    try:
        t = message.type
        if hasattr(Gst, "MessageType"):
            if t == Gst.MessageType.ERROR:
                try:
                    err, dbg = message.parse_error()
                    print(f"[GST] ERROR: {err} dbg={dbg}", flush=True)
                except Exception:
                    print("[GST] ERROR", flush=True)
            elif t == Gst.MessageType.EOS:
                print("[GST] EOS", flush=True)
    except Exception:
        # In ambienti stub/minimali ignoriamo eventuali errori del bus
        pass

def build_player_pipeline(path):
    sink = "kmssink" if USE_KMS else "autovideosink"
    dec = "decodebin"
    # Supporta immagini statiche (png/jpg/bmp) usando imagefreeze per mantenere un frame costante
    try:
        is_image = str(Path(path).suffix or "").lower() in {".png", ".jpg", ".jpeg", ".bmp"}
    except Exception:
        is_image = False
    # Nota: su RPi potresti usare v4l2h264dec se disponibile (già funzione decoder_name se serve)
    if USE_HW_DECODER:
        # Preferisci decodebin (che può usare hw) per compatibilità; potremmo forzare caps in futuro
        dec = "decodebin"
    caps_filter = f"video/x-raw,width={TARGET_WIDTH},height={TARGET_HEIGHT},framerate={TARGET_FPS}"
    # Nota: path quotato per evitare errori con spazi o caratteri speciali
    if is_image:
        desc = (
            f"filesrc location=\"{path}\" ! queue ! {dec} name=dec ! videoconvert ! imagefreeze ! videoconvert ! videoscale ! "
            f"videorate ! {caps_filter} ! videoconvert ! video/x-raw,format=RGBA ! alpha name=af ! videoconvert ! "
            f"videobalance name=vb ! {sink}"
        )
    else:
        desc = (
            f"filesrc location=\"{path}\" ! queue ! {dec} name=dec ! queue ! videoconvert ! videoscale ! "
            f"videorate ! {caps_filter} ! videoconvert ! video/x-raw,format=RGBA ! alpha name=af ! videoconvert ! "
            f"videobalance name=vb ! {sink}"
        )
    print(f"[PLAYER] Pipeline: {desc}", flush=True)
    try:
        pipeline = Gst.parse_launch(desc)
        vb = pipeline.get_by_name("vb")
        af = pipeline.get_by_name("af")
        bus = pipeline.get_bus()
        try:
            bus.add_signal_watch()
            bus.connect("message", on_bus_message, None)
        except Exception:
            # Ambienti senza main loop/signals (stub/Windows)
            pass
        decbin = pipeline.get_by_name("dec")
        if decbin:
            def on_pad_added(db, pad):
                caps = pad.get_current_caps() or pad.query_caps()
                if not caps:
                    return
                s = caps.get_structure(0)
                if not s or not s.get_name().startswith("video/"):
                    return
                if s.has_field("framerate"):
                    try:
                        ok, num, den = s.get_fraction("framerate")
                        if ok and num and den:
                            real = f"{num}/{den}"
                            if real != TARGET_FPS:
                                print(f"[FRAMERATE] Sorgente {real} (target {TARGET_FPS})", flush=True)
                    except Exception as fe:
                        print(f"[FRAMERATE] Errore lettura framerate: {fe}", flush=True)
                if s.has_field("width") and s.has_field("height"):
                    w = s.get_int("width")[1]; h = s.get_int("height")[1]
                    print(f"[VIDEO] Stream {w}x{h}", flush=True)
            decbin.connect("pad-added", on_pad_added)
        return pipeline, vb, af
    except Exception as e:
        # Se fallisce con kmssink e USE_KMS attivo prova fallback
        if USE_KMS:
            print(f"[PLAYER] Errore con kmssink: {e} -> fallback autovideosink", flush=True)
            try:
                fb_desc = desc.replace("kmssink", "autovideosink")
                pipeline = Gst.parse_launch(fb_desc)
                vb = pipeline.get_by_name("vb")
                af = pipeline.get_by_name("af")
                bus = pipeline.get_bus()
                try:
                    bus.add_signal_watch()
                    bus.connect("message", on_bus_message, None)
                except Exception:
                    pass
                decbin = pipeline.get_by_name("dec")
                if decbin:
                    def on_pad_added(db, pad):
                        caps = pad.get_current_caps() or pad.query_caps()
                        if not caps:
                            return
                        s = caps.get_structure(0)
                        if not s or not s.get_name().startswith("video/"):
                            return
                        if s.has_field("framerate"):
                            try:
                                ok, num, den = s.get_fraction("framerate")
                                if ok and num and den:
                                    real = f"{num}/{den}"
                                    if real != TARGET_FPS:
                                        print(f"[FRAMERATE] Sorgente {real} (target {TARGET_FPS})", flush=True)
                            except Exception as fe:
                                print(f"[FRAMERATE] Errore lettura framerate: {fe}", flush=True)
                        if s.has_field("width") and s.has_field("height"):
                            w = s.get_int("width")[1]; h = s.get_int("height")[1]
                            print(f"[VIDEO] Stream {w}x{h}", flush=True)
                    decbin.connect("pad-added", on_pad_added)
                print("[PLAYER] Fallback autovideosink attivo", flush=True)
                return pipeline, vb, af
            except Exception as e2:
                print(f"[PLAYER] Fallback autovideosink fallito: {e2}", flush=True)
        print(f"[PLAYER] ERRORE nella creazione pipeline: {e}", flush=True)
        raise

def pipeline_has_kmssink(p: Any) -> bool:
    try:
        it = p.iterate_elements()
        ok, elem = it.next()
        while ok:
            try:
                fac = elem.get_factory()
                if fac and fac.get_name() == "kmssink":
                    return True
            except Exception:
                pass
            ok, elem = it.next()
    except Exception:
        pass
    return False

def on_bus_message(bus, msg, data):
    t = msg.type
    if t == Gst.MessageType.EOS:
        print("[PLAYER] Fine del video (EOS)", flush=True)
        # Avanzamento playlist se presente
        if playlist["items"]:
            next_index = playlist["index"] + 1
            if next_index >= len(playlist["items"]):
                if playlist["loop"]:
                    next_index = 0
                else:
                    # Fine playlist
                    if player["pipeline"]:
                        player["pipeline"].set_state(Gst.State.NULL)
                    player["state"] = "stopped"
                    print("[PLAYER] Fine playlist", flush=True)
                    return
            playlist["index"] = next_index
            next_path = playlist["items"][next_index]
            print(f"[PLAYLIST] Avanzo a: {next_path}", flush=True)
            # Aggiorna path e adotta se precaricata
            global VIDEO_PATH
            VIDEO_PATH = next_path
            if not adopt_preloaded(next_path):
                stop_play()
                if autoplay.get("enabled"):
                    start_play(AUTOPLAY_FADE_SECONDS)
                else:
                    start_play_with_path(next_path)
            return
        # Nessuna playlist: comportamento originale loop singolo
        if player["loop"] and player["pipeline"]:
            print("[PLAYER] Riavvio loop", flush=True)
            player["pipeline"].seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)
        else:
            stop_play()
            print("[PLAYER] Riproduzione terminata", flush=True)
            if not splash["active"]:
                # Se lo splash è nascosto, mostra il nero in pausa per mantenere un'immagine stabile
                try:
                    if not show_idle_black():
                        show_splash_until_play()
                except Exception:
                    show_splash_until_play()
    elif t == Gst.MessageType.ERROR:
        err, debug = msg.parse_error()
        print(f"[PLAYER] GST ERROR: {err}", flush=True)
        print(f"[PLAYER] Debug info: {debug}", flush=True)
        # Fallback automatico: se kmssink va in errore (permessi DRM o altro), passa ad autovideosink
        try:
            if USE_KMS and player["pipeline"] and pipeline_has_kmssink(player["pipeline"]):
                print("[PLAYER] KMS errore/permessi: fallback ad autovideosink", flush=True)
                # Disattiva KMS e ricrea pipeline sullo stesso media
                globals()["USE_KMS"] = False
                if player["pipeline"]:
                    player["pipeline"].set_state(Gst.State.NULL)
                # Ricrea pipeline e riparti
                def restart_after_fallback():
                    try:
                        ensure_pipeline()
                        if player["vb"]:
                            player["vb"].set_property("brightness", -1.0)
                        player["pipeline"].set_state(Gst.State.PLAYING)
                        player["state"] = "playing"
                        print("[PLAYER] Ripartenza con autovideosink effettuata", flush=True)
                    except Exception as fe:
                        print(f"[PLAYER] Fallback start failed: {fe}", flush=True)
                GLib.idle_add(restart_after_fallback)
                return
        except Exception as _:
            pass
        if player["pipeline"]:
            stop_play()
        else:
            player["state"] = "error"
        if not splash["active"]:
            try:
                if not show_idle_black():
                    show_splash_until_play()
            except Exception:
                show_splash_until_play()
    elif t == Gst.MessageType.WARNING:
        warn, debug = msg.parse_warning()
        print(f"[PLAYER] GST WARNING: {warn}", flush=True)
        print(f"[PLAYER] Debug info: {debug}", flush=True)
    elif t == Gst.MessageType.INFO:
        info, debug = msg.parse_info()
        print(f"[PLAYER] GST INFO: {info}", flush=True)

    # Se test mode è attivo, ignora messaggi player

def ensure_pipeline():
    if player["pipeline"]:
        return
    print(f"[PLAYER] Creo pipeline per: {VIDEO_PATH}", flush=True)
    p, vb, af = build_player_pipeline(VIDEO_PATH)
    player["pipeline"] = p
    player["vb"] = vb
    player["alpha"] = af
    print("[PLAYER] Pipeline creata", flush=True)

def prepare_pipeline_for(path: str):
    """Crea pipeline per 'path', la porta al primo frame visibile e la lascia in PAUSED.
    Obiettivo: al termine di prepare il contenuto sia già presentato sullo schermo (dietro l'overlay)
    per un GO immediato e senza flash.
    """
    # Libera eventualmente un preload precedente
    if preloaded["pipeline"]:
        try:
            preloaded["pipeline"].set_state(Gst.State.NULL)
        except Exception:
            pass
        preloaded.update({"path": None, "pipeline": None, "vb": None, "alpha": None})
    p, vb, af = build_player_pipeline(path)

    # Tenta di abilitare la visualizzazione del frame di preroll (se supportato dal sink)
    try:
        it = p.iterate_elements()
        ok, elem = it.next()
        while ok:
            try:
                elem.set_property("show-preroll-frame", True)
            except Exception:
                pass
            ok, elem = it.next()
    except Exception:
        pass

    # Preroll in PAUSED
    p.set_state(Gst.State.PAUSED)
    # Assicurati di essere esattamente all'inizio
    try:
        p.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0)
    except Exception:
        pass

    # Breve kick in PLAYING per far presentare il primo frame, poi torna in PAUSED
    def _kick_play_then_pause():
        try:
            p.set_state(Gst.State.PLAYING)
        except Exception:
            # anche se fallisce, memorizza comunque la pipeline
            pass

        def _pause_back():
            try:
                p.set_state(Gst.State.PAUSED)
            except Exception:
                pass
            return False

        # ~80ms sono in genere sufficienti per presentare il primo frame
        GLib.timeout_add(80, _pause_back)
        return False

    GLib.idle_add(_kick_play_then_pause)

    preloaded.update({"path": path, "pipeline": p, "vb": vb, "alpha": af})
    print(f"[FASTSTART] Preparata pipeline con primo frame presentato e in PAUSED per {path}", flush=True)

def adopt_preloaded(path: str) -> bool:
    """Se esiste una pipeline precaricata per 'path', adottala come player principale.
    Ritorna True se adottata, False altrimenti.
    """
    try:
        if not preloaded.get("pipeline") or preloaded.get("path") != path:
            return False
        # Chiudi eventuale pipeline corrente
        if player.get("pipeline"):
            try:
                player["pipeline"].set_state(Gst.State.NULL)
            except Exception:
                pass
        # Adotta la precaricata
        player["pipeline"] = preloaded["pipeline"]
        player["vb"] = preloaded.get("vb")
        player["alpha"] = preloaded.get("alpha")
        player["state"] = "playing"
        # Nascondi splash
        if splash.get("active"):
            hide_splash()
        # Porta in PLAYING dalla PAUSED prerollata
        player["pipeline"].set_state(Gst.State.PLAYING)
    finally:
        # Sgancia sempre lo stato preloaded
        preloaded.update({"path": None, "pipeline": None, "vb": None, "alpha": None})
    print("[FASTSTART] Adottata pipeline precaricata", flush=True)
    return True

def validate_media_file(path: Path) -> bool:
    if not path.exists():
        print(f"[PLAYER] ERRORE: File non trovato: {path}", flush=True); return False
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
        if len(header) < 12:
            print(f"[PLAYER] ERRORE: File troppo corto: {path}", flush=True); return False
        # MP4: bytes 4-8 = 'ftyp'
        if header[4:8] == b'ftyp':
            return True
        # Matroska
        if header.startswith(b'\x1a\x45\xdf\xa3'):
            return True
        # AVI: 'RIFF....AVI '
        if header.startswith(b'RIFF') and header[8:12] == b'AVI ':
            return True
        print(f"[PLAYER] ERRORE: Header non riconosciuto ({header[:8].hex()}) per {path}", flush=True)
        return False
    except Exception as e:
        print(f"[PLAYER] ERRORE lettura header {path}: {e}", flush=True)
        return False

def start_play_with_path(path: str):
    global VIDEO_PATH
    VIDEO_PATH = path
    start_play()

def start_play(fade_in_seconds: float = 0.5):
    print(f"[PLAYER] Avvio riproduzione: {VIDEO_PATH}", flush=True)
    # Se overlay nero è attivo dall'idle, nascondilo subito (fade breve) per evitare flash
    try:
        if OVERLAY_ENABLED and overlay.get("active"):
            overlay_fade_to(0.0, 0.2)
            def _hide():
                try: overlay_hide()
                except Exception: pass
                return False
            GLib.timeout_add(250, _hide)
    except Exception:
        pass
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend is None:
            print("[PLAYER] Nessun backend disponibile", flush=True)
            return
        try:
            backend.play(VIDEO_PATH, player.get("loop"), fade_in_seconds)
        except Exception as exc:
            print(f"[PLAYER] Errore backend: {exc}", flush=True)
            player["state"] = "error"
            return
        persist_settings()
        _autoplay_on_play_started(VIDEO_PATH)
        return
    media_path = Path(VIDEO_PATH)
    if not validate_media_file(media_path):
        player["state"] = "error"; return
    _reset_visual_effects_before_play()
    if not adopt_preloaded(VIDEO_PATH):
        ensure_pipeline()
        # Inizializza a nero: preferisci alpha se disponibile, altrimenti brightness
        if player["alpha"]:
            try: player["alpha"].set_property("alpha", 0.0)
            except Exception: pass
        if player["vb"]:
            try: player["vb"].set_property("brightness", -1.0)
            except Exception: pass
        if splash["active"]:
            hide_splash()
        player["pipeline"].set_state(Gst.State.PLAYING)
        player["state"] = "playing"
        if playlist["items"]:
            schedule_preload()
        print("[PLAYER] Riproduzione avviata (nuova pipeline)", flush=True)
    else:
        print("[PLAYER] Riproduzione avviata (pipeline precaricata)", flush=True)
    # Fade-in automatico
    if fade_in_seconds and fade_in_seconds > 0:
        if player["alpha"]:
            fade_opacity_to(1.0, fade_in_seconds, start_from=0.0)
        elif player["vb"]:
            fade_to(0.0, fade_in_seconds, start_from=-1.0)
    # Persist last media
    persist_settings()
    _autoplay_on_play_started(VIDEO_PATH)
    # Una volta che si avvia la riproduzione, la readiness per il "go" può considerarsi consumata
    try:
        show_state.update({"ready": False})
    except Exception:
        pass

def pause_play():
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend and hasattr(backend, "pause"):
            try:
                backend.pause()
            except Exception as exc:
                print(f"[PLAYER] Pause backend fallita: {exc}", flush=True)
        try:
            gui_log("pause")
        except Exception:
            pass
        return
    if player["pipeline"]:
        player["pipeline"].set_state(Gst.State.PAUSED)
        player["state"] = "paused"
        try:
            gui_log("pause")
        except Exception:
            pass

def resume_play():
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend and hasattr(backend, "resume"):
            try:
                backend.resume()
            except Exception as exc:
                print(f"[PLAYER] Resume backend fallita: {exc}", flush=True)
        try:
            gui_log("resume")
        except Exception:
            pass
        return
    if player["pipeline"]:
        player["pipeline"].set_state(Gst.State.PLAYING)
        player["state"] = "playing"
        try:
            gui_log("resume")
        except Exception:
            pass

def stop_play():
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend and hasattr(backend, "stop"):
            try:
                backend.stop()
            except Exception as exc:
                print(f"[PLAYER] Stop backend fallita: {exc}", flush=True)
        player["state"] = "stopped"
        player["vb"] = None
        player["alpha"] = None
    else:
        if player["pipeline"]:
            player["pipeline"].set_state(Gst.State.NULL)
            player["state"] = "stopped"
            player["pipeline"] = None
        player["vb"] = None
        player["alpha"] = None
    try:
        gui_log("stop")
    except Exception:
        pass
    # Se lo splash è nascosto, mostra immagine nera in pausa
    try:
        if not splash.get("active"):
            show_idle_black()
    except Exception:
        pass

def set_loop(on: bool):
    player["loop"] = bool(on)
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend and hasattr(backend, "set_loop"):
            try:
                backend.set_loop(player["loop"])
            except Exception as exc:
                print(f"[PLAYER] Set loop backend fallita: {exc}", flush=True)

def fade_to(target: float, duration_s: float = 1.0, on_complete=None, start_from: float | None = None):
    """Esegue fade non bloccante sul brightness.
    target: valore finale (-1..1)
    duration_s: durata in secondi
    on_complete: callback chiamata al termine
    start_from: se definito forza brightness iniziale prima di iniziare
    """
    global fade_seq
    vb = player["vb"]
    if not vb or duration_s <= 0:
        if vb:
            vb.set_property("brightness", target)
        if on_complete:
            GLib.idle_add(on_complete)
        return
    if start_from is not None:
        vb.set_property("brightness", start_from)
    cur = vb.get_property("brightness")
    steps = max(1, int(duration_s / (FADE_INTERVAL_MS / 1000.0)))
    step_val = (target - cur) / steps
    fade_seq += 1
    my_seq = fade_seq

    def stepper(i=[0], val=[cur]):
        if my_seq != fade_seq:
            return False  # Cancellato da nuovo fade
        if not player["vb"]:
            return False
        val[0] += step_val
        final = (i[0] + 1) >= steps
        vb.set_property("brightness", max(-1.0, min(1.0, target if final else val[0])))
        i[0] += 1
        if final:
            if on_complete:
                GLib.idle_add(on_complete)
            return False
        return True

    GLib.timeout_add(FADE_INTERVAL_MS, stepper)

def fade_opacity_to(target: float, duration_s: float = 1.0, on_complete=None, start_from: float | None = None):
    """Fade sull'opacità (elemento alpha), range 0..1. Fallback: no-op se alpha mancante."""
    global fade_seq
    af = player["alpha"]
    if not af or duration_s <= 0:
        if af:
            af.set_property("alpha", max(0.0, min(1.0, target)))
        if on_complete:
            GLib.idle_add(on_complete)
        return
    if start_from is not None:
        af.set_property("alpha", start_from)
    try:
        cur = float(af.get_property("alpha"))
    except Exception:
        cur = 1.0
    steps = max(1, int(duration_s / (FADE_INTERVAL_MS / 1000.0)))
    step_val = (target - cur) / steps
    fade_seq += 1
    my_seq = fade_seq

    def stepper(i=[0], val=[cur]):
        if my_seq != fade_seq:
            return False
        if not player["alpha"]:
            return False
        val[0] += step_val
        final = (i[0] + 1) >= steps
        af.set_property("alpha", max(0.0, min(1.0, target if final else val[0])))
        i[0] += 1
        if final:
            if on_complete:
                GLib.idle_add(on_complete)
            return False
        return True

    GLib.timeout_add(FADE_INTERVAL_MS, stepper)

def ping_on_player(duration_ms: int = 120):
    """Effetto lampo veloce sul player: preferisci alpha (black flash) o brightness boost."""
    try:
        # Preferisci saturazione alta (flash "bianco")
        if player["vb"] and player["vb"] is not None:
            vb = player["vb"]
            cur_sat = None
            try:
                cur_sat = float(vb.get_property("saturation"))
            except Exception:
                cur_sat = None
            if cur_sat is not None:
                vb.set_property("saturation", 2.0)
                def restore():
                    try:
                        vb.set_property("saturation", cur_sat)
                    except Exception:
                        pass
                    return False
                GLib.timeout_add(max(10, duration_ms), restore)
                return True
        if player["alpha"]:
            af = player["alpha"]
            cur = float(af.get_property("alpha"))
            af.set_property("alpha", 0.0)
            def restore():
                try:
                    af.set_property("alpha", cur)
                except Exception:
                    pass
                return False
            GLib.timeout_add(max(10, duration_ms), restore)
            return True
        elif player["vb"]:
            vb = player["vb"]
            cur = float(vb.get_property("brightness"))
            boost = max(-1.0, min(1.0, cur + 0.6))
            vb.set_property("brightness", boost)
            def restore():
                try:
                    vb.set_property("brightness", cur)
                except Exception:
                    pass
                return False
            GLib.timeout_add(max(10, duration_ms), restore)
            return True
    except Exception:
        return False
    return False

def push_white_splash_frame(appsrc):
    """Pusha un frame di splash completamente bianco (flash)."""
    img = Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (255, 255, 255))
    buf = Gst.Buffer.new_allocate(None, TARGET_WIDTH * TARGET_HEIGHT * 3, None)
    buf.fill(0, img.tobytes())
    now = int(time.time() * Gst.SECOND)
    buf.pts = buf.dts = now
    appsrc.emit("push-buffer", buf)

def ping_on_splash(duration_ms: int = 120):
    """Effetto lampo sempre visibile: frame bianco temporaneo, poi ripristino splash."""
    try:
        if not splash.get("active"):
            return False

        # NB: gli endpoint /test/on e /test/off sono definiti a livello modulo (vedi più sotto)
        appsrc = splash.get("src")
        vb = splash.get("vb")
        if not appsrc:
            return False
        # 1) Boost visivo immediato sul videobalance (più affidabile del solo frame)
        restored = {"done": False}
        cur_bri = None; cur_sat = None
        if vb:
            try:
                cur_bri = float(vb.get_property("brightness"))
            except Exception:
                cur_bri = None
            try:
                cur_sat = float(vb.get_property("saturation"))
            except Exception:
                cur_sat = None
            try:
                if cur_bri is not None:
                    vb.set_property("brightness", 1.0)
                if cur_sat is not None:
                    vb.set_property("saturation", 2.0)
            except Exception:
                pass
        # 2) Inoltre pusha un frame bianco
        GLib.idle_add(push_white_splash_frame, appsrc)
        # Restore dopo la durata
        def restore():
            if restored["done"]:
                return False
            restored["done"] = True
            try:
                # Ripristina frame splash
                push_splash_frame(appsrc)
            except Exception:
                pass
            # Ripristina proprietà vb
            try:
                if vb and (cur_bri is not None):
                    vb.set_property("brightness", cur_bri)
                if vb and (cur_sat is not None):
                    vb.set_property("saturation", cur_sat)
            except Exception:
                pass
            return False
        GLib.timeout_add(max(20, duration_ms), restore)
        return True
    except Exception:
        return False

# ---------- Test mode (pattern dinamico) endpoints (definiti a livello modulo) ----------
def build_test_pipeline():
    sink = "kmssink" if USE_KMS else "autovideosink"
    caps = f"video/x-raw,format=RGB,width={TARGET_WIDTH},height={TARGET_HEIGHT},framerate={TARGET_FPS}"
    desc = f"appsrc name=testsrc is-live=true format=time ! {caps} ! videoconvert ! {sink}"
    print(f"[TEST] Pipeline: {desc}", flush=True)
    p = Gst.parse_launch(desc)
    src = p.get_by_name("testsrc")
    return p, src

def _test_push_frame():
    try:
        if not test_mode["active"] or not test_mode["src"]:
            return False
        W, H = TARGET_WIDTH, TARGET_HEIGHT
        img = Image.new("RGB", (W, H), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        y = test_mode["y"] % H
        x = test_mode["x"] % W
        draw.line([(0, y), (W-1, y)], fill=(255, 255, 255))
        draw.line([(x, 0), (x, H-1)], fill=(255, 0, 0))
        buf = Gst.Buffer.new_allocate(None, W * H * 3, None)
        buf.fill(0, img.tobytes())
        now = int(time.time() * Gst.SECOND)
        buf.pts = buf.dts = now
        test_mode["src"].emit("push-buffer", buf)
        test_mode["y"] = (y + 5) % H
        test_mode["x"] = (x + 7) % W
    except Exception as e:
        print(f"[TEST] push error: {e}", flush=True)
        return False
    return True

@app.post("/test/on")
def api_test_on():
    try:
        # Stop player corrente per evitare conflitti KMS
        if player.get("pipeline"):
            try:
                player["pipeline"].set_state(Gst.State.NULL)
            except Exception:
                pass
            player.update({"pipeline": None, "vb": None, "alpha": None, "state": "stopped"})
        # Nascondi splash
        if splash.get("active"):
            hide_splash()
        p, src = build_test_pipeline()
        test_mode.update({"pipeline": p, "src": src, "active": True, "x": 0, "y": 0})
        p.set_state(Gst.State.PLAYING)
        # Ticker a ~25fps
        if test_mode["ticker"]:
            try: GLib.source_remove(test_mode["ticker"])
            except Exception: pass
        test_mode["ticker"] = GLib.timeout_add(40, _test_push_frame)
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.post("/test/off")
def api_test_off():
    try:
        if test_mode.get("ticker"):
            try: GLib.source_remove(test_mode["ticker"])
            except Exception: pass
            test_mode["ticker"] = None
        if test_mode.get("pipeline"):
            try: test_mode["pipeline"].set_state(Gst.State.NULL)
            except Exception: pass
        test_mode.update({"pipeline": None, "src": None, "active": False})
        # Mostra splash nuovamente
        if not splash.get("active"):
            show_splash_until_play()
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

def _reset_visual_effects_before_play():
    """Ripristina valori standard per evitare che un ping residuo alteri il contenuto."""
    try:
        if player.get("vb"):
            try:
                player["vb"].set_property("saturation", 1.0)
            except Exception:
                pass
    except Exception:
        pass

@app.post("/ping")
def api_ping(duration_ms: int = Query(200), in_time: float | None = Query(None)):
    """Lampo visivo opzionalmente schedulato (in_time epoch o delay)."""
    result = {"ok": True, "scheduled": False, "state": player.get("state"), "delay": 0.0}
    def do_ping():
        scheduled = False
        if player.get("pipeline"):
            scheduled = ping_on_player(duration_ms)
        elif splash.get("active"):
            scheduled = ping_on_splash(duration_ms)
        result["scheduled"] = bool(scheduled)
    delay = schedule_action(in_time, do_ping)
    result["delay"] = delay
    return result

@app.get("/ping")
def api_ping_get(duration_ms: int = Query(200), in_time: float | None = Query(None)):
    return api_ping(duration_ms, in_time)

def schedule_preload():
    """Precarica la prossima entry playlist in stato PAUSED per ridurre il gap."""
    if not playlist["items"]:
        return
    if playlist["index"] < 0:
        return
    # Calcola prossimo indice
    next_index = playlist["index"] + 1
    if next_index >= len(playlist["items"]):
        if playlist["loop"]:
            next_index = 0
        else:
            return
    next_path = playlist["items"][next_index]
    if preloaded["path"] == next_path:
        return  # già pronto
    # Libera eventuale preload precedente
    if preloaded["pipeline"]:
        try:
            preloaded["pipeline"].set_state(Gst.State.NULL)
        except Exception:
            pass
        preloaded.update({"path": None, "pipeline": None, "vb": None})
    try:
        print(f"[PRELOAD] Precarico: {next_path}", flush=True)
        p, vb, af = build_player_pipeline(next_path)
        in_time: float = Query(0.0),
        # Stato PAUSED per preroll (veloce start successivo)
        p.set_state(Gst.State.PAUSED)
        preloaded.update({"path": next_path, "pipeline": p, "vb": vb, "alpha": af})
    except Exception as e:
        print(f"[PRELOAD] Errore preload {next_path}: {e}", flush=True)

# ---------- Fast-start endpoints ----------
@app.post("/faststart/prepare")
def api_faststart_prepare(
    path: Optional[str] = Body(None, embed=True),
    filename: Optional[str] = Body(None, embed=True),
):
    global VIDEO_PATH
    # Risolve il path come in /play
    chosen = None
    if path and filename:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Usa solo uno tra 'path' e 'filename'"})
    if path:
        p = Path(path)
        if not p.exists():
            return JSONResponse(status_code=404, content={"ok": False, "error": f"File non trovato: {path}"})
        chosen = str(p)
    elif filename:
        p = MEDIA_DIR / filename
        if not p.exists():
            return JSONResponse(status_code=404, content={"ok": False, "error": f"File non trovato: {p}"})
        chosen = str(p)
    else:
        chosen = VIDEO_PATH
    # Overlay a nero
    try:
        if OVERLAY_ENABLED:
            overlay_show(alpha=1.0)
    except Exception as e:
        print(f"[FASTSTART] Overlay non disponibile: {e}", flush=True)
    if current_framework["name"] == "gst":
        try:
            prepare_pipeline_for(chosen)
            faststart["prepared_path"] = chosen
            try:
                gui_log(f"faststart_prepare GST", data={"path": chosen})
            except Exception:
                pass
            return {"ok": True, "prepared": chosen}
        except Exception as e:
            try:
                gui_log(f"faststart_prepare error", level="ERROR", data={"path": chosen, "error": str(e)})
            except Exception:
                pass
            return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    elif current_framework["name"] == "cvlc":
        try:
            ensure_backend()
            backend = current_framework.get("backend")
            assert backend is not None
            # Prepara media: play+pauza+seek 0 per mostrare primo frame
            backend.faststart_prepare(chosen)  # type: ignore[attr-defined]
            faststart["prepared_path"] = chosen
            try:
                gui_log("faststart_prepare CVLC", data={"path": chosen})
            except Exception:
                pass
            return {"ok": True, "prepared": chosen}
        except Exception as e:
            try:
                gui_log("faststart_prepare error", level="ERROR", data={"path": chosen, "error": str(e)})
            except Exception:
                pass
            return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    elif current_framework["name"] == "mpv":
        try:
            ensure_backend()
            backend = current_framework.get("backend")
            assert backend is not None
            backend.faststart_prepare(chosen)  # type: ignore[attr-defined]
            faststart["prepared_path"] = chosen
            try:
                gui_log("faststart_prepare MPV", data={"path": chosen})
            except Exception:
                pass
            return {"ok": True, "prepared": chosen}
        except Exception as e:
            try:
                gui_log("faststart_prepare error", level="ERROR", data={"path": chosen, "error": str(e)})
            except Exception:
                pass
            return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    # Altri backend: best-effort
    faststart["prepared_path"] = chosen
    return {"ok": True, "prepared": chosen, "note": "preload best-effort"}

@app.post("/faststart/go")
def api_faststart_go(seconds: float = Query(1.0), in_time: float | None = Query(None)):
    target = faststart.get("prepared_path") or VIDEO_PATH
    if not target:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Nessun media pronto"})
    def _go_now():
        if current_framework["name"] == "gst":
            global VIDEO_PATH
            VIDEO_PATH = target
            if not adopt_preloaded(target):
                # Fallback: start normal
                start_play(fade_in_seconds=0.0)
            # Fade-out overlay
            try:
                if OVERLAY_ENABLED:
                    overlay_fade_to(0.0, seconds)
                    def _hide():
                        try: overlay_hide()
                        except Exception: pass
                        return False
                    GLib.timeout_add(int(max(0.0, seconds) * 1000) + 50, _hide)
            except Exception as e:
                print(f"[FASTSTART] Overlay fade error: {e}", flush=True)
            faststart["prepared_path"] = None
            try:
                gui_log("faststart_go GST", data={"seconds": seconds, "path": target})
            except Exception:
                pass
            return {"ok": True}
        # cvlc/vlc/mpv
        try:
            ensure_backend()
            backend = current_framework.get("backend")
            if not backend:
                raise RuntimeError("Backend non disponibile")
            if current_framework["name"] in {"cvlc", "mpv"} and faststart.get("prepared_path"):
                # riprendi se già preparato
                try:
                    backend.faststart_go()  # type: ignore[attr-defined]
                except Exception:
                    backend.play(target, loop=player.get("loop", False), fade_in=0.0)
            else:
                backend.play(target, loop=player.get("loop", False), fade_in=0.0)
            # Overlay fade
            if OVERLAY_ENABLED:
                overlay_fade_to(0.0, seconds)
                def _hide():
                    try: overlay_hide()
                    except Exception: pass
                    return False
                GLib.timeout_add(int(max(0.0, seconds) * 1000) + 50, _hide)
            faststart["prepared_path"] = None
            try:
                gui_log("faststart_go %s" % current_framework["name"].upper(), data={"seconds": seconds, "path": target})
            except Exception:
                pass
            return {"ok": True}
        except Exception as e:
            try:
                gui_log("faststart_go error", level="ERROR", data={"seconds": seconds, "path": target, "error": str(e)})
            except Exception:
                pass
            return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

    if in_time:
        try:
            t = float(in_time)
        except Exception:
            t = None
        if t:
            schedule_info["play_at"] = t if t >= 1e9 else (time.time() + max(0.0, t))
            def _clear_and_go():
                try: _go_now()
                finally:
                    schedule_info["play_at"] = None
                return False
            delay = schedule_action(in_time, _clear_and_go)
            return {"ok": True, "scheduled": True, "delay": delay}
    return _go_now()

# --- Backward-compatibility aliases (older GUI/clients) ---
@app.post("/faststart_prepare")
def api_faststart_prepare_legacy(
    path: Optional[str] = Body(None, embed=True),
    filename: Optional[str] = Body(None, embed=True),
):
    """Compat: alias di /faststart/prepare per client legacy."""
    return api_faststart_prepare(path=path, filename=filename)

@app.post("/faststart_go")
def api_faststart_go_legacy(seconds: float = Query(1.0), in_time: float | None = Query(None)):
    """Compat: alias di /faststart/go per client legacy."""
    return api_faststart_go(seconds=seconds, in_time=in_time)

# ---------- Update helpers ----------
def download_to(path: Path, url: str, report=None):
    req = Request(url, headers={"User-Agent": "headless-player"})
    tmp_path = path.with_suffix(path.suffix + ".part")
    with urlopen(req, timeout=30) as r, open(tmp_path, "wb") as f:
        total = int(r.headers.get("Content-Length", 0))
        got = 0
        while True:
            chunk = r.read(8192)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if report:
                report(got, total)
    try:
        os.replace(tmp_path, path)
    except PermissionError as e:
        # Ritenta dopo chmod se possibile
        try:
            os.chmod(path, 0o644)
            os.replace(tmp_path, path)
        except Exception:
            raise e

def apply_update(archive: Path):
    # Usa una dir temporanea fuori da APP_DIR per evitare problemi di permessi
    tmp = Path(tempfile.mkdtemp(prefix="release_tmp_", dir=str(_pick_update_dir())))

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
    else:
        try:
            with tarfile.open(archive) as t:
                t.extractall(tmp)
        except Exception:
            raise RuntimeError("Formato update non supportato (zip/tar.*)")

    # Se l'archivio contiene una sola directory di livello superiore, scendi di un livello
    src_root = tmp
    try:
        entries = [p for p in tmp.iterdir() if p.name not in {"__MACOSX", "._"}]
        if len(entries) == 1 and entries[0].is_dir():
            src_root = entries[0]
    except Exception:
        pass

    for root, dirs, files in os.walk(src_root):
        rel = Path(root).relative_to(src_root)
        dest = APP_DIR / rel
        dest.mkdir(parents=True, exist_ok=True)
        for name in files:
            relpath = (rel / name).as_posix()
            # Non distribuire/aggiornare ambienti virtuali locali: possono causare errori di permessi
            # e non sono portabili tra sistemi (es. venv Windows vs Linux). Vanno gestiti da setup.sh.
            if relpath.startswith("headless_venv/") or relpath == "headless_venv":
                continue
            if relpath.startswith("media/"):
                # Consenti eccezioni per i file neri di default
                if relpath not in {"media/black_1280_720.png", "media/black.png"}:
                    continue
            src_file = Path(root) / name
            dst_file = dest / name
            try:
                shutil.copy2(src_file, dest)
            except PermissionError as e:
                raise PermissionError(f"Permesso negato su '{dst_file}'. Esegui /maintenance/fix_permissions e riprova.") from e
            except Exception as e:
                raise RuntimeError(f"Errore copiando '{src_file}' -> '{dst_file}': {e}") from e

    shutil.rmtree(tmp, ignore_errors=True)

# ---------- FastAPI ----------
@app.on_event("startup")
def on_start():
    print("[STARTUP] Avvio applicazione", flush=True)
    def start_glib_and_splash():
        print("[STARTUP] Avvio main loop GLib", flush=True)
        threading.Thread(target=main_loop.run, daemon=True).start()
        time.sleep(0.5)
        print("[STARTUP] Main loop avviato, mostro splash", flush=True)
        show_splash_until_play()
    threading.Thread(target=start_glib_and_splash, daemon=True).start()
    # Avvio opzionale UDP
    if globals().get("UDP_ENABLED"):
        threading.Thread(target=_udp_thread, daemon=True).start()
    if autoplay.get("enabled"):
        GLib.idle_add(lambda: (_autoplay_schedule_initial(), False)[1])

@app.get("/healthz")
def healthz():
    return {"status": "ok", "state": player["state"], "version": VERSION, "ip": get_ip()}

@app.get("/status")
def status():
    # Esponi stato player, media corrente e schedulazioni note
    current_name = None
    try:
        if VIDEO_PATH:
            current_name = Path(VIDEO_PATH).name
    except Exception:
        current_name = None
    return {
        "version_current": VERSION,
        "version_available": current_update["available"],
        "player_state": player["state"],
        "splash_active": splash["active"],
        "overlay_active": overlay["active"],
        "device_name": DEVICE_NAME,
        "name": DEVICE_NAME,
        "current_media": current_name,
        "update_status": current_update["status"],
        "autoplay_enabled": autoplay.get("enabled", False),
        "update_progress": current_update.get("progress"),
        "update_error": current_update.get("error"),
        "update_bytes_done": current_update.get("bytes_done"),
        "update_bytes_total": current_update.get("bytes_total"),
        "last_logs": current_update["log"][-10:],
        "maintenance_status": maintenance["status"],
        "maintenance_last_logs": maintenance["log"][-10:],
        "faststart": {
            "prepared": bool(faststart.get("prepared_path")),
            "path": faststart.get("prepared_path"),
        },
        "scheduled": {
            "play_at": schedule_info.get("play_at"),
            "overlay_fade_at": schedule_info.get("overlay_fade_at"),
        },
        "log_udp": {
            "enabled": LOG_UDP_ENABLED,
            "host": LOG_UDP_HOST,
            "port": LOG_UDP_PORT,
        },
        "timing": {
            "synced": False,
            "method": None,
            "offset_ms": 0,
        },
        "show_ready": bool(show_state.get("ready")),
        "ready_for": show_state.get("for"),
    }

@app.post("/hide_splash")
def api_hide_splash():
    if splash["active"]:
        hide_splash()
        # Se non sta suonando nulla, mostra immagine nera di idle
        try:
            if player.get("state") in {"stopped", "idle", "unknown", None}:
                show_idle_black()
        except Exception:
            pass
        return {"ok": True, "message": "Splash nascosto"}
    else:
        return {"ok": True, "message": "Splash già nascosto"}

@app.post("/show_splash")
def api_show_splash():
    try:
        if not splash.get("active"):
            show_splash_until_play()
        return {"ok": True, "message": "Splash mostrato"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

# ---------- Device name ----------
@app.get("/device/name")
def api_get_device_name():
    return {"ok": True, "name": DEVICE_NAME}

@app.post("/device/name")
def api_set_device_name(name: Optional[str] = Body(None, embed=True)):
    global DEVICE_NAME
    if not name:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Campo 'name' richiesto"})
    DEVICE_NAME = str(name)
    persist_settings({"device_name": DEVICE_NAME})
    return {"ok": True, "name": DEVICE_NAME}

# ---------- UDP Log subscribe (REST) ----------
@app.get("/log/udp/status")
def api_log_udp_status():
    return {"ok": True, "enabled": LOG_UDP_ENABLED, "host": LOG_UDP_HOST, "port": LOG_UDP_PORT}

@app.post("/log/udp/subscribe")
def api_log_udp_subscribe(host: Optional[str] = Body(None, embed=True), port: Optional[int] = Body(None, embed=True), persist: bool = Body(False, embed=True)):
    if not host:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Campo 'host' richiesto"})
    try:
        p = int(port) if port is not None else LOG_UDP_PORT
    except Exception:
        p = LOG_UDP_PORT
    set_log_udp_target(host, p, enabled=True, persist=persist)
    try:
        gui_log("log_subscribe", data={"host": host, "port": p})
    except Exception:
        pass
    return {"ok": True, "enabled": LOG_UDP_ENABLED, "host": LOG_UDP_HOST, "port": LOG_UDP_PORT}

@app.post("/log/udp/unsubscribe")
def api_log_udp_unsubscribe(persist: bool = Body(False, embed=True)):
    set_log_udp_target("", LOG_UDP_PORT, enabled=False, persist=persist)
    return {"ok": True, "enabled": LOG_UDP_ENABLED}


@app.post("/shutdown")
def api_shutdown():
    """Richiede lo spegnimento del sistema. Non riavvia il solo servizio."""
    print("[SHUTDOWN] Richiesta shutdown di sistema", flush=True)

    result = {"ok": False, "message": "", "attempts": []}

    def worker():
        cmds = [
            ["sudo", "-n", "loginctl", "poweroff"],
            ["sudo", "-n", "systemctl", "poweroff"],
            ["sudo", "-n", "shutdown", "-h", "now"],
            ["sudo", "-n", "poweroff"],
            ["loginctl", "poweroff"],
            ["systemctl", "poweroff"],
            ["shutdown", "-h", "now"],
            ["poweroff"],
        ]
        for cmd in cmds:
            try:
                print(f"[SHUTDOWN] Eseguo: {' '.join(cmd)}", flush=True)
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, text=True)
                print("[SHUTDOWN] Comando inviato con successo", flush=True)
                # Se il sistema accetta il comando, chiudiamo l'event loop per una terminazione pulita
                GLib.idle_add(main_loop.quit)
                result.update({"ok": True, "message": "Shutdown in corso", "attempts": []})
                return
            except subprocess.CalledProcessError as exc:
                msg = (exc.stderr or exc.stdout or "").strip()
                print(f"[SHUTDOWN] Comando fallito ({' '.join(cmd)}): {msg}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": msg})
            except subprocess.TimeoutExpired:
                print(f"[SHUTDOWN] Timeout eseguendo {' '.join(cmd)}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": "timeout"})
            except FileNotFoundError:
                print(f"[SHUTDOWN] Comando non trovato: {cmd[0]}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": "not_found"})
            except Exception as exc:
                print(f"[SHUTDOWN] Errore imprevisto con {' '.join(cmd)}: {exc}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": str(exc)})
        result.update({"ok": False, "message": "Nessun comando shutdown accettato"})

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    # Risposta immediata 202; l'handler chiamante può attendersi la disconnessione
    return JSONResponse(status_code=202, content={"ok": True, "message": "Shutdown richiesto"})



@app.post("/play")
def api_play(
    path: Optional[str] = Body(None, embed=True),
    filename: Optional[str] = Body(None, embed=True),
    loop: Optional[bool] = Body(None, embed=True),
    hide_splash_first: bool = Body(True, embed=True),
    fade_in_seconds: float = Body(0.5, embed=True),
    fade_out_seconds: Optional[float] = Body(None, embed=True),
    in_time: Optional[float] = Body(None, embed=True),
    # Fallback da query string (se body vuoto)
    path_q: Optional[str] = Query(None),
    filename_q: Optional[str] = Query(None),
    loop_q: Optional[int] = Query(None),
):
    """Avvia la riproduzione.
    Parametri accettati (JSON body):
      - path: percorso assoluto (deve stare dentro la media dir o essere file leggibile)
      - filename: nome di un file dentro media/ (esclusivo con path)
      - loop: abilita/disabilita loop (facoltativo)
      - hide_splash_first: default True, nasconde lo splash se attivo
    Precedenza: path > filename > default (VIDEO_PATH attuale).
    """
    global VIDEO_PATH

    chosen = None
    # Se non arrivano dal body, usa i parametri query
    path = path or path_q
    filename = filename or filename_q
    if loop is None and loop_q is not None:
        loop = bool(loop_q)
    if path and filename:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Usa solo uno tra 'path' e 'filename'"})

    if path:
        p = Path(path)
        if not p.exists():
            return JSONResponse(status_code=404, content={"ok": False, "error": f"File non trovato: {path}"})
        # Non forzare che sia in media, ma avvisa se fuori
        if not str(p).startswith(str(MEDIA_DIR)):
            print(f"[PLAY] WARNING: file fuori da media/: {p}", flush=True)
        chosen = str(p)
    elif filename:
        p = MEDIA_DIR / filename
        if not p.exists():
            # Fallback intelligente: se filename non esiste (es. vecchio default clip.mp4), prova a scansionare media/
            print(f"[PLAY] '{filename}' non trovato in media/. Provo fallback su altri file presenti…", flush=True)
            items = []
            for fp in sorted(MEDIA_DIR.iterdir()):
                if fp.is_file() and validate_media_file(fp):
                    items.append(str(fp))
            if items:
                playlist["items"] = items
                playlist["index"] = 0
                chosen = items[0]
                print(f"[PLAY] Fallback: avvio {chosen}", flush=True)
            else:
                return JSONResponse(status_code=404, content={"ok": False, "error": f"File non trovato in media/: {filename}"})
        else:
            chosen = str(p)
    else:
        # fallback: playlist se esiste; altrimenti scansione rapida di media/
        if playlist["items"]:
            if 0 <= playlist["index"] < len(playlist["items"]):
                chosen = playlist["items"][playlist["index"]]
            else:
                playlist["index"] = 0
                chosen = playlist["items"][0]
        else:
            items = []
            for p in sorted(MEDIA_DIR.iterdir()):
                if p.is_file() and validate_media_file(p):
                    items.append(str(p))
            if items:
                playlist["items"] = items
                playlist["index"] = 0
                chosen = items[0]
            elif Path(VIDEO_PATH).exists():
                chosen = VIDEO_PATH
            else:
                return JSONResponse(status_code=404, content={"ok": False, "error": "Nessun file in media/: carica un video o specifica filename"})

    if chosen:
        # Stop pipeline corrente se cambia file
        if chosen != VIDEO_PATH:
            stop_play()
        VIDEO_PATH = chosen
        # Se il file è in playlist, aggiorna l'indice corrente alla sua posizione
        try:
            if playlist["items"]:
                if chosen in playlist["items"]:
                    playlist["index"] = playlist["items"].index(chosen)
                else:
                    # se non è presente, resetta index coerentemente
                    playlist["index"] = -1
        except Exception:
            pass

    # Non toccare qui lo splash o la pipeline: ci pensa start_play()

    if loop is not None:
        set_loop(loop)
    def start_logic():
        # Se backend non gst delega
        if current_framework["name"] != "gst":
            ensure_backend()
            backend = current_framework.get("backend")
            if backend:
                try:
                    backend.play(VIDEO_PATH, loop if loop is not None else player.get("loop"), fade_in_seconds)
                except Exception as e:
                    return {"ok": False, "error": str(e)}
                try:
                    gui_log("play", data={"path": VIDEO_PATH, "backend": current_framework["name"], "loop": player.get("loop")})
                except Exception:
                    pass
                return {"ok": True, "playing": VIDEO_PATH, "backend": current_framework["name"], "scheduled": False}
        # GStreamer path
        start_play(fade_in_seconds)
        try:
            gui_log("play", data={"path": VIDEO_PATH, "backend": current_framework["name"], "loop": player.get("loop")})
        except Exception:
            pass
    return {"ok": True, "playing": VIDEO_PATH, "backend": current_framework["name"], "scheduled": False}

    # Fade-out + scheduling: se in_time futuro, pianifica fade-out prima
    if in_time:
        try:
            target = float(in_time)
        except Exception:
            target = None
        if target:
            now = time.time()
            # calcola delay complessivo
            if target >= 1e9:
                delay_total = max(0.0, target - now)
            else:
                delay_total = max(0.0, target)
            # se fade_out richiesto, anticipa
            if fade_out_seconds and fade_out_seconds > 0 and player.get("pipeline"):
                fade_start_delay = max(0.0, delay_total - fade_out_seconds)
                # programma fade_start_delay -> fade out, completato esegue start logic alla deadline
                def fade_then_schedule_start():
                    def after_fade():
                        # avvia reale all'orario (se c'è residuo di tempo lo aspetta)
                        residual = max(0.0, delay_total - fade_out_seconds - (time.time() - now))
                        def _clear_and_start():
                            try:
                                start_logic()
                            finally:
                                schedule_info["play_at"] = None
                            return False
                        if residual > 0:
                            GLib.timeout_add(int(residual * 1000), _clear_and_start)
                        else:
                            _clear_and_start()
                    if player.get("alpha"):
                        fade_opacity_to(0.0, fade_out_seconds, on_complete=after_fade)
                    elif player.get("vb"):
                        fade_to(-1.0, fade_out_seconds, on_complete=after_fade)
                    else:
                        after_fade()
                # Registra il play_at pianificato per rendere l'operazione idempotente lato GUI
                try:
                    schedule_info["play_at"] = target if target >= 1e9 else (time.time() + delay_total)
                except Exception:
                    pass
                GLib.timeout_add(int(fade_start_delay * 1000), lambda: (fade_then_schedule_start(), False)[1])
                try:
                    gui_log("play_scheduled", data={"path": VIDEO_PATH, "backend": current_framework["name"], "at": target, "fade_out_s": fade_out_seconds})
                except Exception:
                    pass
                return {"ok": True, "scheduled": True, "playing": VIDEO_PATH, "backend": current_framework["name"], "in_time": in_time, "fade_out_s": fade_out_seconds}
            else:
                # nessun fade out: programma direttamente start
                def _clear_and_start():
                    try:
                        start_logic()
                    finally:
                        schedule_info["play_at"] = None
                    return False
                # Registra il play_at pianificato per rendere l'operazione idempotente lato GUI
                try:
                    schedule_info["play_at"] = target if target >= 1e9 else (time.time() + delay_total)
                except Exception:
                    pass
                schedule_action(in_time, _clear_and_start)
                try:
                    gui_log("play_scheduled", data={"path": VIDEO_PATH, "backend": current_framework["name"], "at": target})
                except Exception:
                    pass
                return {"ok": True, "scheduled": True, "playing": VIDEO_PATH, "backend": current_framework["name"], "in_time": in_time}

    # esecuzione immediata con eventuale fade-out breve
    if fade_out_seconds and fade_out_seconds > 0:
        # Backend GST: fade native
        if current_framework["name"] == "gst" and player.get("pipeline"):
            def after():
                start_logic()
            if player.get("alpha"):
                fade_opacity_to(0.0, fade_out_seconds, on_complete=after)
            elif player.get("vb"):
                fade_to(-1.0, fade_out_seconds, on_complete=after)
            else:
                start_logic()
            try:
                gui_log("play", data={"path": VIDEO_PATH, "backend": current_framework["name"], "fade_out_s": fade_out_seconds})
            except Exception:
                pass
            return {"ok": True, "scheduled": False, "playing": VIDEO_PATH, "backend": current_framework["name"], "fade_out_s": fade_out_seconds}
        # Backend alternativi: prova a usare metodo fade_out se esiste
        elif current_framework["name"] != "gst":
            ensure_backend()
            be = current_framework.get("backend")
            if hasattr(be, "fade_out") and callable(getattr(be, "fade_out")) and be and hasattr(be, "is_playing") and be.is_playing():
                try:
                    def after_alt():
                        start_logic()
                    # Esegue fade_out poi start nuovo contenuto (che farà fade_in interno se richiesto)
                    be.fade_out(fade_out_seconds)
                    # Pianifica lo start dopo la durata
                    GLib.timeout_add(int(fade_out_seconds * 1000), lambda: (after_alt(), False)[1])
                    return {"ok": True, "scheduled": False, "playing": VIDEO_PATH, "backend": current_framework["name"], "fade_out_s": fade_out_seconds}
                except Exception:
                    pass
        # Se non si può fare fade out, procedi subito
        return start_logic()
    else:
        return start_logic()

@app.get("/play")
def api_play_get(
    path: Optional[str] = Query(None),
    filename: Optional[str] = Query(None),
    loop: Optional[int] = Query(None),
    fade_in_seconds: float = Query(0.5),
    fade_out_seconds: Optional[float] = Query(None),
    in_time: Optional[float] = Query(None),
):
    loop_bool = (loop == 1) if loop is not None else None
    # Riutilizza la stessa logica del POST
    return api_play(path, filename, loop_bool, True, fade_in_seconds, fade_out_seconds, in_time, None, None, None)

@app.post("/play_at")
def api_play_at(
    at: float = Query(...),
    path: Optional[str] = Body(None, embed=True),
    filename: Optional[str] = Body(None, embed=True),
    loop: Optional[bool] = Body(None, embed=True),
    hide_splash_first: bool = Body(True, embed=True),
    fade_in_seconds: float = Body(0.5, embed=True),
    fade_out_seconds: Optional[float] = Body(None, embed=True),
):
    # Wrapper esplicito per chiarezza
    return api_play(path, filename, loop, hide_splash_first, fade_in_seconds, fade_out_seconds, at, None, None, None)

@app.get("/play_at")
def api_play_at_get(
    at: float = Query(...),
    path: Optional[str] = Query(None),
    filename: Optional[str] = Query(None),
    loop: Optional[int] = Query(None),
    fade_in_seconds: float = Query(0.5),
    fade_out_seconds: Optional[float] = Query(None),
):
    loop_bool = (loop == 1) if loop is not None else None
    return api_play(path, filename, loop_bool, True, fade_in_seconds, fade_out_seconds, at, None, None, None)

@app.post("/fade_out_current")
def api_fade_out_current(seconds: float = Body(1.0, embed=True)):
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend and hasattr(backend, "fade_out"):
            try:
                try:
                    if hasattr(backend, "is_playing") and not backend.is_playing():
                        return {"ok": False, "error": "Nessuna riproduzione attiva (fade_out_current ignorato)"}
                except Exception:
                    pass
                backend.fade_out(seconds)
                return {"ok": True}
            except Exception as exc:
                print(f"[PLAYER] Fade-out backend fallita: {exc}", flush=True)
                return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": "Backend fade_out non supportato"}
    if player["alpha"]:
        fade_opacity_to(0.0, seconds)
    else:
        fade_to(-1.0, seconds)
    return {"ok": True}

# ---------- Backend info & switch ----------
@app.get("/framework")
def framework_get():
    return {"ok": True, "current": current_framework["name"], "available": available_backends()}

@app.post("/change_framework")
def change_framework(payload: dict = Body(...)):
    name = payload.get("name") if isinstance(payload, dict) else None
    in_time = payload.get("in_time") if isinstance(payload, dict) else None
    if not name:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Campo 'name' richiesto"})
    if name == current_framework["name"]:
        # comunque puoi schedulare un restart interno (stop/start) se voluto
        def restart_same():
            backend = current_framework.get("backend")
            try:
                if backend and hasattr(backend, "is_playing") and backend.is_playing():
                    backend.stop()
            except Exception:
                print("[BACKEND] Stop backend corrente fallito", flush=True)
            try:
                if backend and hasattr(backend, "shutdown"):
                    backend.shutdown()
            except Exception:
                print("[BACKEND] Shutdown backend corrente fallito", flush=True)
            try:
                init_backend(name, persist=True)
            except Exception as exc:
                print(f"[BACKEND] Restart dello stesso framework fallito: {exc}", flush=True)
            return False

        delay = schedule_action(in_time, restart_same)
        return {
            "ok": True,
            "message": "Gia' attivo",
            "current": current_framework["name"],
            "scheduled": bool(in_time),
            "delay": delay,
        }
    if name not in available_backends():
        return JSONResponse(status_code=404, content={"ok": False, "error": f"Backend '{name}' non registrato"})

    def do_switch():
        try:
            # stop backend corrente
            try:
                if current_framework["backend"]:
                    current_framework["backend"].stop()
            except Exception:
                pass
            init_backend(name, persist=True)
            # Se splash ancora attivo lo lasciamo; nuova riproduzione avverrà su richiesta
        except Exception as e:
            print(f"[BACKEND] Switch error: {e}", flush=True)
        return False

    delay = schedule_action(in_time, do_switch)
    return {"ok": True, "scheduled": bool(in_time), "delay": delay, "target": name, "current": current_framework["name"] if in_time else name}

@app.post("/pause")
def api_pause():
    pause_play(); return {"ok": True}

@app.post("/resume")
def api_resume():
    resume_play(); return {"ok": True}

@app.post("/loop")
def api_loop(on: int = Query(1)):
    set_loop(on == 1); return {"ok": True, "loop": player["loop"]}

@app.post("/stop")
def api_stop():
    stop_play()
    return {"ok": True, "state": player.get("state", "stopped")}

@app.post("/fade_in")
def api_fade_in(seconds: float = Query(1.0), in_time: float | None = Query(None)):
    if in_time:
        def _do():
            return api_fade_in(seconds)
        delay = schedule_action(in_time, _do)
        return {"ok": True, "scheduled": True, "delay": delay}
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend and hasattr(backend, "fade_in"):
            try:
                # Se il backend supporta lo stato, evita no-op quando non sta riproducendo
                try:
                    if hasattr(backend, "is_playing") and not backend.is_playing():
                        return {"ok": False, "error": "Nessuna riproduzione attiva (fade_in ignorato)"}
                except Exception:
                    pass
                backend.fade_in(seconds)
                return {"ok": True}
            except Exception as exc:
                print(f"[PLAYER] Fade-in backend fallita: {exc}", flush=True)
        return {"ok": False, "error": "Backend fade_in non supportato"}
    if player["alpha"]:
        fade_opacity_to(1.0, seconds)
    else:
        fade_to(0.0, seconds)
    return {"ok": True}

@app.post("/fade_out")
def api_fade_out(seconds: float = Query(1.0), in_time: float | None = Query(None)):
    if in_time:
        def _do():
            return api_fade_out(seconds)
        delay = schedule_action(in_time, _do)
        return {"ok": True, "scheduled": True, "delay": delay}
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        if backend and hasattr(backend, "fade_out"):
            try:
                try:
                    if hasattr(backend, "is_playing") and not backend.is_playing():
                        return {"ok": False, "error": "Nessuna riproduzione attiva (fade_out ignorato)"}
                except Exception:
                    pass
                backend.fade_out(seconds)
                return {"ok": True}
            except Exception as exc:
                print(f"[PLAYER] Fade-out backend fallita: {exc}", flush=True)
        return {"ok": False, "error": "Backend fade_out non supportato"}
    if player["alpha"]:
        fade_opacity_to(0.0, seconds)
    else:
        fade_to(-1.0, seconds)
    return {"ok": True}

# ------- Visual fades (backend-specific: cvlc brightness) -------
@app.post("/visual/ftb")
def api_visual_ftb(seconds: float = Query(1.0), in_time: float | None = Query(None)):
    if in_time:
        def _do():
            return api_visual_ftb(seconds)
        delay = schedule_action(in_time, _do)
        # aggiorna schedule_info solo come info (non distinto da play)
        return {"ok": True, "scheduled": True, "delay": delay}
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        # Se backend implementa visual_fade_out, usalo; altrimenti tenta fade_out standard
        # Preferisci overlay KMS se disponibile
        if OVERLAY_ENABLED and OVERLAY_USE_KMS and OVERLAY_KMS_PLANE_ID > 0:
            try:
                overlay_show(alpha=0.0)
                overlay_fade_to(1.0, seconds)
                return {"ok": True, "method": "overlay"}
            except Exception as e:
                print(f"[OVERLAY] FTB fallback a backend: {e}", flush=True)
        if backend and hasattr(backend, "visual_fade_out"):
            try:
                backend.visual_fade_out(seconds)
                return {"ok": True, "method": "backend"}
            except Exception as exc:
                print(f"[PLAYER] Visual FTB backend fallita: {exc}", flush=True)
                return {"ok": False, "error": str(exc)}
        # fallback (audio fade)
        return api_fade_out(seconds)
    # gst: già abbiamo FTB visivo via alpha/brightness
    if player["alpha"]:
        fade_opacity_to(0.0, seconds)
    else:
        fade_to(-1.0, seconds)
    return {"ok": True}

@app.post("/visual/fade_in")
def api_visual_fade_in(seconds: float = Query(1.0), in_time: float | None = Query(None)):
    if in_time:
        def _do():
            return api_visual_fade_in(seconds)
        delay = schedule_action(in_time, _do)
        return {"ok": True, "scheduled": True, "delay": delay}
    if current_framework["name"] != "gst":
        ensure_backend()
        backend = current_framework.get("backend")
        # Preferisci overlay KMS se disponibile
        if OVERLAY_ENABLED and OVERLAY_USE_KMS and OVERLAY_KMS_PLANE_ID > 0:
            try:
                overlay_show(alpha=1.0)
                overlay_fade_to(0.0, seconds)
                def _hide():
                    try: overlay_hide()
                    except Exception: pass
                    return False
                GLib.timeout_add(int(max(0.0, seconds) * 1000) + 50, _hide)
                return {"ok": True, "method": "overlay"}
            except Exception as e:
                print(f"[OVERLAY] Fade-in fallback a backend: {e}", flush=True)
        if backend and hasattr(backend, "visual_fade_in"):
            try:
                backend.visual_fade_in(seconds)
                return {"ok": True, "method": "backend"}
            except Exception as exc:
                print(f"[PLAYER] Visual Fade-in backend fallita: {exc}", flush=True)
                return {"ok": False, "error": str(exc)}
        # fallback (audio fade-in)
        return api_fade_in(seconds)
    if player["alpha"]:
        fade_opacity_to(1.0, seconds)
    else:
        fade_to(0.0, seconds)
    return {"ok": True}

# ---------- Overlay endpoints ----------
@app.post("/overlay/show")
def api_overlay_show(alpha: float = Query(1.0)):
    try:
        overlay_show(alpha=max(0.0, min(1.0, alpha)))
        try:
            gui_log("overlay_show", data={"alpha": alpha})
        except Exception:
            pass
        return {"ok": True, "alpha": alpha}
    except Exception as e:
        try:
            gui_log("overlay_show error", level="ERROR", data={"alpha": alpha, "error": str(e)})
        except Exception:
            pass
        return {"ok": False, "error": str(e)}

@app.post("/overlay/hide")
def api_overlay_hide():
    try:
        overlay_hide()
        try:
            gui_log("overlay_hide")
        except Exception:
            pass
        return {"ok": True}
    except Exception as e:
        try:
            gui_log("overlay_hide error", level="ERROR", data={"error": str(e)})
        except Exception:
            pass
        return {"ok": False, "error": str(e)}

@app.post("/overlay/fade")
def api_overlay_fade(target: float = Query(0.0), seconds: float = Query(1.0), in_time: float | None = Query(None)):
    try:
        def _do():
            overlay_show(alpha=1.0 if target >= 1.0 else 0.0)
            overlay_fade_to(max(0.0, min(1.0, target)), max(0.0, seconds))
            return False
        if in_time:
            try:
                t = float(in_time)
            except Exception:
                t = None
            if t:
                schedule_info["overlay_fade_at"] = t if t >= 1e9 else (time.time() + max(0.0, t))
                def _wrap():
                    try: _do()
                    finally: schedule_info["overlay_fade_at"] = None
                    return False
                delay = schedule_action(in_time, _wrap)
                try:
                    gui_log("overlay_fade_scheduled", data={"target": target, "seconds": seconds, "at": t})
                except Exception:
                    pass
                return {"ok": True, "scheduled": True, "delay": delay}
        _do()
        try:
            gui_log("overlay_fade", data={"target": target, "seconds": seconds})
        except Exception:
            pass
        return {"ok": True}
    except Exception as e:
        try:
            gui_log("overlay_fade error", level="ERROR", data={"target": target, "seconds": seconds, "error": str(e)})
        except Exception:
            pass
        return {"ok": False, "error": str(e)}

@app.post("/overlay/fade_at")
def api_overlay_fade_at(target: float = Query(0.0), seconds: float = Query(1.0), at: float = Query(...)):
    # Alias con parametro esplicito 'at'
    return api_overlay_fade(target=target, seconds=seconds, in_time=at)

@app.get("/media")
def list_media():
    """Elenca i file nella directory media con informazioni dettagliate"""
    files = []
    try:
        for file_path in MEDIA_DIR.iterdir():
            if file_path.is_file():
                stat = file_path.stat()
                
                # Leggi i primi bytes per determinare il tipo
                file_type = "unknown"
                with open(file_path, 'rb') as f:
                    header = f.read(16)
                    if header[4:8] == b'ftyp':
                        file_type = "mp4"
                    elif header.startswith(b'<!DOCTYPE') or header.startswith(b'<html'):
                        file_type = "html"
                    elif header.startswith(b'\x1a\x45\xdf\xa3'):
                        file_type = "mkv"
                    elif header.startswith(b'RIFF') and header[8:12] == b'AVI ':
                        file_type = "avi"
                
                files.append({
                    "name": file_path.name,
                    "path": str(file_path),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "type": file_type,
                    "header": header[:8].hex() if len(header) >= 8 else ""
                })
        
        return {"ok": True, "files": files}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.post("/media/clear")
def media_clear(confirm: int = Query(0)):
    """Cancella TUTTI i contenuti nella directory media del device.
    Per sicurezza richiede confirm=1 come query (es: POST /media/clear?confirm=1).
    """
    if confirm != 1:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Conferma mancante: usa ?confirm=1"})
    removed = 0
    errors = []
    try:
        for entry in MEDIA_DIR.iterdir():
            try:
                if entry.is_file() or entry.is_symlink():
                    entry.unlink(missing_ok=True); removed += 1
                elif entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True); removed += 1
            except Exception as e:
                errors.append(f"{entry.name}: {e}")
        return {"ok": True, "removed": removed, "errors": errors}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.get("/media/clear")
def media_clear_get(confirm: int = Query(0)):
    """Alias GET per compatibilità: richiede comunque confirm=1."""
    return media_clear(confirm)

@app.post("/settings/reload")
def api_settings_reload(data: Optional[dict] = Body(None)):
    """Aggiorna impostazioni runtime e ricrea la pipeline e lo splash se attivo.
    Accetta JSON tipo: {"USE_KMS": false, "USE_HW_DECODER": true, "TARGET_WIDTH": 1280, ... , "restart_play": true, "SPLASH_BLACK": false}
    """
    global USE_KMS, USE_HW_DECODER, TARGET_WIDTH, TARGET_HEIGHT, TARGET_FPS, SPLASH_BLACK
    data = data or {}
    changes = {}
    if "USE_KMS" in data:
        USE_KMS = bool(data["USE_KMS"]); changes["USE_KMS"] = USE_KMS
    if "USE_HW_DECODER" in data:
        USE_HW_DECODER = bool(data["USE_HW_DECODER"]); changes["USE_HW_DECODER"] = USE_HW_DECODER
    if "TARGET_WIDTH" in data:
        TARGET_WIDTH = int(data["TARGET_WIDTH"]); changes["TARGET_WIDTH"] = TARGET_WIDTH
    if "TARGET_HEIGHT" in data:
        TARGET_HEIGHT = int(data["TARGET_HEIGHT"]); changes["TARGET_HEIGHT"] = TARGET_HEIGHT
    if "TARGET_FPS" in data:
        TARGET_FPS = str(data["TARGET_FPS"]); changes["TARGET_FPS"] = TARGET_FPS
    if "SPLASH_BLACK" in data:
        SPLASH_BLACK = bool(data["SPLASH_BLACK"]); changes["SPLASH_BLACK"] = SPLASH_BLACK
    if "UDP_ENABLED" in data:
        global UDP_ENABLED
        UDP_ENABLED = bool(data["UDP_ENABLED"])
        changes["UDP_ENABLED"] = UDP_ENABLED
        if UDP_ENABLED:
            threading.Thread(target=_udp_thread, daemon=True).start()
    if "LOG_UDP_ENABLED" in data or "log_udp_enabled" in data:
        val = bool(data.get("LOG_UDP_ENABLED", data.get("log_udp_enabled")))
        set_log_udp_target(LOG_UDP_HOST, LOG_UDP_PORT, enabled=val, persist=False)
        changes["log_udp_enabled"] = LOG_UDP_ENABLED
    if "LOG_UDP_HOST" in data or "log_udp_host" in data:
        host = str(data.get("LOG_UDP_HOST", data.get("log_udp_host")))
        set_log_udp_target(host, LOG_UDP_PORT, enabled=True, persist=False)
        changes["log_udp_host"] = LOG_UDP_HOST
    if "LOG_UDP_PORT" in data or "log_udp_port" in data:
        try:
            port = int(data.get("LOG_UDP_PORT", data.get("log_udp_port")))
            set_log_udp_target(LOG_UDP_HOST, port, enabled=True, persist=False)
            changes["log_udp_port"] = LOG_UDP_PORT
        except Exception:
            pass
    if "AUTOPLAY_ENABLED" in data or "autoplay_enabled" in data:
        target = data.get("AUTOPLAY_ENABLED", data.get("autoplay_enabled"))
        restart_auto = data.get("restart_autoplay", True)
        _autoplay_set_enabled(bool(target), restart=bool(restart_auto))
        changes["autoplay_enabled"] = autoplay.get("enabled", False)

    restart_play = bool(data.get("restart_play", True))

    # Ricrea pipeline del player
    was_playing = (player["state"] == "playing")
    if player["pipeline"]:
        try:
            player["pipeline"].set_state(Gst.State.NULL)
        except Exception:
            pass
        player["pipeline"] = None; player["vb"] = None
    # Pulisci preload
    preloaded.update({"path": None, "pipeline": None, "vb": None})

    # Ricrea splash con nuovo sink/impostazioni se attivo
    splash_was_active = splash["active"]
    if splash_was_active:
        try:
            hide_splash()
        except Exception:
            pass
        show_splash_until_play()

    if was_playing and restart_play:
        ensure_pipeline();
        if player["vb"]:
            player["vb"].set_property("brightness", -1.0)
        player["pipeline"].set_state(Gst.State.PLAYING)
        player["state"] = "playing"
    else:
        player["state"] = "stopped"
    persist_settings()
    return {"ok": True, "changes": changes, "was_playing": was_playing, "splash_recreated": splash_was_active, "state": player["state"]}

@app.get("/settings/reload")
def api_settings_reload_get(
    USE_KMS: Optional[int] = None,
    USE_HW_DECODER: Optional[int] = None,
    TARGET_WIDTH: Optional[int] = None,
    TARGET_HEIGHT: Optional[int] = None,
    TARGET_FPS: Optional[str] = None,
    SPLASH_BLACK: Optional[int] = None,
    restart_play: int = 1,
    UDP_ENABLED: Optional[int] = None,
    LOG_UDP_ENABLED: Optional[int] = None,
    LOG_UDP_HOST: Optional[str] = None,
    LOG_UDP_PORT: Optional[int] = None,
):
    data = {}
    if USE_KMS is not None: data["USE_KMS"] = bool(USE_KMS)
    if USE_HW_DECODER is not None: data["USE_HW_DECODER"] = bool(USE_HW_DECODER)
    if TARGET_WIDTH is not None: data["TARGET_WIDTH"] = int(TARGET_WIDTH)
    if TARGET_HEIGHT is not None: data["TARGET_HEIGHT"] = int(TARGET_HEIGHT)
    if TARGET_FPS is not None: data["TARGET_FPS"] = str(TARGET_FPS)
    if SPLASH_BLACK is not None: data["SPLASH_BLACK"] = bool(SPLASH_BLACK)
    if UDP_ENABLED is not None: data["UDP_ENABLED"] = bool(UDP_ENABLED)
    if LOG_UDP_ENABLED is not None: data["LOG_UDP_ENABLED"] = bool(LOG_UDP_ENABLED)
    if LOG_UDP_HOST is not None: data["LOG_UDP_HOST"] = str(LOG_UDP_HOST)
    if LOG_UDP_PORT is not None: data["LOG_UDP_PORT"] = int(LOG_UDP_PORT)
    data["restart_play"] = bool(restart_play)
    return api_settings_reload(data)

@app.get("/autoplay")
def api_autoplay_status():
    current = None
    if playlist["items"] and 0 <= playlist["index"] < len(playlist["items"]):
        current = playlist["items"][playlist["index"]]
    return {
        "ok": True,
        "enabled": autoplay.get("enabled", False),
        "playlist_size": len(playlist["items"]),
        "current": current,
    }


@app.post("/autoplay")
def api_autoplay_update(payload: dict = Body(...)):
    if not isinstance(payload, dict):
        payload = {}
    enabled = bool(payload.get("enabled", False))
    restart = payload.get("restart", True)
    delay = payload.get("delay")
    delay_val: float | None
    try:
        delay_val = float(delay) if delay is not None else None
    except (TypeError, ValueError):
        delay_val = None
    _autoplay_set_enabled(enabled, restart=bool(restart), delay=delay_val)
    return api_autoplay_status()

def _safe_media_path(name: str) -> Path:
    """Return a MEDIA_DIR-confined path, stripping any directory tricks."""
    clean = Path(name).name  # drop path components
    clean = clean.replace("\\", "_")
    if clean in {"", ".", ".."}:
        raise ValueError("Nome file non valido")
    dest = MEDIA_DIR / clean
    try:
        dest.resolve().relative_to(MEDIA_DIR.resolve())
    except Exception as exc:
        raise ValueError("Percorso non consentito") from exc
    return dest


def _handle_download_asset(url: str, filename: Optional[str] = None):
    if not url:
        return JSONResponse(status_code=422, content={"ok": False, "error": "Campo 'url' mancante"})
    MEDIA_DIR.mkdir(exist_ok=True)
    name_source = filename or url.split("/")[-1] or "asset.bin"
    try:
        dest = _safe_media_path(name_source.strip())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})

    print(f"[DOWNLOAD] Scarico asset da: {url} -> {dest.name}", flush=True)

    def rep(got, total):
        if total > 0:
            pct = int(got * 100 / total)
            if pct % 20 == 0:
                print(f"[DOWNLOAD] Progresso: {pct}% ({got}/{total} bytes)", flush=True)

    try:
        download_to(dest, url, report=rep)

        # Controlla il file scaricato
        file_size = dest.stat().st_size
        print(f"[DOWNLOAD] File scaricato: {dest} ({file_size} bytes)", flush=True)

        # Controlla i primi bytes per identificare il tipo di file
        with open(dest, 'rb') as f:
            header = f.read(16)
            print(f"[DOWNLOAD] Header file: {header[:8].hex()}", flush=True)

            # Controlla se è un file MP4 valido
            if len(header) >= 8 and header[4:8] == b'ftyp':
                print("[DOWNLOAD] File riconosciuto come MP4", flush=True)
            elif len(header) >= 4 and (header[:4] == b'\x00\x00\x00\x20' or header[:4] == b'\x00\x00\x00\x18'):
                print("[DOWNLOAD] Possibile file MP4 con header diverso", flush=True)
            elif header.startswith(b'<!DOCTYPE') or header.startswith(b'<html'):
                print("[DOWNLOAD] ERRORE: Il server ha restituito HTML invece di un media", flush=True)
                dest.unlink(missing_ok=True)
                return JSONResponse(status_code=502, content={"ok": False, "error": "Sorgente ha restituito HTML"})
            else:
                print(f"[DOWNLOAD] ATTENZIONE: File non riconosciuto come media. Header: {header[:8]}", flush=True)

        return {"ok": True, "stored": str(dest), "size": file_size}
    except Exception as e:
        print(f"[DOWNLOAD] ERRORE: {e}", flush=True)
        dest.unlink(missing_ok=True)
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.post("/download_asset")
def download_asset(payload: dict = Body(...)):
    # Estrai parametri dal JSON body
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Body JSON richiesto"})
    url = payload.get("url")
    filename = payload.get("filename")
    return _handle_download_asset(url, filename)

@app.get("/download_asset")
def download_asset_get(url: str, filename: Optional[str] = None):
    """Alias GET per compatibilità: /download_asset?url=...&filename=..."""
    return _handle_download_asset(url, filename)

@app.post("/upload_asset")
async def upload_asset(file: UploadFile = File(...), filename: Optional[str] = Query(None)):
    """Upload diretto dal client GUI al player.

    Accetta multipart/form-data con campo 'file'.
    Parametro opzionale 'filename' per definire il nome di destinazione in media/.
    """
    MEDIA_DIR.mkdir(exist_ok=True)
    try:
        target_name = (filename or file.filename or "asset.bin").strip()
    except Exception:
        target_name = "asset.bin"
    try:
        dest = _safe_media_path(target_name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
    try:
        # Scrivi a chunk per ridurre RAM
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        try:
            os.replace(tmp, dest)
        except PermissionError as e:
            # Prova a forzare i permessi sul file di destinazione se già presente, poi ritenta
            try:
                os.chmod(dest, 0o644)
            except Exception:
                pass
            try:
                os.replace(tmp, dest)
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                return JSONResponse(
                    status_code=403,
                    content={
                        "ok": False,
                        "error": f"Permesso negato su '{dest}'. Esegui /maintenance/fix_permissions e riprova.",
                    },
                )
        size = dest.stat().st_size
        print(f"[UPLOAD] Ricevuto file: {dest} ({size} bytes)", flush=True)
        return {"ok": True, "stored": str(dest), "size": size}
    except Exception as e:
        try:
            tmp.unlink(missing_ok=True)  # type: ignore[name-defined]
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.get("/network/ethernet/status")
def ethernet_status():
    """Ritorna stato connessioni ethernet e indirizzi IP v4."""
    info = {"nmcli": None, "ip_addr": None}
    try:
        out = subprocess.check_output(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE,IP4.ADDRESS", "connection", "show"], text=True)
        info["nmcli"] = out.strip().splitlines()
    except Exception as e:
        info["nmcli"] = [f"errore: {e}"]
    try:
        out = subprocess.check_output(["ip", "-4", "addr", "show"], text=True)
        info["ip_addr"] = out.strip().splitlines()
    except Exception as e:
        info["ip_addr"] = [f"errore: {e}"]
    return {"ok": True, "info": info}

@app.post("/network/ethernet/config")
def ethernet_config(data: dict = Body(...)):
    """Configura IP statico su connessione ethernet principale via nmcli.
    Body: {"ip_cidr": "192.168.1.50/24", "gateway": "", "dns": ""}
    """
    if not isinstance(data, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Body JSON richiesto"})
    ip_cidr = data.get("ip_cidr")
    gateway = data.get("gateway", "")
    dns = data.get("dns", "")
    if not ip_cidr:
        return JSONResponse(status_code=422, content={"ok": False, "error": "Campo 'ip_cidr' mancante"})
    log = []
    try:
        # Trova prima connessione ethernet
        cmd_find = "nmcli -t -f NAME,TYPE connection show | awk -F: '$2==\"ethernet\"{print $1; exit}'"
        p = subprocess.Popen(["bash", "-lc", cmd_find], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = p.communicate(timeout=5)
        if p.returncode != 0:
            return JSONResponse(status_code=500, content={"ok": False, "error": f"nmcli find failed: {err.strip()}"})
        eth_con = out.strip() or ""
        if not eth_con:
            return JSONResponse(status_code=404, content={"ok": False, "error": "Nessuna connessione ethernet trovata"})
        log.append(f"Connessione ethernet: {eth_con}")
        # Applica configurazione con sudo -n
        cmds = [
            f"nmcli con mod '{eth_con}' ipv4.method manual ipv4.addresses '{ip_cidr}'",
            f"nmcli con mod '{eth_con}' ipv4.gateway '{gateway}'",
            f"nmcli con mod '{eth_con}' ipv4.dns '{dns}'",
            f"nmcli con up '{eth_con}'"
        ]
        for c in cmds:
            rc = subprocess.call(["sudo", "-n", "bash", "-lc", c])
            log.append(f"$ {c} -> rc={rc}")
            if rc != 0:
                return JSONResponse(status_code=500, content={"ok": False, "error": f"Comando fallito: {c}", "log": log})
        return {"ok": True, "log": log}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e), "log": log})

def _start_download_update(url: str, version: Optional[str], callback_url: Optional[str], start_at: Optional[float] = None):
    # Evita doppio avvio se già in corso
    if current_update["status"] == "downloading":
        return {"ok": True, "status": "already-downloading", "progress": current_update.get("progress")}

    current_update["available"] = version or "unknown"
    current_update["callback_url"] = callback_url
    current_update["progress"] = 0
    current_update["error"] = None
    current_update["log"] = current_update["log"][-100:]
    current_update["status"] = "downloading"
    current_update["bytes_done"] = 0
    current_update["bytes_total"] = 0
    # Scegli un percorso scrivibile per il pacchetto scaricato
    upd_dir = _pick_update_dir()
    dest = upd_dir / "update_pkg.bin"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    # Memorizza path per apply successivo
    current_update["pkg_path"] = str(dest)

    log_update(f"Inizio download update: url={url} version={current_update['available']}")

    def worker():
        try:
            # Attendi fino a start_at se fornito
            if start_at:
                delay = max(0.0, float(start_at) - time.time())
                if delay > 0:
                    log_update(f"Attendo {delay:.2f}s per start_at")
                    time.sleep(delay)
            def rep(got, total):
                current_update["bytes_done"] = got
                current_update["bytes_total"] = total
                if total:
                    pct = int(got * 100 / total)
                    if pct != current_update.get("progress"):
                        current_update["progress"] = pct
                        if pct % 10 == 0:
                            log_update(f"Download {pct}% ({got}/{total} bytes)")
            download_to(dest, url, report=rep)

            # Verifica header file scaricato per intercettare HTML (404/errore)
            with open(dest, "rb") as f:
                header = f.read(64)
            if header.startswith(b"<!DOCTYPE") or header.startswith(b"<html"):
                raise RuntimeError("Il server ha restituito HTML (probabile 404/errore) non un archivio update")

            current_update["progress"] = 100
            log_update("Download completato")
            current_update["status"] = "downloaded"
        except Exception as e:
            current_update["status"] = "error"
            current_update["error"] = str(e)
            log_update(f"Errore download: {e}")

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "status": "started", "progress": 0}

@app.post("/download_update")
def download_update_post(payload: dict = Body(...)):
    """Avvia il download via POST JSON: {"url": ..., "version": ..., "callback_url": ...}"""
    url = payload.get("url") if isinstance(payload, dict) else None
    version = payload.get("version") if isinstance(payload, dict) else None
    callback_url = payload.get("callback_url") if isinstance(payload, dict) else None
    start_at = payload.get("start_at") if isinstance(payload, dict) else None
    if not url:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Campo 'url' mancante"})
    return _start_download_update(url, version, callback_url, start_at)

@app.get("/download_update")
def download_update_get(url: str, version: Optional[str] = None, callback_url: Optional[str] = None, start_at: Optional[float] = None):
    """Avvia il download via GET con query params (?url=...&version=...)."""
    if not url:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Parametro 'url' richiesto"})
    return _start_download_update(url, version, callback_url, start_at)

@app.post("/media/playlist")
def api_playlist(loop: bool = Body(True, embed=True)):
    """Crea una playlist con tutti i file validi dentro media/ e avvia dal primo.
    Parametri:
      - loop (bool): se True la playlist riparte da capo.
    Filtra solo file video riconosciuti da validate_media_file.
    """
    items = []
    scan = []
    for p in sorted(MEDIA_DIR.iterdir()):
        if not p.is_file():
            continue
        # esegui check header
        valid = validate_media_file(p)
        # determina tipo
        ftype = "unknown"; header_hex = ""
        try:
            with open(p, 'rb') as fh:
                header = fh.read(16)
                header_hex = header[:8].hex() if len(header) >= 8 else ""
                if len(header) >= 8 and header[4:8] == b'ftyp':
                    ftype = "mp4"
                elif header.startswith(b'\x1a\x45\xdf\xa3'):
                    ftype = "mkv"
                elif len(header) >= 12 and header.startswith(b'RIFF') and header[8:12] == b'AVI ':
                    ftype = "avi"
                elif header.startswith(b'<!DOCTYPE') or header.startswith(b'<html'):
                    ftype = "html"
        except Exception:
            pass
        scan.append({"path": str(p), "valid": valid, "type": ftype, "header": header_hex})
        if valid:
            items.append(str(p))
        else:
            print(f"[PLAYLIST] Ignoro (non valido): {p.name}", flush=True)
    if not items:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Nessun file video valido in media/"})
    playlist["items"] = items
    playlist["index"] = 0
    playlist["loop"] = loop
    print(f"[PLAYLIST] Creata playlist con {len(items)} elementi (loop={loop})", flush=True)
    stop_play()
    start_play_with_path(items[0])
    return {"ok": True, "count": len(items), "current": items[0], "loop": loop, "items": items, "scan": scan}

@app.post("/playlist/apply")
def api_playlist_apply(payload: dict = Body(...)):
    """Applica una playlist esplicita e pre-carica il primo elemento in pausa a t=0.

    Body JSON atteso:
      { "items": ["file1.mp4", "file2.mp4", ...], "loop": true }

    - Gli elementi possono essere nomi file dentro media/ oppure percorsi assoluti già dentro media/.
    - Non avvia la riproduzione: prepara il primo elemento per fast-start.
    - Espone show_ready=true in /status e invia un evento UDP alla GUI se sottoscritta.
    """
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Body JSON richiesto"})
    items_in = payload.get("items")
    loop = bool(payload.get("loop", True))
    if not isinstance(items_in, list) or not items_in:
        return JSONResponse(status_code=422, content={"ok": False, "error": "Campo 'items' mancante o vuoto"})

    resolved: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []
    for it in items_in:
        try:
            name = str(it).strip()
        except Exception:
            continue
        if not name:
            continue
        p = Path(name)
        if not p.is_absolute():
            p = MEDIA_DIR / name
        # Confina a MEDIA_DIR
        try:
            p = p.resolve()
            if not str(p).startswith(str(MEDIA_DIR.resolve())):
                invalid.append(name); continue
        except Exception:
            invalid.append(name); continue
        if not p.exists():
            missing.append(name); continue
        # opzionale: verifica header valido
        if not validate_media_file(p):
            invalid.append(name); continue
        resolved.append(str(p))

    if not resolved:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Nessun item valido trovato", "missing": missing, "invalid": invalid})

    # Aggiorna stato playlist ma non avvia
    playlist["items"] = resolved
    playlist["index"] = 0
    playlist["loop"] = loop

    first = resolved[0]
    # Precarica il primo elemento con fast-start per backend
    try:
        if current_framework["name"] == "gst":
            prepare_pipeline_for(first)
            faststart["prepared_path"] = first
        else:
            ensure_backend()
            be = current_framework.get("backend")
            if be is None:
                raise RuntimeError("Backend non disponibile")
            if hasattr(be, "faststart_prepare"):
                be.faststart_prepare(first)  # type: ignore[attr-defined]
                faststart["prepared_path"] = first
            else:
                # Fallback: non tutti i backend supportano faststart esplicito; non avviare
                faststart["prepared_path"] = first
    except Exception as e:
        try:
            gui_log("playlist_apply error", level="ERROR", kind="playlist", data={"error": str(e)})
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

    # Marca readiness e notifica via UDP log
    try:
        show_state.update({"ready": True, "for": first, "ts": time.time()})
        gui_log("show_ready", level="INFO", kind="playlist", data={"for": first})
    except Exception:
        pass

    return {"ok": True, "count": len(resolved), "prepared": first, "loop": loop, "missing": missing, "invalid": invalid}

@app.get("/playlist/status")
def api_playlist_status():
    return {
        "ok": True,
        "items": playlist["items"],
        "index": playlist["index"],
        "current": playlist["items"][playlist["index"]] if (playlist["items"] and 0 <= playlist["index"] < len(playlist["items"])) else None,
        "loop": playlist["loop"],
    }

@app.post("/playlist/next")
def api_playlist_next():
    if not playlist["items"]:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Playlist vuota"})
    if playlist["index"] < 0:
        playlist["index"] = 0
    next_i = playlist["index"] + 1
    if next_i >= len(playlist["items"]):
        if playlist["loop"]:
            next_i = 0
        else:
            return JSONResponse(status_code=400, content={"ok": False, "error": "Fine playlist"})
    playlist["index"] = next_i
    next_path = playlist["items"][next_i]
    print(f"[PLAYLIST] NEXT -> index={next_i} file={next_path}", flush=True)
    start_play_with_path(next_path)
    return {"ok": True, "current": next_path, "index": next_i}

@app.post("/playlist/prev")
def api_playlist_prev():
    if not playlist["items"]:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Playlist vuota"})
    if playlist["index"] < 0:
        playlist["index"] = 0
    prev_i = playlist["index"] - 1
    if prev_i < 0:
        if playlist["loop"]:
            prev_i = len(playlist["items"]) - 1
        else:
            return JSONResponse(status_code=400, content={"ok": False, "error": "Inizio playlist"})
    playlist["index"] = prev_i
    prev_path = playlist["items"][prev_i]
    print(f"[PLAYLIST] PREV -> index={prev_i} file={prev_path}", flush=True)
    start_play_with_path(prev_path)
    return {"ok": True, "current": prev_path, "index": prev_i}

# Rimosso endpoint /playlist/take: NEXT/PREV avviano direttamente la riproduzione

@app.post("/playlist/loop")
def api_playlist_loop(on: int = Query(1)):
    playlist["loop"] = (on == 1)
    return {"ok": True, "loop": playlist["loop"]}

@app.post("/update")
def update_apply(restart: bool = Body(True, embed=True), start_at: Optional[float] = Body(None, embed=True)):
    # Determina percorso del pacchetto scaricato
    pkg_path = current_update.get("pkg_path")
    pkg = Path(pkg_path) if pkg_path else (APP_DIR / "update_pkg.bin")
    if not pkg.exists():
        return JSONResponse(status_code=400, content={"ok": False, "error": "Nessun pacchetto scaricato"})
    current_update["status"] = "applying"
    if start_at:
        delay = max(0.0, float(start_at) - time.time())
        if delay > 0:
            log_update(f"Apply programmata tra {delay:.2f}s (start_at)")
            time.sleep(delay)
    log_update("Applico update…")
    try:
        apply_update(pkg)
        pkg.unlink(missing_ok=True)
        if (APP_DIR/"VERSION").exists():
            global VERSION
            VERSION = (APP_DIR/"VERSION").read_text().strip()
        current_update["status"] = "ok"
        log_update("Update applicato con successo")
        if restart:
            log_update("Riavvio servizio…")
            os._exit(0)
        return {"ok": True}
    except Exception as e:
        current_update["status"] = "error"
        log_update(f"Errore apply: {e}")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

@app.post("/maintenance/run_setup")
def api_run_setup(force: bool = Body(False, embed=True)):
    if maintenance["status"] == "running" and not force:
        return JSONResponse(status_code=409, content={"ok": False, "error": "setup già in esecuzione"})
    setup_path = str(APP_DIR / "setup.sh")
    if not os.path.exists(setup_path):
        return JSONResponse(status_code=404, content={"ok": False, "error": "setup.sh non trovato"})
    maintenance["status"] = "running"; maintenance["error"] = None
    log_maintenance("Avvio setup.sh…")

    def worker():
        try:
            # Assicurati che sia eseguibile
            try:
                os.chmod(setup_path, 0o755)
            except Exception:
                pass
            # Esegui direttamente lo script con sudo (richiede regola sudoers specifica)
            cmd = ["sudo", "-n", setup_path]
            try:
                p = subprocess.Popen(cmd, cwd=str(APP_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            except Exception:
                # Fallback: prova con sudo bash PATH (se sudoers permette bash)
                p = subprocess.Popen(["sudo", "-n", "/bin/bash", setup_path], cwd=str(APP_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            assert p.stdout is not None
            for line in p.stdout:
                log_maintenance(line.rstrip())
            rc = p.wait()
            if rc == 0:
                maintenance["status"] = "ok"; log_maintenance("setup.sh completato con successo")
            else:
                maintenance["status"] = "error"; maintenance["error"] = f"exit code {rc}"; log_maintenance(f"setup.sh terminato con errore (rc={rc})")
        except Exception as e:
            maintenance["status"] = "error"; maintenance["error"] = str(e); log_maintenance(f"Errore esecuzione setup: {e}")

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "status": maintenance["status"]}

@app.post("/maintenance/fix_permissions")
def api_fix_permissions():
    """Tenta di correggere ownership/permessi su APP_DIR per consentire l'update.

    Esegue pochi comandi mirati via sudo (richiede regola sudoers già installata da setup.sh):
      - chown -R APP_USER:APP_GROUP APP_DIR
      - setfacl ricorsivi e di default per u:video,g:video
    Non riavvia la macchina.
    """
    if maintenance["status"] == "running":
        return JSONResponse(status_code=409, content={"ok": False, "error": "maintenance già in esecuzione"})
    maintenance["status"] = "running"; maintenance["error"] = None
    log_maintenance("Fix permissions su APP_DIR…")

    app_dir = str(APP_DIR)
    app_user = os.environ.get("APP_USER", "video")
    app_group = os.environ.get("APP_GROUP", "video")

    def worker():
        try:
            cmds = [
                ["sudo", "-n", "chown", "-R", f"{app_user}:{app_group}", app_dir],
                ["sudo", "-n", "setfacl", "-R", "-m", f"u:{app_user}:rwx,g:{app_group}:rwx", app_dir],
                ["sudo", "-n", "setfacl", "-dR", "-m", f"u:{app_user}:rwx,g:{app_group}:rwx", app_dir],
            ]
            for cmd in cmds:
                try:
                    log_maintenance(f"Eseguo: {' '.join(cmd)}")
                    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, text=True)
                except subprocess.CalledProcessError as exc:
                    out = (exc.stderr or exc.stdout or "").strip()
                    log_maintenance(f"Comando fallito: {' '.join(cmd)} => {out}")
                    raise
                except Exception as exc:
                    log_maintenance(f"Errore: {exc}")
                    raise
            maintenance["status"] = "ok"
            log_maintenance("Permessi corretti.")
        except Exception as e:
            maintenance["status"] = "error"; maintenance["error"] = str(e)
        
    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "status": maintenance["status"]}

@app.get("/maintenance/status")
def api_maintenance_status():
    return {"ok": True, "status": maintenance["status"], "error": maintenance["error"], "last_logs": maintenance["log"][-50:]}

@app.post("/system/reboot")
def system_reboot():
    print("[REBOOT] Richiesta reboot di sistema", flush=True)
    result = {"ok": False, "message": "", "attempts": []}

    def worker():
        cmds = [
            ["sudo", "-n", "loginctl", "reboot"],
            ["sudo", "-n", "systemctl", "reboot"],
            ["sudo", "-n", "reboot"],
            ["loginctl", "reboot"],
            ["systemctl", "reboot"],
            ["reboot"],
        ]
        for cmd in cmds:
            try:
                print(f"[REBOOT] Eseguo: {' '.join(cmd)}", flush=True)
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, text=True)
                print("[REBOOT] Comando inviato con successo", flush=True)
                GLib.idle_add(main_loop.quit)
                result.update({"ok": True, "message": "Reboot in corso", "attempts": []})
                return
            except subprocess.CalledProcessError as exc:
                msg = (exc.stderr or exc.stdout or "").strip()
                print(f"[REBOOT] Comando fallito ({' '.join(cmd)}): {msg}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": msg})
            except subprocess.TimeoutExpired:
                print(f"[REBOOT] Timeout eseguendo {' '.join(cmd)}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": "timeout"})
            except FileNotFoundError:
                print(f"[REBOOT] Comando non trovato: {cmd[0]}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": "not_found"})
            except Exception as exc:
                print(f"[REBOOT] Errore imprevisto con {' '.join(cmd)}: {exc}", flush=True)
                result["attempts"].append({"cmd": cmd, "error": str(exc)})
        result.update({"ok": False, "message": "Nessun comando reboot accettato"})

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse(status_code=202, content={"ok": True, "message": "Reboot richiesto"})

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=APP_PORT)


# .\.venv\bin\activate
